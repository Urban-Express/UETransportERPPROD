from contextlib import ExitStack
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from app_backend.services import firebase_file_pointer_helpers as pointer_helpers
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    trusted_workflow_execution,
)


class FakeResult:
    def __init__(self, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def scalar_one_or_none(self):
        return self.scalar

    def scalar_one(self):
        return self.scalar

    def one(self):
        return SimpleNamespace(**self.row)


class QueueConnection:
    def __init__(self, *results):
        self.results = list(results)
        self.executions = []

    def execute(self, statement, params=None):
        params = params or {}
        self.executions.append((str(statement), dict(params)))
        result = self.results.pop(0) if self.results else FakeResult()
        if callable(result):
            return result(str(statement), params)
        return result


class FakeEngine:
    def __init__(self, conn):
        self.conn = conn
        self.disposed = False

    def begin(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, traceback):
        return False

    def dispose(self):
        self.disposed = True


class FakeBlob:
    def __init__(self, path):
        self.path = path
        self.uploaded = False
        self.deleted = False
        self.content_type = None

    def upload_from_file(self, file_stream, content_type=None, rewind=False):
        self.uploaded = True
        self.content_type = content_type
        self.rewind = rewind

    def exists(self):
        return not self.deleted

    def delete(self):
        self.deleted = True

    def reload(self):
        pass

    def generate_signed_url(self, **kwargs):
        return f"https://signed.example/{self.path}"


class FakeBucket:
    def __init__(self):
        self.blobs = {}
        self.blob_path = None

    def blob(self, blob_path):
        self.blob_path = blob_path
        if blob_path not in self.blobs:
            self.blobs[blob_path] = FakeBlob(blob_path)
        return self.blobs[blob_path]


def update_scalar(pointer_field):
    return lambda _statement, params: FakeResult(scalar=params[pointer_field])


def update_mapping(*field_names):
    return lambda _statement, params: FakeResult(
        row={field_name: params.get(field_name) for field_name in field_names}
    )


class FirebaseCurrentPointerCorrectionTests(unittest.TestCase):
    def upload_cases(self):
        from app_backend.services.service_02_hr_payroll.integrations import firebase_employee_image_upload
        from app_backend.services.service_04_maintenance_management.integrations import firebase_maintenance_image_upload
        from app_backend.services.service_06_contracts_management.integrations import firebase_contract_document_upload
        from app_backend.services.service_08_financial_management.integrations import firebase_ap_invoice_document_upload
        from app_backend.services.service_08_financial_management.integrations import firebase_ar_invoice_document_upload

        return [
            {
                "name": "employee",
                "module": firebase_employee_image_upload,
                "function": firebase_employee_image_upload.upload_employee_image_to_firebase,
                "payload": {"employee_id": "UE001", "empl_org_id_fk": 1, "updated_by": 7, "storage_folder": "client/evil"},
                "pointer_field": "employee_image_path",
                "previous_field": "previous_employee_image_path",
                "prefix": "firebase_upload_files/employee_images/UE001/versions/",
                "existing_row": lambda previous: {
                    "empl_id_pk": 11,
                    "employee_id": "UE001",
                    "employee_image_path": previous,
                },
                "get_by_id": None,
                "stores_gs": False,
            },
            {
                "name": "maintenance",
                "module": firebase_maintenance_image_upload,
                "function": firebase_maintenance_image_upload.upload_maintenance_image_to_firebase,
                "payload": {"maint_id": 501, "authenticated_org_id": 1, "updated_by": "user", "storage_folder": "client/evil"},
                "pointer_field": "maint_image_path",
                "previous_field": "previous_maint_image_path",
                "prefix": "firebase_upload_files/maintenance_images/501/versions/",
                "existing_row": lambda previous: {
                    "maint_id_pk": 501,
                    "maint_fleet_vehicle_id_fk": 22,
                    "maint_image_path": previous,
                },
                "get_by_id": None,
                "stores_gs": False,
            },
            {
                "name": "contract",
                "module": firebase_contract_document_upload,
                "function": firebase_contract_document_upload.upload_contract_document_to_firebase,
                "payload": {"cont_id": 99, "cont_org_id_fk": 1, "updated_by": "user", "storage_folder": "client/evil"},
                "pointer_field": "cont_link_path",
                "previous_field": "previous_cont_link_path",
                "prefix": "firebase_upload_files/contracts/CT-1/versions/",
                "existing_row": lambda previous: {
                    "cont_id_pk": 99,
                    "cont_contract_number": "CT-1",
                    "cont_link_path": previous,
                },
                "get_by_id": None,
                "stores_gs": False,
            },
            {
                "name": "ap",
                "module": firebase_ap_invoice_document_upload,
                "function": firebase_ap_invoice_document_upload.upload_ap_invoice_document_to_firebase,
                "payload": {"ap_id": 111, "ap_org_id_fk": 1, "updated_by": "user", "storage_folder": "client/evil"},
                "pointer_field": "ap_invoice_file_path",
                "previous_field": "previous_ap_invoice_file_path",
                "prefix": "firebase_upload_files/ap_invoices/AP-1/versions/",
                "existing_row": lambda previous: {
                    "ap_id_pk": 111,
                    "ap_invoice_number": "AP-1",
                    "ap_invoice_file_path": previous,
                },
                "get_by_id": "get_ap_by_id",
                "stores_gs": True,
            },
            {
                "name": "ar",
                "module": firebase_ar_invoice_document_upload,
                "function": firebase_ar_invoice_document_upload.upload_ar_invoice_document_to_firebase,
                "payload": {"ar_id": 222, "ar_org_id_fk": 1, "updated_by": "user", "storage_folder": "client/evil"},
                "pointer_field": "ar_invoice_file_path",
                "previous_field": "previous_ar_invoice_file_path",
                "prefix": "firebase_upload_files/ar_invoices/AR-1/versions/",
                "existing_row": lambda previous: {
                    "ar_id_pk": 222,
                    "ar_invoice_number": "AR-1",
                    "ar_invoice_file_path": previous,
                },
                "get_by_id": "get_ar_by_id",
                "stores_gs": True,
            },
        ]

    def run_upload_case(self, case, previous_path, version_id, *, update_failure=False):
        bucket = FakeBucket()
        previous_blob_path = previous_path.replace("gs://test-bucket/", "")
        bucket.blobs[previous_blob_path] = FakeBlob(previous_blob_path)
        update_result = (
            (lambda _statement, _params: FakeResult(scalar=None))
            if update_failure
            else update_scalar(case["pointer_field"])
        )
        results = []
        if not case["get_by_id"]:
            results.append(FakeResult(row=case["existing_row"](previous_path)))
        results.append(update_result)
        conn = QueueConnection(*results)
        engine = FakeEngine(conn)

        with ExitStack() as stack:
            stack.enter_context(patch.object(case["module"], "FIREBASE_STORAGE_BUCKET", "test-bucket"))
            stack.enter_context(patch.object(case["module"], "initialize_firebase_app"))
            stack.enter_context(patch.object(case["module"].storage, "bucket", return_value=bucket))
            stack.enter_context(patch.object(case["module"], "db_engine", return_value=engine))
            stack.enter_context(patch.object(pointer_helpers, "generate_upload_version_id", return_value=version_id))
            if case["get_by_id"]:
                stack.enter_context(
                    patch.object(
                        case["module"],
                        case["get_by_id"],
                        return_value=case["existing_row"](previous_path),
                    )
                )
            result = case["function"](
                payload=dict(case["payload"]),
                file_stream=BytesIO(b"file-bytes"),
                file_name="profile.jpg",
                content_type="image/jpeg",
            )

        return result, bucket, conn

    def assert_pointer_column_not_assigned(self, conn, pointer_field):
        update_sql, params = conn.executions[-1]
        self.assertNotIn(f"{pointer_field} =", update_sql.lower())
        self.assertNotIn(pointer_field, params)

    def assert_pointer_column_assigned(self, conn, pointer_field):
        update_sql, params = conn.executions[-1]
        self.assertIn(f"{pointer_field} = :{pointer_field}", update_sql.lower())
        self.assertIn(pointer_field, params)

    def test_versioned_blob_path_generation_and_storage_folder_hardening(self):
        first_path, first_version = pointer_helpers.build_versioned_blob_path(
            "firebase_upload_files/employee_images",
            "UE/001",
            "../profile.jpg",
        )
        second_path, second_version = pointer_helpers.build_versioned_blob_path(
            "firebase_upload_files/employee_images",
            "UE/001",
            "../profile.jpg",
        )

        self.assertNotEqual(first_version, second_version)
        self.assertNotEqual(first_path, second_path)
        self.assertIn("/UE_001/versions/", first_path)
        self.assertTrue(first_path.endswith("/profile.jpg"))
        self.assertEqual(
            pointer_helpers.resolve_storage_folder(
                "firebase_upload_files/contracts",
                "other/root",
            ),
            "firebase_upload_files/contracts",
        )
        self.assertEqual(
            pointer_helpers.resolve_storage_folder(
                "firebase_upload_files/contracts",
                "firebase_upload_files/contracts/_workflow_staging/1/REQ",
                allow_workflow_staging=True,
            ),
            "firebase_upload_files/contracts/_workflow_staging/1/REQ",
        )

    def test_standalone_uploads_use_unique_paths_and_keep_previous_objects(self):
        for case in self.upload_cases():
            with self.subTest(service=case["name"]):
                first_result, first_bucket, _ = self.run_upload_case(
                    case,
                    "P0",
                    "VERSION1",
                )
                second_result, second_bucket, _ = self.run_upload_case(
                    case,
                    first_result[case["pointer_field"]],
                    "VERSION2",
                )

                self.assertNotIn("error", first_result)
                self.assertNotIn("error", second_result)
                self.assertNotEqual(
                    first_result["firebase_blob_path"],
                    second_result["firebase_blob_path"],
                )
                self.assertIn("/versions/VERSION1/profile.jpg", first_result["firebase_blob_path"])
                self.assertIn("/versions/VERSION2/profile.jpg", second_result["firebase_blob_path"])
                self.assertTrue(first_result["firebase_blob_path"].startswith(case["prefix"]))
                self.assertTrue(second_result["firebase_blob_path"].startswith(case["prefix"]))
                self.assertEqual(first_result[case["previous_field"]], "P0")
                self.assertEqual(second_result[case["previous_field"]], first_result[case["pointer_field"]])
                expected_first_pointer = (
                    f"gs://test-bucket/{first_result['firebase_blob_path']}"
                    if case["stores_gs"]
                    else first_result["firebase_blob_path"]
                )
                expected_second_pointer = (
                    f"gs://test-bucket/{second_result['firebase_blob_path']}"
                    if case["stores_gs"]
                    else second_result["firebase_blob_path"]
                )
                self.assertEqual(first_result[case["pointer_field"]], expected_first_pointer)
                self.assertEqual(second_result[case["pointer_field"]], expected_second_pointer)
                self.assertFalse(any(blob.deleted for blob in first_bucket.blobs.values()))
                self.assertFalse(any(blob.deleted for blob in second_bucket.blobs.values()))

    def test_failed_pointer_update_deletes_only_new_uncommitted_blob(self):
        for case in self.upload_cases():
            with self.subTest(service=case["name"]):
                result, bucket, _ = self.run_upload_case(
                    case,
                    "P0",
                    "FAILEDVERSION",
                    update_failure=True,
                )
                self.assertIn("error", result)
                cleanup = result["firebase_cleanup"]
                self.assertTrue(cleanup["cleanup_attempted"])
                self.assertTrue(cleanup["cleanup_completed"])
                self.assertTrue(bucket.blobs[cleanup["firebase_blob_path"]].deleted)
                self.assertFalse(bucket.blobs["P0"].deleted)

    def test_upload_not_found_returns_before_firebase_upload(self):
        for case in self.upload_cases():
            with self.subTest(service=case["name"]):
                results = []
                if not case["get_by_id"]:
                    results.append(FakeResult(row=None))
                conn = QueueConnection(*results)
                engine = FakeEngine(conn)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(case["module"], "db_engine", return_value=engine))
                    initialize_mock = stack.enter_context(
                        patch.object(case["module"], "initialize_firebase_app")
                    )
                    bucket_mock = stack.enter_context(
                        patch.object(case["module"].storage, "bucket")
                    )
                    if case["get_by_id"]:
                        stack.enter_context(patch.object(case["module"], case["get_by_id"], return_value=None))
                    result = case["function"](
                        payload=dict(case["payload"]),
                        file_stream=BytesIO(b"file-bytes"),
                        file_name="profile.jpg",
                        content_type="image/jpeg",
                    )
                self.assertIn("error", result)
                self.assertIn("not found", result["error"].lower())
                initialize_mock.assert_not_called()
                bucket_mock.assert_not_called()

    def test_downloads_resolve_current_database_pointer(self):
        from app_backend.services.service_02_hr_payroll.integrations import firebase_employee_image_download
        from app_backend.services.service_04_maintenance_management.integrations import firebase_maintenance_image_download
        from app_backend.services.service_06_contracts_management.integrations import firebase_contract_document_download
        from app_backend.services.service_08_financial_management.integrations import firebase_ap_invoice_document_download
        from app_backend.services.service_08_financial_management.integrations import firebase_ar_invoice_document_download

        download_cases = [
            {
                "name": "employee",
                "module": firebase_employee_image_download,
                "function": firebase_employee_image_download.download_employee_image,
                "payload": {"employee_id": "UE001", "empl_org_id_fk": 1},
                "pointer_field": "employee_image_path",
                "stored_path": "firebase_upload_files/employee_images/UE001/versions/CURRENT/profile.jpg",
                "expected_blob": "firebase_upload_files/employee_images/UE001/versions/CURRENT/profile.jpg",
                "dataframe": pd.DataFrame([{
                    "employee_image_path": "firebase_upload_files/employee_images/UE001/versions/CURRENT/profile.jpg",
                }]),
                "get_by_id": None,
            },
            {
                "name": "maintenance",
                "module": firebase_maintenance_image_download,
                "function": firebase_maintenance_image_download.download_maintenance_image,
                "payload": {"maint_id": 501, "authenticated_org_id": 1},
                "pointer_field": "maint_image_path",
                "stored_path": "firebase_upload_files/maintenance_images/501/versions/CURRENT/profile.jpg",
                "expected_blob": "firebase_upload_files/maintenance_images/501/versions/CURRENT/profile.jpg",
                "dataframe": pd.DataFrame([{
                    "maint_id_pk": 501,
                    "maint_fleet_vehicle_id_fk": 22,
                    "maint_image_path": "firebase_upload_files/maintenance_images/501/versions/CURRENT/profile.jpg",
                }]),
                "get_by_id": None,
            },
            {
                "name": "contract",
                "module": firebase_contract_document_download,
                "function": firebase_contract_document_download.download_contract_document,
                "payload": {"cont_id": 99, "cont_org_id_fk": 1},
                "pointer_field": "cont_link_path",
                "stored_path": "firebase_upload_files/contracts/CT-1/versions/CURRENT/signed.pdf",
                "expected_blob": "firebase_upload_files/contracts/CT-1/versions/CURRENT/signed.pdf",
                "dataframe": pd.DataFrame([{
                    "cont_id_pk": 99,
                    "cont_contract_number": "CT-1",
                    "cont_link_path": "firebase_upload_files/contracts/CT-1/versions/CURRENT/signed.pdf",
                }]),
                "get_by_id": None,
            },
            {
                "name": "ap",
                "module": firebase_ap_invoice_document_download,
                "function": firebase_ap_invoice_document_download.download_ap_invoice_document,
                "payload": {"ap_id": 111, "ap_org_id_fk": 1},
                "pointer_field": "ap_invoice_file_path",
                "stored_path": "gs://test-bucket/firebase_upload_files/ap_invoices/AP-1/versions/CURRENT/invoice.pdf",
                "expected_blob": "firebase_upload_files/ap_invoices/AP-1/versions/CURRENT/invoice.pdf",
                "dataframe": None,
                "get_by_id": "get_ap_by_id",
                "existing_row": {
                    "ap_id_pk": 111,
                    "ap_invoice_number": "AP-1",
                    "ap_invoice_file_path": "gs://test-bucket/firebase_upload_files/ap_invoices/AP-1/versions/CURRENT/invoice.pdf",
                },
            },
            {
                "name": "ar",
                "module": firebase_ar_invoice_document_download,
                "function": firebase_ar_invoice_document_download.download_ar_invoice_document,
                "payload": {"ar_id": 222, "ar_org_id_fk": 1},
                "pointer_field": "ar_invoice_file_path",
                "stored_path": "gs://test-bucket/firebase_upload_files/ar_invoices/AR-1/versions/CURRENT/invoice.pdf",
                "expected_blob": "firebase_upload_files/ar_invoices/AR-1/versions/CURRENT/invoice.pdf",
                "dataframe": None,
                "get_by_id": "get_ar_by_id",
                "existing_row": {
                    "ar_id_pk": 222,
                    "ar_invoice_number": "AR-1",
                    "ar_invoice_file_path": "gs://test-bucket/firebase_upload_files/ar_invoices/AR-1/versions/CURRENT/invoice.pdf",
                },
            },
        ]

        for case in download_cases:
            with self.subTest(service=case["name"]):
                bucket = FakeBucket()
                with ExitStack() as stack:
                    stack.enter_context(patch.object(case["module"], "FIREBASE_STORAGE_BUCKET", "test-bucket"))
                    stack.enter_context(patch.object(case["module"], "initialize_firebase_app"))
                    stack.enter_context(patch.object(case["module"].storage, "bucket", return_value=bucket))
                    if case["get_by_id"]:
                        stack.enter_context(
                            patch.object(case["module"], case["get_by_id"], return_value=case["existing_row"])
                        )
                    else:
                        stack.enter_context(patch.object(case["module"], "db_engine", return_value=FakeEngine(QueueConnection())))
                        stack.enter_context(patch.object(case["module"].pd, "read_sql", return_value=case["dataframe"]))
                    result = case["function"](dict(case["payload"]))

                self.assertNotIn("error", result)
                self.assertEqual(result[case["pointer_field"]], case["stored_path"])
                self.assertEqual(bucket.blob_path, case["expected_blob"])
                self.assertEqual(result["download_url"], f"https://signed.example/{case['expected_blob']}")

    def test_normal_crud_updates_ignore_stale_path_fields(self):
        from app_backend.services.service_02_hr_payroll.logic import employee_master_update_data
        from app_backend.services.service_04_maintenance_management.logic import maintenance_master_update_data
        from app_backend.services.service_06_contracts_management.logic import contracts_management_update_data
        from app_backend.services.service_08_financial_management.logic import accounts_payables_update_data
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_update_data

        employee_conn = QueueConnection(FakeResult(row={"empl_id_pk": 11, "employee_image_path": "P1"}))
        with patch.object(employee_master_update_data, "db_engine", return_value=FakeEngine(employee_conn)), \
             patch.object(
                 employee_master_update_data.pd,
                 "read_sql",
                 return_value=pd.DataFrame([{"empl_id_pk": 11, "employee_image_path": "P1"}]),
             ):
            employee_result = employee_master_update_data.update_employee_master(
                {
                    "empl_id": 11,
                    "empl_org_id_fk": 1,
                    "employee_id": "UE001",
                    "employee_name": "Employee",
                    "employee_image_path": "P0",
                }
            )
        self.assertEqual(employee_result["employee_image_path"], "P1")
        self.assert_pointer_column_not_assigned(employee_conn, "employee_image_path")

        maintenance_conn = QueueConnection(
            FakeResult(row={"maint_id_pk": 501, "maint_image_path": "M2"})
        )
        with patch.object(maintenance_master_update_data, "db_engine", return_value=FakeEngine(maintenance_conn)), \
             patch.object(
                 maintenance_master_update_data.pd,
                 "read_sql",
                 side_effect=[
                     pd.DataFrame([{"maint_id_pk": 501, "maint_image_path": "M2"}]),
                     pd.DataFrame([{"fleet_vehicle_id_pk": 22}]),
                 ],
             ):
            maintenance_result = maintenance_master_update_data.update_maintenance_master(
                {
                    "maint_id": 501,
                    "authenticated_org_id": 1,
                    "maint_fleet_vehicle_id_fk": 22,
                    "maint_image_path": "M0",
                }
            )
        self.assertEqual(maintenance_result["maint_image_path"], "M2")
        self.assert_pointer_column_not_assigned(maintenance_conn, "maint_image_path")

        contract_conn = QueueConnection(
            FakeResult(row={"cont_id_pk": 99, "cont_link_path": "C2"})
        )
        with patch.object(
            contracts_management_update_data.pd,
            "read_sql",
            return_value=pd.DataFrame([{
                "cont_id_pk": 99,
                "cont_org_id_fk": 1,
                "cont_contract_number": "CT-1",
                "cont_link_path": "C2",
            }]),
        ), patch.object(contracts_management_update_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_update_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_update_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            contract_result = contracts_management_update_data.update_contract(
                {
                    "cont_revenue_basis": "PER_BUS",
                    "cont_id": 99,
                    "cont_org_id_fk": 1,
                    "cont_contract_number": "CT-1",
                    "cont_link_path": "C0",
                },
                conn=contract_conn,
            )
        self.assertEqual(contract_result["cont_link_path"], "C2")
        self.assert_pointer_column_not_assigned(contract_conn, "cont_link_path")

        ap_conn = QueueConnection(
            FakeResult(row={"ap_balance_amount": 100, "ap_invoice_file_path": "A2"})
        )
        with patch.object(accounts_payables_update_data, "validate_ap_payload", return_value=None), \
             patch.object(
                 accounts_payables_update_data,
                 "get_ap_by_id",
                 return_value={
                     "ap_org_id_fk": 1,
                     "ap_supp_id_fk": 2,
                     "ap_invoice_number": "AP-1",
                     "ap_invoice_file_path": "A2",
                 },
             ), patch.object(accounts_payables_update_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_update_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_update_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_update_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            ap_result = accounts_payables_update_data.update_accounts_payable(
                {
                    "ap_id": 111,
                    "ap_org_id_fk": 1,
                    "ap_supp_id_fk": 2,
                    "ap_invoice_number": "AP-1",
                    "ap_invoice_file_path": "A0",
                },
                conn=ap_conn,
            )
        self.assertEqual(ap_result["ap_invoice_file_path"], "A2")
        self.assert_pointer_column_not_assigned(ap_conn, "ap_invoice_file_path")

        ar_conn = QueueConnection(
            FakeResult(row={"ar_balance_amount": 100, "ar_invoice_file_path": "R2"})
        )
        with patch.object(accounts_receivables_update_data, "validate_ar_payload", return_value=None), \
             patch.object(
                 accounts_receivables_update_data,
                 "get_ar_by_id",
                 return_value={
                     "ar_org_id_fk": 1,
                     "ar_cust_id_fk": 2,
                     "ar_invoice_number": "AR-1",
                     "ar_invoice_file_path": "R2",
                 },
             ), patch.object(accounts_receivables_update_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_update_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_update_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_update_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            ar_result = accounts_receivables_update_data.update_accounts_receivable(
                {
                    "ar_id": 222,
                    "ar_org_id_fk": 1,
                    "ar_cust_id_fk": 2,
                    "ar_invoice_number": "AR-1",
                    "ar_invoice_file_path": "R0",
                },
                conn=ar_conn,
            )
        self.assertEqual(ar_result["ar_invoice_file_path"], "R2")
        self.assert_pointer_column_not_assigned(ar_conn, "ar_invoice_file_path")

    def test_ordinary_updates_cannot_revert_pointer_committed_during_inflight_update(self):
        from app_backend.services.service_04_maintenance_management.logic import maintenance_master_update_data
        from app_backend.services.service_06_contracts_management.logic import contracts_management_update_data
        from app_backend.services.service_08_financial_management.logic import accounts_payables_update_data
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_update_data

        untrusted_staged_document_fields = {
            "workflow_document_staging_status": "STAGED",
            "workflow_document_cleanup_policy": pointer_helpers.WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY,
            "workflow_document_staged_blob_path": "firebase_upload_files/untrusted/V1/file.pdf",
            "workflow_document_staged_bucket": "bucket",
        }

        maintenance_conn = QueueConnection(
            FakeResult(row={"maint_id_pk": 501, "maint_image_path": "M2"})
        )
        with patch.object(maintenance_master_update_data, "db_engine", return_value=FakeEngine(maintenance_conn)), \
             patch.object(
                 maintenance_master_update_data.pd,
                 "read_sql",
                 side_effect=[
                     pd.DataFrame([{"maint_id_pk": 501, "maint_image_path": "M1"}]),
                     pd.DataFrame([{"fleet_vehicle_id_pk": 22}]),
                 ],
             ):
            maintenance_result = maintenance_master_update_data.update_maintenance_master(
                {
                    "maint_id": 501,
                    "authenticated_org_id": 1,
                    "maint_fleet_vehicle_id_fk": 22,
                    "maint_image_path": "M1",
                }
            )
        self.assertEqual(maintenance_result["maint_image_path"], "M2")
        self.assert_pointer_column_not_assigned(maintenance_conn, "maint_image_path")

        contract_conn = QueueConnection(
            FakeResult(row={"cont_id_pk": 99, "cont_link_path": "C2"})
        )
        with patch.object(
            contracts_management_update_data.pd,
            "read_sql",
            return_value=pd.DataFrame([{
                "cont_id_pk": 99,
                "cont_org_id_fk": 1,
                "cont_contract_number": "CT-1",
                "cont_link_path": "C1",
            }]),
        ), patch.object(contracts_management_update_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_update_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_update_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            contract_result = contracts_management_update_data.update_contract(
                {
                    "cont_revenue_basis": "PER_BUS",
                    "cont_id": 99,
                    "cont_org_id_fk": 1,
                    "cont_contract_number": "CT-1",
                    "cont_link_path": "C1",
                    **untrusted_staged_document_fields,
                },
                conn=contract_conn,
            )
        self.assertEqual(contract_result["cont_link_path"], "C2")
        self.assert_pointer_column_not_assigned(contract_conn, "cont_link_path")

        ap_conn = QueueConnection(
            FakeResult(row={"ap_balance_amount": 100, "ap_invoice_file_path": "A2"})
        )
        with patch.object(accounts_payables_update_data, "validate_ap_payload", return_value=None), \
             patch.object(
                 accounts_payables_update_data,
                 "get_ap_by_id",
                 return_value={
                     "ap_org_id_fk": 1,
                     "ap_supp_id_fk": 2,
                     "ap_invoice_number": "AP-1",
                     "ap_invoice_file_path": "A1",
                 },
             ), patch.object(accounts_payables_update_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_update_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_update_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_update_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            ap_result = accounts_payables_update_data.update_accounts_payable(
                {
                    "ap_id": 111,
                    "ap_org_id_fk": 1,
                    "ap_supp_id_fk": 2,
                    "ap_invoice_number": "AP-1",
                    "ap_invoice_file_path": "A1",
                    **untrusted_staged_document_fields,
                },
                conn=ap_conn,
            )
        self.assertEqual(ap_result["ap_invoice_file_path"], "A2")
        self.assert_pointer_column_not_assigned(ap_conn, "ap_invoice_file_path")

        ar_conn = QueueConnection(
            FakeResult(row={"ar_balance_amount": 100, "ar_invoice_file_path": "R2"})
        )
        with patch.object(accounts_receivables_update_data, "validate_ar_payload", return_value=None), \
             patch.object(
                 accounts_receivables_update_data,
                 "get_ar_by_id",
                 return_value={
                     "ar_org_id_fk": 1,
                     "ar_cust_id_fk": 2,
                     "ar_invoice_number": "AR-1",
                     "ar_invoice_file_path": "R1",
                 },
             ), patch.object(accounts_receivables_update_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_update_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_update_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_update_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            ar_result = accounts_receivables_update_data.update_accounts_receivable(
                {
                    "ar_id": 222,
                    "ar_org_id_fk": 1,
                    "ar_cust_id_fk": 2,
                    "ar_invoice_number": "AR-1",
                    "ar_invoice_file_path": "R1",
                    **untrusted_staged_document_fields,
                },
                conn=ar_conn,
            )
        self.assertEqual(ar_result["ar_invoice_file_path"], "R2")
        self.assert_pointer_column_not_assigned(ar_conn, "ar_invoice_file_path")

    def test_trusted_workflow_replay_promotes_staged_paths_for_contract_ap_ar(self):
        from app_backend.services.service_06_contracts_management.logic import contracts_management_update_data
        from app_backend.services.service_08_financial_management.logic import accounts_payables_update_data
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_update_data

        contract_conn = QueueConnection(update_mapping("cont_id_pk", "cont_link_path"))
        contract_payload = {
            "cont_revenue_basis": "PER_BUS",
            "cont_id": 99,
            "cont_org_id_fk": 1,
            "cont_contract_number": "CT-1",
            "cont_link_path": "firebase_upload_files/contracts/_workflow_staging/1/REQ/CT-1/versions/V2/signed.pdf",
            "workflow_document_staging_status": "STAGED",
            "workflow_document_cleanup_policy": pointer_helpers.WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY,
            "workflow_document_staged_blob_path": "firebase_upload_files/contracts/_workflow_staging/1/REQ/CT-1/versions/V2/signed.pdf",
        }
        with trusted_workflow_execution(), \
             patch.object(
                 contracts_management_update_data.pd,
                 "read_sql",
                 return_value=pd.DataFrame([{
                     "cont_id_pk": 99,
                     "cont_org_id_fk": 1,
                     "cont_contract_number": "CT-1",
                     "cont_link_path": "C1",
                 }]),
             ), patch.object(contracts_management_update_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_update_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_update_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            contract_result = contracts_management_update_data.update_contract(
                contract_payload,
                conn=contract_conn,
            )
        self.assertEqual(contract_result["cont_link_path"], contract_payload["cont_link_path"])
        self.assert_pointer_column_assigned(contract_conn, "cont_link_path")
        self.assertEqual(contract_conn.executions[-1][1]["cont_link_path"], contract_payload["cont_link_path"])

        ap_conn = QueueConnection(
            lambda _statement, params: FakeResult(
                row={
                    "ap_balance_amount": 100,
                    "ap_invoice_file_path": params["ap_invoice_file_path"],
                }
            )
        )
        ap_payload = {
            "ap_id": 111,
            "ap_org_id_fk": 1,
            "ap_supp_id_fk": 2,
            "ap_invoice_number": "AP-1",
            "ap_invoice_file_path": "gs://bucket/firebase_upload_files/ap_invoices/_workflow_staging/1/REQ/AP-1/versions/V2/invoice.pdf",
            "workflow_document_staging_status": "STAGED",
            "workflow_document_cleanup_policy": pointer_helpers.WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY,
            "workflow_document_staged_blob_path": "firebase_upload_files/ap_invoices/_workflow_staging/1/REQ/AP-1/versions/V2/invoice.pdf",
        }
        with trusted_workflow_execution(), \
             patch.object(accounts_payables_update_data, "validate_ap_payload", return_value=None), \
             patch.object(
                 accounts_payables_update_data,
                 "get_ap_by_id",
                 return_value={
                     "ap_org_id_fk": 1,
                     "ap_supp_id_fk": 2,
                     "ap_invoice_number": "AP-1",
                     "ap_invoice_file_path": "A1",
                 },
             ), patch.object(accounts_payables_update_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_update_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_update_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_update_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            ap_result = accounts_payables_update_data.update_accounts_payable(
                ap_payload,
                conn=ap_conn,
            )
        self.assertEqual(ap_result["ap_invoice_file_path"], ap_payload["ap_invoice_file_path"])
        self.assert_pointer_column_assigned(ap_conn, "ap_invoice_file_path")
        self.assertEqual(ap_conn.executions[-1][1]["ap_invoice_file_path"], ap_payload["ap_invoice_file_path"])

        ar_conn = QueueConnection(
            lambda _statement, params: FakeResult(
                row={
                    "ar_balance_amount": 100,
                    "ar_invoice_file_path": params["ar_invoice_file_path"],
                }
            )
        )
        ar_payload = {
            "ar_id": 222,
            "ar_org_id_fk": 1,
            "ar_cust_id_fk": 2,
            "ar_invoice_number": "AR-1",
            "ar_invoice_file_path": "gs://bucket/firebase_upload_files/ar_invoices/_workflow_staging/1/REQ/AR-1/versions/V2/invoice.pdf",
            "workflow_document_staging_status": "STAGED",
            "workflow_document_cleanup_policy": pointer_helpers.WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY,
            "workflow_document_staged_blob_path": "firebase_upload_files/ar_invoices/_workflow_staging/1/REQ/AR-1/versions/V2/invoice.pdf",
        }
        with trusted_workflow_execution(), \
             patch.object(accounts_receivables_update_data, "validate_ar_payload", return_value=None), \
             patch.object(
                 accounts_receivables_update_data,
                 "get_ar_by_id",
                 return_value={
                     "ar_org_id_fk": 1,
                     "ar_cust_id_fk": 2,
                     "ar_invoice_number": "AR-1",
                     "ar_invoice_file_path": "R1",
                 },
             ), patch.object(accounts_receivables_update_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_update_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_update_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_update_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            ar_result = accounts_receivables_update_data.update_accounts_receivable(
                ar_payload,
                conn=ar_conn,
            )
        self.assertEqual(ar_result["ar_invoice_file_path"], ar_payload["ar_invoice_file_path"])
        self.assert_pointer_column_assigned(ar_conn, "ar_invoice_file_path")
        self.assertEqual(ar_conn.executions[-1][1]["ar_invoice_file_path"], ar_payload["ar_invoice_file_path"])

    def test_create_operations_ignore_public_current_path_fields(self):
        from app_backend.services.service_02_hr_payroll.logic import employee_master_create_data
        from app_backend.services.service_04_maintenance_management.logic import maintenance_master_create_data
        from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data
        from app_backend.services.service_08_financial_management.logic import accounts_payables_create_data
        from app_backend.services.service_08_financial_management.logic import accounts_receivables_create_data

        employee_conn = QueueConnection(FakeResult())
        with patch.object(employee_master_create_data, "db_engine", return_value=FakeEngine(employee_conn)), \
             patch.object(employee_master_create_data.pd, "read_sql", return_value=pd.DataFrame()):
            employee_master_create_data.create_employee_master(
                {
                    "empl_org_id_fk": 1,
                    "employee_id": "UE001",
                    "employee_name": "Employee",
                    "employee_image_path": "P0",
                }
            )
        self.assertIsNone(employee_conn.executions[-1][1]["employee_image_path"])

        maintenance_conn = QueueConnection(FakeResult(scalar=501))
        with patch.object(maintenance_master_create_data, "db_engine", return_value=FakeEngine(maintenance_conn)), \
             patch.object(
                 maintenance_master_create_data.pd,
                 "read_sql",
                 return_value=pd.DataFrame([{"fleet_vehicle_id_pk": 22}]),
             ):
            maintenance_master_create_data.create_maintenance(
                {
                    "authenticated_org_id": 1,
                    "maint_fleet_vehicle_id_fk": 22,
                    "maint_image_path": "M0",
                }
            )
        self.assertIsNone(maintenance_conn.executions[-1][1]["maint_image_path"])

        contract_conn = QueueConnection(FakeResult(scalar=99))
        with patch.object(contracts_management_create_data, "validate_contract_references_for_organization", return_value=None), \
             patch.object(contracts_management_create_data, "contract_number_exists", return_value=False), \
             patch.object(
                 contracts_management_create_data,
                 "is_contracts_management_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            contracts_management_create_data.create_contract(
                {
                    "cont_revenue_basis": "PER_BUS",
                    "cont_org_id_fk": 1,
                    "cont_contract_number": "CT-1",
                    "cont_link_path": "C0",
                },
                conn=contract_conn,
            )
        self.assertIsNone(contract_conn.executions[-1][1]["cont_link_path"])

        ap_conn = QueueConnection(
            FakeResult(row={"ap_id_pk": 111, "ap_balance_amount": 100, "ap_invoice_file_path": None})
        )
        with patch.object(accounts_payables_create_data, "validate_ap_payload", return_value=None), \
             patch.object(accounts_payables_create_data, "verify_organization_exists", return_value=True), \
             patch.object(accounts_payables_create_data, "verify_supplier_for_organization", return_value=True), \
             patch.object(accounts_payables_create_data, "duplicate_ap_invoice_exists", return_value=False), \
             patch.object(
                 accounts_payables_create_data,
                 "is_accounts_payables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            accounts_payables_create_data.create_accounts_payable(
                {
                    "ap_org_id_fk": 1,
                    "ap_supp_id_fk": 2,
                    "ap_invoice_number": "AP-1",
                    "ap_invoice_file_path": "A0",
                },
                conn=ap_conn,
            )
        self.assertIsNone(ap_conn.executions[-1][1]["ap_invoice_file_path"])

        ar_conn = QueueConnection(
            FakeResult(row={"ar_id_pk": 222, "ar_balance_amount": 100, "ar_invoice_file_path": None})
        )
        with patch.object(accounts_receivables_create_data, "validate_ar_payload", return_value=None), \
             patch.object(accounts_receivables_create_data, "verify_ar_organization_exists", return_value=True), \
             patch.object(accounts_receivables_create_data, "verify_customer_for_organization", return_value=True), \
             patch.object(accounts_receivables_create_data, "duplicate_ar_invoice_exists", return_value=False), \
             patch.object(
                 accounts_receivables_create_data,
                 "is_accounts_receivables_workflow_approved",
                 return_value=(True, {"workflow_status": "APPROVED"}),
             ):
            accounts_receivables_create_data.create_accounts_receivable(
                {
                    "ar_org_id_fk": 1,
                    "ar_cust_id_fk": 2,
                    "ar_invoice_number": "AR-1",
                    "ar_invoice_file_path": "R0",
                },
                conn=ar_conn,
            )
        self.assertIsNone(ar_conn.executions[-1][1]["ar_invoice_file_path"])


if __name__ == "__main__":
    unittest.main()
