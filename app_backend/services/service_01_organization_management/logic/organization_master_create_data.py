from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

# Create new organization
def create_organization(payload: dict):
    try:
        org_engine = db_engine()
        get_org_name = text("select org_name from organization_master")
        with org_engine.begin() as conn:
            df_org_name = pd.read_sql(sql=get_org_name, con=conn)
        org_name = payload.get("org_name")
        org_name_exists = df_org_name["org_name"].eq(org_name).any()
        if org_name_exists:
            org_exists_message = {"Organization already exists: ": {payload.get("org_name")}}
            return org_exists_message
        else:
            insert_into_organization_master = text("""
            insert into organization_master(
            org_name,
            org_address,
            org_phone_primary,
            org_phone_secondary,
            org_email_primary,
            org_email_secondary,
            company_registration_number,
            created_by
            )values(
            :org_name,
            :org_address,
            :org_phone_primary,
            :org_phone_secondary,
            :org_email_primary,
            :org_email_secondary,
            :company_registration_number,
            :created_by
            )
            """)
            with org_engine.begin() as conn:
                conn.execute(insert_into_organization_master, payload)
                success_message = {"Successfully created organization: ": {payload.get("org_name")}}
                return success_message
    except Exception as e:
        error_message = {"Failed to create organization. Error Message: ": {e}}
        return error_message