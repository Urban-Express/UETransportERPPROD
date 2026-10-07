from io import BytesIO
import unittest
from unittest.mock import patch

import pandas as pd


def pending_workflow(workflow_code: str, workflow_action: str, step_id: int = 456) -> dict:
    return {
        "workflow_confirmation": "N",
        "workflow_status": "PENDING_APPROVAL",
        "workflow_code": workflow_code,
        "workflow_action": workflow_action,
        "workflow_request_id": 123,
        "workflow_instance_id": 234,
        "workflow_instance_step_id": step_id,
        "current_approver_user_principal_name": "approver@example.com",
        "execution_result": None,
    }


class FakeEngine:
    def begin(self):
        return self

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback):
        return False

    def dispose(self):
        pass


class FakeFirebaseBlob:
    def __init__(self):
        self.uploaded = False
        self.content_type = None
        self.rewind = None

    def upload_from_file(self, file_stream, content_type=None, rewind=False, if_generation_match=None):
        self.uploaded = True
        self.content_type = content_type
        self.rewind = rewind
        if rewind:
            file_stream.seek(0)
        self.size = len(file_stream.read())
        self.generation = 123
        self.if_generation_match = if_generation_match

    def reload(self, if_generation_match=None):
        pass


class FakeFirebaseBucket:
    def __init__(self, blob):
        self.blob_value = blob
        self.blob_path = None

    def blob(self, blob_path):
        self.blob_path = blob_path
        return self.blob_value


class WorkflowTransactionPendingResponseTests(unittest.TestCase):
    def assert_pending_submission(self, result, workflow_code, workflow_action, domain_reference_id=None):
        self.assertNotIn("error", result)
        self.assertEqual(result["workflow_status"], "PENDING_APPROVAL")
        self.assertEqual(result["workflow_code"], workflow_code)
        self.assertEqual(result["workflow_action"], workflow_action)
        self.assertEqual(result["workflow_instance_id"], 234)
        self.assertEqual(result["workflow_instance_step_id"], 456)
        self.assertEqual(result["workflow_request_id"], 123)
        self.assertEqual(result["current_approver_user_principal_name"], "approver@example.com")
        self.assertFalse(result["business_operation_executed"])
        self.assertTrue(result["workflow_required"])
        self.assertEqual(result["workflow"]["workflow_status"], "PENDING_APPROVAL")
        if domain_reference_id is not None:
            self.assertEqual(result["domain_reference_id"], domain_reference_id)

    def test_asset_create_update_delete_pending_returns_success_contract(self):
        from app_backend.services.service_08_financial_management.logic import asset_master_create_data
        from app_backend.services.service_08_financial_management.logic import asset_master_delete_data
        from app_backend.services.service_08_financial_management.logic import asset_master_update_data

        with patch.object(asset_master_create_data, "validate_asset_payload", return_value=None), \
             patch.object(asset_master_create_data, "verify_organization_exists", return_value=True), \
             patch.object(
                 asset_master_create_data,
                 "is_asset_master_workflow_approved",
                 return_value=(False, pending_workflow("ASSET_MASTER", "CREATE")),
             ):
            result = asset_master_create_data.create_asset({"asset_org_id_fk": 1, "asset_name": "Asset A"})
        self.assert_pending_submission(result, "ASSET_MASTER", "CREATE")

        with patch.object(asset_master_update_data, "validate_asset_payload", return_value=None), \
             patch.object(asset_master_update_data, "verify_organization_exists", return_value=True), \
             patch.object(asset_master_update_data, "get_asset_by_id", return_value={"asset_org_id_fk": 1}), \
             patch.object(
                 asset_master_update_data,
                 "is_asset_master_workflow_approved",
                 return_value=(False, pending_workflow("ASSET_MASTER", "UPDATE")),
             ):
            result = asset_master_update_data.update_asset({"asset_id": 77, "asset_org_id_fk": 1, "asset_name": "Asset A"})
        self.assert_pending_submission(result, "ASSET_MASTER", "UPDATE", domain_reference_id=77)

        with patch.object(asset_master_delete_data, "get_asset_by_id", return_value={"asset_org_id_fk": 1, "asset_name": "Asset A"}), \
             patch.object(
                 asset_master_delete_data,
                 "is_asset_master_workflow_approved",
                 return_value=(False, pending_workflow("ASSET_MASTER", "DELETE")),
             ):
            result = asset_master_delete_data.delete_asset({"asset_id": 77, "asset_org_id_fk": 1})
        self.assert_pending_submission(result, "ASSET_MASTER", "DELETE", domain_reference_id=77)

    def test_fleet_create_update_delete_pending_returns_success_contract(self):
        from app_backend.services.service_03_fleet_management.logic import fleet_master_create_data
        from app_backend.services.service_03_fleet_management.logic import fleet_master_delete_data
        from app_backend.services.service_03_fleet_management.logic import fleet_master_update_data

        with patch.object(
            fleet_master_create_data,
            "is_fleet_management_workflow_approved",
            return_value=(False, pending_workflow("FLEET_MANAGEMENT", "CREATE")),
        ), patch.object(fleet_master_create_data, "fleet_vehicle_code_exists", return_value=False):
            result = fleet_master_create_data.create_fleet_vehicle({"fleet_org_id_fk": 1, "vehicle_code": "VH-1"})
        self.assert_pending_submission(result, "FLEET_MANAGEMENT", "CREATE")

        existing = pd.DataFrame([{"fleet_vehicle_id_pk": 88, "fleet_org_id_fk": 1, "vehicle_code": "VH-1"}])
        with patch.object(fleet_master_update_data, "db_engine", return_value=FakeEngine()), \
             patch.object(fleet_master_update_data.pd, "read_sql", return_value=existing), \
             patch.object(fleet_master_update_data, "fleet_vehicle_code_exists", return_value=False), \
             patch.object(
                 fleet_master_update_data,
                 "is_fleet_management_workflow_approved",
                 return_value=(False, pending_workflow("FLEET_MANAGEMENT", "UPDATE")),
             ):
            result = fleet_master_update_data.update_fleet_vehicle({"fleet_vehicle_id": 88, "fleet_org_id_fk": 1, "vehicle_code": "VH-1"})
        self.assert_pending_submission(result, "FLEET_MANAGEMENT", "UPDATE", domain_reference_id=88)

        with patch.object(fleet_master_delete_data, "db_engine", return_value=FakeEngine()), \
             patch.object(fleet_master_delete_data.pd, "read_sql", return_value=existing), \
             patch.object(
                 fleet_master_delete_data,
                 "is_fleet_management_workflow_approved",
                 return_value=(False, pending_workflow("FLEET_MANAGEMENT", "DELETE")),
             ):
            result = fleet_master_delete_data.delete_fleet_vehicle({"fleet_vehicle_id": 88, "fleet_org_id_fk": 1})
        self.assert_pending_submission(result, "FLEET_MANAGEMENT", "DELETE", domain_reference_id=88)

    def test_contract_create_update_pending_returns_success_contract(self):
        from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data
        from app_backend.services.service_06_contracts_management.logic import contracts_management_update_data

        with patch.object(contracts_management_create_data, "db_engine", return_value=FakeEngine()), \
             patch.object(contracts_management_create_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_create_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_create_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(False, pending_workflow("CONTRACTS_MANAGEMENT", "CREATE")),
             ):
            result = contracts_management_create_data.create_contract({"cont_org_id_fk": 1, "cont_contract_number": "CT-1"})
        self.assert_pending_submission(result, "CONTRACTS_MANAGEMENT", "CREATE")

        existing = pd.DataFrame([{"cont_id_pk": 99, "cont_org_id_fk": 1, "cont_contract_number": "CT-1", "cont_link_path": None}])
        with patch.object(contracts_management_update_data, "db_engine", return_value=FakeEngine()), \
             patch.object(contracts_management_update_data.pd, "read_sql", return_value=existing), \
             patch.object(contracts_management_update_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_update_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_update_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(False, pending_workflow("CONTRACTS_MANAGEMENT", "UPDATE")),
             ):
            result = contracts_management_update_data.update_contract({"cont_id": 99, "cont_org_id_fk": 1, "cont_contract_number": "CT-1"})
        self.assert_pending_submission(result, "CONTRACTS_MANAGEMENT", "UPDATE", domain_reference_id=99)

    def test_contract_document_is_staged_before_pending_workflow(self):
        from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data

        def upload_contract_document(payload, **kwargs):
            blob_path = f"{payload['storage_folder']}/CT-1/signed.pdf"
            return {
                "cont_link_path": blob_path,
                "firebase_blob_path": blob_path,
                "firebase_bucket": "bucket",
            }

        with patch.object(contracts_management_create_data, "db_engine", return_value=FakeEngine()), \
             patch.object(contracts_management_create_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_create_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_create_data,
                 "upload_contract_document_to_firebase",
                 side_effect=upload_contract_document,
             ) as upload_mock, \
             patch.object(
                 contracts_management_create_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(False, pending_workflow("CONTRACTS_MANAGEMENT", "CREATE")),
             ) as workflow_mock:
            result = contracts_management_create_data.create_contract(
                {
                    "cont_org_id_fk": 1,
                    "cont_contract_number": "CT-1",
                    "file_path": "/tmp/signed.pdf",
                    "content_type": "application/pdf",
                },
                file_stream=BytesIO(b"pdf"),
                file_name="signed.pdf",
                content_type="application/pdf",
            )
        self.assert_pending_submission(result, "CONTRACTS_MANAGEMENT", "CREATE")
        workflow_payload = workflow_mock.call_args.kwargs["payload"]
        upload_payload = upload_mock.call_args.kwargs["payload"]
        self.assertIn("_workflow_staging", upload_payload["storage_folder"])
        self.assertIn("_workflow_staging", workflow_payload["cont_link_path"])
        self.assertEqual(workflow_payload["workflow_document_staging_status"], "STAGED")
        self.assertIn("workflow_document_cleanup_policy", workflow_payload)
        self.assertNotIn("file_path", workflow_payload)
        self.assertNotIn("content_type", workflow_payload)

    def test_contract_update_document_uses_staging_path_not_approved_path(self):
        from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data
        from app_backend.services.service_06_contracts_management.logic import contracts_management_update_data

        existing = pd.DataFrame([
            {
                "cont_id_pk": 99,
                "cont_org_id_fk": 1,
                "cont_contract_number": "CT-1",
                "cont_link_path": "firebase_upload_files/contracts/CT-1/current.pdf",
            }
        ])

        def upload_contract_document(payload, **kwargs):
            blob_path = f"{payload['storage_folder']}/CT-1/replacement.pdf"
            return {
                "cont_link_path": blob_path,
                "firebase_blob_path": blob_path,
                "firebase_bucket": "bucket",
            }

        with patch.object(contracts_management_update_data, "db_engine", return_value=FakeEngine()), \
             patch.object(contracts_management_update_data.pd, "read_sql", return_value=existing), \
             patch.object(contracts_management_update_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_update_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_create_data,
                 "upload_contract_document_to_firebase",
                 side_effect=upload_contract_document,
             ) as upload_mock, \
             patch.object(
                 contracts_management_update_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(False, pending_workflow("CONTRACTS_MANAGEMENT", "UPDATE")),
             ) as workflow_mock:
            result = contracts_management_update_data.update_contract(
                {
                    "cont_id": 99,
                    "cont_org_id_fk": 1,
                    "cont_contract_number": "CT-1",
                    "file_path": "/tmp/replacement.pdf",
                },
                file_stream=BytesIO(b"pdf"),
                file_name="replacement.pdf",
                content_type="application/pdf",
            )
        self.assert_pending_submission(result, "CONTRACTS_MANAGEMENT", "UPDATE", domain_reference_id=99)
        upload_payload = upload_mock.call_args.kwargs["payload"]
        workflow_payload = workflow_mock.call_args.kwargs["payload"]
        self.assertIn("_workflow_staging", upload_payload["storage_folder"])
        self.assertIn("_workflow_staging", workflow_payload["cont_link_path"])
        self.assertNotEqual(
            workflow_payload["cont_link_path"],
            "firebase_upload_files/contracts/CT-1/current.pdf",
        )

    def test_contract_local_path_without_stream_is_rejected_before_workflow(self):
        from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data

        with patch.object(contracts_management_create_data, "db_engine", return_value=FakeEngine()), \
             patch.object(contracts_management_create_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_create_data, "contract_number_exists", return_value=False), \
             patch.object(contracts_management_create_data, "is_contracts_management_workflow_approved") as workflow_mock:
            result = contracts_management_create_data.create_contract(
                {
                    "cont_org_id_fk": 1,
                    "cont_contract_number": "CT-1",
                    "file_path": "/tmp/signed.pdf",
                }
            )
        self.assertIn("error", result)
        self.assertIn("must be uploaded to Firebase before workflow submission", result["error"])
        workflow_mock.assert_not_called()

    def test_contract_staged_document_is_cleaned_when_workflow_submission_fails(self):
        from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data

        upload_response = {
            "cont_link_path": "firebase_upload_files/contracts/_workflow_staging/1/CONTRACTS_MANAGEMENT/CREATE/REQ/CT-1/signed.pdf",
            "firebase_blob_path": "firebase_upload_files/contracts/_workflow_staging/1/CONTRACTS_MANAGEMENT/CREATE/REQ/CT-1/signed.pdf",
            "firebase_bucket": "bucket",
        }
        cleanup_response = {"cleanup_attempted": True, "cleanup_completed": True}
        with patch.object(contracts_management_create_data, "db_engine", return_value=FakeEngine()), \
             patch.object(contracts_management_create_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_create_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_create_data,
                 "upload_contract_document_to_firebase",
                 return_value=upload_response,
             ), \
             patch.object(
                 contracts_management_create_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(False, {"workflow_status": "ERROR", "error": "WORKFLOW_CONFIGURATION_NOT_FOUND"}),
             ), \
             patch.object(
                 contracts_management_create_data,
                 "cleanup_staged_workflow_document",
                 return_value=cleanup_response,
             ) as cleanup_mock:
            result = contracts_management_create_data.create_contract(
                {
                    "cont_org_id_fk": 1,
                    "cont_contract_number": "CT-1",
                    "service_request_id": "REQ",
                },
                file_stream=BytesIO(b"pdf"),
                file_name="signed.pdf",
                content_type="application/pdf",
            )

        self.assertEqual(result["document_cleanup"], cleanup_response)
        cleanup_mock.assert_called_once()
        self.assertEqual(
            cleanup_mock.call_args.args[0]["workflow_document_staged_blob_path"],
            upload_response["firebase_blob_path"],
        )

    def test_ap_create_update_delete_pending_returns_success_contract(self):
        from app_backend.services.service_08_financial_management.logic import accounts_payables_create_data
        from app_backend.services.service_08_financial_management.logic import accounts_payables_delete_data
        from app_backend.services.service_08_financial_management.logic import accounts_payables_update_data

        with patch.object(accounts_payables_create_data, "validate_ap_payload", return_value=None), \
             patch.object(accounts_payables_create_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_create_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_create_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_create_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_PAYABLE", "CREATE")),
             ):
            result = accounts_payables_create_data.create_accounts_payable({"ap_org_id_fk": 1, "ap_supp_id_fk": 2, "ap_invoice_number": "AP-1"})
        self.assert_pending_submission(result, "ACCOUNTS_PAYABLE", "CREATE")

        existing = {"ap_org_id_fk": 1, "ap_supp_id_fk": 2, "ap_invoice_number": "AP-1"}
        with patch.object(accounts_payables_update_data, "validate_ap_payload", return_value=None), \
             patch.object(accounts_payables_update_data, "get_ap_by_id", return_value=existing), \
             patch.object(accounts_payables_update_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_update_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_update_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_update_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_PAYABLE", "UPDATE")),
             ):
            result = accounts_payables_update_data.update_accounts_payable({"ap_id": 111, "ap_org_id_fk": 1, "ap_supp_id_fk": 2, "ap_invoice_number": "AP-1"})
        self.assert_pending_submission(result, "ACCOUNTS_PAYABLE", "UPDATE", domain_reference_id=111)

        with patch.object(accounts_payables_delete_data, "get_ap_by_id", return_value=existing), \
             patch.object(
                 accounts_payables_delete_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_PAYABLE", "DELETE")),
             ):
            result = accounts_payables_delete_data.delete_accounts_payable({"ap_id": 111, "ap_org_id_fk": 1})
        self.assert_pending_submission(result, "ACCOUNTS_PAYABLE", "DELETE", domain_reference_id=111)

    def test_ap_document_is_staged_before_pending_update_workflow(self):
        from app_backend.services.service_08_financial_management.logic import accounts_payables_create_data
        from app_backend.services.service_08_financial_management.logic import accounts_payables_update_data

        existing = {
            "ap_org_id_fk": 1,
            "ap_supp_id_fk": 2,
            "ap_invoice_number": "AP-1",
            "ap_invoice_file_path": "gs://bucket/firebase_upload_files/ap_invoices/AP-1/current.pdf",
        }

        def upload_ap_document(payload, **kwargs):
            blob_path = f"{payload['storage_folder']}/AP-1/invoice.pdf"
            return {
                "ap_invoice_file_path": f"gs://bucket/{blob_path}",
                "firebase_blob_path": blob_path,
                "firebase_bucket": "bucket",
            }

        with patch.object(accounts_payables_update_data, "validate_ap_payload", return_value=None), \
             patch.object(accounts_payables_update_data, "get_ap_by_id", return_value=existing), \
             patch.object(accounts_payables_update_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_update_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_update_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_create_data,
                 "upload_ap_invoice_document_to_firebase",
                 side_effect=upload_ap_document,
             ) as upload_mock, \
             patch.object(
                 accounts_payables_update_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_PAYABLE", "UPDATE")),
             ) as workflow_mock:
            result = accounts_payables_update_data.update_accounts_payable(
                {
                    "ap_id": 111,
                    "ap_org_id_fk": 1,
                    "ap_supp_id_fk": 2,
                    "ap_invoice_number": "AP-1",
                    "file_path": "/tmp/invoice.pdf",
                },
                file_stream=BytesIO(b"pdf"),
                file_name="invoice.pdf",
                content_type="application/pdf",
            )
        self.assert_pending_submission(result, "ACCOUNTS_PAYABLE", "UPDATE", domain_reference_id=111)
        workflow_payload = workflow_mock.call_args.kwargs["payload"]
        upload_payload = upload_mock.call_args.kwargs["payload"]
        self.assertIn("_workflow_staging", upload_payload["storage_folder"])
        self.assertIn("_workflow_staging", workflow_payload["ap_invoice_file_path"])
        self.assertNotEqual(workflow_payload["ap_invoice_file_path"], existing["ap_invoice_file_path"])
        self.assertIn("workflow_document_cleanup_policy", workflow_payload)
        self.assertNotIn("file_path", workflow_payload)

    def test_ap_staged_document_is_cleaned_when_workflow_submission_fails(self):
        from app_backend.services.service_08_financial_management.logic import accounts_payables_create_data

        upload_response = {
            "ap_invoice_file_path": "gs://bucket/firebase_upload_files/ap_invoices/_workflow_staging/1/ACCOUNTS_PAYABLE/CREATE/REQ/AP-1/invoice.pdf",
            "firebase_blob_path": "firebase_upload_files/ap_invoices/_workflow_staging/1/ACCOUNTS_PAYABLE/CREATE/REQ/AP-1/invoice.pdf",
            "firebase_bucket": "bucket",
        }
        cleanup_response = {"cleanup_attempted": True, "cleanup_completed": True}
        with patch.object(accounts_payables_create_data, "validate_ap_payload", return_value=None), \
             patch.object(accounts_payables_create_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_create_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_create_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_create_data,
                 "upload_ap_invoice_document_to_firebase",
                 return_value=upload_response,
             ), \
             patch.object(
                 accounts_payables_create_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(False, {"workflow_status": "ERROR", "error": "WORKFLOW_CONFIGURATION_NOT_FOUND"}),
             ), \
             patch.object(
                 accounts_payables_create_data,
                 "cleanup_staged_workflow_document",
                 return_value=cleanup_response,
             ) as cleanup_mock:
            result = accounts_payables_create_data.create_accounts_payable(
                {
                    "ap_org_id_fk": 1,
                    "ap_supp_id_fk": 2,
                    "ap_invoice_number": "AP-1",
                    "service_request_id": "REQ",
                },
                file_stream=BytesIO(b"pdf"),
                file_name="invoice.pdf",
                content_type="application/pdf",
            )

        self.assertEqual(result["document_cleanup"], cleanup_response)
        cleanup_mock.assert_called_once()
        self.assertEqual(
            cleanup_mock.call_args.args[0]["workflow_document_staged_blob_path"],
            upload_response["firebase_blob_path"],
        )

    def test_ar_create_update_delete_pending_returns_success_contract(self):
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_create_data
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_delete_data
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_update_data

        with patch.object(accounts_receivables_create_data, "validate_ar_payload", return_value=None), \
             patch.object(accounts_receivables_create_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_create_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_create_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_create_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "CREATE")),
             ):
            result = accounts_receivables_create_data.create_accounts_receivable({"ar_org_id_fk": 1, "ar_cust_id_fk": 2, "ar_invoice_number": "AR-1"})
        self.assert_pending_submission(result, "ACCOUNTS_RECEIVABLE", "CREATE")

        existing = {"ar_org_id_fk": 1, "ar_cust_id_fk": 2, "ar_invoice_number": "AR-1"}
        with patch.object(accounts_receivables_update_data, "validate_ar_payload", return_value=None), \
             patch.object(accounts_receivables_update_data, "get_ar_by_id", return_value=existing), \
             patch.object(accounts_receivables_update_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_update_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_update_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_update_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "UPDATE")),
             ):
            result = accounts_receivables_update_data.update_accounts_receivable({"ar_id": 222, "ar_org_id_fk": 1, "ar_cust_id_fk": 2, "ar_invoice_number": "AR-1"})
        self.assert_pending_submission(result, "ACCOUNTS_RECEIVABLE", "UPDATE", domain_reference_id=222)

        with patch.object(accounts_receivables_delete_data, "get_ar_by_id", return_value=existing), \
             patch.object(
                 accounts_receivables_delete_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "DELETE")),
             ):
            result = accounts_receivables_delete_data.delete_accounts_receivable({"ar_id": 222, "ar_org_id_fk": 1})
        self.assert_pending_submission(result, "ACCOUNTS_RECEIVABLE", "DELETE", domain_reference_id=222)

    def test_ar_document_is_staged_before_pending_update_workflow(self):
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_create_data
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_update_data

        existing = {
            "ar_org_id_fk": 1,
            "ar_cust_id_fk": 2,
            "ar_invoice_number": "AR-1",
            "ar_invoice_file_path": "gs://bucket/firebase_upload_files/ar_invoices/AR-1/current.pdf",
        }

        def upload_ar_document(payload, **kwargs):
            blob_path = f"{payload['storage_folder']}/AR-1/invoice.pdf"
            return {
                "ar_invoice_file_path": f"gs://bucket/{blob_path}",
                "firebase_blob_path": blob_path,
                "firebase_bucket": "bucket",
            }

        with patch.object(accounts_receivables_update_data, "validate_ar_payload", return_value=None), \
             patch.object(accounts_receivables_update_data, "get_ar_by_id", return_value=existing), \
             patch.object(accounts_receivables_update_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_update_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_update_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_create_data,
                 "upload_ar_invoice_document_to_firebase",
                 side_effect=upload_ar_document,
             ) as upload_mock, \
             patch.object(
                 accounts_receivables_update_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(False, pending_workflow("ACCOUNTS_RECEIVABLE", "UPDATE")),
             ) as workflow_mock:
            result = accounts_receivables_update_data.update_accounts_receivable(
                {
                    "ar_id": 222,
                    "ar_org_id_fk": 1,
                    "ar_cust_id_fk": 2,
                    "ar_invoice_number": "AR-1",
                    "file_path": "/tmp/invoice.pdf",
                },
                file_stream=BytesIO(b"pdf"),
                file_name="invoice.pdf",
                content_type="application/pdf",
            )
        self.assert_pending_submission(result, "ACCOUNTS_RECEIVABLE", "UPDATE", domain_reference_id=222)
        workflow_payload = workflow_mock.call_args.kwargs["payload"]
        upload_payload = upload_mock.call_args.kwargs["payload"]
        self.assertIn("_workflow_staging", upload_payload["storage_folder"])
        self.assertIn("_workflow_staging", workflow_payload["ar_invoice_file_path"])
        self.assertNotEqual(workflow_payload["ar_invoice_file_path"], existing["ar_invoice_file_path"])
        self.assertIn("workflow_document_cleanup_policy", workflow_payload)
        self.assertNotIn("file_path", workflow_payload)

    def test_ar_staged_document_is_cleaned_when_workflow_submission_fails(self):
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_create_data

        upload_response = {
            "ar_invoice_file_path": "gs://bucket/firebase_upload_files/ar_invoices/_workflow_staging/1/ACCOUNTS_RECEIVABLE/CREATE/REQ/AR-1/invoice.pdf",
            "firebase_blob_path": "firebase_upload_files/ar_invoices/_workflow_staging/1/ACCOUNTS_RECEIVABLE/CREATE/REQ/AR-1/invoice.pdf",
            "firebase_bucket": "bucket",
        }
        cleanup_response = {"cleanup_attempted": True, "cleanup_completed": True}
        with patch.object(accounts_receivables_create_data, "validate_ar_payload", return_value=None), \
             patch.object(accounts_receivables_create_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_create_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_create_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_create_data,
                 "upload_ar_invoice_document_to_firebase",
                 return_value=upload_response,
             ), \
             patch.object(
                 accounts_receivables_create_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(False, {"workflow_status": "ERROR", "error": "WORKFLOW_CONFIGURATION_NOT_FOUND"}),
             ), \
             patch.object(
                 accounts_receivables_create_data,
                 "cleanup_staged_workflow_document",
                 return_value=cleanup_response,
             ) as cleanup_mock:
            result = accounts_receivables_create_data.create_accounts_receivable(
                {
                    "ar_org_id_fk": 1,
                    "ar_cust_id_fk": 2,
                    "ar_invoice_number": "AR-1",
                    "service_request_id": "REQ",
                },
                file_stream=BytesIO(b"pdf"),
                file_name="invoice.pdf",
                content_type="application/pdf",
            )

        self.assertEqual(result["document_cleanup"], cleanup_response)
        cleanup_mock.assert_called_once()
        self.assertEqual(
            cleanup_mock.call_args.args[0]["workflow_document_staged_blob_path"],
            upload_response["firebase_blob_path"],
        )

    def test_standalone_ar_document_upload_does_not_route_through_update_workflow(self):
        from app_backend.services.service_08_financial_management.integrations import firebase_ar_invoice_document_upload
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_update_data

        payload = {
            "ar_id": 222,
            "ar_org_id_fk": 1,
            "user_principal_name": "requester@example.com",
            "updated_by": "requester@example.com",
        }
        file_stream = BytesIO(b"pdf")
        upload_response = {
            "ar_invoice_file_path": "gs://bucket/firebase_upload_files/ar_invoices/AR-1/invoice.pdf",
            "firebase_blob_path": "firebase_upload_files/ar_invoices/AR-1/invoice.pdf",
            "firebase_bucket": "bucket",
        }

        with patch.object(
            firebase_ar_invoice_document_upload,
            "upload_ar_invoice_document_to_firebase",
            return_value=upload_response,
        ) as upload_mock, \
             patch.object(
                 firebase_ar_invoice_document_upload,
                 "get_ar_by_id",
                 return_value={
                     "ar_org_id_fk": 1,
                     "ar_cust_id_fk": 2,
                     "ar_invoice_number": "AR-1",
                 },
             ) as get_ar_mock, \
             patch.object(
                 accounts_receivables_update_data,
                 "update_accounts_receivable",
                 side_effect=AssertionError("Standalone AR document upload must not trigger AR UPDATE workflow."),
             ) as update_mock:
            result = firebase_ar_invoice_document_upload.upload_ar_invoice_document(
                payload,
                file_stream=file_stream,
                file_name="invoice.pdf",
                content_type="application/pdf",
            )

        self.assertEqual(result, upload_response)
        upload_mock.assert_called_once_with(
            payload,
            file_stream,
            "invoice.pdf",
            "application/pdf",
        )
        get_ar_mock.assert_not_called()
        update_mock.assert_not_called()

    def test_uploaders_support_stream_staging_contract_without_pure_staging_db_engine(self):
        from app_backend.services.service_06_contracts_management.integrations import firebase_contract_document_upload
        from app_backend.services.service_08_financial_management.integrations import firebase_ap_invoice_document_upload
        from app_backend.services.service_08_financial_management.integrations import firebase_ar_invoice_document_upload

        upload_cases = [
            (
                firebase_contract_document_upload,
                firebase_contract_document_upload.upload_contract_document_to_firebase,
                {
                    "cont_contract_number": "CT-STREAM",
                    "cont_org_id_fk": 1,
                },
                {
                    "storage_folder": "firebase_upload_files/contracts/_workflow_staging/1/REQ",
                    "update_contract_link": False,
                    "require_contract": False,
                },
                "cont_link_path",
                "firebase_upload_files/contracts/_workflow_staging/1/REQ/CT-STREAM/versions/",
            ),
            (
                firebase_ap_invoice_document_upload,
                firebase_ap_invoice_document_upload.upload_ap_invoice_document_to_firebase,
                {
                    "ap_invoice_number": "AP-STREAM",
                    "ap_org_id_fk": 1,
                    "ap_supp_id_fk": 1,
                },
                {
                    "storage_folder": "firebase_upload_files/ap_invoices/_workflow_staging/1/REQ",
                    "update_ap_document_link": False,
                    "require_ap_invoice": False,
                },
                "ap_invoice_file_path",
                "firebase_upload_files/ap_invoices/_workflow_staging/1/REQ/AP-STREAM/versions/",
            ),
            (
                firebase_ar_invoice_document_upload,
                firebase_ar_invoice_document_upload.upload_ar_invoice_document_to_firebase,
                {
                    "ar_invoice_number": "AR-STREAM",
                    "ar_org_id_fk": 1,
                },
                {
                    "storage_folder": "firebase_upload_files/ar_invoices/_workflow_staging/1/REQ",
                    "update_ar_document_link": False,
                    "require_ar_invoice": False,
                },
                "ar_invoice_file_path",
                "firebase_upload_files/ar_invoices/_workflow_staging/1/REQ/AR-STREAM/versions/",
            ),
        ]

        for module, upload_function, payload, kwargs, durable_field, expected_blob_prefix in upload_cases:
            with self.subTest(module=module.__name__):
                blob = FakeFirebaseBlob()
                bucket = FakeFirebaseBucket(blob)
                with patch.object(module, "FIREBASE_STORAGE_BUCKET", "workflow-test-bucket"), \
                     patch.object(module, "initialize_firebase_app"), \
                     patch.object(module.storage, "bucket", return_value=bucket), \
                     patch.object(module, "db_engine") as db_engine:
                    result = upload_function(
                        payload=payload,
                        file_stream=BytesIO(b"%PDF-1.4\n%%EOF\n"),
                        file_name="document.pdf",
                        content_type="application/pdf",
                        **kwargs,
                    )

                self.assertNotIn("error", result)
                self.assertTrue(blob.uploaded)
                self.assertTrue(blob.rewind)
                self.assertEqual(blob.if_generation_match, 0)
                self.assertEqual(result["workflow_document_attachment"]["storage_generation"], "123")
                self.assertEqual(blob.content_type, "application/pdf")
                self.assertTrue(bucket.blob_path.startswith(expected_blob_prefix))
                self.assertTrue(bucket.blob_path.endswith("/document.pdf"))
                self.assertEqual(result["firebase_blob_path"], bucket.blob_path)
                self.assertEqual(result["firebase_bucket"], "workflow-test-bucket")
                if durable_field == "cont_link_path":
                    self.assertEqual(result[durable_field], bucket.blob_path)
                else:
                    self.assertEqual(result[durable_field], f"gs://workflow-test-bucket/{bucket.blob_path}")
                db_engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
