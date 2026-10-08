"""Focused Customer Master CRUD/API checks against an isolated database.

Run with unittest. By default, use in-memory SQLite for executable SQL and HTTP
coverage. Set CUSTOMER_MASTER_TEST_DATABASE_URL to a disposable loopback
PostgreSQL database ending in _tests to also verify the supplied migration.
Never use the application's database URL or modify its schema.
"""
from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import (
    CheckConstraint, Column, DateTime, ForeignKey, Integer, MetaData,
    String, Table, Text, UniqueConstraint, create_engine, event, text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from app_backend.services.auth_context import get_authenticated_context
from app_backend.services.main import app as consolidated_app
from app_backend.services.service_06_contracts_management.api import main as api
from app_backend.services.service_06_contracts_management.logic import (
    customer_master_create_data as create,
    customer_master_delete_data as delete,
    customer_master_get_data as get,
    customer_master_update_data as update,
)


TEST_DATABASE_URL = os.getenv("CUSTOMER_MASTER_TEST_DATABASE_URL")
SERVICE_DIR = Path(__file__).resolve().parents[1]
CONTACT_GROUPS = (
    "procurement_head", "operation_incharge", "operation_head",
    "finance_incharge", "finance_head", "wcr_incharge", "grn_incharge",
)
FIELD_LIMITS = {"portal_system": 100}
FIELD_LIMITS.update({
    f"{group}_{suffix}": limit
    for group in CONTACT_GROUPS
    for suffix, limit in (
        ("name", 150), ("phone_primary", 30), ("phone_secondary", 30),
        ("email_primary", 254), ("email_secondary", 254),
    )
})
UNCHANGED_FIELDS = {
    "cust_id_pk", "cust_org_id_fk", "cust_code", "cust_name", "cust_category",
    "cust_status", "cust_billing_address", "cust_service_address",
    "cust_tax_registration_number", "cust_credit_period_days", "cust_notes",
    "created_by", "created_at", "updated_by", "updated_at",
}


def revised_values(label="initial"):
    return {field: f"{label}-{index}" for index, field in enumerate(FIELD_LIMITS)}


def auth_context(org_id, principal):
    return {
        "authenticated": True,
        "organization": {"org_id": org_id},
        "user": {"user_id": 1, "user_principal_name": principal},
    }


def fixture_metadata():
    metadata = MetaData()
    Table("organization_master", metadata, Column("org_id_pk", Integer, primary_key=True))
    Table(
        "customer_master", metadata,
        Column("cust_id_pk", Integer, primary_key=True),
        Column("cust_org_id_fk", Integer, ForeignKey("organization_master.org_id_pk"), nullable=False),
        Column("cust_code", String(30), nullable=False),
        Column("cust_name", String(200), nullable=False),
        Column("cust_category", String(100), nullable=False),
        Column("cust_status", String(20), nullable=False, server_default="ACTIVE"),
        *(Column(field, String(limit)) for field, limit in FIELD_LIMITS.items()),
        Column("cust_billing_address", Text),
        Column("cust_service_address", Text),
        Column("cust_tax_registration_number", String(50)),
        Column("cust_credit_period_days", Integer, nullable=False, server_default="0"),
        Column("cust_notes", Text),
        Column("created_by", String(100), nullable=False),
        Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        Column("updated_by", String(100)),
        Column("updated_at", DateTime),
        UniqueConstraint("cust_org_id_fk", "cust_code"),
        CheckConstraint("cust_credit_period_days >= 0"),
        CheckConstraint("cust_status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED', 'BLACKLISTED')"),
    )
    return metadata


class CustomerMasterSchemaTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        if TEST_DATABASE_URL:
            url = make_url(TEST_DATABASE_URL)
            if (url.get_backend_name() != "postgresql"
                    or url.host not in ("localhost", "127.0.0.1")
                    or not (url.database or "").endswith("_tests")):
                raise RuntimeError("Use a disposable loopback PostgreSQL database ending in _tests.")
            self.schema = f"customer_master_test_{uuid4().hex}"
            self.admin = create_engine(url)
            self.stack.callback(self.admin.dispose)
            with self.admin.begin() as conn:
                conn.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
            self.stack.callback(self.drop_schema)
            self.engine = create_engine(url, connect_args={"options": f"-csearch_path={self.schema}"})
        else:
            self.engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
            )
        self.stack.callback(self.engine.dispose)
        self.metadata = fixture_metadata()
        self.metadata.create_all(self.engine)
        self.customers = self.metadata.tables["customer_master"]
        self.org_id = uuid4().int % 1000000 + 1
        self.other_org_id = self.org_id + 1
        with self.engine.begin() as conn:
            conn.execute(self.metadata.tables["organization_master"].insert(), [
                {"org_id_pk": self.org_id}, {"org_id_pk": self.other_org_id},
            ])
        for module in (create, update, get, delete):
            self.stack.enter_context(patch.object(module, "db_engine", return_value=self.engine))
        self.principal = "creator@example.com"
        self.auth = auth_context(self.org_id, self.principal)
        for app in (api.app, consolidated_app):
            overrides = dict(app.dependency_overrides)
            self.stack.callback(self.restore_overrides, app, overrides)
            app.dependency_overrides[get_authenticated_context] = lambda: self.auth
        self.client = self.stack.enter_context(TestClient(api.app))
        self.consolidated_client = self.stack.enter_context(TestClient(consolidated_app))
        self.sql_calls = []
        event.listen(self.engine, "before_execute", self.check_bind_parameters)

    def drop_schema(self):
        with self.admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{self.schema}" CASCADE')

    @staticmethod
    def restore_overrides(app, overrides):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)

    def check_bind_parameters(self, conn, clause, multiparams, params, execution_options):
        if clause.__class__.__name__ == "TextClause":
            # All raw statements from the CRUD modules must use exactly their binds.
            self.assertEqual(set(clause.compile().params), set(params))
            self.sql_calls.append(str(clause))

    def payload(self, **changes):
        return {
            "cust_org_id_fk": self.org_id,
            "cust_code": f"CM-{uuid4().hex[:20]}",
            "cust_name": "Customer test",
            "cust_category": "Corporate",
            "cust_status": "INACTIVE",
            **revised_values(),
            "cust_billing_address": "Billing address",
            "cust_service_address": "Service address",
            "cust_tax_registration_number": "TEST-TRN",
            "cust_credit_period_days": 30,
            "cust_notes": "Customer schema verification",
            "created_by": self.principal,
            "updated_by": self.principal,
            **changes,
        }

    def row(self, code):
        with self.engine.connect() as conn:
            row = conn.execute(self.customers.select().where(self.customers.c.cust_code == code)).mappings().one_or_none()
            return dict(row) if row else None

    def assert_values(self, record, expected):
        for field, value in expected.items():
            self.assertEqual(record[field], value, field)

    def create_row(self, payload=None):
        payload = payload or self.payload()
        response = self.client.post("/api/v1/customers/create", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])
        return payload, self.row(payload["cust_code"])

    def test_create_all_fields_persist_in_correct_columns(self):
        payload, row = self.create_row()
        self.assert_values(row, payload)
        self.assertEqual(set(row), UNCHANGED_FIELDS | set(FIELD_LIMITS))
        self.assertIsNotNone(row["cust_id_pk"])
        self.assertIsInstance(row["created_at"], datetime)

    def test_create_optional_null_omitted_and_defaults(self):
        for explicit_null in (False, True):
            with self.subTest(explicit_null=explicit_null):
                payload = self.payload()
                for field in FIELD_LIMITS:
                    if explicit_null:
                        payload[field] = None
                    else:
                        payload.pop(field)
                payload.pop("cust_status")
                payload.pop("cust_credit_period_days")
                _, row = self.create_row(payload)
                self.assert_values(row, dict.fromkeys(FIELD_LIMITS))
                self.assertEqual(row["cust_status"], "ACTIVE")
                self.assertEqual(row["cust_credit_period_days"], 0)

    def test_created_by_validation_and_authenticated_audit_fields(self):
        for omitted in (False, True):
            payload = self.payload(created_by=None)
            if omitted:
                payload.pop("created_by")
            self.assertEqual(create.create_customer_master(payload), {"error": "created_by is required."})
            self.assertIsNone(self.row(payload["cust_code"]))
        _, row = self.create_row(self.payload(created_by="submitted", updated_by="submitted"))
        self.assertEqual(row["created_by"], self.principal)
        self.assertEqual(row["updated_by"], self.principal)

    def test_duplicate_code_is_case_insensitive_and_organization_scoped(self):
        payload, _ = self.create_row()
        duplicate = {**payload, "cust_code": payload["cust_code"].swapcase()}
        response = self.client.post("/api/v1/customers/create", json=duplicate)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Customer already exists", response.json()["error"])
        self.auth = auth_context(self.other_org_id, self.principal)
        self.create_row({**duplicate, "cust_org_id_fk": self.other_org_id})

    def test_update_all_fields_and_audit_without_changing_created_fields(self):
        payload, before = self.create_row()
        updates = {
            **payload, **revised_values("updated"), "cust_id": before["cust_id_pk"],
            "cust_code": f"CM-{uuid4().hex[:20]}", "cust_name": "Updated customer",
            "cust_category": "Key Account", "cust_status": "SUSPENDED",
            "cust_billing_address": "New billing", "cust_service_address": "New service",
            "cust_tax_registration_number": "NEW-TRN", "cust_credit_period_days": 45,
            "cust_notes": "Updated notes", "created_by": "must not overwrite",
        }
        self.auth = auth_context(self.org_id, "editor@example.com")
        response = self.client.post("/api/v1/customers/update", json=updates)
        self.assertEqual(response.status_code, 200, response.text)
        after = self.row(updates["cust_code"])
        expected = {k: v for k, v in updates.items() if k not in ("cust_id", "created_by", "updated_by")}
        self.assert_values(after, expected)
        self.assertEqual(after["created_by"], before["created_by"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["updated_by"], "editor@example.com")
        self.assertIsInstance(after["updated_at"], datetime)
        self.assertGreaterEqual(after["updated_at"], before["created_at"])

    def test_full_update_clears_omitted_or_null_optional_fields_and_keeps_defaults(self):
        for explicit_null in (False, True):
            with self.subTest(explicit_null=explicit_null):
                payload, before = self.create_row()
                for field in FIELD_LIMITS:
                    if explicit_null:
                        payload[field] = None
                    else:
                        payload.pop(field)
                payload.pop("cust_status")
                payload.pop("cust_credit_period_days")
                payload["cust_id_pk"] = before["cust_id_pk"]
                response = self.client.post("/api/v1/customers/update", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                row = self.row(payload["cust_code"])
                self.assert_values(row, dict.fromkeys(FIELD_LIMITS))
                self.assertEqual(row["cust_status"], "ACTIVE")
                self.assertEqual(row["cust_credit_period_days"], 0)

    def test_get_dataframe_json_service_and_consolidated_api(self):
        payload, row = self.create_row()
        update.update_customer({**payload, "cust_id": row["cust_id_pk"]})
        df, records_json = get.get_customer({"authenticated_org_id": self.org_id})
        self.assertEqual(set(df.columns), UNCHANGED_FIELDS | set(FIELD_LIMITS))
        records = json.loads(records_json)
        for client in (self.client, self.consolidated_client):
            response = client.get("/api/v1/customers")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"success": True, "data": records})
        self.assert_values(records[0], payload)
        for field in ("created_at", "updated_at"):
            self.assertIsInstance(records[0][field], str)
            datetime.fromisoformat(records[0][field])

    def test_delete_both_identifiers_removes_only_target_customer(self):
        retained, _ = self.create_row()
        for identifier in ("cust_id", "cust_id_pk"):
            payload, row = self.create_row()
            response = self.client.post("/api/v1/customers/delete", json={identifier: row["cust_id_pk"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["data"], {"message": f"Successfully deleted customer ID: {row['cust_id_pk']}"})
            self.assertIsNone(self.row(payload["cust_code"]))
        self.assertIsNotNone(self.row(retained["cust_code"]))

    def test_organization_isolation_for_get_update_delete_and_submitted_org(self):
        payload, before = self.create_row()
        self.auth = auth_context(self.other_org_id, self.principal)
        self.assertEqual(self.client.get("/api/v1/customers").json()["data"], [])
        mismatch = self.client.post("/api/v1/customers/create", json=payload)
        self.assertEqual(mismatch.status_code, 403)
        mismatch = self.client.post("/api/v1/customers/update", json={**payload, "cust_id": before["cust_id_pk"]})
        self.assertEqual(mismatch.status_code, 403)
        foreign_update = {**payload, "cust_org_id_fk": self.other_org_id, "cust_id": before["cust_id_pk"]}
        self.assertEqual(self.client.post("/api/v1/customers/update", json=foreign_update).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/customers/delete", json={"cust_id": before["cust_id_pk"]}).status_code, 404)
        self.assertEqual(self.row(payload["cust_code"]), before)

    def test_public_aliases_org_fallback_and_identifier_errors(self):
        payload = self.payload()
        payload["authenticated_org_id"] = payload.pop("cust_org_id_fk")
        self.assertIn("message", create.create_customer(payload))
        row = self.row(payload["cust_code"])
        self.assertEqual(row["cust_org_id_fk"], self.org_id)
        self.assertIn("message", update.update_customer({**payload, "cust_id_pk": row["cust_id_pk"]}))
        self.assertIn("message", delete.delete_customer({"cust_id_pk": row["cust_id_pk"], "authenticated_org_id": self.org_id}))
        for operation in (update.update_customer, delete.delete_customer):
            self.assertEqual(operation({}), {"error": "cust_id is required."})
            self.assertEqual(operation({"cust_id": row["cust_id_pk"], "authenticated_org_id": self.org_id}), {"error": "Customer ID not found."})
        self.assertEqual(get.get_customer(), {"error": "cust_org_id_fk is required."})

    def test_field_length_boundaries_for_create_and_update(self):
        payload, before = self.create_row({**self.payload(), **{f: "x" * n for f, n in FIELD_LIMITS.items()}})
        self.assert_values(before, payload)
        payload["cust_id"] = before["cust_id_pk"]
        response = self.client.post("/api/v1/customers/update", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        for field, limit in FIELD_LIMITS.items():
            for action in ("create", "update"):
                with self.subTest(field=field, action=action):
                    response = self.client.post(f"/api/v1/customers/{action}", json={**payload, field: "x" * (limit + 1)})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn(field, response.json()["error"])
        self.assert_values(self.row(payload["cust_code"]), {f: "x" * n for f, n in FIELD_LIMITS.items()})

    def test_existing_procurement_values_survive_get_and_full_update(self):
        # Seed independently of CREATE to represent a previously migrated record.
        payload = self.payload(**{f: None for f in FIELD_LIMITS if not f.startswith("procurement_head_")})
        with self.engine.begin() as conn:
            conn.execute(self.customers.insert(), payload)
        before = self.row(payload["cust_code"])
        record = self.client.get("/api/v1/customers").json()["data"][0]
        expected = {f: payload[f] for f in FIELD_LIMITS if f.startswith("procurement_head_")}
        self.assert_values(record, expected)
        response = self.client.post("/api/v1/customers/update", json={**record, "cust_notes": "Unrelated field edited"})
        self.assertEqual(response.status_code, 200, response.text)
        after = self.row(payload["cust_code"])
        self.assert_values(after, expected)
        self.assertEqual(after["created_at"], before["created_at"])

    def test_sql_bind_integrity_and_parameterized_values(self):
        payload = self.payload(procurement_head_name="O'Reilly :value; --", portal_system="portal'); DROP TABLE customer_master; --")
        payload, row = self.create_row(payload)
        self.assertIn("message", update.update_customer({**payload, "cust_id": row["cust_id_pk"]}))
        self.assert_values(self.row(payload["cust_code"]), payload)
        self.client.get("/api/v1/customers")
        self.assertIn("message", delete.delete_customer({"cust_id": row["cust_id_pk"], "cust_org_id_fk": self.org_id}))
        for statement in self.sql_calls:
            self.assertNotIn(payload["procurement_head_name"], statement)
            self.assertNotIn(payload["portal_system"], statement)
        for operation in ("insert into customer_master", "update customer_master", "select *", "delete from customer_master"):
            self.assertTrue(any(operation in sql.lower() for sql in self.sql_calls), operation)

    def test_openapi_customer_models_have_only_revised_contract(self):
        for app in (api.app, consolidated_app):
            schemas = app.openapi()["components"]["schemas"]
            expected = (UNCHANGED_FIELDS - {"cust_id_pk", "created_at", "updated_at"}) | set(FIELD_LIMITS)
            for model in ("CustomerMasterPayload", "CustomerMasterUpdatePayload"):
                schema = schemas[model]
                allowed = expected | ({"cust_id", "cust_id_pk"} if model.endswith("UpdatePayload") else set())
                self.assertEqual(set(schema["properties"]), allowed)
                for field in FIELD_LIMITS:
                    self.assertNotIn(field, schema["required"])

    @unittest.skipUnless(TEST_DATABASE_URL, "PostgreSQL test URL required for actual migration verification.")
    def test_supplied_postgres_migration_preserves_existing_procurement_data(self):
        # Exercise the user-supplied SQL only in this test's isolated schema.
        self.metadata.drop_all(self.engine)
        ddl = (SERVICE_DIR / "data/customer_master.sql").read_text()
        original, migration = ddl.split("-- NEW TABLE STRUCTURE SCRIPT", 1)
        mapping = re.findall(r"RENAME COLUMN\s+(\w+)\s+TO\s+(\w+)", migration)
        self.assertEqual(len(mapping), 5)
        before_values = {old: f"preserved-{index}" for index, (old, _) in enumerate(mapping)}
        def script(sql):
            sql = re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", sql, flags=re.M)
            return sql.replace("public.", f'"{self.schema}".')
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE organization_master(org_id_pk INTEGER PRIMARY KEY)")
            conn.execute(text("INSERT INTO organization_master VALUES(:org)"), {"org": self.org_id})
            conn.exec_driver_sql(script(original))
            values = {"cust_org_id_fk": self.org_id, "cust_code": "PRE-MIGRATION", "cust_name": "Existing", "cust_category": "Corporate", **before_values}
            columns = ", ".join(values)
            binds = ", ".join(f":{field}" for field in values)
            conn.execute(text(f"INSERT INTO customer_master ({columns}) VALUES ({binds})"), values)
            conn.exec_driver_sql(script(migration))
        record = self.client.get("/api/v1/customers").json()["data"][0]
        expected = {new: before_values[old] for old, new in mapping}
        self.assert_values(record, expected)
        self.assertEqual(set(record), UNCHANGED_FIELDS | set(FIELD_LIMITS))
        response = self.client.post("/api/v1/customers/update", json={**record, "cust_notes": "After migration"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_values(self.row("PRE-MIGRATION"), expected)


if __name__ == "__main__":
    unittest.main()
