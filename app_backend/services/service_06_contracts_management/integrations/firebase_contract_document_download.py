import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from firebase_admin import storage
from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_06_contracts_management.integrations.firebase_contract_document_upload import (
    FIREBASE_STORAGE_BUCKET,
    initialize_firebase_app,
)

load_dotenv()

SIGNED_URL_EXPIRES_IN_SECONDS = 15 * 60


def get_blob_path_from_contract_link_path(cont_link_path: str):
    if not cont_link_path:
        return None

    gs_bucket_prefix = f"gs://{FIREBASE_STORAGE_BUCKET}/"
    if cont_link_path.startswith(gs_bucket_prefix):
        return cont_link_path.replace(gs_bucket_prefix, "", 1)

    return cont_link_path


def download_contract_document_from_firebase(payload: dict):
    contract_engine = None
    try:
        cont_id = (
            payload.get("cont_id")
            or payload.get("cont_id_pk")
        )
        cont_contract_number = payload.get("cont_contract_number")
        cont_org_id_fk = payload.get("cont_org_id_fk") or payload.get("authenticated_org_id")

        if not cont_id and not cont_contract_number:
            return {"error": "cont_id or cont_contract_number is required."}
        if not cont_org_id_fk:
            return {"error": "cont_org_id_fk is required."}

        contract_engine = db_engine()
        if cont_id:
            get_contract_link_path = text("""
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
            get_contract_link_path = text("""
                select cont_id_pk, cont_contract_number, cont_link_path
                from contracts_management
                where cont_contract_number = :cont_contract_number
                and cont_org_id_fk = :cont_org_id_fk
            """)
            params_get = {
                "cont_contract_number": cont_contract_number,
                "cont_org_id_fk": cont_org_id_fk
            }

        with contract_engine.begin() as conn:
            df_contract_link_path = pd.read_sql(
                sql=get_contract_link_path,
                con=conn,
                params=params_get
            )

        if df_contract_link_path.empty:
            return {"error": "Contract not found."}

        contract_row = df_contract_link_path.iloc[0]
        cont_id = int(contract_row["cont_id_pk"])
        cont_contract_number = contract_row["cont_contract_number"]
        cont_link_path = contract_row["cont_link_path"]
        blob_path = get_blob_path_from_contract_link_path(cont_link_path)

        if not blob_path:
            return {"error": f"cont_link_path is empty for contract: {cont_contract_number}"}

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
            "message": f"Successfully generated contract document download URL: {cont_contract_number}",
            "cont_id": cont_id,
            "cont_contract_number": cont_contract_number,
            "cont_link_path": cont_link_path,
            "file_name": os.path.basename(blob_path),
            "content_type": blob.content_type or "application/octet-stream",
            "download_url": download_url,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": SIGNED_URL_EXPIRES_IN_SECONDS
        }

    except Exception as e:
        return {
            "error": f"Failed to download contract document from Firebase. Error Message: {str(e)}"
        }
    finally:
        if contract_engine is not None:
            contract_engine.dispose()


def download_contract_document(payload: dict):
    return download_contract_document_from_firebase(payload)
