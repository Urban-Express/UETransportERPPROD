from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Delete user
def delete_user(payload: dict):
    try:
        user_engine = db_engine()
        user_id = payload.get('user_id')
        user_org_id_fk = payload.get("user_org_id_fk") or payload.get("authenticated_org_id")
        get_user_id = text("""
            select user_id_pk
            from user_master
            where user_id_pk = :user_id
            and user_org_id_fk = :user_org_id_fk
        """)
        with user_engine.begin() as conn:
            df_user_id = pd.read_sql(
                sql=get_user_id,
                con=conn,
                params={
                    "user_id": user_id,
                    "user_org_id_fk": user_org_id_fk
                }
            )
        if not df_user_id.empty:
            delete_user_master = text("""
            delete from user_master
            where user_id_pk = :user_id
            and user_org_id_fk = :user_org_id_fk
            """)
            params_delete = {
                'user_id': user_id,
                'user_org_id_fk': user_org_id_fk
            }
            with user_engine.begin() as conn:
                conn.execute(delete_user_master, params_delete)
                success_message = {"Successfully deleted user ID: ": {payload.get("user_id")}}
                return success_message
        else:
            return {"error": "User ID not found."}
    except Exception as e:
        error_message = {"Failed to delete user. Error Message: ": {e}}
        return error_message
