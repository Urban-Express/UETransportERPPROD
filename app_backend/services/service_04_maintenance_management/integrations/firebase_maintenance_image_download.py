import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from firebase_admin import storage
from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_04_maintenance_management.integrations.firebase_maintenance_image_upload import (
    FIREBASE_STORAGE_BUCKET,
    initialize_firebase_app,
)

load_dotenv()

SIGNED_URL_EXPIRES_IN_SECONDS = 15 * 60


def get_blob_path_from_maintenance_image_path(maint_image_path: str):
    if not maint_image_path:
        return None

    gs_bucket_prefix = f"gs://{FIREBASE_STORAGE_BUCKET}/"
    if maint_image_path.startswith(gs_bucket_prefix):
        return maint_image_path.replace(gs_bucket_prefix, "", 1)

    return maint_image_path


def download_maintenance_image_from_firebase(payload: dict):
    try:
        maint_id = (
            payload.get("maint_id")
            or payload.get("maint_id_pk")
        )
        authenticated_org_id = payload.get("authenticated_org_id")

        if not maint_id:
            return {"error": "maint_id is required."}
        if not authenticated_org_id:
            return {"error": "authenticated_org_id is required."}

        maintenance_engine = db_engine()
        get_maintenance_image_path = text("""
            select m.maint_id_pk, m.maint_fleet_vehicle_id_fk, m.maint_image_path
            from maintenance_master m
            join fleet_master f
                on f.fleet_vehicle_id_pk = m.maint_fleet_vehicle_id_fk
            where m.maint_id_pk = :maint_id
            and f.fleet_org_id_fk = :authenticated_org_id
        """)

        with maintenance_engine.begin() as conn:
            df_maintenance_image_path = pd.read_sql(
                sql=get_maintenance_image_path,
                con=conn,
                params={
                    "maint_id": maint_id,
                    "authenticated_org_id": authenticated_org_id
                }
            )

        if df_maintenance_image_path.empty:
            return {"error": "Maintenance record not found."}

        maintenance_row = df_maintenance_image_path.iloc[0]
        maint_id = int(maintenance_row["maint_id_pk"])
        maint_fleet_vehicle_id_fk = int(maintenance_row["maint_fleet_vehicle_id_fk"])
        maint_image_path = maintenance_row["maint_image_path"]
        blob_path = get_blob_path_from_maintenance_image_path(maint_image_path)

        if not blob_path:
            return {"error": f"maint_image_path is empty for maintenance record: {maint_id}"}

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
            "message": f"Successfully generated maintenance image download URL: {maint_id}",
            "maint_id": maint_id,
            "maint_fleet_vehicle_id_fk": maint_fleet_vehicle_id_fk,
            "maint_image_path": maint_image_path,
            "file_name": os.path.basename(blob_path),
            "content_type": blob.content_type or "application/octet-stream",
            "download_url": download_url,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": SIGNED_URL_EXPIRES_IN_SECONDS
        }

    except Exception as e:
        return {
            "error": f"Failed to download maintenance image from Firebase. Error Message: {str(e)}"
        }


def download_maintenance_image(payload: dict):
    return download_maintenance_image_from_firebase(payload)
