from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

def get_organization_license(payload: dict):
    try:
        org_id = payload.get("org_id")
        org_license_query = text(
            """
            select * from organization_license_master where org_id_fk = :org_id
            """
        )
        params_get = {
            "org_id": org_id
        }
        org_license_engine = db_engine()
        with org_license_engine.connect() as conn:
            df_org_license_alloc = pd.read_sql(sql=org_license_query, con=conn, params=params_get)

        return df_org_license_alloc, df_org_license_alloc.to_json(orient="records")
    except Exception as e:
        df_org_license_alloc = pd.DataFrame()
        error_message = {"Failed to get organization license allocation. Error message:": e}
        return df_org_license_alloc, error_message