"""Local mocked checks; no database, network, Firebase or workflow service access."""
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import asyncio
import logging
import re
import unittest
from unittest.mock import Mock, patch

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError


# Import the API without loading operational connection settings or creating engines.
with ExitStack() as import_guards:
    import_guards.enter_context(patch("dotenv.load_dotenv", return_value=False))
    import_guards.enter_context(patch("sqlalchemy.create_engine", side_effect=AssertionError("Database access forbidden")))
    import_guards.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network access forbidden")))
    from app_backend.services.service_08_financial_management.api import main as api
    # The consolidated app instantiates the unrelated payroll engine at import.
    # Supply an inert engine whose connection entry points reject any access.
    inert_engine = Mock()
    inert_engine.connect.side_effect = AssertionError("Database access forbidden")
    inert_engine.begin.side_effect = AssertionError("Database access forbidden")
    with patch("app_backend.services.service_01_organization_management.data.db_connect_engine.db_engine", return_value=inert_engine):
        from app_backend.services.main import app as consolidated_app
    from app_backend.services.service_08_financial_management.logic import accounts_receivables_update_data as logic
    from app_backend.services.service_08_financial_management.logic import accounts_receivables_aggregate as aggregate
    from app_backend.services.service_08_financial_management.logic import accounts_receivables_create_data as create_logic
    from app_backend.services.service_08_financial_management.logic import accounts_receivables_delete_data as delete_logic
    from app_backend.services.service_08_financial_management.integrations import accounts_receivables_invoice_document_generator as documents
    from app_backend.services.service_07_alerts_wf_engine.tests.test_workflow_transaction_pending_responses import pending_workflow


def setUpModule():
    global external_guards
    external_guards = ExitStack()
    for target in ("sqlalchemy.create_engine", "sqlalchemy.engine.Engine.connect", "psycopg2.connect",
                   "socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
                   "firebase_admin.initialize_app"):
        external_guards.enter_context(patch(target, side_effect=AssertionError("External access forbidden: " + target)))


def tearDownModule():
    external_guards.close()


def post_json(app, path, payload):
    # In-process ASGI only. Run synchronous handlers inline to avoid relying on
    # sandbox-restricted cross-thread socket notifications in the test runner.
    async def inline(func, *args, **kwargs):
        return func(*args)

    async def request():
        with patch("anyio.to_thread.run_sync", new=inline):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://asgi-test") as client:
                return await client.post(path, json=payload)

    return asyncio.run(request())


REQUEST = {
    "ar_id_pk": 38,
    "ar_org_id_fk": 17,
    "ar_collection_status": "PARTIALLY_RECEIVED",
    "ar_expected_revision": 1,
}
AUTH_CONTEXT = {
    "authenticated": True,
    "user": {
        "user_id": 7,
        "user_principal_name": "ar.unit.test@example.com",
        "user_org_id_fk": 17,
    },
    "organization": {"org_id": 17},
}


class CollectionStatusRequestTests(unittest.TestCase):
    def test_only_supported_statuses_are_accepted(self):
        for status in ("OUTSTANDING", "PARTIALLY_RECEIVED", "RECEIVED"):
            with self.subTest(status=status):
                model = api.AccountsReceivableCollectionStatusPayload(**{**REQUEST, "ar_collection_status": status})
                self.assertEqual(model.ar_collection_status, status)
        for status in ("PAID", "received", " RECEIVED ", "", None, 1, True):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                api.AccountsReceivableCollectionStatusPayload(**{**REQUEST, "ar_collection_status": status})

    def test_all_four_fields_are_required(self):
        for field in REQUEST:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                api.AccountsReceivableCollectionStatusPayload(**{key: value for key, value in REQUEST.items() if key != field})

    def test_ids_require_strict_positive_integers(self):
        for field in ("ar_id_pk", "ar_org_id_fk"):
            for value in (0, -1, True, False, "38", 38.0, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    api.AccountsReceivableCollectionStatusPayload(**{**REQUEST, field: value})

    def test_revision_requires_strict_nonnegative_integer(self):
        for value in (-1, True, False, "1", 1.0, None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                api.AccountsReceivableCollectionStatusPayload(**{**REQUEST, "ar_expected_revision": value})
        model = api.AccountsReceivableCollectionStatusPayload(**{**REQUEST, "ar_expected_revision": 0})
        self.assertEqual(model.ar_expected_revision, 0)

    def test_financial_lines_document_approval_audit_and_unknown_fields_are_forbidden(self):
        extra_fields = {
            "ar_received_amount": 5000, "ar_invoice_amount": 5000, "ar_tax_amount": 50,
            "ar_balance_amount": 0, "lines": [], "ar_invoice_file_path": "untrusted.pdf",
            "ar_approval_status": "APPROVED", "ar_approved_by": "untrusted",
            "ar_approved_at": "2026-10-08", "updated_by": "untrusted",
            "updated_at": "2026-10-08", "ar_revision": 100, "created_by": "untrusted",
            "user_principal_name": "untrusted", "authenticated_user_id": 8,
            "authenticated_user_principal_name": "untrusted", "authenticated_org_id": 18,
            "_accounts_receivables_workflow_approved": True, "workflow_required": False,
            "ar_due_date": "2026-10-08", "ar_contract_id_fk": 1, "ar_id": 38,
            "ar_notes": "untrusted", "unknown": None,
        }
        for field, value in extra_fields.items():
            with self.subTest(field=field), self.assertRaises(ValidationError):
                api.AccountsReceivableCollectionStatusPayload(**REQUEST, **{field: value})

    def test_binding_uses_authenticated_principal_and_organization(self):
        model = api.AccountsReceivableCollectionStatusPayload(**REQUEST)
        bound = api.bind_ar_payload(model, AUTH_CONTEXT, set_updated_by=True)
        self.assertEqual(bound["ar_org_id_fk"], 17)
        self.assertEqual(bound["updated_by"], AUTH_CONTEXT["user"]["user_principal_name"])
        self.assertEqual(bound["authenticated_user_id"], 7)
        self.assertEqual(bound["authenticated_user_principal_name"], bound["updated_by"])

    def test_binding_rejects_another_organization(self):
        model = api.AccountsReceivableCollectionStatusPayload(**{**REQUEST, "ar_org_id_fk": 18})
        with self.assertRaises(api.HTTPException) as error:
            api.bind_ar_payload(model, AUTH_CONTEXT, set_updated_by=True)
        self.assertEqual(error.exception.status_code, 403)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def one_or_none(self):
        if len(self.rows) > 1:
            raise AssertionError("Multiple invoice results")
        return self.first()

    def one(self):
        if len(self.rows) != 1:
            raise RuntimeError("Expected exactly one updated invoice")
        return self.rows[0]


class Transaction:
    def __init__(self, connection, nested=False):
        self.connection = connection
        self.nested = nested

    def __enter__(self):
        self.before = deepcopy(self.connection.invoice)
        self.connection.depth += 1
        self.connection.events.append("savepoint" if self.nested else "begin")
        return self.connection

    def __exit__(self, kind, value, traceback):
        connection = self.connection
        connection.depth -= 1
        if kind or connection.fail_commit:
            connection.invoice = self.before
            connection.events.append("rollback_savepoint" if self.nested else "rollback")
            if connection.fail_commit and not kind:
                raise RuntimeError("Mock commit failed")
        else:
            connection.events.append("release_savepoint" if self.nested else "commit")
        return False


class Connection:
    """Recording database double; every accepted statement has an explicit handler."""
    def __init__(self):
        self.invoice = {
            "ar_id_pk": 38, "ar_org_id_fk": 17, "ar_revision": 1,
            "ar_collection_status": "OUTSTANDING", "updated_by": "previous@example.com",
            "updated_at": "before", "created_by": "original@example.com", "created_at": "original",
            "ar_invoice_amount": "1050.00", "ar_tax_amount": "50.00",
            "ar_received_amount": "20.00", "ar_balance_amount": "1030.00",
            "ar_invoice_number": "TEST-AR-38", "ar_invoice_date": "2026-10-01",
            "ar_due_date": "2026-10-31", "ar_cust_id_fk": 10, "ar_contract_id_fk": 12,
            "ar_billing_period_start": "2026-10-01", "ar_billing_period_end": "2026-10-31",
            "ar_approval_status": "APPROVED", "ar_approved_by": "approver@example.com",
            "ar_approved_at": "approved", "ar_approval_comments": "approved comments",
            "ar_invoice_file_path": "existing/document.pdf", "ar_subtotal_amount": None,
            "ar_invoice_identity_snapshot": {"customer": {"cust_name": "Original"}},
            "ar_notes": "Existing notes", "ar_description": "Transport",
        }
        self.lines = [{"ar_line_id_pk": 9, "ar_line_quantity": "1.0", "ar_line_unit_rate": "1000.00",
                       "ar_line_tax_code": "VAT", "ar_line_tax_rate": "5.0"}]
        self.workflows = []
        self.license_records = [
            {"org_id_fk": 17, "financial_management_module": True},
            {"org_id_fk": 18, "financial_management_module": False},
        ]
        self.permissions = {"FIN_UPDATE"}
        self.user_active = True
        self.user_deleted = False
        self.role_active = True
        self.organization_exists = True
        self.user_org_id = 17
        self.depth = 0
        self.events = []
        self.statements = []
        self.fail_after_update = False
        self.fail_commit = False
        self.fail_lookup = False
        self.fail_license_lookup = False
        self.fail_update_returning = False

    def in_transaction(self):
        return self.depth > 0

    def begin(self):
        return Transaction(self)

    def begin_nested(self):
        return Transaction(self, nested=True)

    def execute(self, statement, params):
        assert self.in_transaction(), "Every database operation must be transactional"
        sql = " ".join(str(statement).lower().split())
        self.statements.append((sql, deepcopy(params)))
        if "from v_user_access_rights rights" in sql:
            for required in ("upper(rights.permission_code) = 'fin_update'", "upper(rights.module_name) = 'finance'",
                             "upper(rights.action_name) = 'update'", "usr.user_id_pk = :user_id",
                             "usr.user_org_id_fk = :org_id", "coalesce(role.is_active, true) = true",
                             "coalesce(usr.is_active, true) = true", "coalesce(usr.is_deleted, false) = false",
                             "join organization_master"):
                assert required in sql, required
            assert params == {"user_id": 7, "principal": "ar.unit.test@example.com", "org_id": 17}
            granted = ("FIN_UPDATE" in self.permissions and self.user_active and not self.user_deleted
                       and self.role_active and self.organization_exists and self.user_org_id == params["org_id"])
            self.events.append("permission")
            return Result([(1,)] if granted else [])
        if "from organization_license_master" in sql:
            assert "where org_id_fk = :organization_id" in sql
            assert "limit" not in sql and "order by" not in sql
            assert sql.startswith("select financial_management_module ")
            assert sql.endswith("where org_id_fk = :organization_id"), "Read all flags without filtering disabled or NULL records"
            self.events.append("license_lookup")
            if self.fail_license_lookup:
                raise RuntimeError("Mock license lookup failed")
            return Result([{"financial_management_module": row["financial_management_module"]}
                           for row in self.license_records if row["org_id_fk"] == params["organization_id"]])
        if "pg_advisory_xact_lock" in sql:
            assert params == {"lock_namespace": "UETransportERP:workflow_domain_conflict", "lock_key": "ACCOUNTS_RECEIVABLE:UPDATE:17:38"}
            self.events.append("submission_lock")
            return Result()
        if "from workflow_instances wi" in sql:
            if self.fail_lookup:
                raise RuntimeError("Mock workflow lookup unavailable")
            assert "for update" in sql and "wi.workflow_status = 'pending_approval'" in sql
            assert "wi.organization_id_fk = :organization_id" in sql
            assert "wi.workflow_code = :workflow_code" in sql and "wi.workflow_action = :workflow_action" in sql
            assert "->> 'ar_id'" in sql and "->> 'ar_id_pk'" in sql
            self.events.append("configured_pending_lock")
            matches = [row for row in self.workflows if row.get("configured", True)
                       and row["workflow_code"] == params["workflow_code"]
                       and row["workflow_action"] == params["workflow_action"]
                       and row["organization_id"] == params["organization_id"]
                       and row["workflow_status"] == "PENDING_APPROVAL"
                       and any(str(row["request_payload"].get(key)) == params["domain_reference_id"] for key in ("ar_id", "ar_id_pk"))]
            return Result(matches[:1])
        if "from accounts_receivables_workflow_requests legacy" in sql:
            assert "workflow_instance_id_fk is null" in sql and "for update" in sql
            assert "legacy.workflow_action = 'update'" in sql and "legacy.workflow_status = 'pending_approval'" in sql
            assert "->> 'ar_id'" in sql and "->> 'ar_id_pk'" in sql
            assert "->> 'authenticated_org_id'" in sql and "->> 'ar_org_id_fk'" in sql
            self.events.append("legacy_pending_lock")
            matches = [row for row in self.workflows if not row.get("configured", True)
                       and row["workflow_action"] == "UPDATE" and row["workflow_status"] == "PENDING_APPROVAL"
                       and any(row["request_payload"].get(key) == params["organization_id"] for key in ("ar_org_id_fk", "authenticated_org_id"))
                       and any(str(row["request_payload"].get(key)) == params["ar_id_text"] for key in ("ar_id", "ar_id_pk"))]
            return Result(matches[:1])
        if sql.startswith("select ar_id_pk,"):
            assert "for update" in sql and "ar_org_id_fk = :org_id" in sql
            assert "coalesce(ar_revision, 0) as ar_revision" in sql
            self.events.append("invoice_lock")
            if self.invoice is None or (self.invoice["ar_id_pk"], self.invoice["ar_org_id_fk"]) != (params["ar_id"], params["org_id"]):
                return Result()
            return Result([{key: (self.invoice[key] or 0) if key == "ar_revision" else self.invoice[key]
                            for key in ("ar_id_pk", "ar_collection_status", "ar_revision")}])
        if sql.startswith("update accounts_receivables "):
            setters = sql.split(" set ", 1)[1].split(" where ", 1)[0]
            assert set(re.findall(r"(\w+)\s*=", setters)) == {"ar_collection_status", "updated_by", "ar_revision"}
            assert "ar_org_id_fk = :org_id" in sql and "coalesce(ar_revision, 0) = :revision" in sql
            assert "ar_revision = coalesce(ar_revision, 0) + 1" in sql
            assert self.invoice["ar_org_id_fk"] == params["org_id"] and self.invoice["ar_id_pk"] == params["ar_id"]
            assert (self.invoice["ar_revision"] or 0) == params["revision"]
            self.events.append("update")
            self.invoice.update(ar_collection_status=params["status"], updated_by=params["principal"],
                                ar_revision=params["revision"] + 1, updated_at="mock_trigger_time")
            if self.fail_after_update:
                raise RuntimeError("Mock failure after write")
            if self.fail_update_returning:
                return Result()
            return Result([{key: self.invoice[key] for key in ("ar_id_pk", "ar_collection_status", "ar_revision")}])
        raise AssertionError("Unexpected SQL: " + sql)


class Engine:
    def __init__(self, connection):
        self.connection = connection
        self.disposed = False

    def begin(self):
        return self.connection.begin()

    def dispose(self):
        self.disposed = True


class MockedCollectionStatusCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.connection = Connection()
        self.engine = Engine(self.connection)
        self.engine_factory = self.stack.enter_context(patch.object(logic, "db_engine", return_value=self.engine))
        # Exercise the real license decision against mocked organization rows.
        for name in ("is_accounts_receivables_workflow_approved", "stage_ar_invoice_document_for_workflow", "cleanup_staged_workflow_document"):
            mock = self.stack.enter_context(patch.object(logic, name, side_effect=AssertionError("Forbidden status-only dependency: " + name)))
            self.addCleanup(mock.assert_not_called)

    def update(self, **changes):
        payload = api.bind_ar_payload({**REQUEST, **changes}, AUTH_CONTEXT, set_updated_by=True)
        return logic.update_ar_collection_status(payload)

    def pending(self, *, configured=True, alias="ar_id_pk", **changes):
        row = {"configured": configured, "workflow_code": "ACCOUNTS_RECEIVABLE", "workflow_action": "UPDATE",
               "workflow_status": "PENDING_APPROVAL", "organization_id": 17,
               "request_payload": {alias: 38, "ar_org_id_fk": 17}, "workflow_instance_id_pk": 100}
        row.update(changes)
        self.connection.workflows.append(row)

    def assert_unmodified(self, before):
        self.assertEqual(self.connection.invoice, before)
        self.assertNotIn("update", self.connection.events)


class CollectionStatusLogicTests(MockedCollectionStatusCase):
    def test_outstanding_to_partially_received(self):
        result = self.update()
        self.assertTrue(result["business_operation_executed"])
        self.assertFalse(result["workflow_required"])
        self.assertEqual(result["ar_collection_status"], "PARTIALLY_RECEIVED")
        self.assertEqual(result["ar_revision"], 2)
        self.assertFalse(result["no_change"])

    def test_partially_received_to_received(self):
        self.connection.invoice["ar_collection_status"] = "PARTIALLY_RECEIVED"
        result = self.update(ar_collection_status="RECEIVED")
        self.assertEqual(result["ar_collection_status"], "RECEIVED")
        self.assertEqual(result["ar_revision"], 2)

    def test_received_to_outstanding(self):
        self.connection.invoice["ar_collection_status"] = "RECEIVED"
        result = self.update(ar_collection_status="OUTSTANDING")
        self.assertEqual(result["ar_collection_status"], "OUTSTANDING")
        self.assertEqual(result["ar_revision"], 2)

    def test_unchanged_status_does_not_write_or_increment_audit_or_revision(self):
        before = deepcopy(self.connection.invoice)
        result = self.update(ar_collection_status="OUTSTANDING")
        self.assertTrue(result["no_change"])
        self.assertFalse(result["business_operation_executed"])
        self.assertEqual(result["ar_revision"], 1)
        self.assert_unmodified(before)

    def test_legacy_null_revision_increments_from_zero(self):
        self.connection.invoice["ar_revision"] = None
        result = self.update(ar_expected_revision=0)
        self.assertEqual(result["ar_revision"], 1)

    def test_legacy_null_revision_noop_remains_null_in_storage(self):
        self.connection.invoice["ar_revision"] = None
        before = deepcopy(self.connection.invoice)
        result = self.update(ar_expected_revision=0, ar_collection_status="OUTSTANDING")
        self.assertEqual(result["ar_revision"], 0)
        self.assert_unmodified(before)

    def test_legacy_null_revision_rejects_nonzero_expected_revision(self):
        self.connection.invoice["ar_revision"] = None
        before = deepcopy(self.connection.invoice)
        self.assertEqual(self.update()["error_code"], "AR_STALE_INVOICE")
        self.assert_unmodified(before)

    def test_all_other_header_fields_lines_and_workflows_are_preserved_in_both_invoice_formats(self):
        for subtotal in (None, "1000.00"):
            with self.subTest(subtotal=subtotal):
                self.connection.invoice.update(ar_subtotal_amount=subtotal, ar_revision=1, ar_collection_status="OUTSTANDING")
                before = deepcopy(self.connection.invoice)
                lines = deepcopy(self.connection.lines)
                workflows = deepcopy(self.connection.workflows)
                result = self.update()
                self.assertNotIn("error", result)
                changed = {key for key in before if before[key] != self.connection.invoice[key]}
                self.assertLessEqual(changed, {"ar_collection_status", "updated_by", "updated_at", "ar_revision"})
                self.assertEqual(self.connection.lines, lines)
                self.assertEqual(self.connection.workflows, workflows)

    def test_no_new_approval_status_restrictions(self):
        for approval in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "CANCELLED"):
            with self.subTest(approval=approval):
                self.connection.invoice.update(ar_revision=1, ar_collection_status="OUTSTANDING", ar_approval_status=approval)
                self.assertNotIn("error", self.update())
                self.assertEqual(self.connection.invoice["ar_approval_status"], approval)

    def test_missing_id_is_rejected_before_database_access(self):
        bound = api.bind_ar_payload(REQUEST, AUTH_CONTEXT, set_updated_by=True)
        bound.pop("ar_id_pk")
        self.assertEqual(logic.update_ar_collection_status(bound)["status_code"], 422)
        self.engine_factory.assert_not_called()

    def test_missing_authenticated_context_is_rejected(self):
        self.assertEqual(logic.update_ar_collection_status(REQUEST)["status_code"], 401)
        self.engine_factory.assert_not_called()

    def test_mismatched_authenticated_organization_is_rejected(self):
        bound = api.bind_ar_payload(REQUEST, AUTH_CONTEXT, set_updated_by=True)
        bound["ar_org_id_fk"] = 18
        self.assertEqual(logic.update_ar_collection_status(bound)["status_code"], 403)
        self.engine_factory.assert_not_called()

    def test_invalid_status_and_revision_are_rejected_without_writes(self):
        before = deepcopy(self.connection.invoice)
        for changes in ({"ar_collection_status": "INVALID"}, {"ar_collection_status": []},
                        {"ar_expected_revision": -1}, {"ar_expected_revision": True}):
            with self.subTest(changes=changes):
                self.assertEqual(self.update(**changes)["status_code"], 422)
                self.assert_unmodified(before)

    def test_extra_business_or_permission_fields_are_rejected_in_logic(self):
        for key in ("ar_received_amount", "lines", "financial_management_module", "permissions", "_accounts_receivables_workflow_approved"):
            with self.subTest(key=key):
                self.assertEqual(self.update(**{key: True})["status_code"], 422)
        self.engine_factory.assert_not_called()

    def test_missing_invoice_and_invoice_in_other_organization_are_rejected(self):
        for invoice in (None, {**self.connection.invoice, "ar_org_id_fk": 18}):
            with self.subTest(invoice_exists=invoice is not None):
                self.connection.invoice = invoice
                before = deepcopy(invoice)
                self.assertEqual(self.update()["status_code"], 404)
                self.assert_unmodified(before)

    def test_fin_update_is_required_even_with_workflow_submit_permission(self):
        self.connection.permissions = {"WF_SUBMIT"}
        before = deepcopy(self.connection.invoice)
        result = self.update()
        self.assertEqual(result["error_code"], "AR_UPDATE_PERMISSION_REQUIRED")
        self.assertEqual(result["status_code"], 403)
        self.assertNotIn("license_lookup", self.connection.events)
        self.assert_unmodified(before)

    def test_workflow_submit_is_not_required(self):
        self.assertEqual(self.connection.permissions, {"FIN_UPDATE"})
        self.assertNotIn("error", self.update())

    def test_inactive_deleted_cross_org_users_inactive_roles_and_missing_org_are_denied(self):
        for field, denied_value in (("user_active", False), ("user_deleted", True), ("role_active", False),
                                    ("user_org_id", 18), ("organization_exists", False)):
            original = getattr(self.connection, field)
            with self.subTest(field=field):
                setattr(self.connection, field, denied_value)
                self.assertEqual(self.update()["status_code"], 403)
                setattr(self.connection, field, original)
        self.assertNotIn("update", self.connection.events)

    def test_module_denial_and_conflict_deny_before_invoice_lookup(self):
        for values, code in (([False], "AR_MODULE_LICENSE_REQUIRED"), ([True, False], "AR_MODULE_LICENSE_AMBIGUOUS")):
            with self.subTest(values=values):
                self.connection.license_records = [{"org_id_fk": 17, "financial_management_module": value} for value in values]
                before = deepcopy(self.connection.invoice)
                result = self.update()
                self.assertEqual((result["status_code"], result["error_code"]), (403, code))
                self.assertNotIn("invoice_lock", self.connection.events)
                self.assert_unmodified(before)

    def test_audit_actor_comes_from_authenticated_context(self):
        bound = api.bind_ar_payload(REQUEST, AUTH_CONTEXT, set_updated_by=True)
        bound.update(updated_by="untrusted@example.com", user_principal_name="untrusted@example.com")
        result = logic.update_ar_collection_status(bound)
        self.assertNotIn("error", result)
        self.assertEqual(self.connection.invoice["updated_by"], "ar.unit.test@example.com")

    def test_stale_revision_rejected_even_for_unchanged_status(self):
        before = deepcopy(self.connection.invoice)
        for status in ("OUTSTANDING", "PARTIALLY_RECEIVED"):
            with self.subTest(status=status):
                result = self.update(ar_collection_status=status, ar_expected_revision=0)
                self.assertEqual((result["status_code"], result["error_code"]), (409, "AR_STALE_INVOICE"))
                self.assert_unmodified(before)

    def test_second_request_with_old_revision_cannot_overwrite_first(self):
        self.assertNotIn("error", self.update())
        before = deepcopy(self.connection.invoice)
        result = self.update(ar_collection_status="RECEIVED")
        self.assertEqual(result["error_code"], "AR_STALE_INVOICE")
        self.assertEqual(self.connection.invoice, before)
        self.assertEqual(self.connection.events.count("update"), 1)

    def test_pending_configured_or_legacy_update_blocks_both_id_aliases(self):
        for configured in (True, False):
            for alias in ("ar_id", "ar_id_pk"):
                with self.subTest(configured=configured, alias=alias):
                    self.connection.workflows = []
                    self.pending(configured=configured, alias=alias)
                    workflows = deepcopy(self.connection.workflows)
                    before = deepcopy(self.connection.invoice)
                    result = self.update()
                    self.assertEqual((result["status_code"], result["error_code"]), (409, "AR_PENDING_UPDATE_WORKFLOW"))
                    self.assert_unmodified(before)
                    self.assertEqual(self.connection.workflows, workflows)

    def test_noop_during_pending_update_is_successful_without_writes(self):
        self.pending()
        before = deepcopy(self.connection.invoice)
        self.assertTrue(self.update(ar_collection_status="OUTSTANDING")["no_change"])
        self.assert_unmodified(before)

    def test_other_workflows_do_not_create_a_false_update_conflict(self):
        self.pending(workflow_action="CREATE")
        self.pending(workflow_action="DELETE")
        self.pending(workflow_status="EXECUTED")
        self.pending(workflow_status="REJECTED")
        self.pending(workflow_code="ACCOUNTS_PAYABLE")
        self.pending(organization_id=18)
        self.pending(request_payload={"ar_id_pk": 39, "ar_org_id_fk": 17})
        self.pending(configured=False, request_payload={"ar_id": 38, "ar_org_id_fk": 18})
        workflows = deepcopy(self.connection.workflows)
        self.assertNotIn("error", self.update())
        self.assertEqual(self.connection.workflows, workflows)

    def test_transaction_and_lock_order_matches_workflow_approval_order(self):
        self.assertNotIn("error", self.update())
        self.assertEqual(self.connection.events, ["begin", "permission", "license_lookup", "submission_lock", "configured_pending_lock",
                                                  "legacy_pending_lock", "invoice_lock", "update", "commit"])
        self.assertTrue(self.engine.disposed)

    def test_caller_owned_connection_uses_savepoint_without_committing_outer_transaction(self):
        bound = api.bind_ar_payload(REQUEST, AUTH_CONTEXT, set_updated_by=True)
        self.connection.depth = 1
        result = logic.update_ar_collection_status(bound, conn=self.connection)
        self.assertNotIn("error", result)
        self.assertEqual(self.connection.depth, 1)
        self.assertEqual(self.connection.events[0], "savepoint")
        self.assertEqual(self.connection.events[-1], "release_savepoint")
        self.assertNotIn("commit", self.connection.events)
        self.engine_factory.assert_not_called()

    def test_inactive_supplied_connection_gets_a_transaction(self):
        bound = api.bind_ar_payload(REQUEST, AUTH_CONTEXT, set_updated_by=True)
        self.assertNotIn("error", logic.update_ar_collection_status(bound, conn=self.connection))
        self.assertEqual(self.connection.events[-1], "commit")
        self.engine_factory.assert_not_called()

    def test_database_lookup_write_and_commit_failures_rollback(self):
        for failure in ("fail_license_lookup", "fail_lookup", "fail_after_update", "fail_commit", "fail_update_returning"):
            with self.subTest(failure=failure):
                setattr(self.connection, failure, True)
                before = deepcopy(self.connection.invoice)
                with self.assertLogs(logic.__name__, logging.ERROR):
                    result = self.update()
                self.assertEqual(result["status_code"], 500)
                self.assertEqual(self.connection.invoice, before)
                self.assertEqual(self.connection.events[-1], "rollback")
                self.assertTrue(self.engine.disposed)
                setattr(self.connection, failure, False)

    def test_caller_owned_savepoint_rolls_back_failure(self):
        self.connection.depth = 1
        self.connection.fail_after_update = True
        before = deepcopy(self.connection.invoice)
        bound = api.bind_ar_payload(REQUEST, AUTH_CONTEXT, set_updated_by=True)
        with self.assertLogs(logic.__name__, logging.ERROR):
            result = logic.update_ar_collection_status(bound, conn=self.connection)
        self.assertEqual(result["status_code"], 500)
        self.assertEqual(self.connection.invoice, before)
        self.assertEqual(self.connection.events[-1], "rollback_savepoint")
        self.assertEqual(self.connection.depth, 1)


class CollectionStatusApiTests(MockedCollectionStatusCase):
    def setUp(self):
        super().setUp()
        for app in (api.app, consolidated_app):
            self.stack.enter_context(patch.dict(app.dependency_overrides, {api.get_authenticated_context: lambda: deepcopy(AUTH_CONTEXT)}))

    def post(self, request=None, app=api.app):
        return post_json(app, "/api/v1/accounts-receivables/collection-status/update", REQUEST if request is None else request)

    def test_endpoint_success_envelope_with_real_license_check_and_mocked_rows(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])
        data = response.json()["data"]
        self.assertEqual((data["ar_id_pk"], data["ar_revision"], data["ar_collection_status"]), (38, 2, "PARTIALLY_RECEIVED"))
        self.assertTrue(data["business_operation_executed"])
        self.assertFalse(data["workflow_required"])

    def test_consolidated_api_inherits_endpoint_without_modification(self):
        response = self.post(app=consolidated_app)
        self.assertEqual(response.status_code, 200, response.text)

    def test_unauthenticated_request_is_rejected(self):
        api.app.dependency_overrides.clear()
        response = self.post()
        self.assertEqual(response.status_code, 401)
        self.engine_factory.assert_not_called()

    def test_extra_fields_are_rejected_by_endpoint(self):
        for field, value in (("ar_received_amount", 5000), ("ar_invoice_amount", 5000), ("lines", []),
                             ("updated_by", "untrusted"), ("financial_management_module", True),
                             ("permissions", ["FIN_UPDATE"]), ("licenses", []),
                             ("ar_approval_status", "APPROVED"), ("ar_invoice_file_path", "untrusted.pdf")):
            with self.subTest(field=field):
                response = self.post({**REQUEST, field: value})
                self.assertEqual(response.status_code, 422)
        self.engine_factory.assert_not_called()

    def test_missing_id_invalid_enum_and_strict_integer_validation(self):
        invalid_requests = [{key: value for key, value in REQUEST.items() if key != "ar_id_pk"},
                            {**REQUEST, "ar_collection_status": "PAID"}, {**REQUEST, "ar_id_pk": True},
                            {**REQUEST, "ar_expected_revision": "1"}, {**REQUEST, "ar_org_id_fk": 17.0}]
        for request in invalid_requests:
            with self.subTest(request=request):
                self.assertEqual(self.post(request).status_code, 422)
        self.engine_factory.assert_not_called()

    def test_other_organization_request_is_forbidden(self):
        self.assertEqual(self.post({**REQUEST, "ar_org_id_fk": 18}).status_code, 403)
        self.engine_factory.assert_not_called()

    def test_permission_denied_envelope(self):
        self.connection.permissions = set()
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["error_code"], "AR_UPDATE_PERMISSION_REQUIRED")

    def test_stale_and_pending_conflicts_return_409(self):
        stale = self.post({**REQUEST, "ar_expected_revision": 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error_code"], "AR_STALE_INVOICE")
        self.pending()
        pending = self.post()
        self.assertEqual(pending.status_code, 409)
        self.assertEqual(pending.json()["error_code"], "AR_PENDING_UPDATE_WORKFLOW")
        self.assertNotIn("update", self.connection.events)

    def test_openapi_contract_has_only_four_required_fields_and_exact_enum(self):
        schema = api.app.openapi()
        operation = schema["paths"]["/api/v1/accounts-receivables/collection-status/update"]["post"]
        self.assertIn({"HTTPBearer": []}, operation["security"])
        model = schema["components"]["schemas"]["AccountsReceivableCollectionStatusPayload"]
        self.assertFalse(model["additionalProperties"])
        self.assertEqual(set(model["required"]), set(REQUEST))
        self.assertEqual(set(model["properties"]), set(REQUEST))
        self.assertEqual(model["properties"]["ar_collection_status"]["enum"], ["OUTSTANDING", "PARTIALLY_RECEIVED", "RECEIVED"])


class OrganizationLicenseTests(MockedCollectionStatusCase):
    def setUp(self):
        super().setUp()
        for app in (api.app, consolidated_app):
            self.stack.enter_context(patch.dict(app.dependency_overrides, {api.get_authenticated_context: lambda: deepcopy(AUTH_CONTEXT)}))

    def set_licenses(self, values, organization_id=17):
        self.connection.license_records = [{"org_id_fk": organization_id, "financial_management_module": value} for value in values]

    def assert_denied(self, values, code):
        self.set_licenses(values)
        before = deepcopy(self.connection.invoice)
        records = deepcopy(self.connection.license_records)
        response = post_json(api.app, "/api/v1/accounts-receivables/collection-status/update", REQUEST)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["error_code"], code)
        self.assertNotIn("invoice_lock", self.connection.events)
        self.assertNotIn("submission_lock", self.connection.events)
        self.assert_unmodified(before)
        self.assertEqual(self.connection.license_records, records)

    def test_single_enabled_license_permits_status_update(self):
        self.set_licenses([True])
        self.assertNotIn("error", self.update())
        self.assertEqual(self.connection.invoice["ar_collection_status"], "PARTIALLY_RECEIVED")

    def test_multiple_enabled_licenses_permit_update(self):
        self.set_licenses([True, True, True])
        response = post_json(consolidated_app, "/api/v1/accounts-receivables/collection-status/update", REQUEST)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["data"]["business_operation_executed"])

    def test_missing_license_is_denied(self):
        self.assert_denied([], "AR_MODULE_LICENSE_REQUIRED")

    def test_single_or_multiple_disabled_licenses_are_denied(self):
        for values in ([False], [False, False]):
            with self.subTest(values=values):
                self.assert_denied(values, "AR_MODULE_LICENSE_REQUIRED")

    def test_conflicting_licenses_are_denied_in_either_order(self):
        for values in ([True, False], [False, True], [True, False, True]):
            with self.subTest(values=values):
                self.assert_denied(values, "AR_MODULE_LICENSE_AMBIGUOUS")

    def test_null_or_non_boolean_values_never_grant_access(self):
        for values in ([None], [False, None], ["true"], [1]):
            with self.subTest(values=values):
                self.assert_denied(values, "AR_MODULE_LICENSE_REQUIRED")
        for values in ([True, None], [None, True]):
            with self.subTest(values=values):
                self.assert_denied(values, "AR_MODULE_LICENSE_AMBIGUOUS")

    def test_other_organization_license_cannot_grant_access(self):
        self.set_licenses([True], organization_id=18)
        self.assertEqual(self.update()["error_code"], "AR_MODULE_LICENSE_REQUIRED")
        self.connection.license_records.append({"org_id_fk": 17, "financial_management_module": False})
        self.assertEqual(self.update()["error_code"], "AR_MODULE_LICENSE_REQUIRED")
        self.assertNotIn("update", self.connection.events)

    def test_other_organization_disabled_license_does_not_deny_access(self):
        # Default fixture has organization 17 enabled and organization 18 disabled.
        records = deepcopy(self.connection.license_records)
        self.assertNotIn("error", self.update())
        self.assertEqual(self.connection.license_records, records)

    def test_authentication_license_snapshot_cannot_override_database_decision(self):
        auth = {**AUTH_CONTEXT, "licenses": [{"org_id_fk": 17, "financial_management_module": True}]}
        self.set_licenses([False])
        with patch.dict(api.app.dependency_overrides, {api.get_authenticated_context: lambda: auth}):
            denied = post_json(api.app, "/api/v1/accounts-receivables/collection-status/update", REQUEST)
        self.assertEqual(denied.status_code, 403)
        auth["licenses"][0]["financial_management_module"] = False
        self.set_licenses([True])
        with patch.dict(api.app.dependency_overrides, {api.get_authenticated_context: lambda: auth}):
            allowed = post_json(api.app, "/api/v1/accounts-receivables/collection-status/update", REQUEST)
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_supplied_organization_one_scenario_with_mocked_record(self):
        self.set_licenses([True], organization_id=1)
        with self.connection.begin():
            self.assertIsNone(logic._ar_collection_status_license_error(self.connection, 1))
            self.assertEqual(logic._ar_collection_status_license_error(self.connection, 17)["status_code"], 403)
        self.assertNotIn("update", self.connection.events)


class ExistingWorkflowPreservationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(api.app.dependency_overrides, {api.get_authenticated_context: lambda: deepcopy(AUTH_CONTEXT)}))
        # Existing endpoints must never acquire the new license/status guard.
        self.license = self.stack.enter_context(patch.object(logic, "_ar_collection_status_license_error", side_effect=AssertionError("Existing authorization changed")))
        self.addCleanup(self.license.assert_not_called)
        for module in (logic, create_logic, delete_logic, aggregate):
            self.stack.enter_context(patch.object(module, "db_engine", side_effect=AssertionError("Unexpected database access")))
        self.payload = {
            "user_principal_name": "ar.unit.test@example.com", "ar_org_id_fk": 17,
            "ar_cust_id_fk": 10, "ar_id_pk": 38, "ar_invoice_number": "TEST-AR-38",
            "ar_invoice_date": "2026-10-01", "ar_currency_code": "AED", "ar_invoice_amount": 1050,
        }
        self.existing = Connection().invoice

    def legacy_dependencies(self, module):
        for name, value in (("verify_ar_organization_exists", True), ("verify_customer_for_organization", True),
                            ("duplicate_ar_invoice_exists", False)):
            self.stack.enter_context(patch.object(module, name, return_value=value))
        if module is logic:
            self.stack.enter_context(patch.object(logic, "get_ar_by_id", return_value=self.existing))

    def assert_pending(self, response, action):
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertTrue(data["workflow_required"])
        self.assertFalse(data["business_operation_executed"])
        self.assertEqual(data["workflow_action"], action)
        self.assertEqual(data["workflow_status"], "PENDING_APPROVAL")

    def test_ordinary_header_changes_and_combined_status_changes_keep_update_workflow(self):
        self.legacy_dependencies(logic)
        changes = ({"ar_received_amount": 100}, {"ar_invoice_amount": 2000}, {"ar_tax_amount": 75},
                   {"ar_invoice_date": "2026-10-02"}, {"ar_due_date": "2026-10-31"},
                   {"ar_contract": "Other reference"}, {"ar_notes": "Changed notes"},
                   {"ar_collection_status": "RECEIVED", "ar_received_amount": 500},
                   {"ar_collection_status": "RECEIVED"})
        for change in changes:
            with self.subTest(change=change), patch.object(logic, "is_accounts_receivables_workflow_approved", return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "UPDATE"))) as workflow:
                response = post_json(api.app, "/api/v1/accounts-receivables/update", {**self.payload, **change})
                self.assert_pending(response, "UPDATE")
                workflow.assert_called_once()
                self.assertEqual(workflow.call_args.kwargs["workflow_action"], "UPDATE")
                for key, value in change.items():
                    self.assertEqual(workflow.call_args.kwargs["payload"][key], value)

    def test_general_header_update_keeps_workflow_denial(self):
        self.legacy_dependencies(logic)
        with patch.object(logic, "is_accounts_receivables_workflow_approved", return_value=(False, {"error": "Requester lacks workflow submit permission."})), \
             patch.object(logic, "cleanup_staged_workflow_document", return_value={}):
            response = post_json(api.app, "/api/v1/accounts-receivables/update", self.payload)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])

    def test_line_update_contract_billing_and_financial_fields_keep_aggregate_workflow(self):
        query_connection = Mock()
        query_connection.execute.return_value.first.return_value = None
        line = {"ar_line_description": "Transport", "ar_line_quantity": "1", "ar_line_unit_rate": "1000"}
        payload = {**self.payload, "ar_contract_id_fk": 12, "ar_billing_period_start": "2026-10-01",
                   "ar_billing_period_end": "2026-10-31", "ar_received_amount": 100, "lines": [line]}
        proposal = {**payload, "idempotency_key": "unit-test", "ar_subtotal_amount": Decimal("1000"),
                    "ar_tax_amount": Decimal("50"), "ar_invoice_amount": Decimal("1050"),
                    "lines": [{**line, "ar_line_net_amount": Decimal("1000"), "ar_line_tax_amount": Decimal("50")} ]}
        with patch.object(aggregate, "ar_connection", return_value=nullcontext(query_connection)), \
             patch.object(aggregate, "read_header", return_value={**self.existing, "ar_subtotal_amount": "1000"}), \
             patch.object(aggregate, "prepare_invoice", return_value=proposal) as prepare, \
             patch.object(documents, "generate_ar_invoice_document", return_value=nullcontext(BytesIO(b"mocked-pdf"))), \
             patch.object(create_logic, "stage_ar_invoice_document_for_workflow", return_value=(proposal, None)), \
             patch.object(aggregate, "is_accounts_receivables_workflow_approved", return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "UPDATE"))) as workflow:
            response = post_json(api.app, "/api/v1/accounts-receivables/update", payload)
        self.assert_pending(response, "UPDATE")
        prepare.assert_called_once()
        for field in ("ar_contract_id_fk", "ar_billing_period_start", "ar_billing_period_end", "ar_received_amount"):
            self.assertEqual(prepare.call_args.args[0][field], payload[field])
        workflow.assert_called_once_with(proposal, "UPDATE")

    def test_existing_create_keeps_workflow(self):
        self.legacy_dependencies(create_logic)
        payload = {key: value for key, value in self.payload.items() if key != "ar_id_pk"}
        with patch.object(create_logic, "is_accounts_receivables_workflow_approved", return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "CREATE"))) as workflow:
            response = post_json(api.app, "/api/v1/accounts-receivables/create", payload)
        self.assert_pending(response, "CREATE")
        self.assertEqual(workflow.call_args.kwargs["workflow_action"], "CREATE")

    def test_existing_delete_keeps_workflow(self):
        payload = {"user_principal_name": "ar.unit.test@example.com", "ar_id_pk": 38}
        with patch.object(delete_logic, "get_ar_by_id", return_value=self.existing), \
             patch.object(delete_logic, "is_accounts_receivables_workflow_approved", return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "DELETE"))) as workflow:
            response = post_json(api.app, "/api/v1/accounts-receivables/delete", payload)
        self.assert_pending(response, "DELETE")
        self.assertEqual(workflow.call_args.kwargs["workflow_action"], "DELETE")


if __name__ == "__main__":
    unittest.main()
