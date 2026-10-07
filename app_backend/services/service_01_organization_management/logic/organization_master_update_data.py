from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Update existing organization
def update_organization(payload: dict):
    try:
        org_engine = db_engine()
        get_org_id = text("select org_id_pk from organization_master")
        with org_engine.begin() as conn:
            df_org_id = pd.read_sql(sql=get_org_id, con=conn)
        org_id = payload.get("org_id")
        org_id_exists = df_org_id["org_id_pk"].eq(org_id).any()
        if org_id_exists:
            update_organization_master = text("""
            update organization_master
            set 
            org_name = :org_name,
            org_address = :org_address,
            org_phone_primary = :org_phone_primary,
            org_phone_secondary = :org_phone_secondary,
            org_email_primary = :org_email_primary,
            org_email_secondary = :org_email_secondary,
            company_registration_number = :company_registration_number,
            created_by = :created_by
            where org_id_pk = :org_id
            """)
            params_update = {
                'org_id': payload.get('org_id'),
                'org_name': payload.get('org_name'),
                'org_address': payload.get('org_address'),
                'org_phone_primary': payload.get('org_phone_primary'),
                'org_phone_secondary': payload.get('org_phone_secondary'),
                'org_email_primary': payload.get('org_email_primary'),
                'org_email_secondary': payload.get('org_email_secondary'),
                'company_registration_number': payload.get('company_registration_number'),
                'created_by': payload.get('created_by')
            }
            with org_engine.begin() as conn:
                conn.execute(update_organization_master, params_update)
                success_message = {"Successfully updated organization: ": {payload.get("org_name")}}
                return success_message
        else:
            return {"Organization ID not found."}
    except Exception as e:
        error_message = {"Failed to update organization. Error Message: ": {e}}
        return error_message