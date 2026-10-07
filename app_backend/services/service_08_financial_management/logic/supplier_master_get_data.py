from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def get_supplier_master(payload: dict | None = None):
    supplier_engine = None
    try:
        payload = payload or {}
        supp_org_id_fk = payload.get("supp_org_id_fk") or payload.get("authenticated_org_id")
        if not supp_org_id_fk:
            return {"error": "supp_org_id_fk is required."}

        supplier_select_query = text("""
            select *
            from supplier_master
            where supp_org_id_fk = :supp_org_id_fk
        """)
        supplier_engine = db_engine()
        with supplier_engine.connect() as conn:
            df_supplier_master = pd.read_sql(
                sql=supplier_select_query,
                con=conn,
                params={"supp_org_id_fk": supp_org_id_fk}
            )

        date_columns = [
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_supplier_master.columns:
                df_supplier_master[date_column] = (
                    df_supplier_master[date_column].astype(str)
                )

        return df_supplier_master, df_supplier_master.to_json(orient="records")
    except Exception as e:
        df_supplier_master = pd.DataFrame()
        error_message = {"Failed to get supplier data. Error Message: ": {e}}
        return df_supplier_master, error_message
    finally:
        if supplier_engine is not None:
            supplier_engine.dispose()


def get_supplier(payload: dict | None = None):
    return get_supplier_master(payload)
