import unittest
from pathlib import Path
from unittest.mock import patch

from app_backend.services.service_07_alerts_wf_engine import workflow_adapter_helpers
from app_backend.services.service_07_alerts_wf_engine import workflow_runtime_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_errors import (
    CURRENT_APPROVER_MISMATCH,
    LEGACY_WORKFLOW_TABLE_NOT_ALLOWED,
    WORKFLOW_IDENTIFIER_MISMATCH,
    WORKFLOW_INSTANCE_NOT_CONFIGURED,
    WORKFLOW_INSTANCE_NOT_FOUND,
    WORKFLOW_INSTANCE_STEP_ID_REQUIRED,
    WORKFLOW_INSTANCE_TYPE_MISMATCH,
    WORKFLOW_ENGINE_MIGRATION_REQUIRED,
    WORKFLOW_STEP_NOT_CURRENT,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    assert_legacy_workflow_table_allowed,
    ensure_workflow_engine_schema,
)


class FakeEngine:
    def __init__(self):
        self.conn = object()
        self.begin_count = 0
        self.exited = False
        self.disposed = False

    def begin(self):
        self.begin_count += 1
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, traceback):
        self.exited = True
        return False

    def dispose(self):
        self.disposed = True


class MissingMigrationConnection:
    def execute(self, statement, params=None):
        return MissingMigrationResult()


class MissingMigrationResult:
    def scalar_one(self):
        return None


class WorkflowSharedCompatibilityTests(unittest.TestCase):
    def test_adapter_helper_imports_repository_allowlist(self):
        self.assertIs(workflow_adapter_helpers.assert_legacy_workflow_table_allowed, assert_legacy_workflow_table_allowed)

    def test_legacy_table_allowlist_uses_stable_error_code(self):
        assert_legacy_workflow_table_allowed("accounts_payables_workflow_requests")
        with self.assertRaisesRegex(ValueError, LEGACY_WORKFLOW_TABLE_NOT_ALLOWED):
            assert_legacy_workflow_table_allowed("user_master")

    def test_start_workflow_uses_caller_owned_connection(self):
        caller_conn = object()
        with patch.object(
            workflow_runtime_engine,
            "_start_workflow_in_conn",
            return_value={"workflow_status": "PENDING_APPROVAL"},
        ) as start_in_conn, patch.object(workflow_runtime_engine, "db_engine") as db_engine:
            result = workflow_runtime_engine.start_workflow(
                workflow_code="ACCOUNTS_PAYABLE",
                workflow_action="CREATE",
                organization_id=1,
                requester_user_principal_name="requester_a",
                request_payload={},
                legacy_table_name="accounts_payables_workflow_requests",
                conn=caller_conn,
            )
        self.assertEqual(result["workflow_status"], "PENDING_APPROVAL")
        start_in_conn.assert_called_once()
        self.assertIs(start_in_conn.call_args.args[0], caller_conn)
        db_engine.assert_not_called()

    def test_start_workflow_without_connection_owns_engine_lifecycle(self):
        engine = FakeEngine()
        with patch.object(
            workflow_runtime_engine,
            "_start_workflow_in_conn",
            return_value={"workflow_status": "PENDING_APPROVAL"},
        ) as start_in_conn, patch.object(workflow_runtime_engine, "db_engine", return_value=engine):
            result = workflow_runtime_engine.start_workflow(
                workflow_code="ACCOUNTS_PAYABLE",
                workflow_action="CREATE",
                organization_id=1,
                requester_user_principal_name="requester_a",
                request_payload={},
                legacy_table_name="accounts_payables_workflow_requests",
            )
        self.assertEqual(result["workflow_status"], "PENDING_APPROVAL")
        self.assertEqual(engine.begin_count, 1)
        self.assertTrue(engine.disposed)
        self.assertIs(start_in_conn.call_args.args[0], engine.conn)

    def test_approve_workflow_step_accepts_trusted_actor(self):
        with patch.object(workflow_runtime_engine, "_act_on_workflow_step", return_value={"ok": True}) as act:
            workflow_runtime_engine.approve_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={"workflow_request_id": 1},
                execution_adapter=lambda action, payload: {},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(act.call_args.kwargs["acting_user_principal_name"], "approver_x")

    def test_reject_workflow_step_accepts_trusted_actor(self):
        with patch.object(workflow_runtime_engine, "_act_on_workflow_step", return_value={"ok": True}) as act:
            workflow_runtime_engine.reject_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={"workflow_request_id": 1},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(act.call_args.kwargs["acting_user_principal_name"], "approver_x")

    def test_payload_identity_cannot_override_trusted_actor(self):
        engine = FakeEngine()

        with patch.object(workflow_runtime_engine, "db_engine", return_value=engine), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_instance_for_update",
                 return_value={
                     "workflow_instance_id_pk": 1,
                     "legacy_workflow_request_id": 10,
                     "workflow_status": "PENDING_APPROVAL",
                     "workflow_code": "ACCOUNTS_PAYABLE",
                     "workflow_action": "CREATE",
                     "legacy_request_table": "accounts_payables_workflow_requests",
                     "organization_id_fk": 1,
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_current_pending_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 1,
                     "workflow_instance_id_fk": 1,
                     "workflow_node_id_fk": 2,
                     "step_status": "PENDING",
                     "assigned_approver_user_principal_name": "approver_x",
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 1,
                     "workflow_instance_id_fk": 1,
                     "step_status": "PENDING",
                 },
             ):
            result = workflow_runtime_engine.approve_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={
                    "workflow_request_id": 10,
                    "workflow_instance_id": 1,
                    "workflow_instance_step_id": 1,
                    "approver_user_principal_name": "approver_x",
                },
                execution_adapter=lambda action, payload: {},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_y",
            )
        self.assertEqual(result["error_code"], CURRENT_APPROVER_MISMATCH)
        self.assertEqual(result["approver_user_principal_name"], "approver_y")

    def test_supplied_step_id_must_match_current_pending_step(self):
        with patch.object(workflow_runtime_engine, "db_engine", return_value=FakeEngine()), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_instance_for_update",
                 return_value={
                     "workflow_instance_id_pk": 1,
                     "legacy_workflow_request_id": 10,
                     "workflow_status": "PENDING_APPROVAL",
                     "workflow_code": "ACCOUNTS_PAYABLE",
                     "workflow_action": "CREATE",
                     "legacy_request_table": "accounts_payables_workflow_requests",
                     "organization_id_fk": 1,
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_current_pending_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 2,
                     "workflow_node_id_fk": 20,
                     "assigned_approver_user_principal_name": "approver_x",
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 99,
                     "workflow_instance_id_fk": 1,
                     "step_status": "PENDING",
                 },
             ):
            result = workflow_runtime_engine.approve_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={
                    "workflow_instance_id": 1,
                    "workflow_instance_step_id": 99,
                },
                execution_adapter=lambda action, payload: {},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(result["error_code"], WORKFLOW_STEP_NOT_CURRENT)
        self.assertEqual(result["current_workflow_instance_step_id"], 2)

    def test_configured_instance_requires_supplied_step_id(self):
        result = workflow_runtime_engine.approve_workflow_step(
            workflow_code="ACCOUNTS_PAYABLE",
            payload={"workflow_instance_id": 1},
            execution_adapter=lambda action, payload: {},
            legacy_table_name="accounts_payables_workflow_requests",
            acting_user_principal_name="approver_x",
        )
        self.assertEqual(result["error_code"], WORKFLOW_INSTANCE_STEP_ID_REQUIRED)

    def test_request_id_only_configured_instance_requires_step_identity(self):
        with patch.object(workflow_runtime_engine, "db_engine", return_value=FakeEngine()), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_instance_for_update",
                 return_value={
                     "workflow_instance_id_pk": 1,
                     "legacy_workflow_request_id": 10,
                     "workflow_status": "PENDING_APPROVAL",
                     "workflow_code": "ACCOUNTS_PAYABLE",
                     "workflow_action": "CREATE",
                     "legacy_request_table": "accounts_payables_workflow_requests",
                     "organization_id_fk": 1,
                 },
             ), \
             patch.object(workflow_runtime_engine, "_get_current_pending_step_for_update") as current_step:
            result = workflow_runtime_engine.approve_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={"workflow_request_id": 10},
                execution_adapter=lambda action, payload: {},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(result["error_code"], WORKFLOW_INSTANCE_STEP_ID_REQUIRED)
        current_step.assert_not_called()

    def test_wrong_workflow_instance_domain_fails_closed_before_step_action(self):
        with patch.object(workflow_runtime_engine, "db_engine", return_value=FakeEngine()), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_instance_for_update",
                 return_value={
                     "workflow_instance_id_pk": 1,
                     "legacy_workflow_request_id": 10,
                     "workflow_status": "PENDING_APPROVAL",
                     "workflow_code": "FLEET_MANAGEMENT",
                     "workflow_action": "UPDATE",
                     "legacy_request_table": "fleet_management_workflow_requests",
                     "organization_id_fk": 1,
                 },
             ), \
             patch.object(workflow_runtime_engine, "_get_current_pending_step_for_update") as current_step:
            result = workflow_runtime_engine.approve_workflow_step(
                workflow_code="ASSET_MASTER",
                payload={"workflow_instance_id": 1, "workflow_instance_step_id": 2},
                execution_adapter=lambda action, payload: {},
                legacy_table_name="asset_master_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(result["error_code"], WORKFLOW_INSTANCE_TYPE_MISMATCH)
        self.assertEqual(result["actual_workflow_code"], "FLEET_MANAGEMENT")
        current_step.assert_not_called()

    def test_supplied_request_and_instance_ids_must_refer_to_same_transaction(self):
        with patch.object(workflow_runtime_engine, "db_engine", return_value=FakeEngine()), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_instance_for_update",
                 return_value={
                     "workflow_instance_id_pk": 1,
                     "legacy_workflow_request_id": 10,
                     "workflow_status": "PENDING_APPROVAL",
                     "workflow_code": "ACCOUNTS_PAYABLE",
                     "workflow_action": "CREATE",
                     "legacy_request_table": "accounts_payables_workflow_requests",
                     "organization_id_fk": 1,
                 },
             ), \
             patch.object(workflow_runtime_engine, "_get_current_pending_step_for_update") as current_step:
            result = workflow_runtime_engine.approve_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={
                    "workflow_request_id": 99,
                    "workflow_instance_id": 1,
                    "workflow_instance_step_id": 2,
                },
                execution_adapter=lambda action, payload: {},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(result["error_code"], WORKFLOW_IDENTIFIER_MISMATCH)
        current_step.assert_not_called()

    def test_final_execution_adapter_receives_workflow_transaction_connection(self):
        engine = FakeEngine()
        seen = {}

        def adapter(action, payload, conn=None):
            seen["action"] = action
            seen["payload"] = payload
            seen["conn"] = conn
            return {"ap_id_pk": 44}

        with patch.object(workflow_runtime_engine, "db_engine", return_value=engine), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_instance_for_update",
                 return_value={
                     "workflow_instance_id_pk": 1,
                     "legacy_workflow_request_id": 10,
                     "workflow_status": "PENDING_APPROVAL",
                     "workflow_code": "ACCOUNTS_PAYABLE",
                     "workflow_action": "CREATE",
                     "legacy_request_table": "accounts_payables_workflow_requests",
                     "organization_id_fk": 1,
                     "request_payload": {"ap_org_id_fk": 1},
                     "route_snapshot": {"nodes": [{"workflow_node_id_pk": 9, "node_type": "END"}], "edges": []},
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_current_pending_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 2,
                     "workflow_instance_id_fk": 1,
                     "workflow_node_id_fk": 8,
                     "step_sequence": 1,
                     "step_status": "PENDING",
                     "assigned_approver_user_principal_name": "approver_x",
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 2,
                     "workflow_instance_id_fk": 1,
                     "step_status": "PENDING",
                 },
             ), \
             patch.object(workflow_runtime_engine, "user_has_workflow_action", return_value=True), \
             patch.object(workflow_runtime_engine, "_claim_final_execution", return_value=True), \
             patch.object(workflow_runtime_engine, "_mark_step_approved"), \
             patch.object(workflow_runtime_engine, "_mark_instance_terminal") as mark_terminal, \
             patch.object(workflow_runtime_engine, "_update_legacy_terminal"):
            result = workflow_runtime_engine.approve_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={
                    "workflow_request_id": 10,
                    "workflow_instance_id": 1,
                    "workflow_instance_step_id": 2,
                },
                execution_adapter=adapter,
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(result["workflow_status"], "EXECUTED")
        self.assertEqual(seen["action"], "CREATE")
        self.assertIs(seen["conn"], engine.conn)
        self.assertIs(mark_terminal.call_args.args[0], engine.conn)

    def test_configured_rejection_cleans_staged_document(self):
        staged_payload = {
            "workflow_document_staging_status": "STAGED",
            "workflow_document_cleanup_policy": "DELETE_STAGED_DOCUMENT_IF_WORKFLOW_REJECTED_OR_CANCELLED",
            "workflow_document_staged_blob_path": "firebase_upload_files/ap_invoices/_workflow_staging/1/REQ/invoice.pdf",
            "workflow_document_staged_bucket": "bucket",
        }
        cleanup_response = {
            "cleanup_required": True,
            "cleanup_attempted": True,
            "cleanup_completed": True,
        }
        engine = FakeEngine()

        def cleanup_after_commit(payload, terminal_status=None, force=False):
            self.assertTrue(engine.exited)
            self.assertTrue(engine.disposed)
            return cleanup_response

        with patch.object(workflow_runtime_engine, "db_engine", return_value=engine), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_instance_for_update",
                 return_value={
                     "workflow_instance_id_pk": 1,
                     "legacy_workflow_request_id": 10,
                     "workflow_status": "PENDING_APPROVAL",
                     "workflow_code": "ACCOUNTS_PAYABLE",
                     "workflow_action": "CREATE",
                     "legacy_request_table": "accounts_payables_workflow_requests",
                     "organization_id_fk": 1,
                     "request_payload": staged_payload,
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_current_pending_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 2,
                     "workflow_instance_id_fk": 1,
                     "workflow_node_id_fk": 8,
                     "step_sequence": 1,
                     "step_status": "PENDING",
                     "assigned_approver_user_principal_name": "approver_x",
                 },
             ), \
             patch.object(
                 workflow_runtime_engine,
                 "_get_step_for_update",
                 return_value={
                     "workflow_instance_step_id_pk": 2,
                     "workflow_instance_id_fk": 1,
                     "step_status": "PENDING",
                 },
             ), \
             patch.object(workflow_runtime_engine, "user_has_workflow_action", return_value=True), \
             patch.object(workflow_runtime_engine, "_mark_step_rejected"), \
             patch.object(workflow_runtime_engine, "_mark_instance_terminal"), \
             patch.object(workflow_runtime_engine, "_update_legacy_terminal"), \
             patch.object(
                 workflow_runtime_engine,
                 "cleanup_staged_workflow_document",
                 side_effect=cleanup_after_commit,
             ) as cleanup_mock:
            result = workflow_runtime_engine.reject_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={
                    "workflow_request_id": 10,
                    "workflow_instance_id": 1,
                    "workflow_instance_step_id": 2,
                },
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )

        self.assertEqual(result["workflow_status"], "REJECTED")
        self.assertEqual(result["document_cleanup"], cleanup_response)
        cleanup_mock.assert_called_once_with(staged_payload, terminal_status="REJECTED")

    def test_terminal_response_reports_state_without_api_error_marker(self):
        result = workflow_runtime_engine._terminal_response(
            {
                "workflow_instance_id_pk": 1,
                "workflow_status": "EXECUTED",
                "workflow_code": "ASSET_MASTER",
                "workflow_action": "CREATE",
                "legacy_workflow_request_id": 10,
                "request_payload": {},
                "execution_result": {"asset_id_pk": 77},
            }
        )
        self.assertEqual(result["workflow_status"], "EXECUTED")
        self.assertTrue(result["already_terminal"])
        self.assertTrue(result["business_operation_executed"])
        self.assertEqual(result["domain_reference_id"], 77)
        self.assertNotIn("error", result)
        self.assertNotIn("error_code", result)

    def test_legacy_record_without_instance_returns_not_configured(self):
        with patch.object(workflow_runtime_engine, "db_engine", return_value=FakeEngine()), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(workflow_runtime_engine, "_get_instance_for_update", return_value=None), \
             patch.object(workflow_runtime_engine, "_get_legacy_request_link_state", return_value={"workflow_request_id_pk": 1, "workflow_instance_id_fk": None}):
            result = workflow_runtime_engine.reject_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={"workflow_request_id": 1},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(result["error_code"], WORKFLOW_INSTANCE_NOT_CONFIGURED)

    def test_true_missing_request_returns_not_found(self):
        with patch.object(workflow_runtime_engine, "db_engine", return_value=FakeEngine()), \
             patch.object(workflow_runtime_engine, "ensure_workflow_engine_schema"), \
             patch.object(workflow_runtime_engine, "ensure_legacy_workflow_table_schema"), \
             patch.object(workflow_runtime_engine, "_get_instance_for_update", return_value=None), \
             patch.object(workflow_runtime_engine, "_get_legacy_request_link_state", return_value=None):
            result = workflow_runtime_engine.reject_workflow_step(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={"workflow_request_id": 999},
                legacy_table_name="accounts_payables_workflow_requests",
                acting_user_principal_name="approver_x",
            )
        self.assertEqual(result["error_code"], WORKFLOW_INSTANCE_NOT_FOUND)

    def test_missing_workflow_schema_returns_migration_required(self):
        with self.assertRaisesRegex(RuntimeError, WORKFLOW_ENGINE_MIGRATION_REQUIRED):
            ensure_workflow_engine_schema(MissingMigrationConnection())

    def test_explicit_sql_migration_contains_legacy_compatibility_indexes(self):
        ddl_path = Path(__file__).resolve().parents[1] / "data" / "workflow_engine_configurable_routing_ddl.sql"
        ddl = ddl_path.read_text()
        self.assertIn("workflow_instance_id_fk", ddl)
        self.assertIn("workflow_version_id_fk", ddl)
        self.assertIn("routing_source", ddl)
        self.assertIn("idx_%I_instance", ddl)
        self.assertIn("idx_%I_version", ddl)


if __name__ == "__main__":
    unittest.main()
