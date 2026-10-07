from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Delete organization
def delete_organization(payload: dict):
    try:
        org_engine = db_engine()
        get_org_id = text("select org_id_pk from organization_master")
        with org_engine.begin() as conn:
            df_org_id = pd.read_sql(sql=get_org_id, con=conn)
        org_id = payload.get('org_id')
        org_id_exists = df_org_id['org_id_pk'].eq(org_id).any()
        if org_id_exists:
            delete_organization_master = text("""
            delete from organization_master where org_id_pk = :org_id
            """)
            params_delete = {
                'org_id': org_id
            }
            with org_engine.begin() as conn:
                conn.execute(delete_organization_master, params_delete)
                success_message = {"Successfully deleted organization ID: ": {payload.get("org_id")}}
                return success_message
        else:
            return {"Organization ID not found"}
    except Exception as e:
        error_message = {"Failed to delete organization. Error Message: ": {e}}
        return error_message