import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app_backend.services.main import app
from app_backend.services.service_07_alerts_wf_engine.api.workflow_admin_api import (
    WorkflowEdgeRequest,
    WorkflowNodeRequest,
    WorkflowValidateRequest,
    normalize_logic_result,
)
from app_backend.services.service_07_alerts_wf_engine.logic import workflow_admin_logic
from app_backend.services.service_07_alerts_wf_engine import workflow_definition_service
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    LEGACY_WORKFLOW_REQUIRED_COLUMNS,
    REQUIRED_ENGINE_COLUMNS,
    ensure_workflow_engine_schema,
)


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeScalarsResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class FakeColumnResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalarsResult(self.values)


class FakeMappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class FakeRowsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self, draft_row):
        self.draft_row = draft_row
        self.executed = []

    def execute(self, statement, params=None):
        sql = str(statement).strip()
        self.executed.append((sql, params or {}))
        if "from workflow_definition_versions wdv" in sql.lower():
            return FakeMappingResult(self.draft_row)
        if "from workflow_definition_versions" in sql.lower():
            return FakeRowsResult([])
        return FakeMappingResult(None)


class FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, traceback):
        return False

    def dispose(self):
        pass


class SchemaCheckConnection:
    def __init__(self):
        self.sql = []

    def execute(self, statement, params=None):
        sql = str(statement).strip()
        self.sql.append(sql)
        lower_sql = sql.lower()
        params = params or {}
        if "to_regclass" in lower_sql:
            table_name = params["table_name"]
            if table_name.startswith("public.workflow_"):
                return FakeScalarResult(table_name)
            return FakeScalarResult(None)
        if "information_schema.columns" in lower_sql:
            return FakeColumnResult(
                REQUIRED_ENGINE_COLUMNS.get(params["table_name"])
                or LEGACY_WORKFLOW_REQUIRED_COLUMNS.get(params["table_name"])
                or ()
            )
        return FakeScalarResult(None)


class WorkflowAdminCorrectionTests(unittest.TestCase):
    def test_save_draft_maps_workflow_action_to_applies_to_action(self):
        for action in ("CREATE", "UPDATE", "DELETE", "ALL"):
            with self.subTest(action=action), patch(
                "app_backend.services.service_07_alerts_wf_engine.logic.workflow_admin_logic.save_workflow_draft"
            ) as save_workflow_draft:
                save_workflow_draft.return_value = {"success": True, "workflow_version_id": 10}
                workflow_admin_logic.save_draft(
                    "ACCOUNTS_PAYABLE",
                    {
                        "organization_id": 1,
                        "workflow_action": action,
                        "nodes": [],
                        "edges": [],
                    },
                    "admin_a",
                )
                saved_payload = save_workflow_draft.call_args.args[1]
                self.assertEqual(saved_payload["applies_to_action"], action)

    def test_payroll_approval_action_maps_to_applies_to_action(self):
        with patch(
            "app_backend.services.service_07_alerts_wf_engine.logic.workflow_admin_logic.save_workflow_draft"
        ) as save_workflow_draft:
            save_workflow_draft.return_value = {"success": True, "workflow_version_id": 11}
            workflow_admin_logic.save_draft(
                "PAYROLL",
                {
                    "organization_id": 1,
                    "workflow_action": "APPROVAL",
                    "nodes": [],
                    "edges": [],
                },
                "admin_a",
            )
            saved_payload = save_workflow_draft.call_args.args[1]
            self.assertEqual(saved_payload["applies_to_action"], "APPROVAL")

    def test_definition_retrieval_maps_workflow_action_to_applies_to_action(self):
        with patch(
            "app_backend.services.service_07_alerts_wf_engine.logic.workflow_admin_logic.get_workflow_definition_payload"
        ) as get_workflow_definition_payload:
            get_workflow_definition_payload.return_value = {}
            workflow_admin_logic.get_definition("ACCOUNTS_PAYABLE", 1, "CREATE")
            self.assertEqual(
                get_workflow_definition_payload.call_args.kwargs["applies_to_action"],
                "CREATE",
            )

    def test_publish_maps_workflow_action_to_applies_to_action(self):
        with patch(
            "app_backend.services.service_07_alerts_wf_engine.logic.workflow_admin_logic.publish_workflow_draft"
        ) as publish_workflow_draft:
            publish_workflow_draft.return_value = {"success": True, "workflow_version_id": 10}
            workflow_admin_logic.publish_draft(
                "ACCOUNTS_PAYABLE",
                {
                    "organization_id": 1,
                    "workflow_action": "CREATE",
                    "workflow_version_id": 10,
                },
                "admin_a",
            )
            publish_payload = publish_workflow_draft.call_args.args[1]
            self.assertEqual(publish_payload["applies_to_action"], "CREATE")

    def test_version_list_maps_workflow_action_to_applies_to_action_filter(self):
        draft_row = {"workflow_definition_id_pk": 1}
        conn = FakeConnection(draft_row)
        with patch.object(workflow_admin_logic, "db_engine", return_value=FakeEngine(conn)), \
             patch.object(workflow_admin_logic, "ensure_workflow_engine_schema"), \
             patch.object(workflow_admin_logic, "_get_definition_any", return_value=draft_row):
            workflow_admin_logic.get_versions("ACCOUNTS_PAYABLE", 1, "CREATE")
        version_queries = [
            params
            for sql, params in conn.executed
            if "from workflow_definition_versions" in sql.lower()
        ]
        self.assertEqual(version_queries[0]["workflow_action"], "CREATE")

    def test_publish_version_id_is_only_an_assertion(self):
        draft_row = {
            "workflow_version_id_pk": 10,
            "workflow_definition_id_fk": 1,
            "workflow_code": "ACCOUNTS_PAYABLE",
            "organization_id_fk": 1,
            "applies_to_action": "CREATE",
            "version_status": "DRAFT",
            "allow_self_approval": False,
        }
        conn = FakeConnection(draft_row)
        with patch.object(workflow_definition_service, "db_engine", return_value=FakeEngine(conn)), \
             patch.object(workflow_definition_service, "ensure_workflow_engine_schema"), \
             patch.object(workflow_definition_service, "_nodes_for_validation", return_value=[]), \
             patch.object(workflow_definition_service, "_edges_for_validation", return_value=[]), \
             patch.object(workflow_definition_service, "validate_workflow_graph", return_value=[]):
            result = workflow_definition_service.publish_workflow_draft(
                "ACCOUNTS_PAYABLE",
                {
                    "organization_id": 1,
                    "workflow_action": "CREATE",
                    "applies_to_action": "CREATE",
                    "workflow_version_id": 99,
                    "published_by": "admin_a",
                },
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "WORKFLOW_VERSION_CONFLICT")
        self.assertFalse(any("update workflow_definition_versions" in sql.lower() for sql, _ in conn.executed))

    def test_publish_without_version_id_resolves_current_scoped_draft(self):
        draft_row = {
            "workflow_version_id_pk": 10,
            "workflow_definition_id_fk": 1,
            "workflow_code": "ACCOUNTS_PAYABLE",
            "organization_id_fk": 1,
            "applies_to_action": "CREATE",
            "version_status": "DRAFT",
            "allow_self_approval": False,
        }
        conn = FakeConnection(draft_row)
        with patch.object(workflow_definition_service, "db_engine", return_value=FakeEngine(conn)), \
             patch.object(workflow_definition_service, "ensure_workflow_engine_schema"), \
             patch.object(workflow_definition_service, "_nodes_for_validation", return_value=[]), \
             patch.object(workflow_definition_service, "_edges_for_validation", return_value=[]), \
             patch.object(workflow_definition_service, "validate_workflow_graph", return_value=[]):
            result = workflow_definition_service.publish_workflow_draft(
                "ACCOUNTS_PAYABLE",
                {
                    "organization_id": 1,
                    "workflow_action": "CREATE",
                    "applies_to_action": "CREATE",
                    "published_by": "admin_a",
                },
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["workflow_version_id"], 10)
        publish_updates = [
            params
            for sql, params in conn.executed
            if "set\n                        version_status = 'published'" in sql.lower()
        ]
        self.assertEqual(publish_updates[0]["workflow_version_id"], 10)

    def test_validate_request_rejects_one_sided_graph_payloads(self):
        node = WorkflowNodeRequest(
            node_key="requester_a",
            node_type="REQUESTER",
            user_principal_name="user_a",
            canvas_x=1,
            canvas_y=2,
        )
        edge = WorkflowEdgeRequest(
            source_node_key="requester_a",
            target_node_key="end",
        )
        with self.assertRaises(ValidationError):
            WorkflowValidateRequest(
                organization_id=1,
                workflow_action="CREATE",
                nodes=[node],
            )
        with self.assertRaises(ValidationError):
            WorkflowValidateRequest(
                organization_id=1,
                workflow_action="CREATE",
                edges=[edge],
            )
        self.assertIsNotNone(WorkflowValidateRequest(organization_id=1, workflow_action="CREATE"))
        self.assertIsNotNone(
            WorkflowValidateRequest(
                organization_id=1,
                workflow_action="CREATE",
                nodes=[node],
                edges=[edge],
            )
        )

    def test_success_response_has_single_outer_success_envelope(self):
        response = normalize_logic_result({"success": True, "workflow_version_id": 10})
        self.assertEqual(response, {"success": True, "data": {"workflow_version_id": 10}})

    def test_schema_readiness_check_performs_no_ddl(self):
        conn = SchemaCheckConnection()
        ensure_workflow_engine_schema(conn)
        ddl = ("create table", "alter table", "create index")
        self.assertFalse(any(sql.lower().startswith(ddl) for sql in conn.sql))

    def test_consolidated_workflow_engine_routes_are_not_duplicated(self):
        route_counts = {}
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if not path.startswith("/api/v1/workflow-engine"):
                continue
            for method in methods:
                route_counts[(method, path)] = route_counts.get((method, path), 0) + 1
        duplicates = [route for route, count in route_counts.items() if count > 1]
        self.assertEqual(duplicates, [])
        self.assertEqual(len(route_counts), 14)
        self.assertIn(("POST", "/api/v1/workflow-engine/instances/{workflow_instance_id}/attachments/{attachment_id}/download"), route_counts)


if __name__ == "__main__":
    unittest.main()
