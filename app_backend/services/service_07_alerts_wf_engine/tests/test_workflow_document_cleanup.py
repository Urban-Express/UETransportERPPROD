import unittest
from unittest.mock import patch

from app_backend.services.service_07_alerts_wf_engine import workflow_document_cleanup


class FakeBlob:
    def __init__(self, exists=True):
        self.exists_value = exists
        self.deleted = False

    def exists(self):
        return self.exists_value

    def delete(self):
        self.deleted = True
        self.exists_value = False


class FakeBucket:
    def __init__(self, blob):
        self.blob_value = blob
        self.requested_blob_path = None

    def blob(self, blob_path):
        self.requested_blob_path = blob_path
        return self.blob_value


class WorkflowDocumentCleanupTests(unittest.TestCase):
    def staged_payload(self):
        return {
            "workflow_document_staging_status": "STAGED",
            "workflow_document_cleanup_policy": "DELETE_STAGED_DOCUMENT_IF_WORKFLOW_REJECTED_OR_CANCELLED",
            "workflow_document_staged_blob_path": "firebase_upload_files/ap_invoices/_workflow_staging/1/REQ/invoice.pdf",
            "workflow_document_staged_bucket": "bucket",
        }

    def test_rejected_staged_document_is_deleted(self):
        blob = FakeBlob(exists=True)
        bucket = FakeBucket(blob)
        with patch.object(workflow_document_cleanup, "initialize_firebase_app"), \
             patch.object(workflow_document_cleanup.storage, "bucket", return_value=bucket) as bucket_mock:
            result = workflow_document_cleanup.cleanup_staged_workflow_document(
                self.staged_payload(),
                terminal_status="REJECTED",
            )

        self.assertTrue(result["cleanup_completed"])
        self.assertFalse(result["already_absent"])
        self.assertTrue(blob.deleted)
        self.assertEqual(bucket.requested_blob_path, self.staged_payload()["workflow_document_staged_blob_path"])
        bucket_mock.assert_called_once_with("bucket")

    def test_missing_staged_document_cleanup_is_idempotent(self):
        blob = FakeBlob(exists=False)
        bucket = FakeBucket(blob)
        with patch.object(workflow_document_cleanup, "initialize_firebase_app"), \
             patch.object(workflow_document_cleanup.storage, "bucket", return_value=bucket):
            result = workflow_document_cleanup.cleanup_staged_workflow_document(
                self.staged_payload(),
                terminal_status="REJECTED",
            )

        self.assertTrue(result["cleanup_completed"])
        self.assertTrue(result["already_absent"])
        self.assertFalse(blob.deleted)

    def test_cancelled_staged_document_is_deleted(self):
        blob = FakeBlob(exists=True)
        bucket = FakeBucket(blob)
        with patch.object(workflow_document_cleanup, "initialize_firebase_app"), \
             patch.object(workflow_document_cleanup.storage, "bucket", return_value=bucket):
            result = workflow_document_cleanup.cleanup_staged_workflow_document(
                self.staged_payload(),
                terminal_status="CANCELLED",
            )

        self.assertTrue(result["cleanup_completed"])
        self.assertTrue(blob.deleted)

    def test_executed_or_promoted_document_is_not_deleted(self):
        promoted_payload = {
            **self.staged_payload(),
            "workflow_document_staging_status": "PROMOTED",
        }
        with patch.object(workflow_document_cleanup.storage, "bucket") as bucket_mock:
            executed = workflow_document_cleanup.cleanup_staged_workflow_document(
                self.staged_payload(),
                terminal_status="EXECUTED",
            )
            promoted = workflow_document_cleanup.cleanup_staged_workflow_document(
                promoted_payload,
                terminal_status="REJECTED",
            )

        self.assertFalse(executed["cleanup_required"])
        self.assertFalse(promoted["cleanup_required"])
        bucket_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
