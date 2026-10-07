from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def get_asset_master(payload: dict | None = None):
    try:
        payload = payload or {}
        asset_org_id_fk = payload.get("asset_org_id_fk") or payload.get("authenticated_org_id")
        if not asset_org_id_fk:
            return {"error": "asset_org_id_fk is required."}

        asset_select_query = text("""
            select *
            from asset_master
            where asset_org_id_fk = :asset_org_id_fk
        """)
        asset_engine = db_engine()
        with asset_engine.connect() as conn:
            df_asset_master = pd.read_sql(
                sql=asset_select_query,
                con=conn,
                params={"asset_org_id_fk": asset_org_id_fk}
            )

        date_columns = [
            "asset_acquisition_date",
            "depreciation_start_date",
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_asset_master.columns:
                df_asset_master[date_column] = df_asset_master[date_column].astype(str)

        return df_asset_master, df_asset_master.to_json(orient="records")
    except Exception as e:
        df_asset_master = pd.DataFrame()
        error_message = {"Failed to get asset master data. Error Message: ": {e}}
        return df_asset_master, error_message


def get_asset(payload: dict | None = None):
    return get_asset_master(payload)
