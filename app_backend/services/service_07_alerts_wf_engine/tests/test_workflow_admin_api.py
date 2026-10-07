import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app_backend.services.main import app
from app_backend.services.service_02_hr_payroll.api import main as hr_payroll_api
from app_backend.services.service_07_alerts_wf_engine.api import workflow_admin_api
from app_backend.services.service_07_alerts_wf_engine.api import main as workflow_service_api


AUTH_CONTEXT = {
    "authenticated": True,
    "user": {
        "user_principal_name": "admin_a",
        "user_org_id_fk": 1,
    },
    "organization": {
        "org_id": 1,
    },
}


class WorkflowAdminApiTests(unittest.TestCase):
    def test_required_workflow_engine_routes_are_registered(self):
        required = {
            ("GET", "/api/v1/workflow-engine/workflows"),
            ("GET", "/api/v1/workflow-engine/workflows/{workflow_code}/eligible-users"),
            ("GET", "/api/v1/workflow-engine/workflows/{workflow_code}/definition"),
            ("PUT", "/api/v1/workflow-engine/workflows/{workflow_code}/draft"),
            ("POST", "/api/v1/workflow-engine/workflows/{workflow_code}/validate"),
            ("POST", "/api/v1/workflow-engine/workflows/{workflow_code}/publish"),
            ("GET", "/api/v1/workflow-engine/workflows/{workflow_code}/versions"),
            ("GET", "/api/v1/workflow-engine/workflows/{workflow_code}/versions/{workflow_version_id}"),
            ("PATCH", "/api/v1/workflow-engine/workflows/{workflow_code}/status"),
            ("GET", "/api/v1/workflow-engine/instances"),
            ("GET", "/api/v1/workflow-engine/instances/{workflow_instance_id}"),
            ("GET", "/api/v1/workflow-engine/instances/{workflow_instance_id}/steps"),
            ("GET", "/api/v1/workflow-engine/inbox"),
        }
        actual = {
            (method, path)
            for path, item in app.openapi()["paths"].items()
            for method in (verb.upper() for verb in item.keys())
        }
        self.assertTrue(required.issubset(actual))

    def test_catalog_requires_token(self):
        with self.assertRaises(HTTPException) as exc:
            workflow_admin_api.get_authenticated_workflow_context(None)
        self.assertEqual(exc.exception.status_code, 401)

    def test_catalog_rejects_user_without_workflow_admin(self):
        with patch(
            "app_backend.services.service_07_alerts_wf_engine.api.workflow_admin_api.has_workflow_admin",
            return_value=False,
        ):
            with self.assertRaises(HTTPException) as exc:
                workflow_admin_api.require_workflow_admin(AUTH_CONTEXT)
        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail, "WORKFLOW_ADMIN_PERMISSION_REQUIRED")

    def test_cross_organization_admin_access_is_denied(self):
        with self.assertRaises(HTTPException) as exc:
            workflow_admin_api.require_same_organization(AUTH_CONTEXT, 2)
        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail, "WORKFLOW_ORGANIZATION_ACCESS_DENIED")

    def test_catalog_returns_all_six_authoritative_workflows(self):
        with patch(
            "app_backend.services.service_07_alerts_wf_engine.api.workflow_admin_api.has_workflow_admin",
            return_value=True,
        ):
            response = workflow_admin_api.get_workflow_catalog_endpoint(AUTH_CONTEXT)
        self.assertTrue(response["success"])
        data = response["data"]
        by_code = {item["workflow_code"]: item for item in data}
        self.assertEqual(set(by_code), {
            "ASSET_MASTER",
            "CONTRACTS_MANAGEMENT",
            "FLEET_MANAGEMENT",
            "PAYROLL",
            "ACCOUNTS_PAYABLE",
            "ACCOUNTS_RECEIVABLE",
        })
        self.assertEqual(by_code["ACCOUNTS_PAYABLE"]["supported_actions"], [
            "CREATE",
            "UPDATE",
            "DELETE",
        ])

    def test_service_specific_approval_models_accept_configured_instance_and_step_ids(self):
        payload_classes = [
            workflow_service_api.FleetWorkflowApprovalPayload,
            workflow_service_api.FleetWorkflowRejectPayload,
            workflow_service_api.PayrollWorkflowApprovalPayload,
            workflow_service_api.PayrollWorkflowRejectPayload,
            workflow_service_api.ContractsWorkflowApprovalPayload,
            workflow_service_api.ContractsWorkflowRejectPayload,
            workflow_service_api.AssetMasterWorkflowApprovalPayload,
            workflow_service_api.AssetMasterWorkflowRejectPayload,
            workflow_service_api.AccountsPayableWorkflowApprovalPayload,
            workflow_service_api.AccountsPayableWorkflowRejectPayload,
            workflow_service_api.AccountsReceivableWorkflowApprovalPayload,
            workflow_service_api.AccountsReceivableWorkflowRejectPayload,
            hr_payroll_api.PayrollWorkflowApprovalPayload,
            hr_payroll_api.PayrollWorkflowRejectPayload,
        ]
        for payload_class in payload_classes:
            with self.subTest(payload_class=payload_class.__name__):
                payload = payload_class(
                    workflow_instance_id=100,
                    workflow_instance_id_pk=101,
                    workflow_instance_step_id=200,
                    workflow_instance_step_id_pk=201,
                    workflow_request_id=300,
                    workflow_request_id_pk=301,
                )
                data = payload.model_dump()
                self.assertEqual(data["workflow_instance_id"], 100)
                self.assertEqual(data["workflow_instance_id_pk"], 101)
                self.assertEqual(data["workflow_instance_step_id"], 200)
                self.assertEqual(data["workflow_instance_step_id_pk"], 201)
                self.assertEqual(data["workflow_request_id"], 300)
                self.assertEqual(data["workflow_request_id_pk"], 301)


if __name__ == "__main__":
    unittest.main()
