from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Get department data
def get_department(payload: dict | None = None):
    try:
        payload = payload or {}
        dep_org_id_fk = payload.get("dep_org_id_fk") or payload.get("authenticated_org_id")
        if not dep_org_id_fk:
            return {"error": "dep_org_id_fk is required."}

        # Running select query and populating data into dataframe
        dep_select_query = text("""
            select *
            from department_master
            where dep_org_id_fk = :dep_org_id_fk
        """)
        dep_engine = db_engine()
        with dep_engine.connect() as conn:
            df_department_master = pd.read_sql(
                sql=dep_select_query,
                con=conn,
                params={"dep_org_id_fk": dep_org_id_fk}
            )

        # Changing data type of created at for better rendering
        df_department_master["created_at"] = df_department_master["created_at"].astype(str)
        # Returning the data
        return df_department_master, df_department_master.to_json(orient="records")
    except Exception as e:
        df_department_master = pd.DataFrame()
        error_message = {"Failed to get department data. Error Message: ": {e}}
        return df_department_master, error_message
