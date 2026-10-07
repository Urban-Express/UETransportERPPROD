from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Get user_access data
def get_user_access(payload: dict):
    try:
        # Running select query and populating data into dataframe
        user_access_select_query = text("select * from v_user_access_rights where user_principal_name = :user_principal_name")
        user_principal_name = payload.get("user_principal_name")
        params = {
            "user_principal_name": user_principal_name
        }
        user_access_engine = db_engine()
        with user_access_engine.connect() as conn:
            df_user_access_master = pd.read_sql(sql=user_access_select_query, con=conn, params=params)

        # Returning the data
        return df_user_access_master, df_user_access_master.to_json(orient="records")
    except Exception as e:
        df_user_access_master = pd.DataFrame()
        error_message = {"Failed to get user_access data. Error Message: ": {e}}
        return df_user_access_master, error_message
