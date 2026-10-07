import os
from datetime import datetime, timedelta, timezone

from firebase_admin import storage

from app_backend.services.service_08_financial_management.integrations.firebase_ar_invoice_document_upload import (
    FIREBASE_STORAGE_BUCKET,
    initialize_firebase_app,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_common import (
    get_ar_by_id,
)

SIGNED_URL_EXPIRES_IN_SECONDS = 15 * 60


def get_blob_path_from_ar_invoice_file_path(ar_invoice_file_path: str):
    if not ar_invoice_file_path:
        return None

    gs_bucket_prefix = f"gs://{FIREBASE_STORAGE_BUCKET}/"
    if ar_invoice_file_path.startswith(gs_bucket_prefix):
        return ar_invoice_file_path.replace(gs_bucket_prefix, "", 1)

    return ar_invoice_file_path


def download_ar_invoice_document_from_firebase(payload: dict):
    try:
        ar_id = payload.get("ar_id") or payload.get("ar_id_pk")
        ar_org_id_fk = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")

        if not ar_id:
            return {
                "error": (
                    "ar_id is required for AR invoice document download because "
                    "ar_invoice_number is only unique by organization."
                )
            }
        if not ar_org_id_fk:
            return {"error": "ar_org_id_fk is required."}

        existing_ar = get_ar_by_id(ar_id, ar_org_id_fk)
        if not existing_ar:
            return {"error": "AR invoice not found."}

        ar_invoice_file_path = existing_ar.get("ar_invoice_file_path")
        blob_path = get_blob_path_from_ar_invoice_file_path(ar_invoice_file_path)

        if not blob_path:
            return {"error": f"ar_invoice_file_path is empty for AR invoice ID: {ar_id}"}

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
            "message": f"Successfully generated AR invoice document download URL: {ar_id}",
            "ar_id": ar_id,
            "ar_invoice_number": existing_ar.get("ar_invoice_number"),
            "ar_invoice_file_path": ar_invoice_file_path,
            "file_name": os.path.basename(blob_path),
            "content_type": blob.content_type or "application/octet-stream",
            "download_url": download_url,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": SIGNED_URL_EXPIRES_IN_SECONDS
        }

    except Exception as e:
        return {
            "error": f"Failed to download AR invoice document from Firebase. Error Message: {str(e)}"
        }


def download_ar_invoice_document(payload: dict):
    return download_ar_invoice_document_from_firebase(payload)
