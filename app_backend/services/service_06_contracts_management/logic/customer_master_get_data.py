from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Get customer data
def get_customer_master(payload: dict | None = None):
    try:
        payload = payload or {}
        cust_org_id_fk = payload.get("cust_org_id_fk") or payload.get("authenticated_org_id")
        if not cust_org_id_fk:
            return {"error": "cust_org_id_fk is required."}

        # Running select query and populating data into dataframe
        customer_select_query = text("""
            select *
            from customer_master
            where cust_org_id_fk = :cust_org_id_fk
        """)
        customer_engine = db_engine()
        with customer_engine.connect() as conn:
            df_customer_master = pd.read_sql(
                sql=customer_select_query,
                con=conn,
                params={"cust_org_id_fk": cust_org_id_fk}
            )

        # Changing date/time columns for better rendering
        date_columns = [
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_customer_master.columns:
                df_customer_master[date_column] = (
                    df_customer_master[date_column].astype(str)
                )

        # Returning the data
        return df_customer_master, df_customer_master.to_json(orient="records")
    except Exception as e:
        df_customer_master = pd.DataFrame()
        error_message = {"Failed to get customer data. Error Message: ": {e}}
        return df_customer_master, error_message


def get_customer(payload: dict | None = None):
    return get_customer_master(payload)
