"""Isolated PostgreSQL integration tests with fake Firebase.

Set PENALTY_TEST_DATABASE_URL to a disposable loopback PostgreSQL database
ending in _tests. Never reads the application's database connection settings.
"""
from concurrent.futures import ThreadPoolExecutor
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
from sqlalchemy.exc import IntegrityError

from app_backend.services.auth_context import get_authenticated_context
from app_backend.services.main import app as consolidated_app
from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_02_hr_payroll.logic import (
    penalty_master_create_data as penalty_create,
    penalty_master_get_data as penalty_get,
    penalty_master_update_data as penalty_update,
    penalty_master_delete_data as penalty_delete,
)
from app_backend.services.service_02_hr_payroll.integrations import (
    firebase_penalty_attachment_upload as penalty_upload,
    firebase_penalty_attachment_download as penalty_download,
)
from app_backend.services.service_02_hr_payroll.logic.test_penalty_management_logic import penalty_payload
from app_backend.services.service_02_hr_payroll.logic.test_fine_management_postgres import FakeBucket


TEST_DATABASE_URL = os.getenv("PENALTY_TEST_DATABASE_URL")
SERVICE_DIR = Path(__file__).resolve().parents[1]


@unittest.skipUnless(TEST_DATABASE_URL, "PENALTY_TEST_DATABASE_URL is required for isolated PostgreSQL tests.")
class PenaltyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(TEST_DATABASE_URL)
        if (url.get_backend_name() != "postgresql" or url.host not in ("localhost", "127.0.0.1")
                or not (url.database or "").endswith("_tests")):
            raise RuntimeError("Use a disposable loopback PostgreSQL database ending in _tests.")
        cls.schema = f"penalties_test_{uuid4().hex}"
        cls.admin = create_engine(url)
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.addClassCleanup(cls.cleanup_schema)
        cls.engine = create_engine(url, connect_args={"options": f"-csearch_path={cls.schema},public"})
        with cls.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE organization_master (org_id_pk BIGSERIAL PRIMARY KEY, org_name TEXT)")
            conn.exec_driver_sql("CREATE TABLE department_master (dep_id_pk BIGSERIAL PRIMARY KEY, dep_org_id_fk BIGINT NOT NULL)")
            conn.exec_driver_sql((SERVICE_DIR / "data/employee_master_creation.sql").read_text())
        # Deliberately no Payroll or recovery tables: CRUD must be independent.
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
            conn.exec_driver_sql((SERVICE_DIR / "data/20260930_add_penalties_module.sql").read_text().replace(
                "public.", f'"{cls.schema}".'
            ))

    def setUp(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("TRUNCATE penalty_master, employee_master, organization_master RESTART IDENTITY CASCADE")
            conn.exec_driver_sql("INSERT INTO organization_master(org_id_pk, org_name) VALUES (1,'Test A'),(2,'Test B')")
            conn.exec_driver_sql("""
                INSERT INTO employee_master(empl_id_pk, empl_org_id_fk, employee_id, employee_name)
                VALUES (1,1,'EMP-A','Same Name'), (2,1,'EMP-B','Same Name'), (3,2,'EMP-C','Same Name')
            """)
        for module in (penalty_create, penalty_get, penalty_update, penalty_delete, penalty_upload, penalty_download):
            patcher = patch.object(module, "db_engine", return_value=self.engine)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = {"authenticated": True, "user": {"user_id": 7,
                        "user_principal_name": "penalty.test@example.invalid"}, "organization": {"org_id": 1}}
        for app in (consolidated_app, hr_api.app):
            previous = dict(app.dependency_overrides)
            self.addCleanup(self.restore_overrides, app, previous)
            app.dependency_overrides[get_authenticated_context] = lambda: self.context
        self.client = TestClient(consolidated_app)
        self.addCleanup(self.client.close)
        self.bucket = FakeBucket()
        for module in (penalty_upload, penalty_download):
            patcher = patch.object(module, "initialize_firebase_app")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(penalty_upload.storage, "bucket", return_value=self.bucket)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def restore_overrides(app, previous):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    def identity(self, penalty_id, **changes):
        return {"penalty_id": penalty_id, "authenticated_org_id": 1,
                "authenticated_user_principal_name": "penalty.test@example.invalid", **changes}

    def create(self, **changes):
        result = penalty_create.create_penalty_master(penalty_payload(**changes))
        self.assertNotIn("error", result, result)
        return result

    def get(self, penalty_id):
        result = penalty_get.get_penalty_master(self.identity(penalty_id))
        self.assertNotIn("error", result, result)
        return result

    def upload(self, penalty_id, name="warning.pdf"):
        result = penalty_upload.upload_penalty_attachment(
            self.identity(penalty_id), BytesIO(b"warning letter"), name, "application/pdf",
        )
        self.assertNotIn("error", result, result)
        return result

    def scalar(self, query, params=None):
        with self.engine.begin() as conn:
            return conn.execute(text(query), params or {}).scalar_one()

    def test_create_employee_display_and_backend_owned_fields(self):
        result = self.create(penalty_attachment_path="forged/path", penalty_org_id_fk=2,
                             created_by="spoofed", updated_by="spoofed")
        self.assertEqual(result["employee_id"], "EMP-A")
        self.assertEqual(result["employee_name"], "Same Name")
        self.assertEqual(result["penalty_empl_id_fk"], 1)
        self.assertEqual(result["penalty_org_id_fk"], 1)
        self.assertEqual(result["created_by"], "penalty.test@example.invalid")
        self.assertEqual(result["updated_by"], "penalty.test@example.invalid")
        self.assertIsNotNone(result["created_at"])
        self.assertIsNone(result["penalty_attachment_path"])
        self.assertNotIn("installments", result)

    def test_amount_precision_and_all_split_boundaries(self):
        for amount, split in (("0.01", "0"), ("9999999999999999.99", "100"), ("999.99", "33.33")):
            with self.subTest(amount=amount, split=split):
                result = self.create(penalty_amount=amount, recovery_split_percentage=split)
                response = self.client.post("/api/v1/penalties/get", json={"penalty_id": result["penalty_id_pk"]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["data"]["penalty_amount"], amount)
                self.assertEqual(response.json()["data"]["recovery_split_percentage"], f"{Decimal(split):.2f}")

    def test_invalid_or_foreign_employee_create_and_update_rejected(self):
        own = self.create()
        for employee_id in (3, 999):
            with self.subTest(employee=employee_id):
                result = penalty_create.create_penalty_master(penalty_payload(penalty_empl_id_fk=employee_id))
                self.assertIn("not found", result["error"])
                result = penalty_update.update_penalty_master(self.identity(own["penalty_id_pk"], penalty_empl_id_fk=employee_id))
                self.assertIn("not found", result["error"])
        self.assertEqual(self.scalar("select count(*) from penalty_master"), 1)
        self.assertEqual(self.get(own["penalty_id_pk"])["penalty_empl_id_fk"], 1)

    def test_all_reads_writes_and_attachments_are_org_scoped(self):
        own = self.create()
        foreign = self.create(authenticated_org_id=2, penalty_empl_id_fk=3)
        foreign_id = foreign["penalty_id_pk"]
        self.assertEqual([row["penalty_id_pk"] for row in penalty_get.list_penalty_master(self.identity(1))], [own["penalty_id_pk"]])
        for function in (penalty_get.get_penalty_master, penalty_delete.delete_penalty_master,
                         penalty_download.download_penalty_attachment):
            self.assertIn("not found", function(self.identity(foreign_id))["error"])
        self.assertIn("not found", penalty_update.update_penalty_master(self.identity(foreign_id, penalty_reason="Changed"))["error"])
        result = penalty_upload.upload_penalty_attachment(self.identity(foreign_id), BytesIO(b"x"), "x.pdf")
        self.assertIn("not found", result["error"])
        self.assertEqual(self.bucket.blobs, {})
        self.assertEqual(self.scalar("select count(*) from penalty_master"), 2)

    def test_warning_toggle_clears_pointer_and_requires_fields_to_reenable(self):
        penalty_id = self.create()["penalty_id_pk"]
        old_pointer = self.upload(penalty_id)["penalty_attachment_path"]
        result = penalty_update.update_penalty_master(self.identity(penalty_id, warning_letter_issued=False))
        self.assertNotIn("error", result)
        for key in ("warning_letter_date", "warning_letter_accepted", "penalty_attachment_path"):
            self.assertIsNone(result[key])
        self.assertTrue(self.bucket.blob(old_pointer).exists())
        failed = penalty_update.update_penalty_master(self.identity(penalty_id, warning_letter_issued=True))
        self.assertIn("required", failed["error"])
        self.assertFalse(self.get(penalty_id)["warning_letter_issued"])
        result = penalty_update.update_penalty_master(self.identity(
            penalty_id, warning_letter_issued=True, warning_letter_date="2026-10-01", warning_letter_accepted=False,
        ))
        self.assertNotIn("error", result)
        self.assertIsNone(result["penalty_attachment_path"])

    def test_financial_toggle_clears_values_and_reenable_requires_them(self):
        penalty_id = self.create()["penalty_id_pk"]
        result = penalty_update.update_penalty_master(self.identity(penalty_id, financial_implication=False))
        self.assertNotIn("error", result)
        self.assertIsNone(result["penalty_amount"])
        self.assertIsNone(result["recovery_split_percentage"])
        failed = penalty_update.update_penalty_master(self.identity(penalty_id, financial_implication=True))
        self.assertIn("required", failed["error"])
        result = penalty_update.update_penalty_master(self.identity(
            penalty_id, financial_implication=True, penalty_amount="500", recovery_split_percentage="0",
        ))
        self.assertNotIn("error", result)
        self.assertEqual(result["penalty_amount"], "500.00")

    def test_partial_update_preserves_pointer_and_changes_financial_fields(self):
        penalty_id = self.create()["penalty_id_pk"]
        pointer = self.upload(penalty_id)["penalty_attachment_path"]
        result = penalty_update.update_penalty_master(self.identity(
            penalty_id, penalty_empl_id_fk=2, penalty_reason="Corrected reason", penalty_date="2026-12-31",
            penalty_amount="123.45", recovery_split_percentage="33.33", penalty_attachment_path="forged",
            created_by="forged", updated_by="forged",
        ))
        self.assertNotIn("error", result)
        self.assertEqual(result["penalty_attachment_path"], pointer)
        self.assertEqual(result["employee_id"], "EMP-B")
        self.assertEqual(result["penalty_amount"], "123.45")
        self.assertEqual(result["updated_by"], "penalty.test@example.invalid")
        self.assertIsNotNone(result["updated_at"])

    def test_delete_financial_and_nonfinancial_penalties(self):
        for financial in (True, False):
            penalty_id = self.create(financial_implication=financial)["penalty_id_pk"]
            result = penalty_delete.delete_penalty_master(self.identity(penalty_id))
            self.assertNotIn("error", result)
            self.assertIn("not found", penalty_get.get_penalty_master(self.identity(penalty_id))["error"])
            self.assertIn("not found", penalty_delete.delete_penalty_master(self.identity(penalty_id))["error"])

    def test_versioned_upload_signed_download_and_pointer_ownership(self):
        penalty_id = self.create(financial_implication=False)["penalty_id_pk"]
        first = self.upload(penalty_id, "../warning.pdf")
        second = self.upload(penalty_id, "C:\\temp\\warning.pdf")
        self.assertNotEqual(first["penalty_attachment_path"], second["penalty_attachment_path"])
        self.assertEqual(second["previous_penalty_attachment_path"], first["penalty_attachment_path"])
        pointer = second["penalty_attachment_path"]
        self.assertTrue(pointer.startswith("firebase_upload_files/penalty_attachments/"))
        self.assertIn("/versions/", pointer)
        self.assertEqual(self.get(penalty_id)["penalty_attachment_path"], pointer)
        result = penalty_download.download_penalty_attachment(self.identity(penalty_id, penalty_attachment_path="forged"))
        self.assertNotIn("error", result)
        self.assertEqual(result["file_name"], "warning.pdf")
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertEqual(result["expires_in_seconds"], 900)
        self.assertGreater(datetime.fromisoformat(result["expires_at"]), datetime.now(timezone.utc))
        blob = self.bucket.blob(pointer)
        self.assertEqual(blob.signing_arguments["version"], "v4")
        self.assertEqual(blob.signing_arguments["method"], "GET")
        self.assertNotIn("download_url", self.get(penalty_id))
        self.assertEqual(self.get(penalty_id)["penalty_attachment_path"], pointer)

    def test_attachment_requires_warning_letter_and_existing_blob(self):
        penalty_id = self.create(warning_letter_issued=False)["penalty_id_pk"]
        result = penalty_upload.upload_penalty_attachment(self.identity(penalty_id), BytesIO(b"x"), "x.pdf")
        self.assertIn("warning_letter_issued", result["error"])
        self.assertEqual(self.bucket.blobs, {})
        self.assertIn("is empty", penalty_download.download_penalty_attachment(self.identity(penalty_id))["error"])
        penalty_id = self.create()["penalty_id_pk"]
        pointer = self.upload(penalty_id)["penalty_attachment_path"]
        self.bucket.blob(pointer).delete()
        self.assertIn("not found", penalty_download.download_penalty_attachment(self.identity(penalty_id))["error"])

    def test_failed_pointer_update_rolls_back_and_cleans_new_blob(self):
        penalty_id = self.create()["penalty_id_pk"]
        old_pointer = self.upload(penalty_id)["penalty_attachment_path"]

        def fail_pointer(conn, cursor, statement, parameters, context, executemany):
            if "update penalty_master set penalty_attachment_path" in statement:
                raise RuntimeError("Simulated database pointer failure")

        event.listen(self.engine, "before_cursor_execute", fail_pointer)
        try:
            with self.assertLogs(penalty_upload.logger, level="ERROR"):
                result = penalty_upload.upload_penalty_attachment(self.identity(penalty_id), BytesIO(b"new"), "new.pdf")
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_pointer)
        self.assertIn("error", result)
        self.assertTrue(result["firebase_cleanup"]["cleanup_completed"])
        self.assertEqual(self.get(penalty_id)["penalty_attachment_path"], old_pointer)
        self.assertTrue(self.bucket.blob(old_pointer).exists())
        self.assertEqual(sum(blob.deleted for blob in self.bucket.blobs.values()), 1)

    def test_failed_upload_preserves_existing_pointer(self):
        penalty_id = self.create()["penalty_id_pk"]
        old_pointer = self.upload(penalty_id)["penalty_attachment_path"]
        blob_type = type(self.bucket.blob(old_pointer))
        with patch.object(blob_type, "upload_from_file", side_effect=RuntimeError("Simulated Firebase failure")):
            with self.assertLogs(penalty_upload.logger, level="ERROR"):
                result = penalty_upload.upload_penalty_attachment(self.identity(penalty_id), BytesIO(b"new"), "new.pdf")
        self.assertIn("error", result)
        self.assertEqual(self.get(penalty_id)["penalty_attachment_path"], old_pointer)

    def test_create_rolls_back_if_response_read_fails(self):
        with patch.object(penalty_create, "read_penalties", side_effect=RuntimeError("Simulated DB failure")):
            with self.assertLogs(penalty_create.logger, level="ERROR"):
                result = penalty_create.create_penalty_master(penalty_payload())
        self.assertIn("error", result)
        self.assertEqual(self.scalar("select count(*) from penalty_master"), 0)

    def test_api_crud_multipart_and_error_envelopes(self):
        response = self.client.post("/api/v1/penalties/create", json=penalty_payload())
        self.assertEqual(response.status_code, 200, response.text)
        penalty = response.json()["data"]
        penalty_id = penalty["penalty_id_pk"]
        self.assertEqual(penalty["penalty_date"], "2026-09-30")
        self.assertTrue(response.json()["success"])
        self.assertEqual(len(self.client.get("/api/v1/penalties").json()["data"]), 1)
        for alias in ("penalty_id", "penalty_id_pk"):
            response = self.client.post("/api/v1/penalties/get", json={alias: penalty_id})
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/v1/penalties/update", json={"penalty_id": penalty_id, "penalty_reason": "Updated"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["penalty_reason"], "Updated")
        response = self.client.post("/api/v1/penalties/attachments/upload", data={"penalty_id_pk": penalty_id},
                                    files={"file": ("warning.pdf", b"warning letter", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post("/api/v1/penalties/attachments/download", json={"penalty_id": penalty_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("download_url", response.json()["data"])
        response = self.client.post("/api/v1/penalties/delete", json={"penalty_id_pk": penalty_id})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/v1/penalties/get", json={"penalty_id": penalty_id})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_api_validation_and_foreign_organization_errors(self):
        for changes, expected in (({"penalty_reason": "   "}, 400), ({"penalty_amount": "0"}, 400),
                                  ({"warning_letter_date": None}, 400), ({"recovery_split_percentage": "101"}, 400),
                                  ({"penalty_org_id_fk": 2}, 403), ({"penalty_empl_id_fk": 3}, 404)):
            response = self.client.post("/api/v1/penalties/create", json=penalty_payload(**changes))
            self.assertEqual(response.status_code, expected, response.text)
            self.assertFalse(response.json()["success"])
        response = self.client.post("/api/v1/penalties/create", json={})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(response.json()["success"])
        self.assertEqual(self.scalar("select count(*) from penalty_master"), 0)

    def test_api_requires_authentication(self):
        for app in (consolidated_app, hr_api.app):
            app.dependency_overrides.pop(get_authenticated_context, None)
        with patch.object(penalty_get, "db_engine") as database:
            response = self.client.get("/api/v1/penalties")
        self.assertIn(response.status_code, (401, 403))
        database.assert_not_called()

    def test_migration_rerun_preserves_data_and_schema_is_minimal(self):
        penalty_id = self.create()["penalty_id_pk"]
        self.apply_migration()
        self.assertEqual(self.get(penalty_id)["penalty_id_pk"], penalty_id)
        with self.engine.begin() as conn:
            columns = conn.execute(text("""
                select column_name from information_schema.columns
                where table_schema = :schema and table_name = 'penalty_master'
            """), {"schema": self.schema}).scalars().all()
            indexes = conn.execute(text("select indexname from pg_indexes where schemaname = :schema and tablename = 'penalty_master'"),
                                   {"schema": self.schema}).scalars().all()
        self.assertCountEqual(columns, ["penalty_id_pk", "penalty_org_id_fk", "penalty_empl_id_fk", "penalty_date",
            "penalty_reason", "warning_letter_issued", "warning_letter_date", "warning_letter_accepted",
            "penalty_attachment_path", "financial_implication", "penalty_amount", "recovery_split_percentage",
            "created_by", "created_at", "updated_by", "updated_at"])
        self.assertTrue({"idx_penalty_org_date", "idx_penalty_org_employee", "idx_penalty_employee_date"} <= set(indexes))

    def test_database_constraints_and_restricted_employee_delete(self):
        penalty_id = self.create()["penalty_id_pk"]
        invalid_assignments = ["penalty_reason = E' \\t\\n'", "penalty_empl_id_fk = 999", "penalty_org_id_fk = 999",
                               "warning_letter_date = null", "warning_letter_accepted = null",
                               "warning_letter_issued = false", "financial_implication = false",
                               "penalty_amount = null", "penalty_amount = 0", "penalty_amount = 'NaN'",
                               "recovery_split_percentage = null", "recovery_split_percentage = -1",
                               "recovery_split_percentage = 101"]
        for assignment in invalid_assignments:
            with self.subTest(assignment=assignment), self.assertRaises(IntegrityError):
                with self.engine.begin() as conn:
                    conn.execute(text(f"update penalty_master set {assignment} where penalty_id_pk = :id"), {"id": penalty_id})
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.execute(text("delete from employee_master where empl_id_pk = 1"))

    def test_warning_disable_waits_for_upload_and_clears_committed_pointer(self):
        penalty_id = self.create()["penalty_id_pk"]
        upload_entered = Event()
        release_upload = Event()
        update_started = Event()
        blob_type = type(self.bucket.blob("unused"))
        original_upload = blob_type.upload_from_file

        def paused_upload(blob, *args, **kwargs):
            upload_entered.set()
            if not release_upload.wait(10):
                raise RuntimeError("Test upload was not released")
            return original_upload(blob, *args, **kwargs)

        def disable_warning():
            update_started.set()
            return penalty_update.update_penalty_master(self.identity(penalty_id, warning_letter_issued=False))

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(blob_type, "upload_from_file", paused_upload):
            future_upload = pool.submit(self.upload, penalty_id)
            try:
                self.assertTrue(upload_entered.wait(5))
                future_update = pool.submit(disable_warning)
                self.assertTrue(update_started.wait(5))
                self.assertFalse(future_update.done())
            finally:
                release_upload.set()
            future_upload.result(timeout=10)
            self.assertNotIn("error", future_update.result(timeout=10))
        final = self.get(penalty_id)
        self.assertFalse(final["warning_letter_issued"])
        self.assertIsNone(final["penalty_attachment_path"])


if __name__ == "__main__":
    unittest.main()
