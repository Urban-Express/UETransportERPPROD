import json
import unittest
from io import BytesIO
from unittest.mock import patch

from app_backend.services.service_06_contracts_management.api import main as contracts_api
from app_backend.services.service_08_financial_management.api import main as financial_api
from app_backend.services.main import app as consolidated_app


AUTH_CONTEXT = {
    "authenticated": True,
    "user": {
        "user_id": 901,
        "user_principal_name": "api.workflow.user@example.com",
        "user_org_id_fk": 77,
    },
    "organization": {
        "org_id": 77,
        "org_name": "API Workflow Test",
    },
}


def pending_response(workflow_code, action):
    return {
        "message": f"{workflow_code} submitted for approval.",
        "business_operation_executed": False,
        "workflow_required": True,
        "workflow_code": workflow_code,
        "workflow_action": action,
        "workflow_status": "PENDING_APPROVAL",
        "workflow": {
            "workflow_code": workflow_code,
            "workflow_action": action,
            "workflow_status": "PENDING_APPROVAL",
            "workflow_request_id": 12345,
        },
    }


class FakeUploadFile:
    def __init__(self, file_name, content, content_type):
        self.file = BytesIO(content)
        self.filename = file_name
        self.content_type = content_type


class WorkflowSafeDocumentApiTests(unittest.TestCase):
    def contract_payload(self):
        return {
            "user_principal_name": "api.workflow.user@example.com",
            "cont_org_id_fk": 77,
            "cont_cust_id_fk": 10,
            "cont_dep_id_fk": 20,
            "cont_contract_number": "API-CT-1",
            "cont_contract_name": "API Contract",
            "cont_start_date": "2026-09-01",
            "cont_end_date": "2027-08-31",
            "cont_status": "DRAFT",
            "cont_revenue_basis": "PER_BUS",
            "cont_currency_code": "AED",
            "cont_no_of_passengers": 0,
            "cont_big_bus_count_gt_34": 1,
            "cont_medium_bus_count_17_34": 0,
            "cont_small_bus_count_lt_17": 0,
            "cont_big_bus_rate_pm": 1000,
            "cont_no_of_billing_months": 12,
            "cont_no_of_work_days_per_week": 5,
            "cont_no_of_round_trips_per_day": 2,
            "total_contract_value": 12000,
        }

    def ap_payload(self):
        return {
            "user_principal_name": "api.workflow.user@example.com",
            "ap_org_id_fk": 77,
            "ap_supp_id_fk": 30,
            "ap_invoice_number": "API-AP-1",
            "ap_invoice_date": "2026-09-01",
            "ap_currency_code": "AED",
            "ap_invoice_amount": 1000,
        }

    def ar_payload(self):
        return {
            "user_principal_name": "api.workflow.user@example.com",
            "ar_org_id_fk": 77,
            "ar_cust_id_fk": 40,
            "ar_invoice_number": "API-AR-1",
            "ar_invoice_date": "2026-09-01",
            "ar_currency_code": "AED",
            "ar_invoice_amount": 1000,
        }

    def test_contract_multipart_create_and_update_dispatch_to_workflow_logic_with_file(self):
        calls = []

        def fake_create(payload, file_stream=None, file_name=None, content_type=None):
            calls.append(("create", payload, file_stream.read(), file_name, content_type))
            return pending_response("CONTRACTS_MANAGEMENT", "CREATE")

        def fake_update(payload, file_stream=None, file_name=None, content_type=None):
            calls.append(("update", payload, file_stream.read(), file_name, content_type))
            return pending_response("CONTRACTS_MANAGEMENT", "UPDATE")

        with patch.object(contracts_api, "create_contract", fake_create), \
             patch.object(contracts_api, "update_contract", fake_update):
            create_response = contracts_api.create_contract_with_document_endpoint(
                payload_json=json.dumps(self.contract_payload()),
                file=FakeUploadFile("contract.pdf", b"contract-bytes", "application/pdf"),
                auth_context=AUTH_CONTEXT,
            )
            update_payload = {**self.contract_payload(), "cont_id": 99}
            update_response = contracts_api.update_contract_with_document_endpoint(
                payload_json=json.dumps(update_payload),
                file=FakeUploadFile("contract.pdf", b"updated-contract", "application/pdf"),
                auth_context=AUTH_CONTEXT,
            )

        self.assertEqual(create_response["data"]["workflow_status"], "PENDING_APPROVAL")
        self.assertEqual(update_response["data"]["workflow_status"], "PENDING_APPROVAL")
        self.assertEqual(calls[0][0], "create")
        self.assertEqual(calls[0][1]["cont_org_id_fk"], 77)
        self.assertEqual(calls[0][1]["user_principal_name"], "api.workflow.user@example.com")
        self.assertEqual(calls[0][1]["created_by"], "api.workflow.user@example.com")
        self.assertEqual(calls[0][1]["updated_by"], "api.workflow.user@example.com")
        self.assertEqual(calls[0][2], b"contract-bytes")
        self.assertEqual(calls[0][3], "contract.pdf")
        self.assertEqual(calls[0][4], "application/pdf")
        self.assertEqual(calls[1][0], "update")
        self.assertEqual(calls[1][1]["cont_id"], 99)
        self.assertEqual(calls[1][1]["updated_by"], "api.workflow.user@example.com")
        self.assertEqual(calls[1][2], b"updated-contract")

    def test_financial_multipart_create_and_update_dispatch_to_workflow_logic_with_file(self):
        endpoint_cases = [
            (
                "AP create",
                "/api/v1/accounts-payables/create-with-document",
                financial_api,
                "create_accounts_payable",
                self.ap_payload(),
                "ACCOUNTS_PAYABLE",
                "CREATE",
                "ap_org_id_fk",
            ),
            (
                "AP update",
                "/api/v1/accounts-payables/update-with-document",
                financial_api,
                "update_accounts_payable",
                {**self.ap_payload(), "ap_id": 111},
                "ACCOUNTS_PAYABLE",
                "UPDATE",
                "ap_org_id_fk",
            ),
            (
                "AR create",
                "/api/v1/accounts-receivables/create-with-document",
                financial_api,
                "create_accounts_receivable",
                self.ar_payload(),
                "ACCOUNTS_RECEIVABLE",
                "CREATE",
                "ar_org_id_fk",
            ),
            (
                "AR update",
                "/api/v1/accounts-receivables/update-with-document",
                financial_api,
                "update_accounts_receivable",
                {**self.ar_payload(), "ar_id": 222},
                "ACCOUNTS_RECEIVABLE",
                "UPDATE",
                "ar_org_id_fk",
            ),
        ]

        route_functions = {
            "/api/v1/accounts-payables/create-with-document": (
                financial_api.create_accounts_payable_with_document_endpoint
            ),
            "/api/v1/accounts-payables/update-with-document": (
                financial_api.update_accounts_payable_with_document_endpoint
            ),
            "/api/v1/accounts-receivables/create-with-document": (
                financial_api.create_accounts_receivable_with_document_endpoint
            ),
            "/api/v1/accounts-receivables/update-with-document": (
                financial_api.update_accounts_receivable_with_document_endpoint
            ),
        }

        for label, path, module, func_name, payload, workflow_code, action, org_field in endpoint_cases:
            with self.subTest(label=label):
                calls = []

                def fake_logic(bound_payload, file_stream=None, file_name=None, content_type=None):
                    calls.append((bound_payload, file_stream.read(), file_name, content_type))
                    return pending_response(workflow_code, action)

                with patch.object(module, func_name, fake_logic):
                    response = route_functions[path](
                        payload_json=json.dumps(payload),
                        file=FakeUploadFile("invoice.pdf", b"invoice-bytes", "application/pdf"),
                        auth_context=AUTH_CONTEXT,
                    )

                self.assertEqual(response["data"]["workflow_status"], "PENDING_APPROVAL")
                self.assertEqual(calls[0][0][org_field], 77)
                self.assertEqual(
                    calls[0][0]["user_principal_name"],
                    "api.workflow.user@example.com",
                )
                self.assertEqual(calls[0][1], b"invoice-bytes")
                self.assertEqual(calls[0][2], "invoice.pdf")
                self.assertEqual(calls[0][3], "application/pdf")

    def test_multipart_business_endpoints_accept_optional_file(self):
        with patch.object(
            contracts_api,
            "create_contract",
            return_value=pending_response("CONTRACTS_MANAGEMENT", "CREATE"),
        ) as create_contract_mock:
            response = contracts_api.create_contract_with_document_endpoint(
                payload_json=json.dumps(self.contract_payload()),
                file=None,
                auth_context=AUTH_CONTEXT,
            )

        self.assertEqual(response["data"]["workflow_status"], "PENDING_APPROVAL")
        create_contract_mock.assert_called_once()
        self.assertEqual(len(create_contract_mock.call_args.args), 1)

    def test_openapi_exposes_multipart_business_document_routes_and_keeps_existing_routes(self):
        contracts_paths = contracts_api.app.openapi()["paths"]
        financial_paths = financial_api.app.openapi()["paths"]
        consolidated_paths = consolidated_app.openapi()["paths"]

        for path in (
            "/api/v1/contracts/create-with-document",
            "/api/v1/contracts/update-with-document",
        ):
            self.assertIn(path, contracts_paths)
            self.assertIn(path, consolidated_paths)
            self.assertIn(
                "multipart/form-data",
                contracts_paths[path]["post"]["requestBody"]["content"],
            )
            self.assertIn(
                "multipart/form-data",
                consolidated_paths[path]["post"]["requestBody"]["content"],
            )

        for path in (
            "/api/v1/accounts-payables/create-with-document",
            "/api/v1/accounts-payables/update-with-document",
            "/api/v1/accounts-receivables/create-with-document",
            "/api/v1/accounts-receivables/update-with-document",
        ):
            self.assertIn(path, financial_paths)
            self.assertIn(path, consolidated_paths)
            self.assertIn(
                "multipart/form-data",
                financial_paths[path]["post"]["requestBody"]["content"],
            )
            self.assertIn(
                "multipart/form-data",
                consolidated_paths[path]["post"]["requestBody"]["content"],
            )

        self.assertIn("/api/v1/contracts/create", contracts_paths)
        self.assertIn("/api/v1/contracts/update", contracts_paths)
        self.assertIn("/api/v1/contracts/documents/upload", contracts_paths)
        self.assertIn("/api/v1/accounts-payables/create", financial_paths)
        self.assertIn("/api/v1/accounts-payables/update", financial_paths)
        self.assertIn("/api/v1/accounts-payables/documents/upload", financial_paths)
        self.assertIn("/api/v1/accounts-receivables/create", financial_paths)
        self.assertIn("/api/v1/accounts-receivables/update", financial_paths)
        self.assertIn("/api/v1/accounts-receivables/documents/upload", financial_paths)


if __name__ == "__main__":
    unittest.main()
