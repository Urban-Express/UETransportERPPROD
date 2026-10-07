from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_08_financial_management.logic.supplier_master_create_data import (
    duplicate_supplier_code_exists,
    get_supplier_params,
    validate_supplier_payload,
    verify_supplier_organization_exists,
)


def update_supplier_master(payload: dict):
    supplier_engine = None
    try:
        supplier_engine = db_engine()
        supp_id = (
            payload.get("supp_id")
            or payload.get("supp_id_pk")
        )
        supp_org_id_fk = payload.get("supp_org_id_fk") or payload.get("authenticated_org_id")

        if not supp_id:
            return {"error": "supp_id is required."}

        validation_error = validate_supplier_payload(payload)
        if validation_error:
            return validation_error

        get_supp_id = text("""
            select supp_id_pk
            from supplier_master
            where supp_id_pk = :supp_id
            and supp_org_id_fk = :supp_org_id_fk
        """)

        with supplier_engine.begin() as conn:
            df_supp_id = pd.read_sql(
                sql=get_supp_id,
                con=conn,
                params={
                    "supp_id": supp_id,
                    "supp_org_id_fk": supp_org_id_fk
                }
            )

        if df_supp_id.empty:
            return {"error": "Supplier ID not found."}

        supp_code = payload.get("supp_code")

        if not verify_supplier_organization_exists(supp_org_id_fk, supplier_engine):
            return {"error": "supp_org_id_fk not found in organization_master."}

        if duplicate_supplier_code_exists(
            supp_org_id_fk,
            supp_code,
            exclude_supp_id=supp_id,
            supplier_engine=supplier_engine
        ):
            return {
                "error": (
                    "Supplier already exists for this organization and supplier code: "
                    f"{supp_code}"
                )
            }

        update_supplier_master_query = text("""
            update supplier_master
            set
                supp_org_id_fk = :supp_org_id_fk,
                supp_code = :supp_code,
                supp_name = :supp_name,
                supp_category = :supp_category,
                supp_status = coalesce(:supp_status, 'ACTIVE'),
                supp_contact_person_name = :supp_contact_person_name,
                supp_contact_person_designation = :supp_contact_person_designation,
                supp_phone_primary = :supp_phone_primary,
                supp_phone_secondary = :supp_phone_secondary,
                supp_email_primary = :supp_email_primary,
                supp_email_secondary = :supp_email_secondary,
                supp_billing_address = :supp_billing_address,
                supp_service_address = :supp_service_address,
                supp_tax_registration_number = :supp_tax_registration_number,
                supp_credit_period_days = coalesce(:supp_credit_period_days, 0),
                supp_notes = :supp_notes,
                updated_by = :updated_by,
                updated_at = CURRENT_TIMESTAMP
            where supp_id_pk = :supp_id
            and supp_org_id_fk = :supp_org_id_fk
        """)

        params_update = {
            **get_supplier_params(payload),
            "supp_id": supp_id,
            "supp_org_id_fk": supp_org_id_fk
        }

        with supplier_engine.begin() as conn:
            conn.execute(update_supplier_master_query, params_update)

        return {
            "message": f"Successfully updated supplier: {supp_code}",
            "supp_id_pk": supp_id,
            "supp_code": supp_code
        }

    except IntegrityError as e:
        return {"error": f"Failed to update supplier due to database constraint. Error Message: {str(e.orig)}"}
    except Exception as e:
        return {"error": f"Failed to update supplier. Error Message: {str(e)}"}
    finally:
        if supplier_engine is not None:
            supplier_engine.dispose()


def update_supplier(payload: dict):
    return update_supplier_master(payload)
