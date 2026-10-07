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
DEFAULT_MAINTENANCE_IMAGE_STORAGE_FOLDER = "firebase_upload_files/maintenance_images"


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


def get_maintenance_identifier(payload: dict):
    maint_id = (
        payload.get("maint_id")
        or payload.get("maint_id_pk")
    )
    maint_fleet_vehicle_id_fk = payload.get("maint_fleet_vehicle_id_fk")
    return maint_id, maint_fleet_vehicle_id_fk


def get_maintenance_image_blob_path(payload: dict, file_name: str):
    storage_folder = resolve_storage_folder(
        DEFAULT_MAINTENANCE_IMAGE_STORAGE_FOLDER,
        payload.get("storage_folder"),
    )
    maint_id, maint_fleet_vehicle_id_fk = get_maintenance_identifier(payload)
    maintenance_key = maint_id or f"fleet_{maint_fleet_vehicle_id_fk}"
    safe_file_name = get_safe_file_name(file_name)

    if not safe_file_name:
        return None

    blob_path, _ = build_versioned_blob_path(
        storage_folder,
        maintenance_key,
        safe_file_name,
    )
    return blob_path


def upload_maintenance_image_to_firebase(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    update_maintenance_image_path: bool = True,
    require_maintenance: bool = True
):
    maintenance_engine = None
    bucket = None
    blob_path = None
    pointer_committed = False
    try:
        updated_by = payload.get("updated_by") or payload.get("created_by")
        authenticated_org_id = payload.get("authenticated_org_id")
        maint_id, maint_fleet_vehicle_id_fk = get_maintenance_identifier(payload)
        safe_file_name = get_safe_file_name(file_name)

        if not maint_id and not maint_fleet_vehicle_id_fk:
            return {"error": "maint_id or maint_fleet_vehicle_id_fk is required."}

        if not file_stream:
            return {"error": "file is required."}

        if not safe_file_name:
            return {"error": "file_name is required."}
        if not authenticated_org_id:
            return {"error": "authenticated_org_id is required."}
        if update_maintenance_image_path and not maint_id:
            return {"error": "maint_id is required to update maint_image_path."}

        maintenance_engine = db_engine()
        previous_maint_image_path = None

        if require_maintenance:
            get_maintenance = text("""
                select m.maint_id_pk, m.maint_fleet_vehicle_id_fk, m.maint_image_path
                from maintenance_master m
                join fleet_master f
                    on f.fleet_vehicle_id_pk = m.maint_fleet_vehicle_id_fk
                where m.maint_id_pk = :maint_id
                and f.fleet_org_id_fk = :authenticated_org_id
            """)

            with maintenance_engine.begin() as conn:
                existing_maintenance = conn.execute(
                    get_maintenance,
                    {
                        "maint_id": maint_id,
                        "authenticated_org_id": authenticated_org_id
                    },
                ).mappings().one_or_none()

            if not existing_maintenance:
                return {"error": "Maintenance record not found."}

            maint_id = int(existing_maintenance["maint_id_pk"])
            maint_fleet_vehicle_id_fk = int(
                existing_maintenance["maint_fleet_vehicle_id_fk"]
            )
            previous_maint_image_path = existing_maintenance.get("maint_image_path")
        else:
            get_fleet_vehicle = text("""
                select fleet_vehicle_id_pk
                from fleet_master
                where fleet_vehicle_id_pk = :maint_fleet_vehicle_id_fk
                and fleet_org_id_fk = :authenticated_org_id
            """)
            with maintenance_engine.begin() as conn:
                existing_fleet_vehicle = conn.execute(
                    get_fleet_vehicle,
                    {
                        "maint_fleet_vehicle_id_fk": maint_fleet_vehicle_id_fk,
                        "authenticated_org_id": authenticated_org_id
                    },
                ).mappings().one_or_none()
            if not existing_fleet_vehicle:
                return {"error": "Fleet vehicle ID not found."}

        storage_folder = resolve_storage_folder(
            DEFAULT_MAINTENANCE_IMAGE_STORAGE_FOLDER,
            payload.get("storage_folder"),
        )
        maintenance_key = maint_id or f"fleet_{maint_fleet_vehicle_id_fk}"
        blob_path, upload_version_id = build_versioned_blob_path(
            storage_folder,
            maintenance_key,
            safe_file_name,
        )
        upload_content_type = get_upload_content_type(payload, safe_file_name, content_type)

        initialize_firebase_app()
        bucket = storage.bucket()
        blob = bucket.blob(blob_path)
        blob.upload_from_file(file_stream, content_type=upload_content_type, rewind=True)

        if update_maintenance_image_path:
            update_image_path = text("""
                update maintenance_master
                set
                    maint_image_path = :maint_image_path,
                    updated_by = :updated_by,
                    updated_at = CURRENT_TIMESTAMP
                where maint_id_pk = :maint_id
                and exists (
                    select 1
                    from fleet_master f
                    where f.fleet_vehicle_id_pk = maintenance_master.maint_fleet_vehicle_id_fk
                    and f.fleet_org_id_fk = :authenticated_org_id
                )
                returning maint_image_path
            """)

            with maintenance_engine.begin() as conn:
                committed_maint_image_path = conn.execute(
                    update_image_path,
                    {
                        "maint_image_path": blob_path,
                        "updated_by": updated_by,
                        "maint_id": maint_id,
                        "authenticated_org_id": authenticated_org_id
                    },
                ).scalar_one_or_none()

            if not committed_maint_image_path:
                raise RuntimeError("Maintenance image pointer update affected zero rows.")
            pointer_committed = True
        else:
            committed_maint_image_path = blob_path

        return {
            "message": f"Successfully uploaded maintenance image: {maint_id or maint_fleet_vehicle_id_fk}",
            "maint_id": maint_id,
            "maint_fleet_vehicle_id_fk": maint_fleet_vehicle_id_fk,
            "maint_image_path": committed_maint_image_path,
            "previous_maint_image_path": previous_maint_image_path,
            "firebase_blob_path": blob_path,
            "firebase_storage_path": f"gs://{FIREBASE_STORAGE_BUCKET}/{blob_path}",
            "firebase_bucket": FIREBASE_STORAGE_BUCKET,
            "file_name": safe_file_name,
            "content_type": upload_content_type,
            "upload_version_id": upload_version_id,
        }

    except Exception as e:
        response = {
            "error": f"Failed to upload maintenance image to Firebase. Error Message: {str(e)}"
        }
        if blob_path and bucket and update_maintenance_image_path and not pointer_committed:
            response["firebase_cleanup"] = delete_firebase_blob_if_exists(bucket, blob_path)
        return response
    finally:
        if maintenance_engine is not None:
            maintenance_engine.dispose()


def upload_maintenance_image(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None
):
    return upload_maintenance_image_to_firebase(
        payload,
        file_stream,
        file_name,
        content_type
    )
