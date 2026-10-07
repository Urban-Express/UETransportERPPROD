from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def get_accounts_payables(payload: dict | None = None):
    try:
        payload = payload or {}
        ap_org_id_fk = payload.get("ap_org_id_fk") or payload.get("authenticated_org_id")
        if not ap_org_id_fk:
            return {"error": "ap_org_id_fk is required."}

        ap_select_query = text("""
            select *
            from accounts_payables
            where (:ap_id is null or ap_id_pk = :ap_id)
            and ap_org_id_fk = :ap_org_id_fk
            and (:ap_supp_id_fk is null or ap_supp_id_fk = :ap_supp_id_fk)
            and (:ap_approval_status is null or ap_approval_status = :ap_approval_status)
            and (:ap_payment_status is null or ap_payment_status = :ap_payment_status)
            order by ap_id_pk desc
        """)
        params = {
            "ap_id": payload.get("ap_id") or payload.get("ap_id_pk"),
            "ap_org_id_fk": ap_org_id_fk,
            "ap_supp_id_fk": payload.get("ap_supp_id_fk"),
            "ap_approval_status": payload.get("ap_approval_status"),
            "ap_payment_status": payload.get("ap_payment_status"),
        }

        ap_engine = db_engine()
        try:
            with ap_engine.connect() as conn:
                df_ap = pd.read_sql(sql=ap_select_query, con=conn, params=params)
        finally:
            ap_engine.dispose()

        date_columns = [
            "ap_invoice_date",
            "ap_due_date",
            "ap_approved_at",
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_ap.columns:
                df_ap[date_column] = df_ap[date_column].astype(str)

        return df_ap, df_ap.to_json(orient="records")
    except Exception as e:
        df_ap = pd.DataFrame()
        error_message = {"Failed to get accounts payables data. Error Message: ": {e}}
        return df_ap, error_message


def get_accounts_payable(payload: dict | None = None):
    return get_accounts_payables(payload)
