from sqlalchemy import text
from sqlalchemy.exc import MultipleResultsFound
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def get_contracts_management(payload: dict | None = None):
    try:
        payload = payload or {}
        cont_org_id_fk = payload.get("cont_org_id_fk") or payload.get("authenticated_org_id")
        if not cont_org_id_fk:
            return {"error": "cont_org_id_fk is required."}

        contract_select_query = text("""
            select *
            from contracts_management
            where cont_org_id_fk = :cont_org_id_fk
        """)
        contract_engine = db_engine()
        try:
            with contract_engine.connect() as conn:
                df_contracts_management = pd.read_sql(
                    sql=contract_select_query,
                    con=conn,
                    params={"cont_org_id_fk": cont_org_id_fk}
                )
        finally:
            contract_engine.dispose()

        date_columns = [
            "cont_start_date",
            "cont_end_date",
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_contracts_management.columns:
                df_contracts_management[date_column] = (
                    df_contracts_management[date_column].astype(str)
                )

        return df_contracts_management, df_contracts_management.to_json(orient="records")
    except Exception as e:
        df_contracts_management = pd.DataFrame()
        error_message = {"Failed to get contracts data. Error Message: ": {e}}
        return df_contracts_management, error_message


def get_contract(payload: dict | None = None):
    return get_contracts_management(payload)


def get_contract_by_id(payload: dict):
    """Exact business DTO; organization is bound by the authenticated API."""
    record_id = payload.get("cont_id_pk")
    organization_id = payload.get("authenticated_org_id")
    if not record_id or int(record_id) <= 0 or not organization_id:
        return {"error": "A positive cont_id_pk and authenticated organization are required."}
    engine = db_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                select * from contracts_management
                where cont_id_pk = :record_id and cont_org_id_fk = :organization_id
            """), {"record_id": record_id, "organization_id": organization_id}).mappings().one_or_none()
        if row is None:
            return {"error": "Contract not found."}
        return dict(row)
    except MultipleResultsFound:
        return {"error": "EXACT_RECORD_INTEGRITY_ERROR"}
    except Exception:
        return {"error": "EXACT_RECORD_QUERY_FAILED"}
    finally:
        engine.dispose()
