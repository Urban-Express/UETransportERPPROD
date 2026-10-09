"""Focused mocked settlement tests. No database, Firebase or network access."""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
import logging
import re
import unittest
from unittest.mock import patch

from pydantic import ValidationError


def external_guards():
    guards = ExitStack()
    guards.enter_context(patch("dotenv.load_dotenv", return_value=False))
    for target in (
        "sqlalchemy.create_engine", "sqlalchemy.engine.Engine.connect", "psycopg2.connect",
        "socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
        "firebase_admin.initialize_app",
    ):
        guards.enter_context(patch(target, side_effect=AssertionError("External access forbidden: " + target)))
    return guards


with external_guards():
    # Reuse the existing offline ASGI harness and recording transaction doubles.
    from app_backend.services.service_08_financial_management.tests import test_ar_collection_status_update as fixtures
    from app_backend.services.service_08_financial_management.logic import accounts_receivables_mark_received as logic

api = fixtures.api
AUTH_CONTEXT = fixtures.AUTH_CONTEXT
REQUEST = {"ar_id_pk": 38, "ar_org_id_fk": 17, "ar_expected_revision": 1}
PATH = "/api/v1/accounts-receivables/mark-received"
RESULT_FIELDS = (
    "ar_id_pk", "ar_invoice_number", "ar_invoice_amount", "ar_received_amount",
    "ar_balance_amount", "ar_collection_status", "ar_revision",
)


def setUpModule():
    global guards
    guards = external_guards()


def tearDownModule():
    guards.close()


class Connection(fixtures.Connection):
    """Model PostgreSQL's generated balance, with injected write/RETURNING failures."""
    def __init__(self):
        super().__init__()
        for field in ("ar_invoice_amount", "ar_received_amount", "ar_balance_amount", "ar_tax_amount"):
            self.invoice[field] = Decimal(self.invoice[field])
        self.returned_changes = {}
        self.revision_race = False

    def execute(self, statement, params):
        sql = " ".join(str(statement).lower().split())
        if sql.startswith("select ar_id_pk,"):
            result = super().execute(statement, params)
            for field in (*RESULT_FIELDS, "ar_org_id_fk"):
                assert field in sql.split(" from ", 1)[0], field
            if not result.rows:
                return result
            return fixtures.Result([{
                **{field: self.invoice[field] for field in (*RESULT_FIELDS, "ar_org_id_fk")},
                "ar_revision": self.invoice["ar_revision"] or 0,
            }])
        if not sql.startswith("update accounts_receivables "):
            return super().execute(statement, params)
        assert self.in_transaction(), "Write requires a transaction"
        self.statements.append((sql, deepcopy(params)))
        setters = sql.split(" set ", 1)[1].split(" where ", 1)[0]
        assert set(re.findall(r"(\w+)\s*=", setters)) == {
            "ar_collection_status", "ar_received_amount", "updated_by", "ar_revision",
        }, "Only settlement and audit/revision fields may be assigned"
        assert "ar_collection_status = 'received'" in setters
        assert "ar_received_amount = ar_invoice_amount" in setters
        assert "updated_by = :principal" in setters
        assert "ar_revision = coalesce(ar_revision, 0) + 1" in setters
        assert "ar_id_pk = :ar_id" in sql and "ar_org_id_fk = :org_id" in sql
        assert "coalesce(ar_revision, 0) = :revision" in sql
        for field in RESULT_FIELDS:
            assert field in sql.split(" returning ", 1)[1]
        assert set(params) == {"ar_id", "org_id", "revision", "principal"}
        self.events.append("update")
        if (self.revision_race or self.invoice["ar_id_pk"] != params["ar_id"]
                or self.invoice["ar_org_id_fk"] != params["org_id"]
                or (self.invoice["ar_revision"] or 0) != params["revision"]):
            return fixtures.Result()
        self.invoice.update(
            ar_collection_status="RECEIVED", ar_received_amount=self.invoice["ar_invoice_amount"],
            updated_by=params["principal"], ar_revision=params["revision"] + 1,
            updated_at="mock_trigger_time",
        )
        self.invoice["ar_balance_amount"] = self.invoice["ar_invoice_amount"] - self.invoice["ar_received_amount"]
        if self.fail_after_update:
            raise RuntimeError("Mock SQL write failure: private database detail")
        if self.fail_update_returning:
            return fixtures.Result()
        # Simulate unexpected persisted values returned by the database.
        self.invoice.update(self.returned_changes)
        return fixtures.Result([{field: self.invoice[field] for field in RESULT_FIELDS}])


class MockedMarkReceivedCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.connection = Connection()
        self.engine = fixtures.Engine(self.connection)
        self.engine_factory = self.stack.enter_context(patch.object(logic, "db_engine", return_value=self.engine))
        for module, names in (
            (api, ("update_accounts_receivable", "update_ar_collection_status")),
            (fixtures.logic, ("update_accounts_receivable", "update_ar_collection_status", "is_accounts_receivables_workflow_approved")),
            (fixtures.create_logic, ("stage_ar_invoice_document_for_workflow",)),
            (fixtures.documents, ("generate_ar_invoice_document",)),
        ):
            for name in names:
                forbidden = self.stack.enter_context(patch.object(module, name, side_effect=AssertionError("Forbidden settlement dependency: " + name)))
                self.addCleanup(forbidden.assert_not_called)

    def bound(self, **changes):
        return api.bind_ar_payload({**REQUEST, **changes}, AUTH_CONTEXT, set_updated_by=True)

    def settle(self, **changes):
        return logic.mark_ar_invoice_received(self.bound(**changes))

    def pending(self, *, configured=True, alias="ar_id_pk", **changes):
        row = {
            "configured": configured, "workflow_code": "ACCOUNTS_RECEIVABLE", "workflow_action": "UPDATE",
            "workflow_status": "PENDING_APPROVAL", "organization_id": 17,
            "request_payload": {alias: 38, "ar_org_id_fk": 17}, "workflow_instance_id_pk": 100,
        }
        row.update(changes)
        self.connection.workflows.append(row)

    def assert_rejected(self, code, *, status=409, **changes):
        before = deepcopy(self.connection.invoice)
        updates = self.connection.events.count("update")
        result = self.settle(**changes)
        self.assertEqual(result["error_code"], code, result)
        self.assertEqual(result["status_code"], status)
        self.assertEqual(self.connection.invoice, before)
        self.assertEqual(self.connection.events.count("update"), updates)
        return result

    def assert_settled(self, **changes):
        before = deepcopy(self.connection.invoice)
        lines = deepcopy(self.connection.lines)
        workflows = deepcopy(self.connection.workflows)
        result = self.settle(**changes)
        self.assertNotIn("error", result, result)
        self.assertEqual(result["message"], "AR invoice marked as fully received.")
        self.assertEqual(result["ar_collection_status"], "RECEIVED")
        self.assertEqual(result["ar_invoice_amount"], str(before["ar_invoice_amount"]))
        self.assertEqual(result["ar_received_amount"], str(before["ar_invoice_amount"]))
        self.assertEqual(result["ar_balance_amount"], "0.00")
        self.assertEqual(result["ar_revision"], (before["ar_revision"] or 0) + 1)
        self.assertFalse(result["workflow_required"])
        self.assertTrue(result["business_operation_executed"])
        self.assertFalse(result["no_change"])
        allowed_changes = {"ar_received_amount", "ar_balance_amount", "ar_collection_status", "ar_revision", "updated_by", "updated_at"}
        self.assertEqual(
            {key: value for key, value in self.connection.invoice.items() if key not in allowed_changes},
            {key: value for key, value in before.items() if key not in allowed_changes},
        )
        self.assertEqual(self.connection.invoice["updated_by"], AUTH_CONTEXT["user"]["user_principal_name"])
        self.assertEqual(self.connection.lines, lines)
        self.assertEqual(self.connection.workflows, workflows)
        self.assertEqual(self.connection.events.count("update"), 1)
        return result


class MarkReceivedRequestTests(unittest.TestCase):
    def test_exact_three_fields_and_strict_integer_bounds(self):
        model = api.AccountsReceivableMarkReceivedPayload(**REQUEST)
        self.assertEqual(model.model_dump(), REQUEST)
        for field in REQUEST:
            invalid = (-1, True, False, "1", 1.0, None)
            if field != "ar_expected_revision":
                invalid += (0,)
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    api.AccountsReceivableMarkReceivedPayload(**{**REQUEST, field: value})
        self.assertEqual(api.AccountsReceivableMarkReceivedPayload(**{**REQUEST, "ar_expected_revision": 0}).ar_expected_revision, 0)

    def test_every_field_is_required(self):
        for field in REQUEST:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                api.AccountsReceivableMarkReceivedPayload(**{key: value for key, value in REQUEST.items() if key != field})

    def test_financial_status_workflow_document_identity_and_unknown_fields_forbidden(self):
        fields = {
            "ar_invoice_amount": "1050.00", "ar_received_amount": "1050.00", "ar_balance_amount": "0.00",
            "ar_tax_amount": "50.00", "ar_collection_status": "RECEIVED", "lines": [],
            "ar_approval_status": "APPROVED", "ar_approved_by": "someone", "ar_approved_at": "2026-10-09",
            "ar_invoice_file_path": "some.pdf", "ar_revision": 5, "updated_by": "someone", "created_by": "someone",
            "updated_at": "2026-10-09", "user_principal_name": "someone", "authenticated_org_id": 17,
            "authenticated_user_id": 7, "authenticated_user_principal_name": "someone",
            "permissions": ["FIN_UPDATE"], "financial_management_module": True, "licenses": [],
            "_accounts_receivables_workflow_approved": True, "workflow_required": False, "unknown": None,
        }
        for field, value in fields.items():
            with self.subTest(field=field), self.assertRaises(ValidationError):
                api.AccountsReceivableMarkReceivedPayload(**REQUEST, **{field: value})


class MarkReceivedLogicTests(MockedMarkReceivedCase):
    def test_outstanding_without_payments(self):
        self.connection.invoice.update(ar_received_amount=Decimal("0.00"), ar_balance_amount=Decimal("1050.00"))
        self.assert_settled()

    def test_partially_received(self):
        self.connection.invoice["ar_collection_status"] = "PARTIALLY_RECEIVED"
        self.assert_settled()

    def test_fully_paid_outstanding_still_updates_status_and_revision(self):
        self.connection.invoice.update(ar_received_amount=Decimal("1050.00"), ar_balance_amount=Decimal("0.00"))
        self.assert_settled()

    def test_received_fully_paid_is_noop_even_with_pending_workflow(self):
        self.pending()
        self.connection.invoice.update(ar_collection_status="RECEIVED", ar_received_amount=Decimal("1050.00"), ar_balance_amount=Decimal("0.00"))
        before = deepcopy(self.connection.invoice)
        result = self.settle()
        self.assertNotIn("error", result)
        self.assertTrue(result["no_change"])
        self.assertFalse(result["workflow_required"])
        self.assertFalse(result["business_operation_executed"])
        self.assertEqual(result["ar_revision"], 1)
        self.assertEqual(result["ar_balance_amount"], "0.00")
        self.assertEqual(result["ar_received_amount"], "1050.00")
        self.assertEqual(self.connection.invoice, before)
        self.assertNotIn("update", self.connection.events)

    def test_received_but_underpaid_is_rejected_without_repair(self):
        self.connection.invoice["ar_collection_status"] = "RECEIVED"
        self.assert_rejected("AR_PAYMENT_DATA_INCONSISTENT")

    def test_received_with_inconsistent_balance_is_rejected(self):
        self.connection.invoice.update(ar_collection_status="RECEIVED", ar_received_amount=Decimal("1050.00"))
        self.assert_rejected("AR_PAYMENT_DATA_INCONSISTENT")

    def test_overpayment_is_rejected(self):
        self.connection.invoice.update(ar_received_amount=Decimal("1050.01"), ar_balance_amount=Decimal("-0.01"))
        self.assert_rejected("AR_PAYMENT_DATA_INCONSISTENT")

    def test_missing_invalid_negative_and_nonfinite_amounts_are_rejected(self):
        original = deepcopy(self.connection.invoice)
        for field in ("ar_invoice_amount", "ar_received_amount", "ar_balance_amount"):
            for value in (None, "", "invalid", True, False, Decimal("-0.01"), Decimal("NaN"),
                          Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
                          Decimal("0.001"), Decimal("10000000000000000.00")):
                with self.subTest(field=field, value=value):
                    self.connection.invoice = {**original, field: value}
                    # Signaling NaN intentionally cannot be equality-compared.
                    result = self.settle()
                    self.assertEqual(result["error_code"], "AR_PAYMENT_DATA_INCONSISTENT", result)
                    self.assertEqual(result["status_code"], 409)
                    self.assertNotIn("update", self.connection.events)

    def test_stored_balance_must_equal_invoice_less_received(self):
        self.connection.invoice["ar_balance_amount"] = Decimal("1029.99")
        self.assert_rejected("AR_PAYMENT_DATA_INCONSISTENT")

    def test_zero_invoice_can_be_settled(self):
        self.connection.invoice.update(ar_invoice_amount=Decimal("0.00"), ar_received_amount=Decimal("0.00"), ar_balance_amount=Decimal("0.00"))
        self.assert_settled()

    def test_decimal_precision_is_preserved_at_numeric_limit(self):
        self.connection.invoice.update(ar_invoice_amount=Decimal("9999999999999999.99"),
                                       ar_received_amount=Decimal("9999999999999999.98"), ar_balance_amount=Decimal("0.01"))
        self.assert_settled()

    def test_stale_revision_rejected(self):
        self.assert_rejected("AR_STALE_INVOICE", ar_expected_revision=0)

    def test_stale_revision_is_rejected_even_for_noop(self):
        self.connection.invoice.update(ar_collection_status="RECEIVED", ar_received_amount=Decimal("1050.00"), ar_balance_amount=Decimal("0.00"))
        self.assert_rejected("AR_STALE_INVOICE", ar_expected_revision=0)

    def test_null_revision_is_zero_and_increments_once(self):
        self.connection.invoice["ar_revision"] = None
        self.assert_settled(ar_expected_revision=0)

    def test_null_revision_noop_does_not_write_zero(self):
        self.connection.invoice.update(ar_revision=None, ar_collection_status="RECEIVED", ar_received_amount=Decimal("1050.00"), ar_balance_amount=Decimal("0.00"))
        result = self.settle(ar_expected_revision=0)
        self.assertEqual(result["ar_revision"], 0)
        self.assertTrue(result["no_change"])
        self.assertIsNone(self.connection.invoice["ar_revision"])
        self.assertNotIn("update", self.connection.events)

    def test_repeated_request_needs_current_revision_and_then_is_noop(self):
        self.assert_settled()
        self.assert_rejected("AR_STALE_INVOICE")
        result = self.settle(ar_expected_revision=2)
        self.assertTrue(result["no_change"])
        self.assertEqual(self.connection.events.count("update"), 1)

    def test_configured_and_legacy_pending_update_both_id_aliases(self):
        for configured in (True, False):
            for alias in ("ar_id", "ar_id_pk"):
                with self.subTest(configured=configured, alias=alias):
                    self.connection.workflows = []
                    self.pending(configured=configured, alias=alias)
                    self.assert_rejected("AR_PENDING_UPDATE_WORKFLOW")

    def test_legacy_pending_authenticated_org_alias(self):
        self.pending(configured=False, request_payload={"ar_id": 38, "authenticated_org_id": 17})
        self.assert_rejected("AR_PENDING_UPDATE_WORKFLOW")

    def test_pending_update_blocks_status_correction_on_fully_paid_invoice(self):
        self.connection.invoice.update(ar_received_amount=Decimal("1050.00"), ar_balance_amount=Decimal("0.00"))
        self.pending()
        self.assert_rejected("AR_PENDING_UPDATE_WORKFLOW")

    def test_unrelated_workflows_do_not_block_settlement(self):
        for changes in ({"workflow_action": "CREATE"}, {"workflow_action": "DELETE"},
                        {"workflow_status": "EXECUTED"}, {"workflow_status": "REJECTED"},
                        {"workflow_code": "ACCOUNTS_PAYABLE"}, {"organization_id": 18},
                        {"request_payload": {"ar_id_pk": 39, "ar_org_id_fk": 17}}):
            self.pending(**changes)
        self.pending(configured=False, request_payload={"ar_id_pk": 38, "ar_org_id_fk": 18})
        self.assert_settled()

    def test_invalid_collection_status(self):
        for status in (None, "PAID", "received", "", " RECEIVED "):
            with self.subTest(status=status):
                self.connection.invoice["ar_collection_status"] = status
                self.assert_rejected("AR_INVALID_SETTLEMENT_STATUS")

    def test_existing_approval_statuses_remain_unrestricted_and_unchanged(self):
        original = deepcopy(self.connection.invoice)
        for status in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "CANCELLED"):
            with self.subTest(status=status):
                self.connection.invoice = {**original, "ar_approval_status": status}
                self.connection.events = []
                self.assert_settled()

    def test_permission_missing_or_wf_submit_only_is_denied(self):
        for permissions in (set(), {"WF_SUBMIT"}):
            with self.subTest(permissions=permissions):
                self.connection.permissions = permissions
                self.assert_rejected("AR_UPDATE_PERMISSION_REQUIRED", status=403)
        self.assertNotIn("license_lookup", self.connection.events)

    def test_fin_update_without_wf_submit_is_sufficient(self):
        self.assertEqual(self.connection.permissions, {"FIN_UPDATE"})
        self.assert_settled()

    def test_inactive_deleted_cross_org_users_and_inactive_roles_are_denied(self):
        for attribute, value in (("user_active", False), ("user_deleted", True), ("role_active", False),
                                 ("organization_exists", False), ("user_org_id", 18)):
            with self.subTest(attribute=attribute):
                original = getattr(self.connection, attribute)
                setattr(self.connection, attribute, value)
                self.assert_rejected("AR_UPDATE_PERMISSION_REQUIRED", status=403)
                setattr(self.connection, attribute, original)

    def test_missing_disabled_null_and_conflicting_licenses(self):
        cases = (([], "AR_MODULE_LICENSE_REQUIRED"), ([False], "AR_MODULE_LICENSE_REQUIRED"),
                 ([None], "AR_MODULE_LICENSE_REQUIRED"), ([False, None], "AR_MODULE_LICENSE_REQUIRED"),
                 ([1], "AR_MODULE_LICENSE_REQUIRED"), (["true"], "AR_MODULE_LICENSE_REQUIRED"),
                 ([True, False], "AR_MODULE_LICENSE_AMBIGUOUS"), ([False, True], "AR_MODULE_LICENSE_AMBIGUOUS"),
                 ([True, None], "AR_MODULE_LICENSE_AMBIGUOUS"))
        for flags, code in cases:
            with self.subTest(flags=flags):
                self.connection.license_records = [{"org_id_fk": 17, "financial_management_module": flag} for flag in flags]
                self.assert_rejected(code, status=403)
        self.assertNotIn("submission_lock", self.connection.events)

    def test_all_enabled_license_records_allow_settlement(self):
        self.connection.license_records.append({"org_id_fk": 17, "financial_management_module": True})
        self.assertIs(logic._ar_collection_status_license_error, fixtures.logic._ar_collection_status_license_error)
        self.assert_settled()

    def test_another_organization_license_does_not_grant_access(self):
        self.connection.license_records = [{"org_id_fk": 18, "financial_management_module": True}]
        self.assert_rejected("AR_MODULE_LICENSE_REQUIRED", status=403)

    def test_cross_organization_invoice_is_not_found(self):
        self.connection.invoice["ar_org_id_fk"] = 18
        self.assert_rejected("AR_INVOICE_NOT_FOUND", status=404)

    def test_missing_invoice_is_not_found(self):
        self.connection.invoice = None
        self.assert_rejected("AR_INVOICE_NOT_FOUND", status=404)

    def test_logic_requires_authenticated_identity(self):
        for field in ("authenticated_org_id", "authenticated_user_id", "authenticated_user_principal_name"):
            for value in (None, "", True, 0):
                with self.subTest(field=field, value=value):
                    result = logic.mark_ar_invoice_received({**self.bound(), field: value})
                    self.assertEqual(result["error_code"], "AR_AUTHENTICATION_REQUIRED")
                    self.assertEqual(result["status_code"], 401)
        self.engine_factory.assert_not_called()

    def test_logic_rejects_org_mismatch_without_database_access(self):
        result = logic.mark_ar_invoice_received({**self.bound(), "ar_org_id_fk": 18})
        self.assertEqual(result["error_code"], "AR_ORGANIZATION_MISMATCH")
        self.engine_factory.assert_not_called()

    def test_logic_rejects_non_dict_extra_fields_and_invalid_identifiers(self):
        payloads = [None, [], {**self.bound(), "ar_received_amount": "1000.00"}]
        payloads += [{**self.bound(), field: value} for field, value in (
            ("ar_id_pk", True), ("ar_id_pk", 0), ("ar_id_pk", "38"),
            ("ar_expected_revision", -1), ("ar_expected_revision", True), ("ar_expected_revision", None),
        )]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(logic.mark_ar_invoice_received(payload)["status_code"], 422)
        self.engine_factory.assert_not_called()

    def test_audit_value_uses_authenticated_principal(self):
        result = logic.mark_ar_invoice_received({**self.bound(), "updated_by": "untrusted", "user_principal_name": "untrusted"})
        self.assertNotIn("error", result)
        self.assertEqual(self.connection.invoice["updated_by"], AUTH_CONTEXT["user"]["user_principal_name"])


class MarkReceivedTransactionTests(MockedMarkReceivedCase):
    def test_single_transaction_and_workflow_before_invoice_lock_order(self):
        self.assert_settled()
        self.assertEqual(self.connection.events, [
            "begin", "permission", "license_lookup", "submission_lock", "configured_pending_lock",
            "legacy_pending_lock", "invoice_lock", "update", "commit",
        ])
        self.assertTrue(self.engine.disposed)

    def test_supplied_active_transaction_uses_savepoint_without_outer_commit(self):
        self.connection.depth = 1
        result = logic.mark_ar_invoice_received(self.bound(), conn=self.connection)
        self.assertNotIn("error", result)
        self.assertEqual(self.connection.depth, 1)
        self.assertEqual(self.connection.events[0], "savepoint")
        self.assertEqual(self.connection.events[-1], "release_savepoint")
        self.assertNotIn("commit", self.connection.events)
        self.engine_factory.assert_not_called()

    def test_supplied_inactive_connection_gets_owned_transaction(self):
        result = logic.mark_ar_invoice_received(self.bound(), conn=self.connection)
        self.assertNotIn("error", result)
        self.assertEqual(self.connection.events[-1], "commit")
        self.engine_factory.assert_not_called()

    def test_database_lookup_write_and_commit_failures_roll_back(self):
        for failure in ("fail_license_lookup", "fail_lookup", "fail_after_update", "fail_commit"):
            with self.subTest(failure=failure):
                before = deepcopy(self.connection.invoice)
                setattr(self.connection, failure, True)
                with self.assertLogs(logic.__name__, logging.ERROR):
                    result = self.settle()
                self.assertEqual(result["error_code"], "AR_MARK_RECEIVED_FAILED", result)
                self.assertEqual(result["status_code"], 500)
                self.assertEqual(self.connection.invoice, before)
                self.assertEqual(self.connection.events[-1], "rollback")
                self.assertTrue(self.engine.disposed)
                setattr(self.connection, failure, False)

    def test_unexpected_returned_values_roll_back_after_write(self):
        cases = (
            {"ar_collection_status": "OUTSTANDING"}, {"ar_received_amount": Decimal("1049.99")},
            {"ar_balance_amount": Decimal("0.01")}, {"ar_received_amount": None},
            {"ar_invoice_amount": Decimal("NaN")}, {"ar_revision": 3},
            {"ar_invoice_amount": Decimal("2000.00"), "ar_received_amount": Decimal("2000.00")},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                before = deepcopy(self.connection.invoice)
                self.connection.returned_changes = changes
                with self.assertLogs(logic.__name__, logging.ERROR):
                    result = self.settle()
                self.assertEqual(result["error_code"], "AR_MARK_RECEIVED_FAILED", result)
                self.assertEqual(self.connection.invoice, before)
                self.assertEqual(self.connection.events[-1], "rollback")

    def test_revision_predicate_failure_or_missing_returning_rolls_back(self):
        for failure in ("revision_race", "fail_update_returning"):
            with self.subTest(failure=failure):
                before = deepcopy(self.connection.invoice)
                setattr(self.connection, failure, True)
                result = self.settle()
                self.assertEqual(result["error_code"], "AR_STALE_INVOICE")
                self.assertEqual(self.connection.invoice, before)
                self.assertEqual(self.connection.events[-1], "rollback")
                setattr(self.connection, failure, False)

    def test_savepoint_rolls_back_only_operation_preserving_caller_work(self):
        self.connection.depth = 1
        self.connection.invoice["ar_notes"] = "Uncommitted caller work"
        self.connection.fail_after_update = True
        before = deepcopy(self.connection.invoice)
        with self.assertLogs(logic.__name__, logging.ERROR):
            result = logic.mark_ar_invoice_received(self.bound(), conn=self.connection)
        self.assertEqual(result["status_code"], 500)
        self.assertEqual(self.connection.invoice, before)
        self.assertEqual(self.connection.depth, 1)
        self.assertEqual(self.connection.events[-1], "rollback_savepoint")
        self.assertNotIn("rollback", self.connection.events)
        self.assertNotIn("commit", self.connection.events)

    def test_validation_failure_also_rolls_back_savepoint(self):
        self.connection.depth = 1
        before = deepcopy(self.connection.invoice)
        result = logic.mark_ar_invoice_received(self.bound(ar_expected_revision=0), conn=self.connection)
        self.assertEqual(result["error_code"], "AR_STALE_INVOICE")
        self.assertEqual(self.connection.invoice, before)
        self.assertEqual(self.connection.depth, 1)
        self.assertEqual(self.connection.events[-1], "rollback_savepoint")


class MarkReceivedApiTests(MockedMarkReceivedCase):
    def setUp(self):
        super().setUp()
        for app in (api.app, fixtures.consolidated_app):
            self.stack.enter_context(patch.dict(app.dependency_overrides, {api.get_authenticated_context: lambda: deepcopy(AUTH_CONTEXT)}))

    def post(self, request=None, *, app=api.app):
        return fixtures.post_json(app, PATH, REQUEST if request is None else request)

    def test_success_envelope_exact_decimal_strings(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"success": True, "data": {
            "message": "AR invoice marked as fully received.", "ar_id_pk": 38,
            "ar_invoice_number": "TEST-AR-38", "ar_invoice_amount": "1050.00",
            "ar_received_amount": "1050.00", "ar_balance_amount": "0.00",
            "ar_collection_status": "RECEIVED", "ar_revision": 2,
            "workflow_required": False, "business_operation_executed": True, "no_change": False,
        }})

    def test_consolidated_app_inherits_route_unchanged(self):
        response = self.post(app=fixtures.consolidated_app)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["ar_received_amount"], "1050.00")

    def test_noop_envelope(self):
        self.connection.invoice.update(ar_collection_status="RECEIVED", ar_received_amount=Decimal("1050.00"), ar_balance_amount=Decimal("0.00"))
        response = self.post()
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["no_change"])
        self.assertFalse(data["business_operation_executed"])
        self.assertFalse(data["workflow_required"])
        self.assertEqual(data["ar_revision"], 1)
        self.assertNotIn("update", self.connection.events)

    def test_missing_authentication(self):
        api.app.dependency_overrides.clear()
        response = self.post()
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["success"])
        self.engine_factory.assert_not_called()

    def test_missing_authenticated_user_id_or_principal(self):
        for field in ("user_id", "user_principal_name"):
            with self.subTest(field=field):
                context = deepcopy(AUTH_CONTEXT)
                context["user"].pop(field)
                with patch.dict(api.app.dependency_overrides, {api.get_authenticated_context: lambda: context}):
                    self.assertEqual(self.post().status_code, 401)
        self.engine_factory.assert_not_called()

    def test_org_mismatch_has_machine_readable_code_in_both_apps(self):
        for app in (api.app, fixtures.consolidated_app):
            with self.subTest(app=app.title):
                response = self.post({**REQUEST, "ar_org_id_fk": 18}, app=app)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error_code"], "AR_ORGANIZATION_MISMATCH")
                self.assertFalse(response.json()["success"])
        self.engine_factory.assert_not_called()

    def test_extra_financial_and_other_fields_rejected_before_logic(self):
        for field, value in (
            ("ar_invoice_amount", "1050.00"), ("ar_received_amount", "1050.00"), ("ar_balance_amount", "0.00"),
            ("ar_collection_status", "RECEIVED"), ("lines", []), ("updated_by", "someone"),
            ("permissions", ["FIN_UPDATE"]), ("financial_management_module", True),
            ("ar_approval_status", "APPROVED"), ("ar_invoice_file_path", "some.pdf"),
        ):
            with self.subTest(field=field):
                self.assertEqual(self.post({**REQUEST, field: value}).status_code, 422)
        self.engine_factory.assert_not_called()

    def test_missing_and_invalid_request_fields_return_422(self):
        payloads = [{key: value for key, value in REQUEST.items() if key != field} for field in REQUEST]
        payloads += [{**REQUEST, field: value} for field, value in (
            ("ar_id_pk", 0), ("ar_id_pk", True), ("ar_org_id_fk", 17.0), ("ar_expected_revision", "1"),
        )]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(response.status_code, 422)
                self.assertFalse(response.json()["success"])
        self.engine_factory.assert_not_called()

    def test_permission_and_license_error_envelopes(self):
        self.connection.permissions = set()
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "AR_UPDATE_PERMISSION_REQUIRED")
        self.connection.permissions = {"FIN_UPDATE"}
        for flags, code in (([False], "AR_MODULE_LICENSE_REQUIRED"), ([True, False], "AR_MODULE_LICENSE_AMBIGUOUS")):
            with self.subTest(flags=flags):
                self.connection.license_records = [{"org_id_fk": 17, "financial_management_module": flag} for flag in flags]
                response = self.post()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error_code"], code)
                self.assertFalse(response.json()["success"])

    def test_invoice_error_status_codes(self):
        before = deepcopy(self.connection.invoice)
        cases = (({"ar_revision": 2}, "AR_STALE_INVOICE", 409),
                 ({"ar_received_amount": None}, "AR_PAYMENT_DATA_INCONSISTENT", 409),
                 ({"ar_collection_status": "PAID"}, "AR_INVALID_SETTLEMENT_STATUS", 409),
                 ({"ar_org_id_fk": 18}, "AR_INVOICE_NOT_FOUND", 404))
        for changes, code, status in cases:
            with self.subTest(code=code):
                self.connection.invoice = {**before, **changes}
                response = self.post()
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error_code"], code)
                self.assertFalse(response.json()["success"])
        self.connection.invoice = before
        self.pending()
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error_code"], "AR_PENDING_UPDATE_WORKFLOW")

    def test_database_failure_returns_safe_error_and_no_success(self):
        self.connection.fail_after_update = True
        before = deepcopy(self.connection.invoice)
        with self.assertLogs(logic.__name__, logging.ERROR):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {
            "success": False, "error": "Failed to mark AR invoice as received; the operation was rolled back.",
            "error_code": "AR_MARK_RECEIVED_FAILED",
        })
        self.assertEqual(self.connection.invoice, before)
        self.assertNotIn("private database detail", response.text)

    def test_openapi_has_exact_request_contract_and_bearer_auth(self):
        for app in (api.app, fixtures.consolidated_app):
            with self.subTest(app=app.title):
                schema = app.openapi()
                self.assertIn({"HTTPBearer": []}, schema["paths"][PATH]["post"]["security"])
                model = schema["components"]["schemas"]["AccountsReceivableMarkReceivedPayload"]
                self.assertFalse(model["additionalProperties"])
                self.assertEqual(set(model["required"]), set(REQUEST))
                self.assertEqual(set(model["properties"]), set(REQUEST))


if __name__ == "__main__":
    unittest.main()
