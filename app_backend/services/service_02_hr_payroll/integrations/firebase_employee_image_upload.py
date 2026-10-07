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
)

load_dotenv()

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_STORAGE_BUCKET = (
    os.getenv("FIREBASE_STORAGE_BUCKET")
    or os.getenv("FIREBASE_PROJECT_NAME")
)
FIREBASE_PROJECT_ACCESS_BASE64 = os.getenv("FIREBASE_PROJECT_ACCESS_BASE64")
DEFAULT_EMPLOYEE_IMAGE_STORAGE_FOLDER = "firebase_upload_files/employee_images"


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
    """
    Initializes Firebase Admin SDK using service account details from .env.
    """

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


def upload_employee_image_to_firebase(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None
):
    """
    Uploads an employee file to Firebase Storage and updates employee_image_path
    in employee_master for the matching employee_id.
    """

    employee_engine = None
    bucket = None
    blob_path = None
    pointer_committed = False
    try:
        employee_id = payload.get("employee_id")
        storage_folder = resolve_storage_folder(
            DEFAULT_EMPLOYEE_IMAGE_STORAGE_FOLDER,
            payload.get("storage_folder"),
        )
        updated_by = payload.get("updated_by")
        empl_org_id_fk = payload.get("empl_org_id_fk") or payload.get("authenticated_org_id")
        safe_file_name = get_safe_file_name(file_name)

        if not employee_id:
            return {"error": "employee_id is required."}

        if not file_stream:
            return {"error": "file is required."}

        if not safe_file_name:
            return {"error": "file_name is required."}

        if not empl_org_id_fk:
            return {"error": "empl_org_id_fk is required."}

        employee_engine = db_engine()
        get_employee = text("""
            select empl_id_pk, employee_id, employee_image_path
            from employee_master
            where employee_id = :employee_id
            and empl_org_id_fk = :empl_org_id_fk
        """)

        with employee_engine.begin() as conn:
            existing_employee = conn.execute(
                get_employee,
                {
                    "employee_id": employee_id,
                    "empl_org_id_fk": empl_org_id_fk
                },
            ).mappings().one_or_none()

        if not existing_employee:
            return {"error": "Employee ID not found."}

        empl_id = existing_employee["empl_id_pk"]
        employee_id = existing_employee["employee_id"]
        previous_employee_image_path = existing_employee.get("employee_image_path")

        initialize_firebase_app()

        blob_path, upload_version_id = build_versioned_blob_path(
            storage_folder,
            employee_id,
            safe_file_name,
        )
        upload_content_type = get_upload_content_type(payload, safe_file_name, content_type)

        bucket = storage.bucket()
        blob = bucket.blob(blob_path)
        blob.upload_from_file(file_stream, content_type=upload_content_type, rewind=True)

        update_employee_image_path = text("""
            update employee_master
            set
                employee_image_path = :employee_image_path,
                updated_by = :updated_by
            where empl_id_pk = :empl_id
            and empl_org_id_fk = :empl_org_id_fk
            returning employee_image_path
        """)

        params_update = {
            "employee_image_path": blob_path,
            "updated_by": updated_by,
            "empl_id": empl_id,
            "empl_org_id_fk": empl_org_id_fk
        }

        with employee_engine.begin() as conn:
            committed_employee_image_path = conn.execute(
                update_employee_image_path,
                params_update,
            ).scalar_one_or_none()

        if not committed_employee_image_path:
            raise RuntimeError("Employee image pointer update affected zero rows.")
        pointer_committed = True

        return {
            "message": f"Successfully uploaded employee image for employee: {employee_id}",
            "empl_id_pk": empl_id,
            "employee_id": employee_id,
            "employee_image_path": committed_employee_image_path,
            "previous_employee_image_path": previous_employee_image_path,
            "firebase_blob_path": blob_path,
            "firebase_storage_path": f"gs://{FIREBASE_STORAGE_BUCKET}/{blob_path}",
            "firebase_bucket": FIREBASE_STORAGE_BUCKET,
            "file_name": safe_file_name,
            "content_type": upload_content_type,
            "upload_version_id": upload_version_id,
        }

    except Exception as e:
        response = {
            "error": f"Failed to upload employee image to Firebase. Error Message: {str(e)}"
        }
        if blob_path and bucket and not pointer_committed:
            response["firebase_cleanup"] = delete_firebase_blob_if_exists(bucket, blob_path)
        return response
    finally:
        if employee_engine is not None:
            employee_engine.dispose()


def upload_employee_image(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None
):
    return upload_employee_image_to_firebase(payload, file_stream, file_name, content_type)
