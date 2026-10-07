import base64
import json
import os

import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, storage


load_dotenv()

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_STORAGE_BUCKET = (
    os.getenv("FIREBASE_STORAGE_BUCKET")
    or os.getenv("FIREBASE_PROJECT_NAME")
)
FIREBASE_PROJECT_ACCESS_BASE64 = os.getenv("FIREBASE_PROJECT_ACCESS_BASE64")
WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY = (
    "DELETE_STAGED_DOCUMENT_IF_WORKFLOW_REJECTED_OR_CANCELLED"
)
WORKFLOW_DOCUMENT_CLEANUP_TERMINAL_STATUSES = {"REJECTED", "CANCELLED"}
PROMOTED_DOCUMENT_STATUSES = {"PROMOTED", "RETAINED"}


def initialize_firebase_app():
    if firebase_admin._apps:
        return firebase_admin.get_app()

    if not FIREBASE_PROJECT_ACCESS_BASE64:
        raise Exception("FIREBASE_PROJECT_ACCESS_BASE64 is missing in .env.")

    if not FIREBASE_STORAGE_BUCKET:
        raise Exception("FIREBASE_STORAGE_BUCKET or FIREBASE_PROJECT_NAME is missing in .env.")

    service_account_json = base64.b64decode(FIREBASE_PROJECT_ACCESS_BASE64).decode("utf-8")
    service_account_info = json.loads(service_account_json)
    firebase_credentials = credentials.Certificate(service_account_info)

    return firebase_admin.initialize_app(
        firebase_credentials,
        {
            "projectId": FIREBASE_PROJECT_ID,
            "storageBucket": FIREBASE_STORAGE_BUCKET,
        },
    )


def cleanup_staged_workflow_document(
    payload: dict | None,
    terminal_status: str | None = None,
    force: bool = False,
) -> dict:
    payload = payload or {}
    blob_path = payload.get("workflow_document_staged_blob_path")
    cleanup_policy = payload.get("workflow_document_cleanup_policy")
    staging_status = (payload.get("workflow_document_staging_status") or "").upper()
    terminal_status = (terminal_status or "").upper()

    cleanup_required = (
        blob_path
        and cleanup_policy == WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY
        and staging_status not in PROMOTED_DOCUMENT_STATUSES
        and (force or terminal_status in WORKFLOW_DOCUMENT_CLEANUP_TERMINAL_STATUSES)
    )
    if not cleanup_required:
        return {
            "cleanup_required": False,
            "cleanup_attempted": False,
            "firebase_blob_path": blob_path,
            "firebase_bucket": payload.get("workflow_document_staged_bucket"),
        }

    bucket_name = payload.get("workflow_document_staged_bucket") or FIREBASE_STORAGE_BUCKET
    try:
        initialize_firebase_app()
        bucket = storage.bucket(bucket_name) if bucket_name else storage.bucket()
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return {
                "cleanup_required": True,
                "cleanup_attempted": True,
                "cleanup_completed": True,
                "already_absent": True,
                "firebase_blob_path": blob_path,
                "firebase_bucket": bucket_name,
            }
        blob.delete()
        return {
            "cleanup_required": True,
            "cleanup_attempted": True,
            "cleanup_completed": True,
            "already_absent": False,
            "firebase_blob_path": blob_path,
            "firebase_bucket": bucket_name,
        }
    except Exception as exc:
        return {
            "cleanup_required": True,
            "cleanup_attempted": True,
            "cleanup_completed": False,
            "firebase_blob_path": blob_path,
            "firebase_bucket": bucket_name,
            "error": f"Failed to cleanup staged workflow document. Error Message: {str(exc)}",
        }
