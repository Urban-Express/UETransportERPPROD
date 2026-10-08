"""Opt-in PostgreSQL tests using RAILWAY_DB_URL and session-local TEMP tables.

Run with CONTRACT_NUMERIC_DB_CHECKS=1 python -m unittest <this module> -v.
Copies schema only, never production rows. Serial defaults use private temporary
sequences. All DML resolves only in pg_temp; the outer transaction is rolled back.
No migrations, persistent table changes, real approvals, or Firebase writes occur.
The workflow/CRUD SQL is real; auth and storage use isolated test fixtures.
"""
from contextlib import contextmanager, ExitStack
from decimal import Decimal
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_06_contracts_management.logic.test_contracts_management_numeric_fields import (
    api, auth_context, CASES, consolidated_app, contract_payload, DECIMALS, FIELDS,
    get_authenticated_context, POPULATED, REQUESTER, REVIEWER, create, update,
)
from app_backend.services.service_06_contracts_management.logic import contracts_management_delete_data as delete
from app_backend.services.service_06_contracts_management.logic import contracts_management_get_data as get
from app_backend.services.service_06_contracts_management.integrations import firebase_contract_document_upload as upload
from app_backend.services.service_07_alerts_wf_engine import contracts_management_wf as workflow
from app_backend.services.service_07_alerts_wf_engine import workflow_adapter_helpers as helpers
from app_backend.services.service_07_alerts_wf_engine import workflow_runtime_engine as runtime
from app_backend.services.service_07_alerts_wf_engine import workflow_document_cleanup as cleanup
from app_backend.services.service_07_alerts_wf_engine import workflow_document_download as download
from app_backend.services.service_07_alerts_wf_engine.tests.test_workflow_attachment_download import VersionedBucket


class TransactionEngine:
    """Keep normal engine entry points inside one rollback-only connection."""
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def begin(self):
        with self.conn.begin_nested():
            yield self.conn

    @contextmanager
    def connect(self):
        yield self.conn

    def dispose(self):
        pass


@unittest.skipUnless(os.getenv("CONTRACT_NUMERIC_DB_CHECKS") == "1", "Explicit Railway TEMP-table test opt-in required.")
class ContractNumericPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = db_engine()
        cls.addClassCleanup(cls.engine.dispose)
        cls.conn = cls.engine.connect()
        cls.addClassCleanup(cls.conn.close)
        transaction = cls.conn.begin()
        cls.addClassCleanup(transaction.rollback)
        cls.conn.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
        cls.columns = {row["column_name"]: dict(row) for row in cls.conn.execute(text("""
            SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'contracts_management'
        """)).mappings()}
        for field in FIELDS:
            column = cls.columns[field]
            if (column["data_type"], column["numeric_precision"], column["numeric_scale"], column["is_nullable"], column["column_default"]) != ("numeric", 14, 2, "YES", None):
                raise RuntimeError(f"{field} does not match the supplied database contract.")

        # Excluding public prevents missing test tables from resolving to live data.
        cls.conn.exec_driver_sql("SET LOCAL search_path = pg_temp")
        tables = (
            "contracts_management", "contracts_management_workflow_requests",
            "workflow_definitions", "workflow_definition_versions", "workflow_nodes",
            "workflow_edges", "workflow_instances", "workflow_instance_steps",
        )
        quote = cls.conn.dialect.identifier_preparer.quote
        for table_index, table in enumerate(tables):
            cls.conn.exec_driver_sql(f"CREATE TEMP TABLE {quote(table)} (LIKE public.{quote(table)} INCLUDING ALL) ON COMMIT DROP")
            defaults = cls.conn.execute(text("""
                SELECT column_name, column_default FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table
            """), {"table": table}).mappings()
            for column_index, column in enumerate(defaults):
                if (column["column_default"] or "").startswith("nextval("):
                    sequence = f"contract_qa_seq_{table_index}_{column_index}"
                    cls.conn.exec_driver_sql(f"CREATE TEMP SEQUENCE {sequence}")
                    cls.conn.exec_driver_sql(f"ALTER TABLE pg_temp.{quote(table)} ALTER COLUMN {quote(column['column_name'])} SET DEFAULT nextval('pg_temp.{sequence}'::regclass)")

        cls.conn.exec_driver_sql("""
            CREATE TEMP TABLE customer_master (cust_id_pk INTEGER PRIMARY KEY, cust_org_id_fk INTEGER);
            CREATE TEMP TABLE department_master (dep_id_pk INTEGER PRIMARY KEY, dep_org_id_fk INTEGER);
            INSERT INTO customer_master VALUES (10,77),(11,88);
            INSERT INTO department_master VALUES (20,77),(21,88);
            CREATE TEMP TABLE user_master (user_id_pk INTEGER PRIMARY KEY, user_principal_name TEXT,
                user_org_id_fk INTEGER, display_name TEXT, email TEXT,
                is_active BOOLEAN DEFAULT TRUE, is_deleted BOOLEAN DEFAULT FALSE);
            CREATE TEMP VIEW v_user_access_rights AS
                SELECT user_principal_name, 'WORKFLOW'::text AS module_name, action_name
                FROM user_master CROSS JOIN (VALUES ('SUBMIT'), ('APPROVE')) AS actions(action_name);
        """)
        cls.conn.execute(text("INSERT INTO user_master (user_id_pk,user_principal_name,user_org_id_fk) VALUES (1,:requester,77),(2,:reviewer,77)"), {"requester": REQUESTER, "reviewer": REVIEWER})
        cls.conn.exec_driver_sql("""
            INSERT INTO workflow_definitions (workflow_definition_id_pk,workflow_code,workflow_name,service_name,entity_name,organization_id_fk)
                VALUES (1,'CONTRACTS_MANAGEMENT','Contract test','service_06_contracts_management','contracts_management',77);
            INSERT INTO workflow_definition_versions (workflow_version_id_pk,workflow_definition_id_fk,version_number,version_status,applies_to_action)
                VALUES (1,1,1,'PUBLISHED','ALL');
        """)
        cls.conn.execute(text("""
            INSERT INTO workflow_nodes (workflow_node_id_pk,workflow_version_id_fk,node_key,node_type,user_principal_name)
                VALUES (1,1,'requester','REQUESTER',:requester),(2,1,'reviewer','APPROVER',:reviewer),(3,1,'end','END',NULL)
        """), {"requester": REQUESTER, "reviewer": REVIEWER})
        cls.conn.exec_driver_sql("""
            INSERT INTO workflow_edges (workflow_version_id_fk,source_node_id_fk,target_node_id_fk,edge_sequence)
                VALUES (1,1,2,1),(1,2,3,2)
        """)
        cls.scoped_engine = TransactionEngine(cls.conn)

    def setUp(self):
        savepoint = self.conn.begin_nested()
        self.addCleanup(savepoint.rollback)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (create, update, delete, get, upload, helpers, runtime):
            self.stack.enter_context(patch.object(module, "db_engine", return_value=self.scoped_engine))
        self.auth = auth_context()
        for app in (api.app, consolidated_app):
            previous = dict(app.dependency_overrides)
            self.stack.callback(self.restore_overrides, app, previous)
            app.dependency_overrides[get_authenticated_context] = lambda: self.auth
        self.client = self.stack.enter_context(TestClient(api.app))
        self.consolidated_client = self.stack.enter_context(TestClient(consolidated_app))
        self.bucket = VersionedBucket()
        for module in (upload, cleanup, download):
            self.stack.enter_context(patch.object(module, "initialize_firebase_app"))
            self.stack.enter_context(patch.object(module, "FIREBASE_STORAGE_BUCKET", "task164-test-bucket"))
        self.stack.enter_context(patch.object(upload.storage, "bucket", return_value=self.bucket))

    @staticmethod
    def restore_overrides(app, previous):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    def payload(self, **values):
        return contract_payload(cont_contract_number=f"CNQA-{uuid4().hex[:20]}", **values)

    def row(self, contract_id):
        row = self.conn.execute(text("SELECT * FROM contracts_management WHERE cont_id_pk=:id"), {"id": contract_id}).mappings().one_or_none()
        return dict(row) if row else None

    def submit(self, payload, action="create", document=None, client=None, multipart=False):
        client = client or self.client
        path = f"/api/v1/contracts/{action}"
        if multipart and document is None:
            response = client.post(path + "-with-document", files={"payload": (None, json.dumps(payload))})
        elif document is None:
            response = client.post(path, json=payload)
        else:
            response = client.post(path + "-with-document", data={"payload": json.dumps(payload)}, files={"file": ("contract.pdf", document, "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        pending = response.json()["data"]
        self.assertEqual(pending.get("workflow_status"), "PENDING_APPROVAL", pending)
        self.assertFalse(pending["business_operation_executed"])
        proposed = self.conn.execute(text("SELECT request_payload FROM workflow_instances WHERE workflow_instance_id_pk=:id"), {"id": pending["workflow_instance_id"]}).scalar_one()
        legacy = self.conn.execute(text("SELECT request_payload FROM contracts_management_workflow_requests WHERE workflow_request_id_pk=:id"), {"id": pending["workflow_request_id"]}).scalar_one()
        for field in FIELDS:
            if action == "update" and field not in payload:
                self.assertNotIn(field, proposed)
                self.assertNotIn(field, legacy)
            else:
                self.assertIn(field, proposed)
                self.assertEqual(proposed[field], payload.get(field))
                self.assertEqual(legacy[field], payload.get(field))
        return pending, proposed

    def act(self, pending, reject=False):
        action = workflow.reject_contracts_management_workflow if reject else workflow.approve_contracts_management_workflow
        result = action({
            "workflow_instance_id": pending["workflow_instance_id"],
            "workflow_instance_step_id": pending["workflow_instance_step_id"],
            "cont_org_id_fk": 77,
            "acting_user_principal_name": REVIEWER,
        })
        self.assertEqual(result.get("workflow_status"), "REJECTED" if reject else "EXECUTED", result)
        if not reject:
            self.assertTrue(result["business_operation_executed"])
            return result["execution_result"]["cont_id_pk"]
        return result

    def assert_round_trip(self, contract_id, values):
        stored = self.row(contract_id)
        exact = get.get_contract_by_id({"cont_id_pk": contract_id, "authenticated_org_id": 77})
        _, records = get.get_contracts_management({"cont_org_id_fk": 77})
        listed = next(row for row in json.loads(records) if row["cont_id_pk"] == contract_id)
        api_exact = self.client.get(f"/api/v1/contracts/{contract_id}")
        api_list = self.client.get("/api/v1/contracts")
        self.assertEqual(api_exact.status_code, 200, api_exact.text)
        self.assertEqual(api_list.status_code, 200, api_list.text)
        http_rows = [api_exact.json()["data"], next(row for row in api_list.json()["data"] if row["cont_id_pk"] == contract_id)]
        for field in FIELDS:
            expected = values.get(field)
            for row in (stored, exact, listed, *http_rows):
                self.assertIn(field, row)
                if expected is None:
                    self.assertIsNone(row[field], (field, row[field]))
                else:
                    self.assertNotIsInstance(row[field], str)
                    self.assertEqual(Decimal(str(row[field])), Decimal(str(expected)), field)
            if expected is not None:
                self.assertIsInstance(stored[field], Decimal)
                self.assertEqual(stored[field].as_tuple().exponent, -2)
        return stored

    def create_approved(self, values, **kwargs):
        payload = self.payload(**values)
        pending, proposed = self.submit(payload, **kwargs)
        self.assertIsNone(self.conn.execute(text("SELECT cont_id_pk FROM contracts_management WHERE cont_contract_number=:number"), {"number": payload["cont_contract_number"]}).scalar_one_or_none())
        contract_id = self.act(pending)
        self.assert_round_trip(contract_id, values)
        return contract_id, payload, proposed

    def test_create_all_four_populated(self):
        self.create_approved(POPULATED)

    def test_create_all_four_null(self):
        self.create_approved(CASES["null"])

    def test_create_old_payload_omits_all_four(self):
        self.create_approved({}, client=self.consolidated_client)

    def test_create_zero_values(self):
        self.create_approved(CASES["zero"])

    def test_create_two_decimal_precision(self):
        self.create_approved(DECIMALS)

    def test_update_transitions_and_one_field_change(self):
        contract_id, payload, _ = self.create_approved({})
        payload["cont_id_pk"] = contract_id
        cases = (POPULATED, DECIMALS, {**DECIMALS, "cont_per_day_rate": 250.00}, CASES["null"], POPULATED, CASES["zero"], {})
        for values in cases:
            with self.subTest(values=values):
                before = self.row(contract_id)
                pending, _ = self.submit({**payload, **values}, action="update")
                self.assertEqual(self.row(contract_id), before, "Pending update changed persisted contract")
                self.assertEqual(self.act(pending), contract_id)
                expected = {field: values.get(field, before[field]) for field in FIELDS}
                after = self.assert_round_trip(contract_id, expected)
                for key in before.keys() - set(FIELDS) - {"updated_at"}:
                    self.assertEqual(after[key], before[key], key)

    def assert_legacy_update_preservation(self, multipart):
        contract_id, payload, _ = self.create_approved(POPULATED)
        legacy_payload = {key: value for key, value in payload.items() if key not in FIELDS}
        legacy_payload["cont_id_pk"] = contract_id
        expected = dict(POPULATED)
        clear_field = FIELDS[1] if multipart else FIELDS[0]
        changes = ({}, {clear_field: None}, {FIELDS[2]: 0})
        for values in changes:
            with self.subTest(multipart=multipart, changes=values):
                before = self.row(contract_id)
                pending, _ = self.submit(
                    {**legacy_payload, **values}, "update", multipart=multipart,
                    client=self.client if multipart else self.consolidated_client,
                )
                self.assertEqual(self.row(contract_id), before)
                self.assertEqual(self.act(pending), contract_id)
                expected.update(values)
                after = self.assert_round_trip(contract_id, expected)
                for key in before.keys() - set(values) - {"updated_at"}:
                    self.assertEqual(after[key], before[key], key)

    def test_legacy_json_update_preserves_omitted_fields_and_explicit_null(self):
        self.assert_legacy_update_preservation(multipart=False)

    def test_legacy_multipart_update_preserves_omitted_fields_and_explicit_null(self):
        self.assert_legacy_update_preservation(multipart=True)

    def test_get_one_and_fractional_values(self):
        self.create_approved(dict(zip(FIELDS, (1, 1.50, 250.00, 1250.75))))

    def test_historical_record_exact_get_update_and_delete(self):
        # Seed through SQL without the new columns, as a historical record would be.
        payload = self.payload()
        params = create.get_contract_insert_params(payload)
        for field in FIELDS:
            params.pop(field)
        columns = ", ".join(params)
        binds = ", ".join(f":{key}" for key in params)
        contract_id = self.conn.execute(text(f"INSERT INTO contracts_management ({columns}) VALUES ({binds}) RETURNING cont_id_pk"), params).scalar_one()
        self.assert_round_trip(contract_id, {})
        pending, _ = self.submit({**payload, "cont_id_pk": contract_id, **POPULATED}, "update")
        self.act(pending)
        self.assert_round_trip(contract_id, POPULATED)
        result = delete.delete_contracts_management({"cont_id_pk": contract_id, "cont_org_id_fk": 77})
        self.assertNotIn("error", result)
        self.assertIsNone(self.row(contract_id))

    def test_delete_and_cross_organization_scoping(self):
        contract_id, payload, _ = self.create_approved(POPULATED)
        before = self.row(contract_id)
        self.assertIn("error", delete.delete_contract({"cont_id_pk": contract_id, "cont_org_id_fk": 88}))
        self.assertIn("error", get.get_contract_by_id({"cont_id_pk": contract_id, "authenticated_org_id": 88}))
        self.assertIn("error", update.update_contract({**payload, "cont_id_pk": contract_id, "cont_org_id_fk": 88}))
        _, rows = get.get_contract({"cont_org_id_fk": 88})
        self.assertEqual(json.loads(rows), [])
        self.assertEqual(self.row(contract_id), before)
        response = self.client.post("/api/v1/contracts/delete", json={"cont_id_pk": contract_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.row(contract_id))

    def test_references_and_duplicate_number_checks(self):
        contract_id, payload, _ = self.create_approved(POPULATED)
        before = self.row(contract_id)
        for field, value, error in (("cont_cust_id_fk", 11, "Customer ID not found."), ("cont_dep_id_fk", 21, "Department ID not found.")):
            for func, extra in ((create.create_contract, {}), (update.update_contract, {"cont_id_pk": contract_id})):
                with self.subTest(field=field, func=func.__name__):
                    result = func({**self.payload(**POPULATED), **extra, field: value})
                    self.assertEqual(result.get("error"), error)
        duplicate = create.create_contract({**payload, "cont_contract_number": payload["cont_contract_number"].lower()})
        self.assertIn("Contract already exists", duplicate["error"])
        self.assertEqual(self.row(contract_id), before)

    def test_document_create_replacement_and_rejected_update(self):
        first_bytes = b"%PDF-1.4\nOriginal contract\n%%EOF"
        contract_id, payload, proposed = self.create_approved(POPULATED, document=first_bytes)
        original_pointer = self.row(contract_id)["cont_link_path"]
        self.assertEqual(original_pointer, proposed["cont_link_path"])
        original_path = proposed["workflow_document_staged_blob_path"]
        self.assertTrue(self.bucket.blob(original_path).exists())

        next_bytes = b"%PDF-1.4\nReplacement contract\n%%EOF"
        pending, proposed = self.submit({**payload, "cont_id_pk": contract_id, **DECIMALS}, "update", document=next_bytes)
        self.assertEqual(self.row(contract_id)["cont_link_path"], original_pointer)
        metadata = proposed["workflow_document_attachment"]
        instance = {"workflow_instance_id": pending["workflow_instance_id"], "workflow_code": "CONTRACTS_MANAGEMENT", "workflow_action": "UPDATE", "organization_id": 77, "workflow_status": "PENDING_APPROVAL", "request_payload": proposed}
        downloaded = download.download_workflow_document(instance, metadata["attachment_id"], metadata["version_id"])
        self.assertEqual(self.bucket.fetch(downloaded["download_url"]), next_bytes)
        self.act(pending)
        self.assert_round_trip(contract_id, DECIMALS)
        self.assertEqual(self.row(contract_id)["cont_link_path"], proposed["cont_link_path"])
        self.assertNotEqual(proposed["cont_link_path"], original_pointer)
        self.assertTrue(self.bucket.blob(proposed["workflow_document_staged_blob_path"]).exists())

        before = self.row(contract_id)
        pending, rejected = self.submit({**payload, "cont_id_pk": contract_id, **CASES["null"]}, "update", document=b"Rejected replacement")
        self.assertEqual(self.row(contract_id), before)
        self.act(pending, reject=True)
        self.assertEqual(self.row(contract_id), before)
        self.assertFalse(self.bucket.blob(rejected["workflow_document_staged_blob_path"]).exists())
        self.assertTrue(self.bucket.blob(proposed["workflow_document_staged_blob_path"]).exists())


if __name__ == "__main__":
    unittest.main()
