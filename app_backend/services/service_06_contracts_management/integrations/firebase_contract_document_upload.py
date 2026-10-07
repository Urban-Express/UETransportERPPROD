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

load_dotenv()

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_STORAGE_BUCKET = (
    os.getenv("FIREBASE_STORAGE_BUCKET")
    or os.getenv("FIREBASE_PROJECT_NAME")
)
FIREBASE_PROJECT_ACCESS_BASE64 = os.getenv("FIREBASE_PROJECT_ACCESS_BASE64")
DEFAULT_CONTRACT_STORAGE_FOLDER = "firebase_upload_files/contracts"


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


def get_contract_identifier(payload: dict):
    cont_id = (
        payload.get("cont_id")
        or payload.get("cont_id_pk")
    )
    cont_contract_number = payload.get("cont_contract_number")
    cont_org_id_fk = payload.get("cont_org_id_fk")
    return cont_id, cont_contract_number, cont_org_id_fk


def get_contract_document_blob_path(
    payload: dict,
    file_name: str,
    allow_workflow_staging: bool = False,
):
    storage_folder = resolve_storage_folder(
        DEFAULT_CONTRACT_STORAGE_FOLDER,
        payload.get("storage_folder"),
        allow_workflow_staging=allow_workflow_staging,
    )
    cont_id, cont_contract_number, _ = get_contract_identifier(payload)
    contract_key = cont_contract_number or cont_id
    safe_file_name = get_safe_file_name(file_name)

    if not safe_file_name:
        return None

    blob_path, _ = build_versioned_blob_path(
        storage_folder,
        contract_key,
        safe_file_name,
    )
    return blob_path


def upload_contract_document_to_firebase(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    storage_folder: str | None = None,
    update_contract_link: bool = True,
    require_contract: bool = True
):
    contract_engine = None
    bucket = None
    blob_path = None
    pointer_committed = False

    def get_contract_engine():
        nonlocal contract_engine
        if contract_engine is None:
            contract_engine = db_engine()
        return contract_engine

    try:
        if storage_folder:
            payload = {**payload, "storage_folder": storage_folder}
        updated_by = payload.get("updated_by") or payload.get("created_by")
        cont_id, cont_contract_number, cont_org_id_fk = get_contract_identifier(payload)
        cont_org_id_fk = cont_org_id_fk or payload.get("authenticated_org_id")
        safe_file_name = get_safe_file_name(file_name)

        if not cont_id and not cont_contract_number:
            return {"error": "cont_id or cont_contract_number is required."}

        if not file_stream:
            return {"error": "file is required."}

        if not safe_file_name:
            return {"error": "file_name is required."}
        if not cont_org_id_fk:
            return {"error": "cont_org_id_fk is required."}

        if require_contract:
            if cont_id:
                get_contract = text("""
                    select cont_id_pk, cont_contract_number, cont_link_path
                    from contracts_management
                    where cont_id_pk = :cont_id
                    and cont_org_id_fk = :cont_org_id_fk
                """)
                params_get = {
                    "cont_id": cont_id,
                    "cont_org_id_fk": cont_org_id_fk
                }
            else:
                get_contract = text("""
                    select cont_id_pk, cont_contract_number, cont_link_path
                    from contracts_management
                    where cont_contract_number = :cont_contract_number
                    and cont_org_id_fk = :cont_org_id_fk
                """)
                params_get = {
                    "cont_contract_number": cont_contract_number,
                    "cont_org_id_fk": cont_org_id_fk
                }

            with get_contract_engine().begin() as conn:
                existing_contract = conn.execute(
                    get_contract,
                    params_get,
                ).mappings().one_or_none()

            if not existing_contract:
                return {"error": "Contract not found."}

            cont_id = int(existing_contract["cont_id_pk"])
            cont_contract_number = existing_contract["cont_contract_number"]
            previous_cont_link_path = existing_contract.get("cont_link_path")
        else:
            previous_cont_link_path = None

        storage_folder = resolve_storage_folder(
            DEFAULT_CONTRACT_STORAGE_FOLDER,
            payload.get("storage_folder"),
            allow_workflow_staging=not update_contract_link,
        )
        contract_key = cont_contract_number or cont_id
        blob_path, upload_version_id = build_versioned_blob_path(
            storage_folder,
            contract_key,
            safe_file_name,
        )
        upload_content_type = get_upload_content_type(payload, safe_file_name, content_type)

        initialize_firebase_app()
        bucket = storage.bucket()
        blob = bucket.blob(blob_path)
        attachment = None
        if update_contract_link:
            blob.upload_from_file(file_stream, content_type=upload_content_type, rewind=True)
        else:
            attachment = upload_staged_document(
                blob, file_stream, safe_file_name, upload_content_type,
                upload_version_id, "CONTRACT_DOCUMENT", cont_org_id_fk,
            )

        if update_contract_link:
            update_contract_link_path = text("""
                update contracts_management
                set
                    cont_link_path = :cont_link_path,
                    updated_by = :updated_by,
                    updated_at = CURRENT_TIMESTAMP
                where cont_id_pk = :cont_id
                and cont_org_id_fk = :cont_org_id_fk
                returning cont_link_path
            """)

            with get_contract_engine().begin() as conn:
                committed_cont_link_path = conn.execute(
                    update_contract_link_path,
                    {
                        "cont_link_path": blob_path,
                        "updated_by": updated_by,
                        "cont_id": cont_id,
                        "cont_org_id_fk": cont_org_id_fk
                    },
                ).scalar_one_or_none()

            if not committed_cont_link_path:
                raise RuntimeError("Contract document pointer update affected zero rows.")
            pointer_committed = True
        else:
            committed_cont_link_path = blob_path

        return {
            "message": f"Successfully uploaded contract document: {cont_contract_number or cont_id}",
            "cont_id": cont_id,
            "cont_contract_number": cont_contract_number,
            "cont_link_path": committed_cont_link_path,
            "previous_cont_link_path": previous_cont_link_path,
            "firebase_blob_path": blob_path,
            "firebase_storage_path": f"gs://{FIREBASE_STORAGE_BUCKET}/{blob_path}",
            "firebase_bucket": FIREBASE_STORAGE_BUCKET,
            "file_name": safe_file_name,
            "content_type": upload_content_type,
            "upload_version_id": upload_version_id,
            **({"workflow_document_attachment": attachment} if attachment else {}),
        }

    except Exception as e:
        response = {
            "error": f"Failed to upload contract document to Firebase. Error Message: {str(e)}"
        }
        if blob_path and bucket and update_contract_link and not pointer_committed:
            response["firebase_cleanup"] = delete_firebase_blob_if_exists(bucket, blob_path)
        return response
    finally:
        if contract_engine is not None:
            contract_engine.dispose()


def upload_contract_document(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None
):
    return upload_contract_document_to_firebase(
        payload,
        file_stream,
        file_name,
        content_type
    )
