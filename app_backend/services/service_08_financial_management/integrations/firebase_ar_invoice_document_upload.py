import base64
import json
import mimetypes
import os

import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, storage
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    build_versioned_blob_path,
    delete_firebase_blob_if_exists,
    resolve_storage_folder,
    safe_file_name,
    upload_staged_document,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_common import (
    get_ar_by_id,
)

load_dotenv()

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_STORAGE_BUCKET = (
    os.getenv("FIREBASE_STORAGE_BUCKET")
    or os.getenv("FIREBASE_PROJECT_NAME")
)
FIREBASE_PROJECT_ACCESS_BASE64 = os.getenv("FIREBASE_PROJECT_ACCESS_BASE64")
DEFAULT_AR_STORAGE_FOLDER = "firebase_upload_files/ar_invoices"


def get_safe_file_name(file_name: str):
    return safe_file_name(file_name)


def get_upload_content_type(payload: dict, file_name: str, content_type: str | None):
    return (
        content_type
        or payload.get("content_type")
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )


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
            "storageBucket": FIREBASE_STORAGE_BUCKET
        }
    )


def get_ar_identifier(payload: dict):
    ar_id = payload.get("ar_id") or payload.get("ar_id_pk")
    ar_invoice_number = payload.get("ar_invoice_number")
    ar_org_id_fk = payload.get("ar_org_id_fk")
    return ar_id, ar_invoice_number, ar_org_id_fk


def get_ar_invoice_document_blob_path(
    payload: dict,
    file_name: str,
    allow_workflow_staging: bool = False,
):
    storage_folder = resolve_storage_folder(
        DEFAULT_AR_STORAGE_FOLDER,
        payload.get("storage_folder"),
        allow_workflow_staging=allow_workflow_staging,
    )
    ar_id, ar_invoice_number, _ = get_ar_identifier(payload)
    ar_key = ar_invoice_number or ar_id
    safe_file_name = get_safe_file_name(file_name)

    if not safe_file_name:
        return None

    blob_path, _ = build_versioned_blob_path(
        storage_folder,
        ar_key,
        safe_file_name,
    )
    return blob_path


def upload_ar_invoice_document_to_firebase(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    storage_folder: str | None = None,
    update_ar_document_link: bool = True,
    require_ar_invoice: bool = True
):
    bucket = None
    blob_path = None
    pointer_committed = False
    try:
        if storage_folder:
            payload = {**payload, "storage_folder": storage_folder}
        ar_id, ar_invoice_number, ar_org_id_fk = get_ar_identifier(payload)
        ar_org_id_fk = ar_org_id_fk or payload.get("authenticated_org_id")
        safe_file_name = get_safe_file_name(file_name)

        if not ar_id and not ar_invoice_number:
            return {"error": "ar_id or ar_invoice_number is required."}

        if not file_stream:
            return {"error": "file is required."}

        if not safe_file_name:
            return {"error": "file_name is required."}
        if not ar_org_id_fk:
            return {"error": "ar_org_id_fk is required."}
        if update_ar_document_link and not ar_id:
            return {"error": "ar_id is required to update ar_invoice_file_path."}

        previous_ar_invoice_file_path = None
        if require_ar_invoice:
            if not ar_id:
                return {
                    "error": (
                        "ar_id is required for AR invoice document upload because "
                        "ar_invoice_number is only unique by organization."
                    )
                }

            existing_ar = get_ar_by_id(ar_id, ar_org_id_fk)
            if not existing_ar:
                return {"error": "AR invoice not found."}
            if update_ar_document_link and existing_ar.get("ar_subtotal_amount") is not None:
                return {"error": "Invalid external upload: line-based invoices use a canonical generated PDF; update the invoice through its AR workflow."}

            ar_id = existing_ar.get("ar_id_pk") or ar_id
            ar_invoice_number = existing_ar.get("ar_invoice_number")
            previous_ar_invoice_file_path = existing_ar.get("ar_invoice_file_path")

        resolved_storage_folder = resolve_storage_folder(
            DEFAULT_AR_STORAGE_FOLDER,
            payload.get("storage_folder"),
            allow_workflow_staging=not update_ar_document_link,
        )
        ar_key = ar_invoice_number or ar_id
        blob_path, upload_version_id = build_versioned_blob_path(
            resolved_storage_folder,
            ar_key,
            safe_file_name,
        )
        upload_content_type = get_upload_content_type(payload, safe_file_name, content_type)

        initialize_firebase_app()
        bucket = storage.bucket()
        blob = bucket.blob(blob_path)
        attachment = None
        if update_ar_document_link:
            blob.upload_from_file(file_stream, content_type=upload_content_type, rewind=True)
        else:
            attachment = upload_staged_document(
                blob, file_stream, safe_file_name, upload_content_type,
                upload_version_id, "AR_INVOICE", ar_org_id_fk,
            )
        firebase_storage_path = f"gs://{FIREBASE_STORAGE_BUCKET}/{blob_path}"

        if update_ar_document_link:
            if not ar_id:
                return {"error": "ar_id is required to update ar_invoice_file_path."}

            update_ar_invoice_file_path = text("""
                update accounts_receivables
                set
                    ar_invoice_file_path = :ar_invoice_file_path,
                    updated_by = :updated_by
                where ar_id_pk = :ar_id
                and ar_org_id_fk = :ar_org_id_fk
                returning ar_invoice_file_path
            """)

            ar_engine = db_engine()
            try:
                with ar_engine.begin() as conn:
                    committed_ar_invoice_file_path = conn.execute(
                        update_ar_invoice_file_path,
                        {
                            "ar_invoice_file_path": firebase_storage_path,
                            "updated_by": payload.get("updated_by") or payload.get("created_by"),
                            "ar_id": ar_id,
                            "ar_org_id_fk": ar_org_id_fk
                        },
                    ).scalar_one_or_none()
            finally:
                ar_engine.dispose()

            if not committed_ar_invoice_file_path:
                raise RuntimeError("AR invoice document pointer update affected zero rows.")
            pointer_committed = True
        else:
            committed_ar_invoice_file_path = firebase_storage_path

        return {
            "message": f"Successfully uploaded AR invoice document: {ar_invoice_number or ar_id}",
            "ar_id": ar_id,
            "ar_invoice_number": ar_invoice_number,
            "ar_invoice_file_path": committed_ar_invoice_file_path,
            "previous_ar_invoice_file_path": previous_ar_invoice_file_path,
            "firebase_blob_path": blob_path,
            "firebase_storage_path": firebase_storage_path,
            "firebase_bucket": FIREBASE_STORAGE_BUCKET,
            "file_name": safe_file_name,
            "content_type": upload_content_type,
            "upload_version_id": upload_version_id,
            **({"workflow_document_attachment": attachment} if attachment else {}),
        }

    except Exception as e:
        response = {
            "error": f"Failed to upload AR invoice document to Firebase. Error Message: {str(e)}"
        }
        if blob_path and bucket and not pointer_committed:
            response["firebase_cleanup"] = delete_firebase_blob_if_exists(bucket, blob_path)
        return response


def upload_ar_invoice_document(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None
):
    return upload_ar_invoice_document_to_firebase(
        payload,
        file_stream,
        file_name,
        content_type
    )
