from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Get role data
def get_role():
    try:
        # Running select query and populating data into dataframe
        role_select_query = text("select * from role_master")
        role_engine = db_engine()
        with role_engine.connect() as conn:
            df_role_master = pd.read_sql(sql=role_select_query, con=conn)

        # Changing data type of created at for better rendering
        df_role_master["created_at"] = df_role_master["created_at"].astype(str)
        # Returning the data
        return df_role_master, df_role_master.to_json(orient="records")
    except Exception as e:
        df_role_master = pd.DataFrame()
        error_message = {"Failed to get role data. Error Message: ": {e}}
        return df_role_master, error_message