import unittest
from unittest.mock import patch

from app_backend.services.service_07_alerts_wf_engine.workflow_catalog import (
    get_workflow_catalog_entry,
    validate_workflow_action,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_validation import (
    validate_workflow_graph,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine import (
    _next_approver_from_snapshot,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine import (
    _clean_workflow_payload,
    _find_existing_pending_replay,
    _find_pending_domain_conflict,
    _idempotency_identity,
    _lock_pending_domain_conflict,
    _resolve_route_for_requester,
    _start_workflow_in_conn,
    strip_internal_flags,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    assert_legacy_workflow_table_allowed,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    is_trusted_workflow_execution,
    trusted_workflow_execution,
)


class EmptyMappingResult:
    def mappings(self):
        return self

    def first(self):
        return None


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return EmptyMappingResult()


class WorkflowEngineCoreTests(unittest.TestCase):
    def test_catalog_registers_required_workflows(self):
        expected_codes = {
            "ASSET_MASTER",
            "CONTRACTS_MANAGEMENT",
            "FLEET_MANAGEMENT",
            "PAYROLL",
            "ACCOUNTS_PAYABLE",
            "ACCOUNTS_RECEIVABLE",
        }
        for workflow_code in expected_codes:
            self.assertIsNotNone(get_workflow_catalog_entry(workflow_code))

    def test_fleet_rejects_unsupported_actions(self):
        valid, error = validate_workflow_action("FLEET_MANAGEMENT", "APPROVAL")
        self.assertFalse(valid)
        self.assertEqual(error, "UNSUPPORTED_WORKFLOW_ACTION")

    def test_all_is_not_valid_runtime_action(self):
        valid, error = validate_workflow_action("ACCOUNTS_PAYABLE", "ALL")
        self.assertFalse(valid)
        self.assertEqual(error, "UNSUPPORTED_WORKFLOW_ACTION")

    def test_strip_internal_workflow_flags(self):
        payload = {
            "user_principal_name": "user_a",
            "_accounts_payables_workflow_approved": True,
        }
        self.assertEqual(strip_internal_flags(payload), {"user_principal_name": "user_a"})

    def test_clean_workflow_payload_removes_ephemeral_file_fields(self):
        payload = {
            "user_principal_name": "user_a",
            "cont_link_path": "gs://bucket/contracts/1.pdf",
            "file_path": "/tmp/local.pdf",
            "contract_file_path": "/tmp/local-contract.pdf",
            "content_type": "application/pdf",
            "_contracts_management_workflow_approved": True,
        }
        self.assertEqual(
            _clean_workflow_payload(payload),
            {
                "user_principal_name": "user_a",
                "cont_link_path": "gs://bucket/contracts/1.pdf",
            },
        )

    def test_trusted_execution_context_is_server_scoped(self):
        self.assertFalse(is_trusted_workflow_execution())
        with trusted_workflow_execution():
            self.assertTrue(is_trusted_workflow_execution())
        self.assertFalse(is_trusted_workflow_execution())

    def test_resolve_sequential_route_for_requester(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"workflow_node_id_pk": 3, "node_type": "APPROVER", "user_principal_name": "approver_y"},
            {"workflow_node_id_pk": 4, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 2, "target_node_id_fk": 3},
            {"source_node_id_fk": 3, "target_node_id_fk": 4},
        ]
        route = _resolve_route_for_requester(nodes, edges, "user_a", False)
        self.assertNotIn("error", route)
        self.assertEqual(
            [node["user_principal_name"] for node in route["approver_nodes"]],
            ["approver_x", "approver_y"],
        )

    def test_rejects_ambiguous_route(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"workflow_node_id_pk": 3, "node_type": "APPROVER", "user_principal_name": "approver_y"},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 1, "target_node_id_fk": 3},
        ]
        route = _resolve_route_for_requester(nodes, edges, "user_a", False)
        self.assertEqual(route["error"], "UNSUPPORTED_BRANCHING")

    def test_no_line_manager_override_in_route_resolution(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"workflow_node_id_pk": 3, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 2, "target_node_id_fk": 3},
        ]
        route = _resolve_route_for_requester(nodes, edges, "user_a", False)
        self.assertEqual(route["approver_nodes"][0]["user_principal_name"], "approver_x")
        self.assertNotEqual(route["approver_nodes"][0]["user_principal_name"], "manager_m")

    def test_missing_requester_route_fails_closed(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "user_b"},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"workflow_node_id_pk": 3, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 2, "target_node_id_fk": 3},
        ]
        route = _resolve_route_for_requester(nodes, edges, "user_a", False)
        self.assertEqual(route["error"], "REQUESTER_ROUTE_NOT_FOUND")

    def test_wildcard_requester_route_is_used_when_exact_requester_absent(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": None},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"workflow_node_id_pk": 3, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 2, "target_node_id_fk": 3},
        ]
        route = _resolve_route_for_requester(nodes, edges, "user_a", False)
        self.assertNotIn("error", route)
        self.assertEqual(route["path"][0]["workflow_node_id_pk"], 1)
        self.assertEqual(route["approver_nodes"][0]["user_principal_name"], "approver_x")

    def test_exact_requester_route_takes_precedence_over_wildcard(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": None},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "wildcard_approver"},
            {"workflow_node_id_pk": 3, "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"workflow_node_id_pk": 4, "node_type": "APPROVER", "user_principal_name": "exact_approver"},
            {"workflow_node_id_pk": 5, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 2, "target_node_id_fk": 5},
            {"source_node_id_fk": 3, "target_node_id_fk": 4},
            {"source_node_id_fk": 4, "target_node_id_fk": 5},
        ]
        route = _resolve_route_for_requester(nodes, edges, "user_a", False)
        self.assertNotIn("error", route)
        self.assertEqual(route["path"][0]["workflow_node_id_pk"], 3)
        self.assertEqual(route["approver_nodes"][0]["user_principal_name"], "exact_approver")

    def test_explicit_self_approval_requires_policy_flag(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "user_a"},
            {"workflow_node_id_pk": 3, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 2, "target_node_id_fk": 3},
        ]
        self.assertEqual(
            _resolve_route_for_requester(nodes, edges, "user_a", False)["error"],
            "SELF_APPROVAL_NOT_ALLOWED",
        )
        self.assertNotIn("error", _resolve_route_for_requester(nodes, edges, "user_a", True))

    def test_next_approver_uses_route_snapshot(self):
        route_snapshot = {
            "nodes": [
                {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "user_a"},
                {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_x"},
                {"workflow_node_id_pk": 3, "node_type": "APPROVER", "user_principal_name": "approver_y"},
                {"workflow_node_id_pk": 4, "node_type": "END", "user_principal_name": None},
            ],
            "edges": [
                {"source_node_id_fk": 1, "target_node_id_fk": 2},
                {"source_node_id_fk": 2, "target_node_id_fk": 3},
                {"source_node_id_fk": 3, "target_node_id_fk": 4},
            ],
        }
        next_approver = _next_approver_from_snapshot(route_snapshot, 2)
        self.assertEqual(next_approver["user_principal_name"], "approver_y")
        self.assertIsNone(_next_approver_from_snapshot(route_snapshot, 3))

    def test_validation_rejects_branching(self):
        nodes = [
            {"node_key": "requester_a", "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"node_key": "approver_y", "node_type": "APPROVER", "user_principal_name": "approver_y"},
            {"node_key": "end", "node_type": "END"},
        ]
        edges = [
            {"source_node_key": "requester_a", "target_node_key": "approver_x"},
            {"source_node_key": "requester_a", "target_node_key": "approver_y"},
        ]
        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action", return_value=True):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", False, nodes, edges)
        self.assertIn("UNSUPPORTED_BRANCHING", {error["code"] for error in errors})

    def test_validation_rejects_disconnected_approver(self):
        nodes = [
            {"node_key": "requester_a", "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"node_key": "approver_y", "node_type": "APPROVER", "user_principal_name": "approver_y"},
            {"node_key": "end", "node_type": "END"},
        ]
        edges = [
            {"source_node_key": "requester_a", "target_node_key": "approver_x"},
            {"source_node_key": "approver_x", "target_node_key": "end"},
        ]
        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action", return_value=True):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", False, nodes, edges)
        self.assertIn("UNREACHABLE_APPROVER", {error["code"] for error in errors})

    def test_validation_rejects_duplicate_requester_identity(self):
        nodes = [
            {"node_key": "requester_a", "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"node_key": "requester_a_again", "node_type": "REQUESTER", "user_principal_name": "USER_A"},
            {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"node_key": "end", "node_type": "END"},
        ]
        edges = [
            {"source_node_key": "requester_a", "target_node_key": "approver_x"},
            {"source_node_key": "requester_a_again", "target_node_key": "approver_x"},
            {"source_node_key": "approver_x", "target_node_key": "end"},
        ]
        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action", return_value=True):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", False, nodes, edges)
        self.assertIn("DUPLICATE_REQUESTER_IDENTITY", {error["code"] for error in errors})

    def test_validation_allows_single_wildcard_requester(self):
        nodes = [
            {"node_key": "requester_any", "node_type": "REQUESTER", "user_principal_name": None},
            {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"node_key": "end", "node_type": "END"},
        ]
        edges = [
            {"source_node_key": "requester_any", "target_node_key": "approver_x"},
            {"source_node_key": "approver_x", "target_node_key": "end"},
        ]
        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch(
                 "app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action",
                 side_effect=lambda conn, upn, action, org: action != "SUBMIT",
             ):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", False, nodes, edges)
        self.assertNotIn("REQUESTER_USER_INVALID", {error["code"] for error in errors})
        self.assertNotIn("REQUESTER_SUBMIT_PERMISSION_MISSING", {error["code"] for error in errors})
        self.assertNotIn("SELF_APPROVAL_NOT_ALLOWED", {error["code"] for error in errors})

    def test_validation_rejects_wildcard_requester_self_approval_risk(self):
        nodes = [
            {"node_key": "requester_any", "node_type": "REQUESTER", "user_principal_name": None},
            {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"node_key": "end", "node_type": "END"},
        ]
        edges = [
            {"source_node_key": "requester_any", "target_node_key": "approver_x"},
            {"source_node_key": "approver_x", "target_node_key": "end"},
        ]
        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action", return_value=True):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", False, nodes, edges)
        self.assertIn("SELF_APPROVAL_NOT_ALLOWED", {error["code"] for error in errors})

        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action", return_value=True):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", True, nodes, edges)
        self.assertNotIn("SELF_APPROVAL_NOT_ALLOWED", {error["code"] for error in errors})

    def test_validation_rejects_multiple_wildcard_requesters(self):
        nodes = [
            {"node_key": "requester_any_1", "node_type": "REQUESTER", "user_principal_name": None},
            {"node_key": "requester_any_2", "node_type": "REQUESTER", "user_principal_name": None},
            {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"node_key": "end", "node_type": "END"},
        ]
        edges = [
            {"source_node_key": "requester_any_1", "target_node_key": "approver_x"},
            {"source_node_key": "requester_any_2", "target_node_key": "approver_x"},
            {"source_node_key": "approver_x", "target_node_key": "end"},
        ]
        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action", return_value=True):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", False, nodes, edges)
        self.assertIn("DUPLICATE_WILDCARD_REQUESTER", {error["code"] for error in errors})

    def test_validation_rejects_unsupported_edge_shape(self):
        nodes = [
            {"node_key": "requester_a", "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"node_key": "end", "node_type": "END"},
        ]
        edges = [
            {"source_node_key": "requester_a", "target_node_key": "approver_x", "edge_type": "CONDITIONAL", "condition_json": {"amount_gt": 100}},
            {"source_node_key": "approver_x", "target_node_key": "end"},
            {"source_node_key": "end", "target_node_key": "approver_x"},
        ]
        with patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.organization_exists", return_value=True), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.get_active_user", return_value={"user_principal_name": "x"}), \
             patch("app_backend.services.service_07_alerts_wf_engine.workflow_validation.user_has_workflow_action", return_value=True):
            errors = validate_workflow_graph(None, "ACCOUNTS_PAYABLE", 1, "CREATE", False, nodes, edges)
        error_codes = {error["code"] for error in errors}
        self.assertIn("UNSUPPORTED_EDGE_TYPE", error_codes)
        self.assertIn("UNSUPPORTED_EDGE_CONDITION", error_codes)
        self.assertIn("END_NODE_HAS_OUTGOING_EDGE", error_codes)

    def test_runtime_rejects_unsupported_edge_metadata(self):
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "user_a"},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_x"},
            {"workflow_node_id_pk": 3, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2, "edge_type": "CONDITIONAL"},
            {"source_node_id_fk": 2, "target_node_id_fk": 3},
        ]
        self.assertEqual(
            _resolve_route_for_requester(nodes, edges, "user_a", False)["error"],
            "UNSUPPORTED_EDGE_TYPE",
        )

    def test_legacy_table_allowlist_rejects_dynamic_table_names(self):
        assert_legacy_workflow_table_allowed("payroll_workflow_requests")
        with self.assertRaises(ValueError):
            assert_legacy_workflow_table_allowed("payroll_workflow_requests; drop table user_master")

    def test_submit_helper_rejects_authenticated_principal_mismatch(self):
        from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
            submit_configured_workflow,
        )

        response = submit_configured_workflow(
            workflow_code="PAYROLL",
            workflow_action="APPROVAL",
            payload={"user_principal_name": "body_user"},
            organization_id=1,
            legacy_table_name="payroll_workflow_requests",
            authenticated_user_principal_name="token_user",
        )
        self.assertEqual(response["error_code"], "AUTHENTICATED_PRINCIPAL_MISMATCH")

    def test_workflow_adapter_scopes_configured_approval_to_authenticated_org(self):
        from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
            approve_configured_or_legacy_workflow,
        )

        with patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers.approve_workflow_step",
            return_value={"error_code": "WORKFLOW_INSTANCE_NOT_FOUND"},
        ) as approve_mock:
            response = approve_configured_or_legacy_workflow(
                workflow_code="ACCOUNTS_PAYABLE",
                payload={
                    "workflow_request_id": 10,
                    "acting_user_principal_name": "approver@example.com",
                    "authenticated_org_id": 3,
                    "ap_org_id_fk": 3,
                },
                legacy_table_name="accounts_payables_workflow_requests",
                execution_adapter=lambda action, request_payload: {},
                legacy_organization_field_names=("ap_org_id_fk",),
            )

        self.assertEqual(response["error_code"], "WORKFLOW_INSTANCE_NOT_FOUND")
        self.assertEqual(approve_mock.call_args.kwargs["organization_id"], 3)

    def test_update_delete_idempotency_identity_uses_domain_reference(self):
        identity = _idempotency_identity(
            "ASSET_MASTER",
            "UPDATE",
            1,
            "requester_a",
            {"asset_id": 99, "user_principal_name": "requester_a"},
        )
        self.assertEqual(identity["domain_reference_id"], 99)
        self.assertIn("domain_reference:99", identity["lock_key"])

    def test_explicit_idempotency_replay_is_scoped_to_requester(self):
        identity = _idempotency_identity(
            "ASSET_MASTER",
            "UPDATE",
            1,
            "requester_a",
            {
                "asset_id": 99,
                "service_request_id": "REQ-123",
                "user_principal_name": "requester_a",
            },
        )
        self.assertIn("requester:requester_a:service_request:REQ-123", identity["lock_key"])

        conn = RecordingConnection()
        replay = _find_existing_pending_replay(
            conn,
            "ASSET_MASTER",
            "UPDATE",
            1,
            "requester_a",
            {
                "asset_id": 99,
                "service_request_id": "REQ-123",
                "user_principal_name": "requester_a",
            },
            identity,
        )
        self.assertIsNone(replay)
        self.assertIn(
            "lower(wi.requester_user_principal_name) = lower(:requester_user_principal_name)",
            conn.statements[0][0],
        )

    def test_domain_reference_without_explicit_key_is_conflict_not_replay(self):
        identity = _idempotency_identity(
            "ASSET_MASTER",
            "UPDATE",
            1,
            "requester_a",
            {"asset_id": 99, "user_principal_name": "requester_a"},
        )
        replay_conn = RecordingConnection()
        replay = _find_existing_pending_replay(
            replay_conn,
            "ASSET_MASTER",
            "UPDATE",
            1,
            "requester_a",
            {"asset_id": 99, "user_principal_name": "requester_a"},
            identity,
        )
        self.assertIsNone(replay)
        self.assertEqual(replay_conn.statements, [])

        conflict_conn = RecordingConnection()
        conflict = _find_pending_domain_conflict(
            conflict_conn,
            "ASSET_MASTER",
            "UPDATE",
            1,
            identity,
        )
        self.assertIsNone(conflict)
        self.assertIn("asset_id", conflict_conn.statements[0][0])

    def test_domain_reference_uses_second_advisory_lock(self):
        identity = _idempotency_identity(
            "ASSET_MASTER",
            "UPDATE",
            1,
            "requester_a",
            {
                "asset_id": 99,
                "service_request_id": "REQ-123",
                "user_principal_name": "requester_a",
            },
        )
        conn = RecordingConnection()
        _lock_pending_domain_conflict(conn, "ASSET_MASTER", "UPDATE", 1, identity)

        self.assertEqual(len(conn.statements), 1)
        self.assertIn("pg_advisory_xact_lock", conn.statements[0][0])
        self.assertEqual(
            conn.statements[0][1]["lock_namespace"],
            "UETransportERP:workflow_domain_conflict",
        )
        self.assertEqual(conn.statements[0][1]["lock_key"], "ASSET_MASTER:UPDATE:1:99")

    def test_no_domain_reference_skips_second_advisory_lock(self):
        identity = _idempotency_identity(
            "ASSET_MASTER",
            "CREATE",
            1,
            "requester_a",
            {"asset_name": "A", "user_principal_name": "requester_a"},
        )
        conn = RecordingConnection()
        _lock_pending_domain_conflict(conn, "ASSET_MASTER", "CREATE", 1, identity)
        self.assertEqual(conn.statements, [])

    def test_start_workflow_locks_domain_before_conflict_search(self):
        call_order = []
        nodes = [
            {"workflow_node_id_pk": 1, "node_type": "REQUESTER", "user_principal_name": "requester_a"},
            {"workflow_node_id_pk": 2, "node_type": "APPROVER", "user_principal_name": "approver_a"},
            {"workflow_node_id_pk": 3, "node_type": "END", "user_principal_name": None},
        ]
        edges = [
            {"source_node_id_fk": 1, "target_node_id_fk": 2},
            {"source_node_id_fk": 2, "target_node_id_fk": 3},
        ]

        def lock_domain(*args, **kwargs):
            call_order.append("domain_lock")

        def find_conflict(*args, **kwargs):
            call_order.append("conflict_lookup")
            return {"workflow_instance_id_pk": 55}

        with patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.ensure_workflow_engine_schema"
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.ensure_legacy_workflow_table_schema"
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.get_active_user",
            return_value={"user_principal_name": "requester_a"},
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.user_has_workflow_action",
            return_value=True,
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.get_workflow_definition",
            return_value={"workflow_definition_id_pk": 10},
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.get_published_version",
            return_value={
                "workflow_version_id_pk": 20,
                "version_number": 1,
                "applies_to_action": "UPDATE",
                "allow_self_approval": False,
            },
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.get_nodes",
            return_value=nodes,
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine.get_edges",
            return_value=edges,
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine._lock_idempotent_submission"
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine._find_existing_pending_replay",
            return_value=None,
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine._lock_pending_domain_conflict",
            side_effect=lock_domain,
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine._find_pending_domain_conflict",
            side_effect=find_conflict,
        ), patch(
            "app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine._pending_conflict_response",
            return_value={"error_code": "WORKFLOW_PENDING_CONFLICT"},
        ):
            result = _start_workflow_in_conn(
                object(),
                "ASSET_MASTER",
                "UPDATE",
                1,
                "requester_a",
                {
                    "asset_id": 99,
                    "service_request_id": "REQ-123",
                    "user_principal_name": "requester_a",
                },
                "asset_master_workflow_requests",
                {},
            )

        self.assertEqual(result["error_code"], "WORKFLOW_PENDING_CONFLICT")
        self.assertEqual(call_order, ["domain_lock", "conflict_lookup"])

    def test_adapters_do_not_accept_client_internal_flags(self):
        import app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf as ap_wf

        payload = {
            "user_principal_name": "user_a",
            "ap_org_id_fk": 1,
            "_accounts_payables_workflow_approved": True,
        }
        with patch.object(ap_wf, "verify_accounts_payables_workflow_table"), \
             patch.object(ap_wf, "submit_configured_workflow", return_value={"workflow_confirmation": "N", "workflow_status": "PENDING_APPROVAL"}) as submit_mock:
            response = ap_wf.trigger_accounts_payables_workflow(payload, "CREATE")
        self.assertEqual(response["workflow_confirmation"], "N")
        submit_mock.assert_called_once()

    def test_trusted_execution_short_circuits_before_schema_verification(self):
        import app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf as ap_wf
        import app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf as ar_wf
        import app_backend.services.service_07_alerts_wf_engine.asset_master_wf as asset_wf
        import app_backend.services.service_07_alerts_wf_engine.contracts_management_wf as contract_wf
        import app_backend.services.service_07_alerts_wf_engine.fleet_management_wf as fleet_wf

        cases = [
            (asset_wf, "verify_asset_master_workflow_table", "_asset_master_workflow_approved"),
            (fleet_wf, "ensure_fleet_management_workflow_table", "_fleet_management_workflow_approved"),
            (contract_wf, "verify_contracts_management_workflow_table", "_contracts_management_workflow_approved"),
            (ap_wf, "verify_accounts_payables_workflow_table", "_accounts_payables_workflow_approved"),
            (ar_wf, "verify_accounts_receivables_workflow_table", "_accounts_receivables_workflow_approved"),
        ]
        for module, verify_name, flag_name in cases:
            with self.subTest(module=module.__name__), \
                 trusted_workflow_execution(), \
                 patch.object(module, verify_name, side_effect=AssertionError("schema call not allowed")), \
                 patch.object(module, "submit_configured_workflow") as submit_mock:
                response = module.__dict__[f"trigger_{module.WORKFLOW_REQUEST_TABLE.removesuffix('_workflow_requests')}_workflow"](
                    {
                        "user_principal_name": "requester_a",
                        flag_name: True,
                    },
                    "create",
                )
            self.assertEqual(response["workflow_confirmation"], "Y")
            self.assertEqual(response["workflow_action"], "CREATE")
            submit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
