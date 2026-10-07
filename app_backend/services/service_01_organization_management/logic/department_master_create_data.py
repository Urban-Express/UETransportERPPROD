from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Create new department
def create_department(payload: dict):
    try:
        dep_engine = db_engine()
        dep_org_id_fk = payload.get("dep_org_id_fk") or payload.get("authenticated_org_id")
        dep_name = payload.get("department_name")
        get_dep_name = text("""
            select department_name
            from department_master
            where dep_org_id_fk = :dep_org_id_fk
            and lower(department_name) = lower(:department_name)
        """)
        with dep_engine.begin() as conn:
            df_dep_name = pd.read_sql(
                sql=get_dep_name,
                con=conn,
                params={
                    "dep_org_id_fk": dep_org_id_fk,
                    "department_name": dep_name
                }
            )
        if not df_dep_name.empty:
            return {"error": f"Department already exists: {dep_name}"}
        else:
            insert_into_department_master = text("""
            insert into department_master(
            dep_org_id_fk,
            department_name,
            cost_center_flag,
            profit_center_flag,
            created_by
            )values(
            :dep_org_id_fk,
            :department_name,
            :cost_center_flag,
            :profit_center_flag,
            :created_by
            )
            """)
            params_insert = {
                "dep_org_id_fk": dep_org_id_fk,
                "department_name": dep_name,
                "cost_center_flag": payload.get("cost_center_flag"),
                "profit_center_flag": payload.get("profit_center_flag"),
                "created_by": payload.get("created_by")
            }
            with dep_engine.begin() as conn:
                conn.execute(insert_into_department_master, params_insert)
                success_message = {"Successfully created department: ": {payload.get("department_name")}}
                return success_message
    except Exception as e:
        error_message = {"Failed to create department. Error Message: ": {e}}
        return error_message
