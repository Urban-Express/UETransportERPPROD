from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Delete department
def delete_department(payload: dict):
    try:
        dep_engine = db_engine()
        dep_id = payload.get('dep_id')
        dep_org_id_fk = payload.get("dep_org_id_fk") or payload.get("authenticated_org_id")
        get_dep_id = text("""
            select dep_id_pk
            from department_master
            where dep_id_pk = :dep_id
            and dep_org_id_fk = :dep_org_id_fk
        """)
        with dep_engine.begin() as conn:
            df_dep_id = pd.read_sql(
                sql=get_dep_id,
                con=conn,
                params={
                    "dep_id": dep_id,
                    "dep_org_id_fk": dep_org_id_fk
                }
            )
        if not df_dep_id.empty:
            delete_department_master = text("""
            delete from department_master
            where dep_id_pk = :dep_id
            and dep_org_id_fk = :dep_org_id_fk
            """)
            params_delete = {
                'dep_id': dep_id,
                'dep_org_id_fk': dep_org_id_fk
            }
            with dep_engine.begin() as conn:
                conn.execute(delete_department_master, params_delete)
                success_message = {"Successfully deleted department ID: ": {payload.get("dep_id")}}
                return success_message
        else:
            return {"error": "Department ID not found."}
    except Exception as e:
        error_message = {"Failed to delete department. Error Message: ": {e}}
        return error_message
