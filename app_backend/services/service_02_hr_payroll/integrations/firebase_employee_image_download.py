import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from firebase_admin import storage
from sqlalchemy import text
import pandas as pd

from app_backend.services.service_02_hr_payroll.integrations.firebase_employee_image_upload import (
    FIREBASE_STORAGE_BUCKET,
    initialize_firebase_app
)
from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine

load_dotenv()

SIGNED_URL_EXPIRES_IN_SECONDS = 15 * 60


def get_blob_path_from_employee_image_path(employee_image_path: str):
    if not employee_image_path:
        return None

    gs_bucket_prefix = f"gs://{FIREBASE_STORAGE_BUCKET}/"
    if employee_image_path.startswith(gs_bucket_prefix):
        return employee_image_path.replace(gs_bucket_prefix, "", 1)

    return employee_image_path


def download_employee_image_from_firebase(payload: dict):
    """
    Downloads an employee file from Firebase Storage using employee_image_path
    for the matching employee_id.
    """

    try:
        employee_id = payload.get("employee_id")
        empl_org_id_fk = payload.get("empl_org_id_fk") or payload.get("authenticated_org_id")

        if not employee_id:
            return {"error": "employee_id is required."}

        employee_engine = db_engine()
        get_employee_image_path = text("""
            select employee_image_path
            from employee_master
            where employee_id = :employee_id
            and empl_org_id_fk = :empl_org_id_fk
        """)

        with employee_engine.begin() as conn:
            df_employee_image_path = pd.read_sql(
                sql=get_employee_image_path,
                con=conn,
                params={
                    "employee_id": employee_id,
                    "empl_org_id_fk": empl_org_id_fk
                }
            )

        if df_employee_image_path.empty:
            return {"error": "Employee ID not found."}

        employee_image_path = df_employee_image_path.iloc[0]["employee_image_path"]
        blob_path = get_blob_path_from_employee_image_path(employee_image_path)

        if not blob_path:
            return {"error": f"employee_image_path is empty for employee: {employee_id}"}

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
            "message": f"Successfully generated employee image download URL for employee: {employee_id}",
            "employee_id": employee_id,
            "employee_image_path": employee_image_path,
            "file_name": os.path.basename(blob_path),
            "content_type": blob.content_type or "application/octet-stream",
            "download_url": download_url,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": SIGNED_URL_EXPIRES_IN_SECONDS
        }

    except Exception as e:
        return {
            "error": f"Failed to download employee image from Firebase. Error Message: {str(e)}"
        }


def download_employee_image(payload: dict):
    return download_employee_image_from_firebase(payload)
