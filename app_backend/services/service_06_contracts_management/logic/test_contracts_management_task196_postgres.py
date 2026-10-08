"""Real SQL constraints and workflow execution in disposable loopback databases.

Requires CONTRACT_TASK196_TEST_DATABASE_URL; never defaults to Railway.
Only identity authentication and object storage are test doubles.
"""
from decimal import Decimal
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .task196_postgres_fixture import DisposableContractDatabase, REVIEWER
from .test_contracts_management_numeric_postgres import ContractNumericPostgresTest, TransactionEngine
from .test_contracts_management_numeric_fields import api, consolidated_app, create, update, FIELDS
from .test_contracts_management_task196 import priced_payload
from .contracts_management_validation import CONTRACT_REVENUE_BASES, CONTRACT_STATUSES, CONTRACT_COUNT_FIELDS
from app_backend.services.service_07_alerts_wf_engine.api import main as workflow_api
from app_backend.services.service_07_alerts_wf_engine.api import workflow_admin_api
from app_backend.services.service_07_alerts_wf_engine.logic import workflow_runtime_logic
from app_backend.services.service_07_alerts_wf_engine.logic.workflow_attachment_logic import public_instance


EXECUTION_EVIDENCE = []


def insert_row(conn, payload):
    params = create.get_contract_insert_params(payload)
    return conn.execute(text(f"INSERT INTO public.contracts_management ({', '.join(params)}) VALUES ({', '.join(':'+key for key in params)}) RETURNING cont_id_pk"), params).scalar_one()


class ContractTask196DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = DisposableContractDatabase(legacy=True, migrate=True)
        cls.addClassCleanup(cls.database.close)
        cls.conn = cls.database.engine.connect()
        cls.addClassCleanup(cls.conn.close)
        cls.transaction = cls.conn.begin()
        cls.addClassCleanup(cls.transaction.rollback)

    def setUp(self):
        savepoint = self.conn.begin_nested()
        self.addCleanup(savepoint.rollback)

    def persist(self, payload, accepted):
        payload = {**payload, "cont_contract_number": f"T196-{uuid4().hex[:20]}"}
        if not accepted:
            with self.assertRaises(IntegrityError), self.conn.begin_nested():
                insert_row(self.conn, payload)
            return
        contract_id = insert_row(self.conn, payload)
        row = self.conn.execute(text("SELECT * FROM public.contracts_management WHERE cont_id_pk=:id"), {"id": contract_id}).mappings().one()
        self.assertEqual(row["cont_revenue_basis"], payload["cont_revenue_basis"])
        for field in FIELDS:
            expected = payload.get(field)
            self.assertEqual(row[field], Decimal(str(expected)) if expected is not None else None)
        self.assertEqual(row["cont_no_of_buses"], sum(payload.get(field, 0) for field in CONTRACT_COUNT_FIELDS[1:]))

    def test_exact_column_types_no_defaults_and_later_fields_preserved(self):
        columns = {r["column_name"]: dict(r) for r in self.conn.execute(text("SELECT column_name,data_type,numeric_precision,numeric_scale,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='contracts_management'")).mappings()}
        for field in FIELDS:
            col = columns[field]
            self.assertEqual((col["data_type"], col["numeric_precision"], col["numeric_scale"], col["is_nullable"], col["column_default"]), ("numeric", 14, 2, "YES", None))
        self.assertEqual(columns["total_contract_value"]["data_type"], "double precision")
        self.assertTrue({"cont_link_path", "cont_approval_status"}.issubset(columns))

    def test_nonnegative_independent_of_basis_status_and_applicable_fields(self):
        for basis in CONTRACT_REVENUE_BASES:
            for status in CONTRACT_STATUSES:
                for field in FIELDS:
                    with self.subTest(basis=basis, status=status, field=field):
                        self.persist(priced_payload(basis, cont_status=status, **{field: -0.01}), False)

    def test_legacy_nonapplicable_positive_values_remain_valid(self):
        for basis in CONTRACT_REVENUE_BASES:
            self.persist(priced_payload(basis, cont_no_of_days=22.5, cont_per_day_rate=1250.75, cont_no_of_kms=1200.5, cont_per_km_rate=3.75,
                                       cont_no_of_passengers=20, cont_per_passenger_rate_pm=50, cont_big_bus_count_gt_34=1, cont_big_bus_rate_pm=1000), True)

    def test_existing_integer_counts_enforced_by_database(self):
        for field in CONTRACT_COUNT_FIELDS:
            for value in (None, -1):
                with self.subTest(field=field, value=value):
                    self.persist(priced_payload("PER_DAY", **{field: value}), False)

    def test_org_customer_department_foreign_keys_unchanged(self):
        for changes in ({"cont_org_id_fk": 999}, {"cont_cust_id_fk": 11}, {"cont_dep_id_fk": 21}):
            self.persist(priced_payload("PER_DAY", **changes), False)


def _matrix_test(payload, accepted):
    def test(self):
        self.persist(payload, accepted)
    return test


for _index, _basis in enumerate((*CONTRACT_REVENUE_BASES, "PER_KM", "UNKNOWN", None), 1):
    setattr(ContractTask196DatabaseTest, f"test_RB_{_index:02}", _matrix_test(priced_payload(_basis), _index <= 5))

for _prefix, _basis, _quantity, _rate, _q, _r in (
    ("DAY", "PER_DAY", *FIELDS[:2], 22.50, 1250.75),
    ("KM", "PER_KILOMETER", *FIELDS[2:], 1200.50, 3.75),
):
    for _index, (_status, _value, _price, _pass) in enumerate((
        ("DRAFT", None, None, True), ("DRAFT", 0, 0, True), ("ACTIVE", _q, _r, True),
        ("ACTIVE", _q, 0, True), ("ACTIVE", None, _r, False), ("ACTIVE", 0, _r, False),
        ("ACTIVE", _q, None, False), ("DRAFT", -1, 100, False), ("ACTIVE", _q, -1, False),
    ), 1):
        setattr(ContractTask196DatabaseTest, f"test_{_prefix}_{_index:02}", _matrix_test(priced_payload(_basis, cont_status=_status, **{_quantity: _value, _rate: _price}), _pass))


def _status_case(basis, status):
    def test(self):
        self.persist(priced_payload(basis, cont_status=status), True)
        if basis in ("PER_DAY", "PER_KILOMETER"):
            quantity, rate = FIELDS[:2] if basis == "PER_DAY" else FIELDS[2:]
            invalid = ({quantity: None}, {quantity: 0}, {rate: None})
            zero_rates = {rate: 0}
        elif basis == "PER_PASSENGER":
            invalid = ({"cont_no_of_passengers": 0}, {"cont_per_passenger_rate_pm": None})
            zero_rates = {"cont_per_passenger_rate_pm": 0}
        else:
            invalid = ({"cont_big_bus_count_gt_34": 0}, {"cont_big_bus_rate_pm": None},
                       {"cont_medium_bus_count_17_34": 1, "cont_medium_bus_rate_pm": None},
                       {"cont_small_bus_count_lt_17": 1, "cont_small_bus_rate_pm": None})
            if basis == "PER_PASSENGER_AND_PER_BUS":
                invalid += ({"cont_no_of_passengers": 0}, {"cont_per_passenger_rate_pm": None})
            zero_rates = {"cont_big_bus_rate_pm": 0, "cont_per_passenger_rate_pm": 0}
        self.persist(priced_payload(basis, cont_status=status, **zero_rates), True)
        for changes in invalid:
            with self.subTest(changes=changes):
                self.persist(priced_payload(basis, cont_status=status, **changes), status == "DRAFT")
    return test


for _basis in CONTRACT_REVENUE_BASES:
    for _status in CONTRACT_STATUSES:
        setattr(ContractTask196DatabaseTest, f"test_status_{_basis}_{_status}", _status_case(_basis, _status))


class ContractTask196WorkflowTest(ContractNumericPostgresTest):
    """Reuse the accepted transport/storage harness; use real public FKs as well."""
    __unittest_skip__ = False  # This fixture requires its own loopback-only URL.

    @classmethod
    def setUpClass(cls):
        cls.database = DisposableContractDatabase(legacy=True, migrate=True)
        cls.addClassCleanup(cls.database.close)
        cls.engine = cls.database.engine
        cls.conn = cls.engine.connect()
        cls.addClassCleanup(cls.conn.close)
        transaction = cls.conn.begin()
        cls.addClassCleanup(transaction.rollback)
        cls.scoped_engine = TransactionEngine(cls.conn)

    def setUp(self):
        super().setUp()
        previous = dict(workflow_api.app.dependency_overrides)
        self.stack.callback(self.restore_overrides, workflow_api.app, previous)
        self.reviewer_auth = {
            "authenticated": True, "user": {"user_id": 2, "user_principal_name": REVIEWER, "user_org_id_fk": 77},
            "organization": {"org_id": 77},
        }
        workflow_api.app.dependency_overrides[workflow_api.get_authenticated_workflow_context] = lambda: self.reviewer_auth
        # Runtime router entries retain their original dependency provider;
        # authenticate a synthetic token as in the existing review test harness.
        self.stack.enter_context(patch.object(workflow_admin_api, "get_authenticated_user", return_value=self.reviewer_auth))
        for module in (workflow_admin_api, workflow_runtime_logic):
            self.stack.enter_context(patch.object(module, "db_engine", return_value=self.scoped_engine))

    def act(self, pending, reject=False):
        reviewed = self.consolidated_client.get(f"/api/v1/workflow-engine/instances/{pending['workflow_instance_id']}", headers={"Authorization": "Bearer task196-isolated-test"})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        review_payload = reviewed.json()["data"]["request_payload"]
        stored_payload = self.conn.execute(text("SELECT request_payload FROM workflow_instances WHERE workflow_instance_id_pk=:id"), {"id": pending["workflow_instance_id"]}).scalar_one()
        for field in ("cont_revenue_basis", "total_contract_value", *FIELDS):
            self.assertEqual(field in review_payload, field in stored_payload)
            self.assertEqual(review_payload.get(field), stored_payload.get(field))
        response = self.consolidated_client.post(
            f"/api/v1/workflow/contracts-management/{'reject' if reject else 'approve'}",
            json={"workflow_instance_id": pending["workflow_instance_id"],
                  "workflow_instance_step_id": pending["workflow_instance_step_id"],
                  "cont_org_id_fk": 77, "comments": "Task196 isolated verification"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(result.get("workflow_status"), "REJECTED" if reject else "EXECUTED", result)
        instance = self.conn.execute(text("SELECT workflow_status,request_payload FROM workflow_instances WHERE workflow_instance_id_pk=:id"), {"id": pending["workflow_instance_id"]}).mappings().one()
        self.assertEqual(instance["workflow_status"], "REJECTED" if reject else "EXECUTED")
        step_status = self.conn.execute(text("SELECT step_status FROM workflow_instance_steps WHERE workflow_instance_step_id_pk=:id"), {"id": pending["workflow_instance_step_id"]}).scalar_one()
        self.assertEqual(step_status, "REJECTED" if reject else "APPROVED")
        if reject:
            EXECUTION_EVIDENCE.append({"test": self.id(), "isolated": True, "workflow_instance_id": pending["workflow_instance_id"], "workflow_status": "REJECTED", "step_status": step_status})
            return result
        self.assertTrue(result["business_operation_executed"])
        contract_id = result["execution_result"]["cont_id_pk"]
        row = self.row(contract_id)
        self.assertEqual(row["cont_approval_status"], "APPROVED")
        EXECUTION_EVIDENCE.append({"test": self.id(), "isolated": True, "workflow_instance_id": pending["workflow_instance_id"],
                                   "workflow_status": instance["workflow_status"], "step_status": step_status, "approver_review_verified": True, "row": {key: row[key] for key in ("cont_id_pk", "cont_revenue_basis", "cont_approval_status", "total_contract_value", *FIELDS)}})
        return contract_id

    def commercial_payload(self, basis, **changes):
        return priced_payload(basis, cont_contract_number=f"T196-{uuid4().hex[:20]}", **changes)

    def assert_round_trip(self, contract_id, values):
        stored = super().assert_round_trip(contract_id, values)
        exact = self.client.get(f"/api/v1/contracts/{contract_id}").json()["data"]
        listed = next(row for row in self.client.get("/api/v1/contracts").json()["data"] if row["cont_id_pk"] == contract_id)
        for row in (exact, listed):
            for field in ("cont_revenue_basis", "cont_status", "cont_approval_status", "cont_no_of_buses", "total_contract_value"):
                self.assertEqual(row[field], stored[field], field)
        return stored

    def test_preworkflow_invalid_requests_do_not_allocate_instances_or_contract_ids(self):
        before = self.conn.exec_driver_sql("SELECT (SELECT count(*) FROM workflow_instances),(SELECT count(*) FROM contracts_management_workflow_requests),(SELECT count(*) FROM contracts_management),(SELECT last_value FROM contracts_management_cont_id_pk_seq)").one()
        for multipart in (False, True):
            for changes in ({"cont_revenue_basis": "PER_KM"}, {"cont_no_of_days": None}, {"cont_no_of_passengers": None}, {"cont_per_day_rate": -1}):
                payload = self.commercial_payload("PER_DAY", **changes)
                if multipart:
                    response = self.client.post("/api/v1/contracts/create-with-document", files={"payload": (None, json.dumps(payload)), "file": ("test.pdf", b"do not stage", "application/pdf")})
                else:
                    response = self.client.post("/api/v1/contracts/create", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        after = self.conn.exec_driver_sql("SELECT (SELECT count(*) FROM workflow_instances),(SELECT count(*) FROM contracts_management_workflow_requests),(SELECT count(*) FROM contracts_management),(SELECT last_value FROM contracts_management_cont_id_pk_seq)").one()
        self.assertEqual(after, before)

    def test_existing_incomplete_draft_cannot_transition_to_active_with_omitted_pricing(self):
        for basis in ("PER_DAY", "PER_KILOMETER"):
            payload = self.commercial_payload(basis, cont_status="DRAFT", **dict.fromkeys(FIELDS))
            pending, _ = self.submit(payload)
            contract_id = self.act(pending)
            before = self.row(contract_id)
            candidate = {k: v for k, v in payload.items() if k not in FIELDS}
            candidate.update(cont_id_pk=contract_id, cont_status="ACTIVE")
            for multipart in (False, True):
                count = self.conn.exec_driver_sql("SELECT count(*) FROM workflow_instances").scalar_one()
                if multipart:
                    response = self.client.post("/api/v1/contracts/update-with-document", files={"payload": (None, json.dumps(candidate))})
                else:
                    response = self.client.post("/api/v1/contracts/update", json=candidate)
                self.assertGreaterEqual(response.status_code, 400, response.text)
                self.assertEqual(self.conn.exec_driver_sql("SELECT count(*) FROM workflow_instances").scalar_one(), count)
                self.assertEqual(self.row(contract_id), before)

    def test_omitted_total_preserves_latest_value_at_approval(self):
        payload = self.commercial_payload("PER_DAY", total_contract_value=125000)
        pending, _ = self.submit(payload)
        contract_id = self.act(pending)
        payload.pop("total_contract_value")
        pending, _ = self.submit({**payload, "cont_id_pk": contract_id}, "update")
        self.conn.execute(text("UPDATE contracts_management SET total_contract_value=130000 WHERE cont_id_pk=:id"), {"id": contract_id})
        self.act(pending)
        self.assertEqual(self.row(contract_id)["total_contract_value"], 130000)


def _approved_case(basis, multipart):
    def test(self):
        payload = self.commercial_payload(basis, total_contract_value=125000)
        pending, proposed = self.submit(payload, multipart=multipart)
        review = public_instance({"workflow_code": "CONTRACTS_MANAGEMENT", "workflow_action": "CREATE", "request_payload": proposed})["request_payload"]
        for key in ("cont_revenue_basis", *FIELDS):
            self.assertEqual(review[key], payload[key])
        contract_id = self.act(pending)
        original = self.assert_round_trip(contract_id, payload)
        self.assertEqual(original["cont_revenue_basis"], basis)
        # Legacy UPDATE has none of the four new fields and no total.
        candidate = {k: v for k, v in payload.items() if k not in (*FIELDS, "total_contract_value")}
        candidate["cont_id_pk"] = contract_id
        pending, _ = self.submit(candidate, "update", multipart=multipart)
        self.assertEqual(self.row(contract_id), original)
        self.act(pending)
        self.assert_round_trip(contract_id, payload)
        self.assertEqual(self.row(contract_id)["total_contract_value"], 125000)
        # Applicable zero rate is valid. Unrelated NULL remains distinct from 0.
        values = dict.fromkeys(FIELDS, 0)
        if basis == "PER_DAY":
            values.update(cont_no_of_days=23.75, cont_per_day_rate=0, cont_no_of_kms=None)
        elif basis == "PER_KILOMETER":
            values.update(cont_no_of_days=None, cont_no_of_kms=1300.25, cont_per_km_rate=0)
        else:
            values["cont_no_of_days"] = None
        pending, _ = self.submit({**candidate, **values, "total_contract_value": None}, "update", multipart=multipart)
        self.act(pending)
        stored = self.assert_round_trip(contract_id, values)
        self.assertEqual(stored["total_contract_value"], 125000)
        self.assertEqual(stored["cont_revenue_basis"], basis)
        # Invalid references and duplicate numbers do not change this record.
        for field, value in (("cont_cust_id_fk", 11), ("cont_dep_id_fk", 21)):
            self.assertIn("error", update.update_contract({**candidate, field: value}))
        self.assertIn("Contract already exists", create.create_contract(payload)["error"])
        self.assertEqual(self.row(contract_id), stored)
        response = self.client.post("/api/v1/contracts/delete", json={"cont_id_pk": contract_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.row(contract_id))
    return test


def _rejection_case(basis, multipart):
    def test(self):
        payload = self.commercial_payload(basis)
        pending, _ = self.submit(payload, multipart=multipart)
        self.act(pending, reject=True)
        self.assertIsNone(self.conn.execute(text("SELECT cont_id_pk FROM contracts_management WHERE cont_contract_number=:n"), {"n": payload["cont_contract_number"]}).scalar_one_or_none())
        payload = self.commercial_payload(basis)
        pending, _ = self.submit(payload, multipart=multipart)
        contract_id = self.act(pending)
        before = self.row(contract_id)
        pending, _ = self.submit({**payload, "cont_id_pk": contract_id, "total_contract_value": 0}, "update", multipart=multipart)
        self.act(pending, reject=True)
        self.assertEqual(self.row(contract_id), before)
    return test


TOTAL_CASES = (("omitted", 125000, "OMITTED", 125000), ("null", 125000, None, 125000),
               ("zero", 125000, 0, 0), ("numeric", 125000, 150000, 150000),
               ("historical_null", None, None, None), ("null_to_numeric", None, 50000, 50000))


def _total_case(basis, multipart, existing, incoming, expected):
    def test(self):
        payload = self.commercial_payload(basis, total_contract_value=existing)
        # Historical equivalent row, seeded directly before workflow UPDATE.
        contract_id = insert_row(self.conn, payload)
        payload["cont_id_pk"] = contract_id
        if incoming == "OMITTED":
            payload.pop("total_contract_value")
        else:
            payload["total_contract_value"] = incoming
        before = self.row(contract_id)
        pending, _ = self.submit(payload, "update", multipart=multipart)
        self.assertEqual(self.row(contract_id), before)
        self.act(pending)
        self.assertEqual(self.row(contract_id)["total_contract_value"], expected)
        exact = self.client.get(f"/api/v1/contracts/{contract_id}").json()["data"]
        listed = next(r for r in self.client.get("/api/v1/contracts").json()["data"] if r["cont_id_pk"] == contract_id)
        self.assertEqual(exact["total_contract_value"], expected)
        self.assertEqual(listed["total_contract_value"], expected)
    return test


def _documents_case(basis):
    def test(self):
        # The accepted document regression already verifies staging, signed
        # download, replacement, rejected cleanup and current-pointer retention.
        original = self.payload
        self.payload = lambda **values: original(cont_revenue_basis=basis, cont_status="DRAFT", **values)
        self.test_document_create_replacement_and_rejected_update()
    return test


for _basis in CONTRACT_REVENUE_BASES:
    for _multipart in (False, True):
        _suffix = f"{_basis}_{'multipart' if _multipart else 'json'}"
        setattr(ContractTask196WorkflowTest, f"test_approved_create_update_get_delete_{_suffix}", _approved_case(_basis, _multipart))
        if _basis in ("PER_DAY", "PER_KILOMETER"):
            setattr(ContractTask196WorkflowTest, f"test_rejected_create_update_{_suffix}", _rejection_case(_basis, _multipart))
            for _label, _existing, _incoming, _expected in TOTAL_CASES:
                setattr(ContractTask196WorkflowTest, f"test_total_{_label}_{_suffix}", _total_case(_basis, _multipart, _existing, _incoming, _expected))
    if _basis in ("PER_DAY", "PER_KILOMETER"):
        setattr(ContractTask196WorkflowTest, f"test_documents_{_basis}", _documents_case(_basis))


def load_tests(loader, tests, pattern):
    # Run the original 13 tests in their own retained module, exactly once.
    own = [ContractTask196WorkflowTest(name) for name in sorted(ContractTask196WorkflowTest.__dict__) if name.startswith("test_")]
    return unittest.TestSuite([loader.loadTestsFromTestCase(ContractTask196DatabaseTest), *own])


if __name__ == "__main__":
    unittest.main()
