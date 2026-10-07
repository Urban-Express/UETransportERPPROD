from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Assign role to user
def assign_user_role(payload: dict):
    try:
        user_role_engine = db_engine()
        user_org_id_fk = payload.get("user_org_id_fk") or payload.get("authenticated_org_id")
        get_target_user = text("""
            select user_id_pk
            from user_master
            where user_id_pk = :user_id_fk
            and user_org_id_fk = :user_org_id_fk
        """)
        with user_role_engine.begin() as conn:
            df_target_user = pd.read_sql(
                sql=get_target_user,
                con=conn,
                params={
                    "user_id_fk": payload.get("user_id"),
                    "user_org_id_fk": user_org_id_fk
                }
            )

        if df_target_user.empty:
            return {"error": "User ID not found."}

        insert_into_user_role_mapping = text("""
            insert into user_role_mapping(
                user_id_fk,
                role_id_fk
            ) values (
                :user_id_fk,
                :role_id_fk
            )
        """)
        params_insert = {
            "user_id_fk": payload.get("user_id"),
            "role_id_fk": payload.get("role_id")
        }
        with user_role_engine.begin() as conn:
            conn.execute(insert_into_user_role_mapping, params_insert)

        return {
            "message": f"Successfully assigned role ID {payload.get('role_id')} to user ID {payload.get('user_id')}"
        }

    except Exception as e:
        return {"error": f"Failed to assign role to user. Error Message: {str(e)}"}
