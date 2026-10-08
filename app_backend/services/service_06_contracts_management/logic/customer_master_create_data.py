from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


def get_customer_insert_params(payload: dict):
    return {
        "cust_org_id_fk": payload.get("cust_org_id_fk"),
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
        "created_by": payload.get("created_by"),
        "updated_by": payload.get("updated_by")
    }


# Create new customer
def create_customer_master(payload: dict):
    try:
        customer_engine = db_engine()
        cust_code = payload.get("cust_code")
        cust_org_id_fk = payload.get("cust_org_id_fk") or payload.get("authenticated_org_id")

        if cust_code and cust_org_id_fk:
            get_customer_code = text("""
                select cust_code
                from customer_master
                where cust_org_id_fk = :cust_org_id_fk
                and lower(cust_code) = lower(:cust_code)
            """)

            with customer_engine.begin() as conn:
                df_customer_code = pd.read_sql(
                    sql=get_customer_code,
                    con=conn,
                    params={
                        "cust_org_id_fk": cust_org_id_fk,
                        "cust_code": cust_code
                    }
                )

            if not df_customer_code.empty:
                return {"error": f"Customer already exists: {cust_code}"}

        insert_into_customer_master = text("""
            insert into customer_master(
                cust_org_id_fk,
                cust_code,
                cust_name,
                cust_category,
                cust_status,
                portal_system,
                procurement_head_name,
                procurement_head_phone_primary,
                procurement_head_phone_secondary,
                procurement_head_email_primary,
                procurement_head_email_secondary,
                operation_incharge_name,
                operation_incharge_phone_primary,
                operation_incharge_phone_secondary,
                operation_incharge_email_primary,
                operation_incharge_email_secondary,
                operation_head_name,
                operation_head_phone_primary,
                operation_head_phone_secondary,
                operation_head_email_primary,
                operation_head_email_secondary,
                finance_incharge_name,
                finance_incharge_phone_primary,
                finance_incharge_phone_secondary,
                finance_incharge_email_primary,
                finance_incharge_email_secondary,
                finance_head_name,
                finance_head_phone_primary,
                finance_head_phone_secondary,
                finance_head_email_primary,
                finance_head_email_secondary,
                wcr_incharge_name,
                wcr_incharge_phone_primary,
                wcr_incharge_phone_secondary,
                wcr_incharge_email_primary,
                wcr_incharge_email_secondary,
                grn_incharge_name,
                grn_incharge_phone_primary,
                grn_incharge_phone_secondary,
                grn_incharge_email_primary,
                grn_incharge_email_secondary,
                cust_billing_address,
                cust_service_address,
                cust_tax_registration_number,
                cust_credit_period_days,
                cust_notes,
                created_by,
                updated_by
            ) values (
                :cust_org_id_fk,
                :cust_code,
                :cust_name,
                :cust_category,
                :cust_status,
                :portal_system,
                :procurement_head_name,
                :procurement_head_phone_primary,
                :procurement_head_phone_secondary,
                :procurement_head_email_primary,
                :procurement_head_email_secondary,
                :operation_incharge_name,
                :operation_incharge_phone_primary,
                :operation_incharge_phone_secondary,
                :operation_incharge_email_primary,
                :operation_incharge_email_secondary,
                :operation_head_name,
                :operation_head_phone_primary,
                :operation_head_phone_secondary,
                :operation_head_email_primary,
                :operation_head_email_secondary,
                :finance_incharge_name,
                :finance_incharge_phone_primary,
                :finance_incharge_phone_secondary,
                :finance_incharge_email_primary,
                :finance_incharge_email_secondary,
                :finance_head_name,
                :finance_head_phone_primary,
                :finance_head_phone_secondary,
                :finance_head_email_primary,
                :finance_head_email_secondary,
                :wcr_incharge_name,
                :wcr_incharge_phone_primary,
                :wcr_incharge_phone_secondary,
                :wcr_incharge_email_primary,
                :wcr_incharge_email_secondary,
                :grn_incharge_name,
                :grn_incharge_phone_primary,
                :grn_incharge_phone_secondary,
                :grn_incharge_email_primary,
                :grn_incharge_email_secondary,
                :cust_billing_address,
                :cust_service_address,
                :cust_tax_registration_number,
                :cust_credit_period_days,
                :cust_notes,
                :created_by,
                :updated_by
            )
        """)

        params_insert = {
            **get_customer_insert_params(payload),
            "cust_org_id_fk": cust_org_id_fk
        }
        if params_insert.get("created_by") is None:
            return {"error": "created_by is required."}

        with customer_engine.begin() as conn:
            conn.execute(insert_into_customer_master, params_insert)

        return {"message": f"Successfully created customer: {cust_code}"}

    except Exception as e:
        return {"error": f"Failed to create customer. Error Message: {str(e)}"}


def create_customer(payload: dict):
    return create_customer_master(payload)
