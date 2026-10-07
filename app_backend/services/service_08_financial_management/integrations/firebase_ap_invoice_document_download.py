import os
from datetime import datetime, timedelta, timezone

from firebase_admin import storage

from app_backend.services.service_08_financial_management.integrations.firebase_ap_invoice_document_upload import (
    FIREBASE_STORAGE_BUCKET,
    initialize_firebase_app,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_common import get_ap_by_id

SIGNED_URL_EXPIRES_IN_SECONDS = 15 * 60


def get_blob_path_from_ap_invoice_file_path(ap_invoice_file_path: str):
    if not ap_invoice_file_path:
        return None

    gs_bucket_prefix = f"gs://{FIREBASE_STORAGE_BUCKET}/"
    if ap_invoice_file_path.startswith(gs_bucket_prefix):
        return ap_invoice_file_path.replace(gs_bucket_prefix, "", 1)

    return ap_invoice_file_path


def download_ap_invoice_document_from_firebase(payload: dict):
    try:
        ap_id = payload.get("ap_id") or payload.get("ap_id_pk")
        ap_org_id_fk = payload.get("ap_org_id_fk") or payload.get("authenticated_org_id")

        if not ap_id:
            return {
                "error": (
                    "ap_id is required for AP invoice document download because "
                    "ap_invoice_number is only unique by organization and supplier."
                )
            }
        if not ap_org_id_fk:
            return {"error": "ap_org_id_fk is required."}

        existing_ap = get_ap_by_id(ap_id, ap_org_id_fk)
        if not existing_ap:
            return {"error": "AP invoice not found."}

        ap_invoice_file_path = existing_ap.get("ap_invoice_file_path")
        blob_path = get_blob_path_from_ap_invoice_file_path(ap_invoice_file_path)

        if not blob_path:
            return {"error": f"ap_invoice_file_path is empty for AP invoice ID: {ap_id}"}

        initialize_firebase_app()
        bucket = storage.bucket()
        blob = bucket.blob(blob_path)

        if not blob.exists():
            return {"error": f"File not found in Firebase Storage: {blob_path}"}

        blob.reload()
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=SIGNED_URL_EXPIRES_IN_SECONDS
        )
        download_url = blob.generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="GET"
        )

        return {
            "message": f"Successfully generated AP invoice document download URL: {ap_id}",
            "ap_id": ap_id,
            "ap_invoice_number": existing_ap.get("ap_invoice_number"),
            "ap_invoice_file_path": ap_invoice_file_path,
            "file_name": os.path.basename(blob_path),
            "content_type": blob.content_type or "application/octet-stream",
            "download_url": download_url,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": SIGNED_URL_EXPIRES_IN_SECONDS
        }

    except Exception as e:
        return {
            "error": f"Failed to download AP invoice document from Firebase. Error Message: {str(e)}"
        }


def download_ap_invoice_document(payload: dict):
    return download_ap_invoice_document_from_firebase(payload)
