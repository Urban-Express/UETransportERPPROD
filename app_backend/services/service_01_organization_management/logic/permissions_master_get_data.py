from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Get permissions data
def get_permissions():
    try:
        # Running select query and populating data into dataframe
        permissions_select_query = text("select * from permission_master")
        permissions_engine = db_engine()
        with permissions_engine.connect() as conn:
            df_permissions_master = pd.read_sql(sql=permissions_select_query, con=conn)

        # Changing data type of created at for better rendering
        df_permissions_master["created_at"] = df_permissions_master["created_at"].astype(str)
        # Returning the data
        return df_permissions_master, df_permissions_master.to_json(orient="records")
    except Exception as e:
        df_permissions_master = pd.DataFrame()
        error_message = {"Failed to get permissions data. Error Message: ": {e}}
        return df_permissions_master, error_message