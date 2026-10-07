"""PostgreSQL CRUD/API tests with fake Firebase; never use the application DB URL.

Set FINE_TEST_DATABASE_URL to a disposable loopback PostgreSQL database whose
name ends in _tests. Each run creates and removes its own isolated schema.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from app_backend.services.auth_context import get_authenticated_context
from app_backend.services.main import app as consolidated_app
from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_02_hr_payroll.logic import (
    employee_master_get_data as employee_get,
    fine_master_create_data as fine_create,
    fine_master_get_data as fine_get,
    fine_master_update_data as fine_update,
    fine_master_delete_data as fine_delete,
    payroll_module,
)
from app_backend.services.service_02_hr_payroll.integrations import (
    firebase_fine_attachment_upload as fine_upload,
    firebase_fine_attachment_download as fine_download,
)
from app_backend.services.service_02_hr_payroll.logic.test_fine_management_logic import fine_payload


TEST_DATABASE_URL = os.getenv("FINE_TEST_DATABASE_URL")
SERVICE_DIR = Path(__file__).resolve().parents[1]


class FakeBlob:
    def __init__(self, path):
        self.path = path
        self.uploaded = False
        self.deleted = False
        self.content_type = None
        self.signing_arguments = None

    def upload_from_file(self, stream, content_type=None, rewind=False):
        if rewind:
            stream.seek(0)
        self.content = stream.read()
        self.content_type = content_type
        self.uploaded = True

    def exists(self):
        return self.uploaded and not self.deleted

    def delete(self):
        self.deleted = True

    def reload(self):
        pass

    def generate_signed_url(self, **kwargs):
        self.signing_arguments = kwargs
        return f"https://signed.example.invalid/{self.path}"


class FakeBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, path):
        return self.blobs.setdefault(path, FakeBlob(path))


@unittest.skipUnless(TEST_DATABASE_URL, "FINE_TEST_DATABASE_URL is required for isolated PostgreSQL tests.")
class FinePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(TEST_DATABASE_URL)
        if (url.get_backend_name() != "postgresql" or url.host not in ("localhost", "127.0.0.1")
                or not (url.database or "").endswith("_tests")):
            raise RuntimeError("Use a disposable loopback PostgreSQL database ending in _tests.")
        cls.schema = f"fines_test_{uuid4().hex}"
        cls.admin = create_engine(url)
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.addClassCleanup(cls.cleanup_schema)
        cls.engine = create_engine(url, connect_args={"options": f"-csearch_path={cls.schema},public"})
        with cls.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE organization_master (org_id_pk BIGSERIAL PRIMARY KEY, org_name TEXT)")
            conn.exec_driver_sql("CREATE TABLE department_master (dep_id_pk BIGSERIAL PRIMARY KEY, dep_org_id_fk BIGINT NOT NULL)")
            conn.exec_driver_sql((SERVICE_DIR / "data/employee_master_creation.sql").read_text())
            conn.exec_driver_sql(
                (SERVICE_DIR / "data/simplified_payroll_module_ddl.sql").read_text().replace(
                    "public.", f'"{cls.schema}".'
                )
            )
        cls.apply_migration()

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')
        cls.admin.dispose()

    @classmethod
    def apply_migration(cls):
        with cls.engine.begin() as conn:
            conn.exec_driver_sql((SERVICE_DIR / "data/20260929_add_fines_module.sql").read_text().replace(
                "public.", f'"{cls.schema}".'
            ))

    def setUp(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                TRUNCATE fine_master, payroll_correction_recovery, payroll_employee_detail,
                    payroll_adjustment, payroll_run, employee_master, organization_master
                RESTART IDENTITY CASCADE
            """)
            conn.exec_driver_sql("INSERT INTO organization_master(org_id_pk, org_name) VALUES (1,'Test A'),(2,'Test B')")
            conn.exec_driver_sql("""
                INSERT INTO employee_master(empl_id_pk, empl_org_id_fk, employee_id, employee_name,
                    employee_designation, monthly_basic_salary)
                VALUES (1,1,'EMP-A','Same Name','Accountant',3000),
                       (2,1,'EMP-B','Same Name','Driver',3000),
                       (3,2,'EMP-C','Same Name','Driver',3000)
            """)
        for module in (fine_create, fine_get, fine_update, fine_delete, fine_upload, fine_download, employee_get):
            patcher = patch.object(module, "db_engine", return_value=self.engine)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = {"authenticated": True, "user": {"user_id": 7,
                        "user_principal_name": "fine.test@example.invalid"}, "organization": {"org_id": 1}}
        old_overrides = dict(consolidated_app.dependency_overrides)
        old_hr_overrides = dict(hr_api.app.dependency_overrides)
        self.addCleanup(self.restore_overrides, old_overrides, old_hr_overrides)
        consolidated_app.dependency_overrides[get_authenticated_context] = lambda: self.context
        # Consolidated routes retain their originating service dependency provider.
        hr_api.app.dependency_overrides[get_authenticated_context] = lambda: self.context
        self.client = TestClient(consolidated_app)
        self.addCleanup(self.client.close)
        self.bucket = FakeBucket()
        for module in (fine_upload, fine_download):
            patcher = patch.object(module, "initialize_firebase_app")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(fine_upload.storage, "bucket", return_value=self.bucket)
        patcher.start()
        self.addCleanup(patcher.stop)

    def restore_overrides(self, overrides, hr_overrides):
        consolidated_app.dependency_overrides.clear()
        consolidated_app.dependency_overrides.update(overrides)
        hr_api.app.dependency_overrides.clear()
        hr_api.app.dependency_overrides.update(hr_overrides)

    def create(self, **changes):
        result = fine_create.create_fine_master(fine_payload(**changes))
        self.assertNotIn("error", result, result)
        return result

    def identity(self, fine_id, **changes):
        return {"fine_id": fine_id, "authenticated_org_id": 1,
                "authenticated_user_principal_name": "fine.test@example.invalid", **changes}

    def get(self, fine_id):
        result = fine_get.get_fine_master(self.identity(fine_id))
        self.assertNotIn("error", result, result)
        return result

    def upload(self, fine_id, name="receipt.pdf"):
        result = fine_upload.upload_fine_attachment(self.identity(fine_id), BytesIO(b"fine receipt"), name, "application/pdf")
        self.assertNotIn("error", result, result)
        return result

    def scalar(self, query, params=None):
        with self.engine.begin() as conn:
            return conn.execute(text(query), params or {}).scalar_one()

    def test_create_uses_employee_pk_without_driver_qualification(self):
        result = self.create(fine_empl_id_fk=1)
        self.assertEqual(result["employee_id"], "EMP-A")
        self.assertEqual(result["fine_empl_id_fk"], 1)
        self.assertIsNone(result["fine_attachment_path"])
        # There is deliberately no Fleet table in this test database.
        self.assertEqual(self.scalar("select count(*) from fine_master"), 1)

    def test_small_amount_and_split_are_persisted_exactly(self):
        result = self.create(amount_paid="0.01", recovery_split="25")
        with self.engine.begin() as conn:
            row = conn.execute(text("select amount_paid, recovery_split from fine_master")).one()
        self.assertEqual(tuple(row), (Decimal("0.01"), Decimal("25.00")))
        self.assertEqual(self.scalar("select count(*) from payroll_adjustment"), 0)
        self.assertNotIn("remaining_amount", result)
        self.assertNotIn("installments", result)

    def test_all_conditional_combinations_and_employee_branch(self):
        for accountable, payer in (("DRIVER", "DRIVER"), ("URBAN_EXPRESS", "DRIVER"),
                                   ("URBAN_EXPRESS", "URBAN_EXPRESS")):
            result = self.create(fine_accountability=accountable, payment_authority=payer,
                                 amount_paid=None, recovery_split=None)
            self.assertIsNone(result["amount_paid"])
        result = self.create(fine_on="EMPLOYEE", fine_accountability=None, payment_authority=None,
                             amount_paid=None, recovery_split=None)
        self.assertEqual(result["fine_empl_id_fk"], 1)

    def test_invalid_or_foreign_employee_is_rejected_without_writes(self):
        for employee in (3, 999):
            with self.subTest(employee=employee):
                result = fine_create.create_fine_master(fine_payload(fine_empl_id_fk=employee))
                self.assertIn("not found", result["error"])
        self.assertEqual(self.scalar("select count(*) from fine_master"), 0)

    def test_create_forces_pointer_and_audit_fields(self):
        result = self.create(fine_attachment_path="gs://forged/path", created_by="spoofed", updated_by="spoofed",
                             fine_org_id_fk=2)
        self.assertIsNone(result["fine_attachment_path"])
        self.assertEqual(result["fine_org_id_fk"], 1)
        self.assertEqual(result["created_by"], "fine.test@example.invalid")
        self.assertEqual(result["updated_by"], "fine.test@example.invalid")

    def test_get_missing_and_foreign_fines(self):
        fine_id = self.create()["fine_id_pk"]
        for params in (self.identity(999), self.identity(fine_id, authenticated_org_id=2)):
            self.assertIn("not found", fine_get.get_fine_master(params)["error"])

    def test_list_is_org_scoped_and_includes_employee_names(self):
        self.create()
        self.create(authenticated_org_id=2, fine_empl_id_fk=3)
        rows = fine_get.list_fine_master({"authenticated_org_id": 1})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["employee_id"], "EMP-A")
        self.assertEqual(rows[0]["employee_name"], "Same Name")
        self.assertNotIn("download_url", rows[0])

    def test_update_employee_amount_split_and_date(self):
        fine_id = self.create()["fine_id_pk"]
        result = fine_update.update_fine_master(self.identity(
            fine_id, fine_empl_id_fk=2, amount_paid="999.99", recovery_split="30", fine_date="2026-12-31",
        ))
        self.assertNotIn("error", result, result)
        self.assertEqual(result["employee_id"], "EMP-B")
        self.assertEqual(Decimal(str(result["amount_paid"])), Decimal("999.99"))
        self.assertEqual(Decimal(str(result["recovery_split"])), Decimal("30"))

    def test_partial_update_preserves_omitted_fields_and_pointer(self):
        fine_id = self.create()["fine_id_pk"]
        pointer = self.upload(fine_id)["fine_attachment_path"]
        result = fine_update.update_fine_master(self.identity(fine_id, amount_paid="300", fine_attachment_path="evil"))
        self.assertNotIn("error", result, result)
        self.assertEqual(Decimal(str(result["recovery_split"])), Decimal("50"))
        self.assertEqual(result["fine_attachment_path"], pointer)

    def test_update_between_conditional_branches(self):
        fine_id = self.create()["fine_id_pk"]
        result = fine_update.update_fine_master(self.identity(
            fine_id, payment_authority="DRIVER", amount_paid=None, recovery_split=None,
        ))
        self.assertNotIn("error", result, result)
        self.assertIsNone(result["amount_paid"])
        result = fine_update.update_fine_master(self.identity(
            fine_id, payment_authority="URBAN_EXPRESS", amount_paid="0.01", recovery_split="25",
        ))
        self.assertNotIn("error", result, result)
        result = fine_update.update_fine_master(self.identity(
            fine_id, fine_on="EMPLOYEE", fine_accountability=None, payment_authority=None,
            amount_paid=None, recovery_split=None,
        ))
        self.assertNotIn("error", result, result)
        self.assertEqual(result["fine_on"], "EMPLOYEE")

    def test_invalid_update_preserves_original(self):
        fine_id = self.create()["fine_id_pk"]
        for changes in ({"fine_empl_id_fk": 3}, {"amount_paid": None}, {"recovery_split": "101"},
                        {"payment_authority": "DRIVER"}):
            result = fine_update.update_fine_master(self.identity(fine_id, **changes))
            self.assertIn("error", result)
        self.assertEqual(Decimal(str(self.get(fine_id)["amount_paid"])), Decimal("1000"))

    def test_cross_org_update_and_delete_are_rejected(self):
        fine_id = self.create()["fine_id_pk"]
        self.assertIn("not found", fine_update.update_fine_master(
            self.identity(fine_id, authenticated_org_id=2, amount_paid="5"))["error"])
        self.assertIn("not found", fine_delete.delete_fine_master(
            self.identity(fine_id, authenticated_org_id=2))["error"])
        self.assertEqual(self.get(fine_id)["fine_id_pk"], fine_id)

    def test_delete_removes_record_and_keeps_uploaded_object(self):
        fine_id = self.create()["fine_id_pk"]
        pointer = self.upload(fine_id)["fine_attachment_path"]
        result = fine_delete.delete_fine_master(self.identity(fine_id))
        self.assertNotIn("error", result, result)
        self.assertEqual(result["fine_id_pk"], fine_id)
        self.assertEqual(result["message"], "Successfully deleted Fine.")
        self.assertEqual(self.scalar("select count(*) from fine_master"), 0)
        self.assertEqual(fine_get.list_fine_master({"authenticated_org_id": 1}), [])
        self.assertIn("not found", fine_get.get_fine_master(self.identity(fine_id))["error"])
        self.assertIn("not found", fine_delete.delete_fine_master(self.identity(fine_id))["error"])
        self.assertTrue(self.bucket.blob(pointer).exists())
        self.assertIn("not found", fine_download.download_fine_attachment(self.identity(fine_id))["error"])

    def test_delete_non_payment_fine(self):
        fine_id = self.create(payment_authority="DRIVER", amount_paid=None, recovery_split=None)["fine_id_pk"]
        self.assertNotIn("error", fine_delete.delete_fine_master(self.identity(fine_id)))
        self.assertEqual(self.scalar("select count(*) from fine_master"), 0)

    def test_deleted_fine_cannot_be_updated(self):
        fine_id = self.create()["fine_id_pk"]
        fine_delete.delete_fine_master(self.identity(fine_id))
        self.assertIn("not found", fine_update.update_fine_master(self.identity(fine_id, amount_paid="500"))["error"])

    def test_create_and_update_roll_back_on_error(self):
        with patch.object(fine_create, "read_fines", side_effect=RuntimeError("read failed")):
            with self.assertLogs(fine_create.logger, level="ERROR"):
                self.assertIn("error", fine_create.create_fine_master(fine_payload()))
        self.assertEqual(self.scalar("select count(*) from fine_master"), 0)
        fine_id = self.create()["fine_id_pk"]
        with patch.object(fine_update, "read_fines", side_effect=RuntimeError("read failed")):
            with self.assertLogs(fine_update.logger, level="ERROR"):
                self.assertIn("error", fine_update.update_fine_master(self.identity(fine_id, amount_paid="500")))
        self.assertEqual(Decimal(str(self.get(fine_id)["amount_paid"])), Decimal("1000"))

    def test_concurrent_partial_updates_preserve_both_changes(self):
        fine_id = self.create()["fine_id_pk"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(fine_update.update_fine_master, self.identity(fine_id, amount_paid="700"))
            b = executor.submit(fine_update.update_fine_master, self.identity(fine_id, recovery_split="30"))
            for future in (a, b):
                self.assertNotIn("error", future.result(timeout=10))
        record = self.get(fine_id)
        self.assertEqual(Decimal(str(record["amount_paid"])), Decimal("700"))
        self.assertEqual(Decimal(str(record["recovery_split"])), Decimal("30"))

    def test_repeated_update_does_not_duplicate_fines(self):
        fine_id = self.create()["fine_id_pk"]
        for _ in range(3):
            self.assertNotIn("error", fine_update.update_fine_master(self.identity(fine_id, recovery_split="100")))
        self.assertEqual(self.scalar("select count(*) from fine_master"), 1)
        self.assertEqual(self.scalar("select count(*) from payroll_adjustment"), 0)

    def test_migration_is_idempotent_and_has_no_recovery_table(self):
        fine_id = self.create()["fine_id_pk"]
        self.apply_migration()
        self.apply_migration()
        self.assertEqual(self.get(fine_id)["fine_id_pk"], fine_id)
        self.assertEqual(self.scalar("""
            select count(*) from information_schema.tables
            where table_schema = :schema and table_name = 'fine_recovery_installment'
        """, {"schema": self.schema}), 0)

    def test_database_has_no_constraints_or_removed_fields(self):
        self.assertEqual(self.scalar("""
            select count(*) from pg_constraint c
            join pg_class t on t.oid = c.conrelid
            join pg_namespace n on n.oid = t.relnamespace
            where n.nspname = :schema and t.relname = 'fine_master'
        """, {"schema": self.schema}), 0)
        with self.engine.begin() as conn:
            columns = conn.execute(text("""
                select column_name, is_nullable from information_schema.columns
                where table_schema = :schema and table_name = 'fine_master'
            """), {"schema": self.schema}).all()
        self.assertTrue(all(nullable == "YES" for _, nullable in columns))
        self.assertTrue({"fine_status", "cancelled_by", "cancelled_at", "cancellation_reason"}.isdisjoint(
            name for name, _ in columns
        ))
        with self.engine.begin() as conn:
            conn.exec_driver_sql("insert into fine_master default values")
            conn.exec_driver_sql("""
                insert into fine_master(fine_id_pk, fine_org_id_fk, fine_empl_id_fk,
                    fine_on, amount_paid, recovery_split)
                values (null, -1, 999, 'MANUAL', -1, 101), (1, null, null, null, null, null)
            """)
        self.assertEqual(self.scalar("select count(*) from fine_master"), 3)

    def test_monthly_payroll_does_not_consume_fine_data(self):
        fine_id = self.create(amount_paid="10000", recovery_split="25")["fine_id_pk"]
        payroll = object.__new__(payroll_module.MonthlyPayrollRun)
        payroll.payroll_engine = self.engine
        run = payroll.create_payroll_run_header({
            "payroll_org_id_fk": 1, "payroll_run_code": "FINE_NO_INTEGRATION", "payroll_year": 2026,
            "payroll_month": 9, "payroll_period_start_date": "2026-09-01",
            "payroll_period_end_date": "2026-09-30", "created_by": "test",
        })
        self.assertNotIn("error", run, run)
        result = payroll.process_payroll_run({
            "payroll_run_id": run["payroll_run_id_pk"], "payroll_org_id_fk": 1, "employee_ids": [1],
        })
        self.assertNotIn("error", result, result)
        self.assertEqual(self.scalar("select fine_deduction from payroll_employee_detail"), Decimal("0"))
        self.assertEqual(self.scalar("select total_deduction from payroll_employee_detail"), Decimal("0"))
        self.assertEqual(self.scalar("select net_salary from payroll_employee_detail"), Decimal("3000"))
        self.assertEqual(self.scalar("select count(*) from payroll_adjustment"), 0)
        for status in ("PROCESSED", "APPROVED", "PAID"):
            with self.engine.begin() as conn:
                conn.execute(text("update payroll_run set payroll_status = :status"), {"status": status})
            self.create(amount_paid="0.01", recovery_split="25")
        self.assertNotIn("error", fine_update.update_fine_master(self.identity(fine_id, amount_paid="20")))
        self.assertNotIn("error", fine_delete.delete_fine_master(self.identity(fine_id)))
        self.assertEqual(self.scalar("select net_salary from payroll_employee_detail"), Decimal("3000"))

    def test_existing_manual_fine_adjustment_still_works(self):
        self.create()
        payroll = object.__new__(payroll_module.MonthlyPayrollRun)
        payroll.payroll_engine = self.engine
        adjustment = payroll.create_payroll_adjustment({
            "payroll_adjustment_org_id_fk": 1, "payroll_adjustment_empl_id_fk": 1,
            "payroll_year": 2026, "payroll_month": 9, "adjustment_type": "FINE_DEDUCTION",
            "adjustment_amount": 12, "created_by": "test",
        })
        self.assertNotIn("error", adjustment, adjustment)
        result = payroll.approve_payroll_adjustment({
            "payroll_adjustment_id": adjustment["payroll_adjustment_id_pk"],
            "payroll_adjustment_org_id_fk": 1, "updated_by": "test",
        })
        self.assertNotIn("error", result, result)
        self.assertEqual(self.scalar("select adjustment_status from payroll_adjustment"), "APPROVED")

    def test_api_crud_auth_audit_and_explicit_null_update(self):
        response = self.client.post("/api/v1/fines/create", json={
            "fine_date": "2026-09-29", "fine_on": "DRIVER", "fine_empl_id_fk": 1,
            "fine_accountability": "DRIVER", "payment_authority": "URBAN_EXPRESS",
            "amount_paid": "0.01", "recovery_split": "25", "created_by": "forged",
            "fine_attachment_path": "forged",
        })
        self.assertEqual(response.status_code, 200, response.text)
        fine = response.json()["data"]
        fine_id = fine["fine_id_pk"]
        self.assertEqual(fine["created_by"], "fine.test@example.invalid")
        self.assertIsNone(fine["fine_attachment_path"])
        self.assertEqual(Decimal(str(fine["amount_paid"])), Decimal("0.01"))
        response = self.client.post("/api/v1/fines/update", json={
            "fine_id_pk": fine_id, "payment_authority": "DRIVER", "amount_paid": None, "recovery_split": None,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["data"]["amount_paid"])
        self.assertEqual(self.client.post("/api/v1/fines/get", json={"fine_id": fine_id}).status_code, 200)
        self.assertEqual(len(self.client.get("/api/v1/fines").json()["data"]), 1)
        self.assertEqual(self.client.post("/api/v1/fines/delete", json={"fine_id": fine_id}).status_code, 200)

    def test_api_rejects_forged_org_and_invalid_employee(self):
        fine_id = self.create()["fine_id_pk"]
        for route in ("get", "update", "delete", "attachments/download"):
            response = self.client.post(f"/api/v1/fines/{route}", json={
                "fine_id": fine_id, "fine_org_id_fk": 2, "amount_paid": "500",
            })
            self.assertEqual(response.status_code, 403, response.text)
        response = self.client.post("/api/v1/fines/create", json={
            "fine_empl_id_fk": 3, "fine_date": "2026-09-29", "fine_on": "EMPLOYEE",
        })
        self.assertEqual(response.status_code, 404, response.text)

    def test_api_requires_authentication_on_all_routes(self):
        consolidated_app.dependency_overrides.clear()
        hr_api.app.dependency_overrides.clear()
        for route in ("get", "update", "delete", "attachments/download"):
            response = self.client.post(f"/api/v1/fines/{route}", json={"fine_id": 1})
            self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.client.get("/api/v1/fines").status_code, 401)
        response = self.client.post("/api/v1/fines/create", json={
            "fine_empl_id_fk": 1, "fine_date": "2026-09-29", "fine_on": "EMPLOYEE",
        })
        self.assertEqual(response.status_code, 401, response.text)
        response = self.client.post("/api/v1/fines/attachments/upload",
                                    data={"fine_id": "1"}, files={"file": ("a.pdf", b"test", "application/pdf")})
        self.assertEqual(response.status_code, 401, response.text)

    def test_existing_employee_lookup_returns_all_org_employees(self):
        response = self.client.get("/api/v1/employees")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({row["empl_id_pk"] for row in response.json()["data"]}, {1, 2})
        self.context["organization"]["org_id"] = 2
        response = self.client.get("/api/v1/employees")
        self.assertEqual({row["empl_id_pk"] for row in response.json()["data"]}, {3})

    def test_api_org_isolation_for_list_get_update_delete_and_attachments(self):
        fine_id = self.create()["fine_id_pk"]
        self.context["organization"]["org_id"] = 2
        self.assertEqual(self.client.get("/api/v1/fines").json()["data"], [])
        for route in ("get", "update", "delete", "attachments/download"):
            response = self.client.post(f"/api/v1/fines/{route}", json={"fine_id": fine_id, "amount_paid": "5"})
            self.assertEqual(response.status_code, 404, response.text)
        response = self.client.post("/api/v1/fines/attachments/upload",
                                    data={"fine_id": str(fine_id)}, files={"file": ("a.pdf", b"test", "application/pdf")})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertFalse(self.bucket.blobs)

    def test_attachment_upload_download_signed_expiry_and_same_filename_replacement(self):
        fine_id = self.create()["fine_id_pk"]
        first = self.upload(fine_id, "../receipt.pdf")
        second = self.upload(fine_id, "../receipt.pdf")
        self.assertNotEqual(first["fine_attachment_path"], second["fine_attachment_path"])
        self.assertEqual(second["previous_fine_attachment_path"], first["fine_attachment_path"])
        self.assertEqual(second["file_name"], "receipt.pdf")
        self.assertTrue(self.bucket.blob(first["fine_attachment_path"]).exists())
        self.assertEqual(self.get(fine_id)["fine_attachment_path"], second["fine_attachment_path"])
        download = fine_download.download_fine_attachment(self.identity(fine_id))
        self.assertNotIn("error", download, download)
        self.assertEqual(download["expires_in_seconds"], 900)
        self.assertGreater(datetime.fromisoformat(download["expires_at"]), datetime.now(timezone.utc))
        self.assertLess((datetime.fromisoformat(download["expires_at"]) - datetime.now(timezone.utc)).total_seconds(), 901)
        args = self.bucket.blob(second["fine_attachment_path"]).signing_arguments
        self.assertEqual((args["version"], args["method"]), ("v4", "GET"))
        self.assertEqual(download["content_type"], "application/pdf")
        self.assertNotIn("signed.example", self.get(fine_id)["fine_attachment_path"])

    def test_attachment_upload_and_download_endpoints(self):
        fine_id = self.create()["fine_id_pk"]
        response = self.client.post("/api/v1/fines/attachments/upload",
                                    data={"fine_id_pk": str(fine_id), "storage_folder": "untrusted"},
                                    files={"file": ("receipt.pdf", b"proof", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["data"]["fine_attachment_path"].startswith(
            f"firebase_upload_files/fine_attachments/{fine_id}/versions/"))
        response = self.client.post("/api/v1/fines/attachments/download", json={"fine_id_pk": fine_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["expires_in_seconds"], 900)

    def test_attachment_missing_fine_pointer_and_object(self):
        self.assertIn("not found", fine_download.download_fine_attachment(self.identity(999))["error"])
        fine_id = self.create()["fine_id_pk"]
        self.assertIn("is empty", fine_download.download_fine_attachment(self.identity(fine_id))["error"])
        pointer = self.upload(fine_id)["fine_attachment_path"]
        self.bucket.blob(pointer).delete()
        self.assertIn("not found", fine_download.download_fine_attachment(self.identity(fine_id))["error"])

    def test_attachment_eligibility_and_deleted_fine(self):
        for changes in ({"payment_authority": "DRIVER"}, {"fine_accountability": "URBAN_EXPRESS"},
                        {"fine_on": "EMPLOYEE", "fine_accountability": None, "payment_authority": None}):
            fine_id = self.create(**changes, amount_paid=None, recovery_split=None)["fine_id_pk"]
            result = fine_upload.upload_fine_attachment(self.identity(fine_id), BytesIO(b"file"), "a.pdf")
            self.assertIn("Invalid attachment eligibility", result["error"])
        fine_id = self.create()["fine_id_pk"]
        fine_delete.delete_fine_master(self.identity(fine_id))
        result = fine_upload.upload_fine_attachment(self.identity(fine_id), BytesIO(b"file"), "a.pdf")
        self.assertIn("not found", result["error"])
        self.assertFalse(self.bucket.blobs)

    def test_attachment_pointer_failure_cleans_only_new_blob(self):
        fine_id = self.create()["fine_id_pk"]
        old = self.upload(fine_id)["fine_attachment_path"]
        def fail_pointer(conn, cursor, statement, params, context, executemany):
            if statement.lstrip().startswith("update fine_master set fine_attachment_path"):
                raise RuntimeError("pointer failure")
        event.listen(self.engine, "before_cursor_execute", fail_pointer)
        try:
            with self.assertLogs(fine_upload.logger, level="ERROR"):
                result = fine_upload.upload_fine_attachment(self.identity(fine_id), BytesIO(b"replacement"), "receipt.pdf")
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_pointer)
        self.assertIn("error", result)
        self.assertTrue(result["firebase_cleanup"]["cleanup_completed"])
        self.assertEqual(self.get(fine_id)["fine_attachment_path"], old)
        self.assertTrue(self.bucket.blob(old).exists())
        self.assertEqual(sum(blob.deleted for blob in self.bucket.blobs.values()), 1)

    def test_attachment_commit_failure_rolls_back_pointer_and_cleans_blob(self):
        fine_id = self.create()["fine_id_pk"]
        old = self.upload(fine_id)["fine_attachment_path"]
        engine = self.engine
        class FailingCommitEngine:
            @contextmanager
            def begin(self):
                with engine.begin() as conn:
                    yield conn
                    raise RuntimeError("simulated commit failure")
            def dispose(self):
                pass
        with patch.object(fine_upload, "db_engine", return_value=FailingCommitEngine()):
            with self.assertLogs(fine_upload.logger, level="ERROR"):
                result = fine_upload.upload_fine_attachment(self.identity(fine_id), BytesIO(b"new"), "receipt.pdf")
        self.assertTrue(result["firebase_cleanup"]["cleanup_completed"])
        self.assertEqual(self.get(fine_id)["fine_attachment_path"], old)

    def test_attachment_upload_failure_preserves_previous_pointer(self):
        fine_id = self.create()["fine_id_pk"]
        old = self.upload(fine_id)["fine_attachment_path"]
        with patch.object(FakeBlob, "upload_from_file", side_effect=RuntimeError("upload failure")):
            with self.assertLogs(fine_upload.logger, level="ERROR"):
                result = fine_upload.upload_fine_attachment(self.identity(fine_id), BytesIO(b"new"), "receipt.pdf")
        self.assertIn("error", result)
        self.assertEqual(self.get(fine_id)["fine_attachment_path"], old)
        self.assertTrue(self.bucket.blob(old).exists())

    def test_attachment_replacement_and_update_preserve_both_changes(self):
        fine_id = self.create()["fine_id_pk"]
        entered, release = Event(), Event()
        original_upload = FakeBlob.upload_from_file
        def slow_upload(blob, *args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timed out")
            return original_upload(blob, *args, **kwargs)
        with patch.object(FakeBlob, "upload_from_file", slow_upload):
            with ThreadPoolExecutor(max_workers=2) as executor:
                upload = executor.submit(fine_upload.upload_fine_attachment, self.identity(fine_id),
                                         BytesIO(b"proof"), "receipt.pdf")
                self.assertTrue(entered.wait(5))
                update = executor.submit(fine_update.update_fine_master, self.identity(fine_id, amount_paid="700"))
                release.set()
                uploaded = upload.result(timeout=10)
                self.assertNotIn("error", uploaded, uploaded)
                self.assertNotIn("error", update.result(timeout=10))
        fine = self.get(fine_id)
        self.assertEqual(fine["fine_attachment_path"], uploaded["fine_attachment_path"])
        self.assertEqual(Decimal(str(fine["amount_paid"])), Decimal("700"))


    def test_api_preserves_large_decimal_amount_exactly(self):
        response = self.client.post("/api/v1/fines/create", json={
            "fine_date": "2026-09-29", "fine_on": "DRIVER", "fine_empl_id_fk": 1,
            "fine_accountability": "DRIVER", "payment_authority": "URBAN_EXPRESS",
            "amount_paid": "9999999999999999.99", "recovery_split": "25.25",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["amount_paid"], "9999999999999999.99")
        self.assertEqual(response.json()["data"]["recovery_split"], "25.25")

    def test_attachment_request_validation_and_missing_fine(self):
        fine_id = self.create()["fine_id_pk"]
        cases = ((self.identity(fine_id), None, "a.pdf"),
                 (self.identity(fine_id), BytesIO(b"x"), None),
                 (self.identity(fine_id), BytesIO(b"x"), ".."),
                 (self.identity(999), BytesIO(b"x"), "a.pdf"))
        for params, stream, name in cases:
            with self.subTest(name=name):
                self.assertIn("error", fine_upload.upload_fine_attachment(params, stream, name))
        self.assertFalse(self.bucket.blobs)

    def test_historical_fine_remains_readable_after_employee_details_change(self):
        fine_id = self.create()["fine_id_pk"]
        with self.engine.begin() as conn:
            conn.exec_driver_sql("update employee_master set employee_designation = 'Inactive', employee_name = 'Renamed Employee' where empl_id_pk = 1")
        record = self.get(fine_id)
        self.assertEqual(record["fine_empl_id_fk"], 1)
        self.assertEqual(record["employee_name"], "Renamed Employee")

    def test_no_editable_fields_or_missing_fine_id(self):
        fine_id = self.create()["fine_id_pk"]
        self.assertIn("required", fine_update.update_fine_master(self.identity(fine_id, fine_attachment_path="evil"))["error"])
        self.assertIn("fine_id", fine_get.get_fine_master({"authenticated_org_id": 1})["error"])
        self.assertIn("fine_id", fine_delete.delete_fine_master({
            "authenticated_org_id": 1, "authenticated_user_principal_name": "test",
        })["error"])


if __name__ == "__main__":
    unittest.main()
