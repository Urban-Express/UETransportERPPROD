from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Get user data
def get_user(payload: dict | None = None):
    try:
        payload = payload or {}
        user_org_id_fk = payload.get("user_org_id_fk") or payload.get("authenticated_org_id")
        if not user_org_id_fk:
            return {"error": "user_org_id_fk is required."}

        # Running select query and populating data into dataframe
        user_select_query = text("""
            select *
            from user_master
            where user_org_id_fk = :user_org_id_fk
        """)
        user_engine = db_engine()
        with user_engine.connect() as conn:
            df_user_master = pd.read_sql(
                sql=user_select_query,
                con=conn,
                params={"user_org_id_fk": user_org_id_fk}
            )

        # Changing date/time columns for better rendering
        for date_column in ["created_at", "updated_at"]:
            if date_column in df_user_master.columns:
                df_user_master[date_column] = df_user_master[date_column].astype(str)
        # Returning the data
        return df_user_master, df_user_master.to_json(orient="records")
    except Exception as e:
        df_user_master = pd.DataFrame()
        error_message = {"Failed to get user data. Error Message: ": {e}}
        return df_user_master, error_message
