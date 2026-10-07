"""Advances validation, API, and isolated PostgreSQL CRUD tests.

Run with python -m unittest app_backend.services.service_02_hr_payroll.logic.test_advance_management_logic.
For SQL tests, set ADVANCE_TEST_DATABASE_URL to a disposable loopback PostgreSQL
database ending in _tests. Application database settings are never used.
"""
from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app_backend.services.auth_context import get_authenticated_context
from app_backend.services.main import app as consolidated_app
from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_02_hr_payroll.logic import (
    advance_master_create_data as advance_create,
    advance_master_get_data as advance_get,
    advance_master_update_data as advance_update,
    advance_master_delete_data as advance_delete,
    employee_master_create_data as employee_create,
    employee_master_get_data as employee_get,
    employee_master_update_data as employee_update,
    employee_master_delete_data as employee_delete,
)


SERVICE_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.getenv("ADVANCE_TEST_DATABASE_URL")
ADVANCE_MODULES = (advance_create, advance_get, advance_update, advance_delete)
CONTEXT = {
    "authenticated": True,
    "user": {"user_id": 7, "user_principal_name": "advances.test@example.invalid"},
    "organization": {"org_id": 1},
}
ROUTES = {("GET", "/api/v1/advances")} | {
    ("POST", f"/api/v1/advances/{suffix}") for suffix in ("create", "update", "delete")
}


def advance_payload(**changes):
    return {
        "advance_empl_id_fk": 1,
        "advance_date": "2026-10-01",
        "advance_reason": "Employee personal advance",
        "advance_amount": "4000.00",
        "recovery_split_percentage": "50.00",
        "authenticated_org_id": 1,
        "authenticated_user_principal_name": CONTEXT["user"]["user_principal_name"],
        **changes,
    }


def restore_overrides(app, previous):
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


class AdvanceValidationTests(unittest.TestCase):
    def test_valid_dates_amounts_and_split_boundaries(self):
        for split in ("0", "50", "100"):
            fields = advance_create.get_advance_params(advance_payload(recovery_split_percentage=split))
            self.assertEqual(fields["advance_date"], date(2026, 10, 1))
            self.assertEqual(fields["advance_amount"], Decimal("4000.00"))
            self.assertEqual(fields["recovery_split_percentage"], Decimal(split))

    def test_required_fields(self):
        for field in ("advance_empl_id_fk", "advance_date", "advance_reason",
                      "advance_amount", "recovery_split_percentage"):
            for missing in (True, False):
                payload = advance_payload()
                if missing:
                    payload.pop(field)
                else:
                    payload[field] = None
                with self.subTest(field=field, missing=missing):
                    self.assertIn("error", advance_create.create_advance_master(payload))

    def test_invalid_business_fields(self):
        invalid = {
            "advance_empl_id_fk": [0, -1, True, "1.5", "bad"],
            "advance_date": ["bad", "2026-02-30", datetime(2026, 10, 1)],
            "advance_reason": ["", " \t\n", 123],
            "advance_amount": ["0", "-1", "NaN", "Infinity", "1.001", "1000000000000"],
            "recovery_split_percentage": ["-1", "101", "NaN", "Infinity", "50.001"],
        }
        with patch.object(advance_create, "db_engine") as factory:
            for field, values in invalid.items():
                for value in values:
                    with self.subTest(field=field, value=value):
                        result = advance_create.create_advance_master(advance_payload(**{field: value}))
                        self.assertIn("error", result)
            factory.assert_not_called()

    def test_decimal_storage_boundaries(self):
        for amount in ("0.01", "999999999999.99", "1.0000"):
            self.assertEqual(advance_create.get_advance_params(
                advance_payload(advance_amount=amount))["advance_amount"], Decimal(amount))

    def test_past_and_future_dates_are_allowed(self):
        for value in ("2000-01-01", "2099-12-31", date(2026, 10, 1)):
            self.assertIsInstance(advance_create.get_advance_params(
                advance_payload(advance_date=value))["advance_date"], date)

    def test_authenticated_identity_is_required(self):
        for field in ("authenticated_org_id", "authenticated_user_principal_name"):
            payload = advance_payload(created_by="forged", updated_by="forged")
            payload.pop(field)
            self.assertIn(field, advance_create.create_advance_master(payload)["error"])
        self.assertIn("error", advance_get.get_advance_master())

    def test_id_aliases_and_missing_invalid_ids(self):
        for alias in ("advance_id", "advance_id_pk"):
            self.assertEqual(advance_create.advance_identity(advance_payload(**{alias: 5}))["advance_id"], 5)
        for value in (None, 0, -1, True, "bad", "1.5"):
            for func in (advance_update.update_advance_master, advance_delete.delete_advance_master):
                with self.subTest(value=value, func=func.__name__):
                    self.assertIn("error", func(advance_payload(advance_id=value)))

    def test_unexpected_errors_are_controlled_and_engines_disposed(self):
        for module, func in (
            (advance_create, advance_create.create_advance_master),
            (advance_get, advance_get.get_advance_master),
            (advance_update, advance_update.update_advance_master),
            (advance_delete, advance_delete.delete_advance_master),
        ):
            engine = MagicMock()
            engine.begin.side_effect = RuntimeError("private connection details")
            with patch.object(module, "db_engine", return_value=engine), self.assertLogs(module.__name__):
                result = func(advance_payload(advance_id=1))
            self.assertIn("error", result)
            self.assertNotIn("private", result["error"])
            engine.dispose.assert_called_once()


class AdvanceApiTests(unittest.TestCase):
    def test_exact_routes_are_authenticated_and_exposed_once_in_both_apps(self):
        for app in (hr_api.app, consolidated_app):
            routes = [(method, route.path) for route in app.routes
                      for method in getattr(route, "methods", ())
                      if route.path.startswith("/api/v1/advances")]
            self.assertCountEqual(routes, ROUTES)
            schema = app.openapi()
            for method, path in ROUTES:
                self.assertTrue(schema["paths"][path][method.lower()]["security"])

    def test_all_routes_require_authentication(self):
        for app in (hr_api.app, consolidated_app):
            previous = dict(app.dependency_overrides)
            self.addCleanup(restore_overrides, app, previous)
            app.dependency_overrides.pop(get_authenticated_context, None)
            with TestClient(app) as client:
                for method, path in ROUTES:
                    response = client.request(method, path, json=advance_payload(advance_id=1))
                    self.assertEqual(response.status_code, 401, response.text)
                    self.assertFalse(response.json()["success"])
                    self.assertIn("error", response.json())

    def test_audit_and_organization_come_from_authentication(self):
        for app in (hr_api.app, consolidated_app):
            previous = dict(app.dependency_overrides)
            self.addCleanup(restore_overrides, app, previous)
            app.dependency_overrides[get_authenticated_context] = lambda: CONTEXT
            with TestClient(app) as client, patch.object(hr_api, "create_advance_master",
                                                        return_value={"advance_id_pk": 1}) as create:
                response = client.post("/api/v1/advances/create", json=advance_payload(
                    created_by="forged", updated_by="forged", authenticated_org_id=999,
                    authenticated_user_principal_name="forged",
                ))
                self.assertEqual(response.json(), {"success": True, "data": {"advance_id_pk": 1}})
                bound = create.call_args.args[0]
                self.assertEqual(bound["authenticated_org_id"], 1)
                for field in ("created_by", "updated_by", "authenticated_user_principal_name"):
                    self.assertEqual(bound[field], CONTEXT["user"]["user_principal_name"])

    def test_standard_error_envelopes(self):
        previous = dict(hr_api.app.dependency_overrides)
        self.addCleanup(restore_overrides, hr_api.app, previous)
        hr_api.app.dependency_overrides[get_authenticated_context] = lambda: CONTEXT
        with TestClient(hr_api.app) as client:
            for message, status in (
                ("advance_reason is required.", 400),
                ("Advance not found.", 404),
                ("Forbidden organization access.", 403),
                ("Failed to create advance record.", 500),
            ):
                with patch.object(hr_api, "create_advance_master", return_value={"error": message}):
                    response = client.post("/api/v1/advances/create", json=advance_payload())
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json(), {"success": False, "error": message})
            response = client.post("/api/v1/advances/create", json={})
            self.assertEqual(response.status_code, 422)
            self.assertFalse(response.json()["success"])


@unittest.skipUnless(TEST_DATABASE_URL, "ADVANCE_TEST_DATABASE_URL is required for isolated PostgreSQL tests.")
class AdvancePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(TEST_DATABASE_URL)
        if (url.get_backend_name() != "postgresql" or url.host not in ("localhost", "127.0.0.1")
                or not (url.database or "").endswith("_tests")):
            raise RuntimeError("Use a disposable loopback PostgreSQL database ending in _tests.")
        cls.schema = f"advances_test_{uuid4().hex}"
        cls.admin = create_engine(url)
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.addClassCleanup(cls.cleanup_schema)
        cls.engine = create_engine(url, connect_args={"options": f"-csearch_path={cls.schema},public"})
        with cls.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE organization_master (org_id_pk BIGSERIAL PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE department_master (dep_id_pk BIGSERIAL PRIMARY KEY, dep_org_id_fk BIGINT)")
            conn.exec_driver_sql((SERVICE_DIR / "data/employee_master_creation.sql").read_text())
            # The existing Employee API uses these fields, but its bootstrap DDL omits them.
            # Supply its current string payload fields only in this disposable test fixture.
            conn.exec_driver_sql("ALTER TABLE employee_master ADD COLUMN employee_image_path TEXT")
            conn.exec_driver_sql("ALTER TABLE employee_master ADD COLUMN reporting_to_employee_id TEXT")
        # No Payroll, recovery, Workflow, or attachment tables are provisioned.
        cls.apply_ddl()

    @classmethod
    def cleanup_schema(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')
        cls.admin.dispose()

    @classmethod
    def apply_ddl(cls):
        with cls.engine.begin() as conn:
            conn.exec_driver_sql((SERVICE_DIR / "data/advance_master.sql").read_text().replace(
                "public.", f'"{cls.schema}".',
            ))

    def setUp(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("TRUNCATE advance_master, employee_master, organization_master RESTART IDENTITY CASCADE")
            conn.exec_driver_sql("INSERT INTO organization_master VALUES (1), (2)")
            conn.exec_driver_sql("""
                INSERT INTO employee_master(empl_org_id_fk, employee_id, employee_name)
                VALUES (1,'EMP-A','Employee A'), (1,'EMP-B','Employee B'), (2,'EMP-C','Employee C')
            """)
        for module in ADVANCE_MODULES + (employee_create, employee_get, employee_update, employee_delete):
            patcher = patch.object(module, "db_engine", return_value=self.engine)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = {**CONTEXT, "organization": {"org_id": 1}}
        for app in (hr_api.app, consolidated_app):
            previous = dict(app.dependency_overrides)
            self.addCleanup(restore_overrides, app, previous)
            app.dependency_overrides[get_authenticated_context] = lambda: self.context
        self.client = TestClient(consolidated_app)
        self.addCleanup(self.client.close)

    def create(self, **changes):
        result = advance_create.create_advance_master(advance_payload(**changes))
        self.assertNotIn("error", result)
        return result["advance_id_pk"]

    def rows(self, org=1):
        return advance_get.get_advance_master({"authenticated_org_id": org})

    def test_create_and_storage_for_zero_fifty_and_hundred_percent(self):
        for split in ("0", "50", "100"):
            advance_id = self.create(recovery_split_percentage=split, created_by="forged", updated_by="forged")
            row = next(row for row in self.rows() if row["advance_id_pk"] == advance_id)
            self.assertEqual(row["advance_amount"], "4000.00")
            self.assertEqual(row["recovery_split_percentage"], f"{split}.00")
            self.assertEqual(row["created_by"], CONTEXT["user"]["user_principal_name"])
            self.assertEqual(row["updated_by"], row["created_by"])
            self.assertIsNotNone(row["created_at"])
            self.assertIsNone(row["updated_at"])
            self.assertEqual(row["advance_date"], date(2026, 10, 1))

    def test_missing_and_foreign_employees_are_rejected(self):
        for employee in (999, 3):
            response = self.client.post("/api/v1/advances/create", json=advance_payload(advance_empl_id_fk=employee))
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.rows(), [])

    def test_list_has_employee_identity_and_organization_isolation(self):
        own_id = self.create()
        foreign_id = self.create(advance_empl_id_fk=3, authenticated_org_id=2)
        rows = self.rows()
        self.assertEqual([row["advance_id_pk"] for row in rows], [own_id])
        self.assertEqual((rows[0]["empl_id_pk"], rows[0]["employee_id"], rows[0]["employee_name"]),
                         (1, "EMP-A", "Employee A"))
        self.assertEqual([row["advance_id_pk"] for row in self.rows(2)], [foreign_id])
        response = self.client.get("/api/v1/advances?authenticated_org_id=2")
        self.assertEqual([row["advance_id_pk"] for row in response.json()["data"]], [own_id])

    def test_update_all_business_fields_and_audit_using_both_id_aliases(self):
        advance_id = self.create()
        original = self.rows()[0]
        for alias in ("advance_id", "advance_id_pk"):
            response = self.client.post("/api/v1/advances/update", json=advance_payload(
                **{alias: advance_id}, advance_empl_id_fk=2, advance_date="2026-11-02",
                advance_reason="Updated employee personal advance", advance_amount="5000.00",
                recovery_split_percentage="0", created_by="forged", updated_by="forged",
            ))
            self.assertEqual(response.status_code, 200, response.text)
        row = self.rows()[0]
        self.assertEqual(row["advance_empl_id_fk"], 2)
        self.assertEqual(row["employee_id"], "EMP-B")
        self.assertEqual(row["advance_date"], date(2026, 11, 2))
        self.assertEqual(row["advance_reason"], "Updated employee personal advance")
        self.assertEqual(row["advance_amount"], "5000.00")
        self.assertEqual(row["recovery_split_percentage"], "0.00")
        self.assertEqual(row["created_at"], original["created_at"])
        self.assertEqual(row["created_by"], original["created_by"])
        self.assertEqual(row["updated_by"], CONTEXT["user"]["user_principal_name"])
        self.assertIsNotNone(row["updated_at"])

    def test_update_invalid_id_or_foreign_record_is_not_found(self):
        foreign_id = self.create(advance_empl_id_fk=3, authenticated_org_id=2)
        before = self.rows(2)
        for advance_id in (999, foreign_id):
            response = self.client.post("/api/v1/advances/update", json=advance_payload(advance_id=advance_id))
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.rows(2), before)

    def test_changing_to_missing_or_foreign_employee_fails_without_changes(self):
        advance_id = self.create()
        before = self.rows()
        for employee in (999, 3):
            response = self.client.post("/api/v1/advances/update", json=advance_payload(
                advance_id=advance_id, advance_empl_id_fk=employee,
            ))
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(self.rows(), before)

    def test_create_and_update_reject_invalid_fields_without_changes(self):
        advance_id = self.create()
        before = self.rows()
        for field, values in {
            "advance_reason": ["", " \t\n"],
            "advance_date": ["2026-02-30"],
            "advance_amount": ["0", "-10", "1.001", "1000000000000"],
            "recovery_split_percentage": ["-1", "101", "33.333"],
        }.items():
            for value in values:
                for action in ("create", "update"):
                    with self.subTest(field=field, value=value, action=action):
                        response = self.client.post(f"/api/v1/advances/{action}", json=advance_payload(
                            advance_id=advance_id, **{field: value},
                        ))
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertFalse(response.json()["success"])
                        self.assertEqual(self.rows(), before)

    def test_api_missing_reason_and_null_fields_are_rejected(self):
        for field in ("advance_reason", "advance_date", "advance_amount", "recovery_split_percentage"):
            for missing in (True, False):
                payload = advance_payload()
                if missing:
                    payload.pop(field)
                else:
                    payload[field] = None
                response = self.client.post("/api/v1/advances/create", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.rows(), [])

    def test_api_requires_id_for_update_and_delete(self):
        for action, payload in (("update", advance_payload()), ("delete", {})):
            response = self.client.post(f"/api/v1/advances/{action}", json=payload)
            self.assertEqual(response.status_code, 400, response.text)

    def test_hard_delete_using_both_id_aliases(self):
        for alias in ("advance_id", "advance_id_pk"):
            advance_id = self.create()
            response = self.client.post("/api/v1/advances/delete", json={alias: advance_id})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["data"]["advance_id_pk"], advance_id)
            with self.engine.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT count(*) FROM advance_master")).scalar_one(), 0)

    def test_delete_invalid_id_and_foreign_record_are_blocked(self):
        foreign_id = self.create(advance_empl_id_fk=3, authenticated_org_id=2)
        before = self.rows(2)
        for advance_id in (999, foreign_id):
            response = self.client.post("/api/v1/advances/delete", json={"advance_id": advance_id})
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.rows(2), before)

    def test_real_api_crud_and_json_serialization_in_both_apps(self):
        for app in (hr_api.app, consolidated_app):
            with TestClient(app) as client:
                created = client.post("/api/v1/advances/create", json=advance_payload())
                self.assertEqual(created.status_code, 200, created.text)
                self.assertTrue(created.json()["success"])
                advance_id = created.json()["data"]["advance_id_pk"]
                row = next(row for row in client.get("/api/v1/advances").json()["data"]
                           if row["advance_id_pk"] == advance_id)
                self.assertEqual(row["advance_date"], "2026-10-01")
                self.assertEqual(row["advance_amount"], "4000.00")
                self.assertIsInstance(row["created_at"], str)
                updated = client.post("/api/v1/advances/update", json=advance_payload(
                    advance_id_pk=advance_id, advance_amount="5000.00",
                ))
                self.assertTrue(updated.json()["success"])
                self.assertTrue(client.post("/api/v1/advances/delete",
                                            json={"advance_id_pk": advance_id}).json()["success"])

    def test_ddl_is_repeatable_and_has_only_required_columns_and_indexes(self):
        advance_id = self.create()
        self.apply_ddl()
        self.assertEqual(self.rows()[0]["advance_id_pk"], advance_id)
        with self.engine.connect() as conn:
            columns = conn.execute(text("""
                SELECT column_name, data_type, numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = 'advance_master'
                ORDER BY ordinal_position
            """), {"schema": self.schema}).mappings().all()
            indexes = conn.execute(text("""
                SELECT indexname FROM pg_indexes WHERE schemaname = :schema AND tablename = 'advance_master'
            """), {"schema": self.schema}).scalars().all()
            tables = conn.execute(text("""
                SELECT tablename FROM pg_tables WHERE schemaname = :schema
            """), {"schema": self.schema}).scalars().all()
        self.assertEqual([row["column_name"] for row in columns], [
            "advance_id_pk", "advance_empl_id_fk", "advance_date", "advance_reason", "advance_amount",
            "recovery_split_percentage", "created_by", "created_at", "updated_by", "updated_at",
        ])
        numeric = {row["column_name"]: (row["numeric_precision"], row["numeric_scale"]) for row in columns}
        self.assertEqual(numeric["advance_amount"], (14, 2))
        self.assertEqual(numeric["recovery_split_percentage"], (5, 2))
        self.assertCountEqual(indexes, ["advance_master_pkey", "idx_advance_employee", "idx_advance_date"])
        self.assertCountEqual(tables, ["organization_master", "department_master", "employee_master", "advance_master"])

    def test_database_constraints_and_employee_fk_restriction(self):
        advance_id = self.create()
        for assignment in (
            "advance_reason = null", "advance_reason = ''", "advance_reason = E' \\t\\n'",
            "advance_date = null", "advance_empl_id_fk = 999", "advance_amount = null",
            "advance_amount = 0", "advance_amount = -1", "advance_amount = 'NaN'",
            "recovery_split_percentage = null", "recovery_split_percentage = -1",
            "recovery_split_percentage = 101", "recovery_split_percentage = 'NaN'",
        ):
            with self.subTest(assignment=assignment), self.assertRaises(IntegrityError):
                with self.engine.begin() as conn:
                    conn.execute(text(f"UPDATE advance_master SET {assignment} WHERE advance_id_pk = :id"),
                                 {"id": advance_id})
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.exec_driver_sql("DELETE FROM employee_master WHERE empl_id_pk = 1")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("UPDATE employee_master SET empl_id_pk = 10 WHERE empl_id_pk = 1")
        self.assertEqual(self.rows()[0]["advance_empl_id_fk"], 10)

    def test_existing_employee_crud_with_isolated_database(self):
        payload = {"empl_org_id_fk": 1, "employee_id": "EMP-NEW", "employee_name": "New Employee"}
        created = self.client.post("/api/v1/employees/create", json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        listed = self.client.get("/api/v1/employees")
        self.assertEqual(listed.status_code, 200, listed.text)
        employee_id = next(row["empl_id_pk"] for row in listed.json()["data"]
                           if row["employee_id"] == "EMP-NEW")
        updated = self.client.post("/api/v1/employees/update", json={
            **payload, "empl_id_pk": employee_id, "employee_name": "Updated Employee",
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        deleted = self.client.post("/api/v1/employees/delete", json={"empl_id_pk": employee_id})
        self.assertEqual(deleted.status_code, 200, deleted.text)


if __name__ == "__main__":
    unittest.main()

