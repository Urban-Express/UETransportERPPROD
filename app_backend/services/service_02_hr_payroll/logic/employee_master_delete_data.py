from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Delete employee
def delete_employee_master(payload: dict):
    try:
        employee_engine = db_engine()
        empl_id = (
            payload.get("empl_id")
            or payload.get("empl_id_pk")
        )
        empl_org_id_fk = payload.get("empl_org_id_fk") or payload.get("authenticated_org_id")

        if not empl_id:
            return {"error": "empl_id is required."}

        get_empl_id = text("""
            select empl_id_pk
            from employee_master
            where empl_id_pk = :empl_id
            and empl_org_id_fk = :empl_org_id_fk
        """)

        with employee_engine.begin() as conn:
            df_empl_id = pd.read_sql(
                sql=get_empl_id,
                con=conn,
                params={
                    "empl_id": empl_id,
                    "empl_org_id_fk": empl_org_id_fk
                }
            )

        if df_empl_id.empty:
            return {"error": "Employee ID not found."}

        delete_employee_master_query = text("""
            delete from employee_master
            where empl_id_pk = :empl_id
            and empl_org_id_fk = :empl_org_id_fk
        """)

        params_delete = {
            "empl_id": empl_id,
            "empl_org_id_fk": empl_org_id_fk
        }

        with employee_engine.begin() as conn:
            conn.execute(delete_employee_master_query, params_delete)

        return {"message": f"Successfully deleted employee ID: {empl_id}"}

    except Exception as e:
        return {"error": f"Failed to delete employee. Error Message: {str(e)}"}


def delete_employee(payload: dict):
    return delete_employee_master(payload)
