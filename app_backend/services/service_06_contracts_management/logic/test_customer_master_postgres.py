"""Explicitly opt-in checks against the migrated application PostgreSQL database.

Run only when authorized: CUSTOMER_MASTER_LIVE_CHECKS=1 python -m unittest
app_backend.services.service_06_contracts_management.logic.test_customer_master_postgres -v

Uses RAILWAY_DB_URL through the existing db_engine(). Creates uniquely named
disposable customers, cleans them up, and only reads pre-existing customers.
Does not execute migrations or create/alter tables, schemas, or organizations.
"""
from contextlib import ExitStack
import csv
from datetime import datetime
import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_06_contracts_management.logic.test_customer_master_schema import (
    FIELD_LIMITS, SERVICE_DIR, UNCHANGED_FIELDS, api, auth_context,
    consolidated_app, create, delete, get, get_authenticated_context,
    revised_values, update,
)


@unittest.skipUnless(os.getenv("CUSTOMER_MASTER_LIVE_CHECKS") == "1", "Live database checks require explicit opt-in.")
class CustomerMasterPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = db_engine()
        cls.addClassCleanup(cls.engine.dispose)
        with cls.engine.connect() as conn:
            cls.columns = {row["column_name"]: dict(row) for row in conn.execute(text("""
                SELECT column_name, character_maximum_length, is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'customer_master'
            """)).mappings()}
            if set(cls.columns) != UNCHANGED_FIELDS | set(FIELD_LIMITS):
                raise RuntimeError("Customer Master must already have the revised schema.")
            cls.org_id = conn.execute(text("SELECT MIN(org_id_pk) FROM organization_master")).scalar_one()
            if cls.org_id is None:
                raise RuntimeError("An existing organization is required; the test will not create one.")
            cls.foreign_org_id = conn.execute(text("SELECT MAX(org_id_pk) + 1 FROM organization_master")).scalar_one()
            cls.existing_customers = [dict(row) for row in conn.execute(text("SELECT * FROM customer_master")).mappings()]

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.codes = []
        self.addCleanup(self.cleanup_customers)
        self.auth = auth_context(self.org_id, "customer-schema-test@example.invalid")
        for module in (create, update, get, delete):
            self.stack.enter_context(patch.object(module, "db_engine", return_value=self.engine))
        for app in (api.app, consolidated_app):
            overrides = dict(app.dependency_overrides)
            self.stack.callback(self.restore_overrides, app, overrides)
            app.dependency_overrides[get_authenticated_context] = lambda: self.auth
        self.client = self.stack.enter_context(TestClient(api.app))
        self.consolidated_client = self.stack.enter_context(TestClient(consolidated_app))
        event.listen(self.engine, "before_execute", self.check_binds)
        self.stack.callback(event.remove, self.engine, "before_execute", self.check_binds)

    @staticmethod
    def restore_overrides(app, overrides):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)

    def check_binds(self, conn, clause, multiparams, params, execution_options):
        if clause.__class__.__name__ == "TextClause":
            self.assertEqual(set(clause.compile().params), set(params))

    def cleanup_customers(self):
        for code in self.codes:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM customer_master WHERE cust_org_id_fk=:org AND cust_code=:code"), {"org": self.org_id, "code": code})
            self.assertIsNone(self.row(code), "Disposable customer cleanup failed.")

    def payload(self):
        code = f"CMQA-{uuid4().hex[:24]}"
        self.codes.append(code)
        return {
            "cust_org_id_fk": self.org_id, "cust_code": code,
            "cust_name": "Disposable Customer Master QA", "cust_category": "Corporate",
            "cust_status": "INACTIVE", **revised_values(),
            "cust_billing_address": "QA billing", "cust_service_address": "QA service",
            "cust_tax_registration_number": "QA-TRN", "cust_credit_period_days": 30,
            "cust_notes": "Temporary CRUD schema verification",
            "created_by": self.auth["user"]["user_principal_name"],
            "updated_by": self.auth["user"]["user_principal_name"],
        }

    def row(self, code):
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM customer_master WHERE cust_org_id_fk=:org AND cust_code=:code"), {"org": self.org_id, "code": code}).mappings().one_or_none()
            return dict(row) if row else None

    def assert_values(self, row, expected):
        for field, value in expected.items():
            # Avoid printing real customer values in a failed baseline comparison.
            self.assertTrue(row[field] == value, f"Value mismatch in {field}.")

    def test_deployed_schema_lengths_and_nullability(self):
        self.assertEqual(set(self.columns), UNCHANGED_FIELDS | set(FIELD_LIMITS))
        for field, length in FIELD_LIMITS.items():
            self.assertEqual(self.columns[field]["character_maximum_length"], length, field)
            self.assertEqual(self.columns[field]["is_nullable"], "YES", field)

    def test_create_update_get_delete_all_fields_and_audit(self):
        payload = self.payload()
        response = self.client.post("/api/v1/customers/create", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        before = self.row(payload["cust_code"])
        self.assert_values(before, payload)
        self.assertIsInstance(before["created_at"], datetime)
        changed = {
            **payload, **revised_values("updated"), "cust_id_pk": before["cust_id_pk"],
            "cust_name": "Updated disposable customer", "cust_category": "Key Account",
            "cust_status": "SUSPENDED", "cust_billing_address": "Updated billing",
            "cust_service_address": "Updated service", "cust_tax_registration_number": "QA-NEW",
            "cust_credit_period_days": 45, "cust_notes": "Updated test",
        }
        self.auth = auth_context(self.org_id, "customer-schema-editor@example.invalid")
        response = self.client.post("/api/v1/customers/update", json=changed)
        self.assertEqual(response.status_code, 200, response.text)
        after = self.row(payload["cust_code"])
        self.assert_values(after, {**changed, "updated_by": self.auth["user"]["user_principal_name"]})
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertIsInstance(after["updated_at"], datetime)
        self.assertGreaterEqual(after["updated_at"], before["created_at"])
        for client in (self.client, self.consolidated_client):
            response = client.get("/api/v1/customers")
            self.assertEqual(response.status_code, 200, response.text)
            record = next(row for row in response.json()["data"] if row["cust_id_pk"] == before["cust_id_pk"])
            self.assertEqual(set(record), UNCHANGED_FIELDS | set(FIELD_LIMITS))
            self.assert_values(record, revised_values("updated"))
            for field in ("created_at", "updated_at"):
                self.assertIsInstance(record[field], str)
                datetime.fromisoformat(record[field])
        response = self.client.post("/api/v1/customers/delete", json={"cust_id": before["cust_id_pk"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.row(payload["cust_code"]))

    def test_optional_fields_defaults_creator_validation_and_duplicates(self):
        for explicit_null in (False, True):
            payload = self.payload()
            for field in FIELD_LIMITS:
                if explicit_null:
                    payload[field] = None
                else:
                    payload.pop(field)
            payload.pop("cust_status")
            payload.pop("cust_credit_period_days")
            self.assertEqual(create.create_customer({**payload, "created_by": None}), {"error": "created_by is required."})
            self.assertIsNone(self.row(payload["cust_code"]))
            self.assertIn("message", create.create_customer(payload))
            row = self.row(payload["cust_code"])
            self.assert_values(row, dict.fromkeys(FIELD_LIMITS))
            self.assertEqual(row["cust_status"], "ACTIVE")
            self.assertEqual(row["cust_credit_period_days"], 0)
            self.codes.append(payload["cust_code"].swapcase())
            duplicate = create.create_customer({**payload, "cust_code": payload["cust_code"].swapcase()})
            self.assertIn("Customer already exists", duplicate["error"])
            self.assertIn("message", update.update_customer({**payload, "cust_id": row["cust_id_pk"]}))
            self.assert_values(self.row(payload["cust_code"]), dict.fromkeys(FIELD_LIMITS))
            self.assertIn("message", delete.delete_customer({"cust_id_pk": row["cust_id_pk"], "authenticated_org_id": self.org_id}))
            self.assertIsNone(self.row(payload["cust_code"]))

    def test_foreign_organization_cannot_get_update_or_delete_customer(self):
        payload = self.payload()
        self.assertIn("message", create.create_customer(payload))
        before = self.row(payload["cust_code"])
        foreign = {**payload, "cust_id": before["cust_id_pk"], "cust_org_id_fk": self.foreign_org_id}
        self.assertEqual(update.update_customer(foreign), {"error": "Customer ID not found."})
        self.assertEqual(delete.delete_customer(foreign), {"error": "Customer ID not found."})
        df, _ = get.get_customer({"cust_org_id_fk": self.foreign_org_id})
        self.assertTrue(df.empty)
        self.assertEqual(self.row(payload["cust_code"]), before)

    def test_existing_customer_is_unchanged_and_procurement_values_are_exposed(self):
        self.assertTrue(self.existing_customers, "No pre-existing customer available.")
        for before in self.existing_customers:
            with self.engine.connect() as conn:
                after = dict(conn.execute(text("SELECT * FROM customer_master WHERE cust_id_pk=:id AND cust_org_id_fk=:org"), {"id": before["cust_id_pk"], "org": before["cust_org_id_fk"]}).mappings().one())
            self.assertTrue(after == before, "A pre-existing customer changed during the test run.")
            self.auth = auth_context(before["cust_org_id_fk"], "customer-schema-test@example.invalid")
            response = self.client.get("/api/v1/customers")
            self.assertEqual(response.status_code, 200)
            record = next(item for item in response.json()["data"] if item["cust_id_pk"] == before["cust_id_pk"])
            self.assert_values(record, {field: before[field] for field in FIELD_LIMITS if field.startswith("procurement_head_")})
        print(f"Existing-data evidence: {len(self.existing_customers)} customer(s) unchanged; procurement GET values match the database.")

    def test_existing_procurement_data_matches_pre_migration_csv(self):
        mapping = re.findall(r"RENAME COLUMN\s+(\w+)\s+TO\s+(\w+)", (SERVICE_DIR / "data/customer_master.sql").read_text())
        self.assertEqual(len(mapping), 5)
        with (SERVICE_DIR / "data/customer_master.csv").open() as source:
            baseline = list(csv.DictReader(source))
        if not baseline:
            self.skipTest("The CSV has headers only; no historical data baseline is available.")
        checked = 0
        nonempty = 0
        for previous in baseline:
            with self.engine.connect() as conn:
                row = conn.execute(text("SELECT * FROM customer_master WHERE cust_id_pk=:id AND cust_org_id_fk=:org"), {"id": previous["cust_id_pk"], "org": previous["cust_org_id_fk"]}).mappings().one_or_none()
            if row is None:
                continue
            checked += 1
            expected = {new: previous[old] or None for old, new in mapping}
            nonempty += sum(value is not None for value in expected.values())
            self.assert_values(row, expected)
            self.auth = auth_context(row["cust_org_id_fk"], "customer-schema-test@example.invalid")
            response = self.client.get("/api/v1/customers")
            self.assertEqual(response.status_code, 200)
            record = next(item for item in response.json()["data"] if item["cust_id_pk"] == row["cust_id_pk"])
            self.assert_values(record, expected)
        self.assertGreater(checked, 0, "No existing customer matched the CSV baseline.")
        self.assertGreater(nonempty, 0, "No populated procurement baseline values available.")
        print(f"Existing-data evidence: {checked} customer(s), {nonempty} populated procurement values preserved.")


if __name__ == "__main__":
    unittest.main()
