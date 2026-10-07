from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Get user and role mapping data
def get_user_role(payload: dict | None = None):
    try:
        payload = payload or {}
        user_org_id_fk = payload.get("user_org_id_fk") or payload.get("authenticated_org_id")
        if not user_org_id_fk:
            return {"error": "user_org_id_fk is required."}

        # Running select query and populating data into dataframe
        user_role_select_query = text("""
            select
                ur.user_role_id_pk,
                ur.user_id_fk,
                u.user_principal_name,
                u.display_name,
                u.email,
                ur.role_id_fk,
                r.role_name,
                r.role_description,
                ur.created_at
            from user_role_mapping ur
            join user_master u
                on u.user_id_pk = ur.user_id_fk
            join role_master r
                on r.role_id_pk = ur.role_id_fk
            where u.user_org_id_fk = :user_org_id_fk
            order by
                u.user_principal_name,
                r.role_name
        """)
        user_role_engine = db_engine()
        with user_role_engine.connect() as conn:
            df_user_role_mapping = pd.read_sql(
                sql=user_role_select_query,
                con=conn,
                params={"user_org_id_fk": user_org_id_fk}
            )

        # Changing data type of created at for better rendering
        df_user_role_mapping["created_at"] = df_user_role_mapping["created_at"].astype(str)
        # Returning the data
        return df_user_role_mapping, df_user_role_mapping.to_json(orient="records")
    except Exception as e:
        df_user_role_mapping = pd.DataFrame()
        error_message = {"Failed to get user role mapping data. Error Message: ": {e}}
        return df_user_role_mapping, error_message
