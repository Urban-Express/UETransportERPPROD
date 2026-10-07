from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Get organization data
def get_organization():
    try:
        # Running select query and populating data into dataframe
        org_select_query = text("select * from organization_master")
        org_engine = db_engine()
        with org_engine.connect() as conn:
            df_organization_master = pd.read_sql(sql=org_select_query, con=conn)

        # Changing data type of created at for better rendering
        df_organization_master["created_at"] = df_organization_master["created_at"].astype(str)
        # Returning the data
        return df_organization_master, df_organization_master.to_json(orient="records")
    except Exception as e:
        df_organization_master = pd.DataFrame()
        error_message = {"Failed to get organization data. Error Message: ": {e}}
        return df_organization_master, error_message