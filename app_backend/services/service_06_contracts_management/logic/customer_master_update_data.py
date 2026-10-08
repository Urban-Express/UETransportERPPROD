from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Update existing customer
def update_customer_master(payload: dict):
    try:
        customer_engine = db_engine()
        cust_id = (
            payload.get("cust_id")
            or payload.get("cust_id_pk")
        )
        cust_org_id_fk = payload.get("cust_org_id_fk") or payload.get("authenticated_org_id")

        if not cust_id:
            return {"error": "cust_id is required."}

        get_cust_id = text("""
            select cust_id_pk
            from customer_master
            where cust_id_pk = :cust_id
            and cust_org_id_fk = :cust_org_id_fk
        """)

        with customer_engine.begin() as conn:
            df_cust_id = pd.read_sql(
                sql=get_cust_id,
                con=conn,
                params={
                    "cust_id": cust_id,
                    "cust_org_id_fk": cust_org_id_fk
                }
            )

        if df_cust_id.empty:
            return {"error": "Customer ID not found."}

        update_customer_master_query = text("""
            update customer_master
            set
                cust_org_id_fk = :cust_org_id_fk,
                cust_code = :cust_code,
                cust_name = :cust_name,
                cust_category = :cust_category,
                cust_status = :cust_status,
                portal_system = :portal_system,
                procurement_head_name = :procurement_head_name,
                procurement_head_phone_primary = :procurement_head_phone_primary,
                procurement_head_phone_secondary = :procurement_head_phone_secondary,
                procurement_head_email_primary = :procurement_head_email_primary,
                procurement_head_email_secondary = :procurement_head_email_secondary,
                operation_incharge_name = :operation_incharge_name,
                operation_incharge_phone_primary = :operation_incharge_phone_primary,
                operation_incharge_phone_secondary = :operation_incharge_phone_secondary,
                operation_incharge_email_primary = :operation_incharge_email_primary,
                operation_incharge_email_secondary = :operation_incharge_email_secondary,
                operation_head_name = :operation_head_name,
                operation_head_phone_primary = :operation_head_phone_primary,
                operation_head_phone_secondary = :operation_head_phone_secondary,
                operation_head_email_primary = :operation_head_email_primary,
                operation_head_email_secondary = :operation_head_email_secondary,
                finance_incharge_name = :finance_incharge_name,
                finance_incharge_phone_primary = :finance_incharge_phone_primary,
                finance_incharge_phone_secondary = :finance_incharge_phone_secondary,
                finance_incharge_email_primary = :finance_incharge_email_primary,
                finance_incharge_email_secondary = :finance_incharge_email_secondary,
                finance_head_name = :finance_head_name,
                finance_head_phone_primary = :finance_head_phone_primary,
                finance_head_phone_secondary = :finance_head_phone_secondary,
                finance_head_email_primary = :finance_head_email_primary,
                finance_head_email_secondary = :finance_head_email_secondary,
                wcr_incharge_name = :wcr_incharge_name,
                wcr_incharge_phone_primary = :wcr_incharge_phone_primary,
                wcr_incharge_phone_secondary = :wcr_incharge_phone_secondary,
                wcr_incharge_email_primary = :wcr_incharge_email_primary,
                wcr_incharge_email_secondary = :wcr_incharge_email_secondary,
                grn_incharge_name = :grn_incharge_name,
                grn_incharge_phone_primary = :grn_incharge_phone_primary,
                grn_incharge_phone_secondary = :grn_incharge_phone_secondary,
                grn_incharge_email_primary = :grn_incharge_email_primary,
                grn_incharge_email_secondary = :grn_incharge_email_secondary,
                cust_billing_address = :cust_billing_address,
                cust_service_address = :cust_service_address,
                cust_tax_registration_number = :cust_tax_registration_number,
                cust_credit_period_days = :cust_credit_period_days,
                cust_notes = :cust_notes,
                updated_by = :updated_by,
                updated_at = CURRENT_TIMESTAMP
            where cust_id_pk = :cust_id
            and cust_org_id_fk = :cust_org_id_fk
        """)

        params_update = {
            "cust_id": cust_id,
            "cust_org_id_fk": cust_org_id_fk,
            "cust_code": payload.get("cust_code"),
            "cust_name": payload.get("cust_name"),
            "cust_category": payload.get("cust_category"),
            "cust_status": payload.get("cust_status", "ACTIVE"),
            "portal_system": payload.get("portal_system"),
            "procurement_head_name": payload.get("procurement_head_name"),
            "procurement_head_phone_primary": payload.get("procurement_head_phone_primary"),
            "procurement_head_phone_secondary": payload.get("procurement_head_phone_secondary"),
            "procurement_head_email_primary": payload.get("procurement_head_email_primary"),
            "procurement_head_email_secondary": payload.get("procurement_head_email_secondary"),
            "operation_incharge_name": payload.get("operation_incharge_name"),
            "operation_incharge_phone_primary": payload.get("operation_incharge_phone_primary"),
            "operation_incharge_phone_secondary": payload.get("operation_incharge_phone_secondary"),
            "operation_incharge_email_primary": payload.get("operation_incharge_email_primary"),
            "operation_incharge_email_secondary": payload.get("operation_incharge_email_secondary"),
            "operation_head_name": payload.get("operation_head_name"),
            "operation_head_phone_primary": payload.get("operation_head_phone_primary"),
            "operation_head_phone_secondary": payload.get("operation_head_phone_secondary"),
            "operation_head_email_primary": payload.get("operation_head_email_primary"),
            "operation_head_email_secondary": payload.get("operation_head_email_secondary"),
            "finance_incharge_name": payload.get("finance_incharge_name"),
            "finance_incharge_phone_primary": payload.get("finance_incharge_phone_primary"),
            "finance_incharge_phone_secondary": payload.get("finance_incharge_phone_secondary"),
            "finance_incharge_email_primary": payload.get("finance_incharge_email_primary"),
            "finance_incharge_email_secondary": payload.get("finance_incharge_email_secondary"),
            "finance_head_name": payload.get("finance_head_name"),
            "finance_head_phone_primary": payload.get("finance_head_phone_primary"),
            "finance_head_phone_secondary": payload.get("finance_head_phone_secondary"),
            "finance_head_email_primary": payload.get("finance_head_email_primary"),
            "finance_head_email_secondary": payload.get("finance_head_email_secondary"),
            "wcr_incharge_name": payload.get("wcr_incharge_name"),
            "wcr_incharge_phone_primary": payload.get("wcr_incharge_phone_primary"),
            "wcr_incharge_phone_secondary": payload.get("wcr_incharge_phone_secondary"),
            "wcr_incharge_email_primary": payload.get("wcr_incharge_email_primary"),
            "wcr_incharge_email_secondary": payload.get("wcr_incharge_email_secondary"),
            "grn_incharge_name": payload.get("grn_incharge_name"),
            "grn_incharge_phone_primary": payload.get("grn_incharge_phone_primary"),
            "grn_incharge_phone_secondary": payload.get("grn_incharge_phone_secondary"),
            "grn_incharge_email_primary": payload.get("grn_incharge_email_primary"),
            "grn_incharge_email_secondary": payload.get("grn_incharge_email_secondary"),
            "cust_billing_address": payload.get("cust_billing_address"),
            "cust_service_address": payload.get("cust_service_address"),
            "cust_tax_registration_number": payload.get(
                "cust_tax_registration_number"
            ),
            "cust_credit_period_days": payload.get("cust_credit_period_days", 0),
            "cust_notes": payload.get("cust_notes"),
            "updated_by": payload.get("updated_by")
        }

        with customer_engine.begin() as conn:
            conn.execute(update_customer_master_query, params_update)

        return {
            "message": f"Successfully updated customer: {payload.get('cust_code')}"
        }

    except Exception as e:
        return {"error": f"Failed to update customer. Error Message: {str(e)}"}


def update_customer(payload: dict):
    return update_customer_master(payload)
