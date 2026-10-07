from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Get employee data
def get_employee_master(payload: dict | None = None):
    try:
        payload = payload or {}
        empl_org_id_fk = payload.get("empl_org_id_fk") or payload.get("authenticated_org_id")
        if not empl_org_id_fk:
            return {"error": "empl_org_id_fk is required."}

        # Running select query and populating data into dataframe
        employee_select_query = text("""
            select *
            from employee_master
            where empl_org_id_fk = :empl_org_id_fk
        """)
        employee_engine = db_engine()
        with employee_engine.connect() as conn:
            df_employee_master = pd.read_sql(
                sql=employee_select_query,
                con=conn,
                params={"empl_org_id_fk": empl_org_id_fk}
            )

        # Changing date/time columns for better rendering
        date_columns = [
            "joining_date",
            "passport_expiry_date",
            "visa_expiry_date",
            "emirates_id_expiry_date",
            "driver_licence_expiry_date",
            "permit_expiry_date",
            "insurance_expiry_date",
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_employee_master.columns:
                df_employee_master[date_column] = df_employee_master[date_column].astype(str)

        # Returning the data
        return df_employee_master, df_employee_master.to_json(orient="records")
    except Exception as e:
        df_employee_master = pd.DataFrame()
        error_message = {"Failed to get employee data. Error Message: ": {e}}
        return df_employee_master, error_message


def get_employee(payload: dict | None = None):
    return get_employee_master(payload)
