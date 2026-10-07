from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


SUPPLIER_STATUSES = {
    "ACTIVE",
    "INACTIVE",
    "SUSPENDED",
    "BLACKLISTED",
}


def get_supplier_params(payload: dict):
    return {
        "supp_org_id_fk": payload.get("supp_org_id_fk"),
        "supp_code": payload.get("supp_code"),
        "supp_name": payload.get("supp_name"),
        "supp_category": payload.get("supp_category"),
        "supp_status": payload.get("supp_status", "ACTIVE"),
        "supp_contact_person_name": payload.get("supp_contact_person_name"),
        "supp_contact_person_designation": payload.get(
            "supp_contact_person_designation"
        ),
        "supp_phone_primary": payload.get("supp_phone_primary"),
        "supp_phone_secondary": payload.get("supp_phone_secondary"),
        "supp_email_primary": payload.get("supp_email_primary"),
        "supp_email_secondary": payload.get("supp_email_secondary"),
        "supp_billing_address": payload.get("supp_billing_address"),
        "supp_service_address": payload.get("supp_service_address"),
        "supp_tax_registration_number": payload.get(
            "supp_tax_registration_number"
        ),
        "supp_credit_period_days": payload.get("supp_credit_period_days", 0),
        "supp_notes": payload.get("supp_notes"),
        "created_by": payload.get("created_by"),
        "updated_by": payload.get("updated_by"),
    }


def validate_supplier_payload(payload: dict):
    required_fields = [
        "supp_org_id_fk",
        "supp_code",
        "supp_name",
        "supp_category",
    ]
    for field_name in required_fields:
        if payload.get(field_name) is None or payload.get(field_name) == "":
            return {"error": f"{field_name} is required."}

    supp_status = payload.get("supp_status", "ACTIVE")
    if supp_status not in SUPPLIER_STATUSES:
        return {"error": f"Invalid supp_status: {supp_status}"}

    supp_credit_period_days = payload.get("supp_credit_period_days", 0)
    if supp_credit_period_days is None:
        supp_credit_period_days = 0

    if not isinstance(supp_credit_period_days, int) or isinstance(
        supp_credit_period_days,
        bool
    ):
        return {"error": "supp_credit_period_days must be an integer."}

    if supp_credit_period_days < 0:
        return {"error": "supp_credit_period_days must be greater than or equal to zero."}

    return None


def verify_supplier_organization_exists(supp_org_id_fk, supplier_engine=None):
    supplier_engine = supplier_engine or db_engine()
    get_org_query = text("""
        select org_id_pk
        from organization_master
        where org_id_pk = :supp_org_id_fk
    """)

    with supplier_engine.begin() as conn:
        df_org = pd.read_sql(
            sql=get_org_query,
            con=conn,
            params={"supp_org_id_fk": supp_org_id_fk}
        )

    return not df_org.empty


def duplicate_supplier_code_exists(
    supp_org_id_fk,
    supp_code,
    exclude_supp_id=None,
    supplier_engine=None
):
    supplier_engine = supplier_engine or db_engine()
    duplicate_query = text("""
        select supp_id_pk
        from supplier_master
        where supp_org_id_fk = :supp_org_id_fk
        and supp_code = :supp_code
        and (:exclude_supp_id is null or supp_id_pk <> :exclude_supp_id)
    """)

    with supplier_engine.begin() as conn:
        df_duplicate = pd.read_sql(
            sql=duplicate_query,
            con=conn,
            params={
                "supp_org_id_fk": supp_org_id_fk,
                "supp_code": supp_code,
                "exclude_supp_id": exclude_supp_id
            }
        )

    return not df_duplicate.empty


def create_supplier_master(payload: dict):
    supplier_engine = None
    try:
        validation_error = validate_supplier_payload(payload)
        if validation_error:
            return validation_error

        supplier_engine = db_engine()
        supp_org_id_fk = payload.get("supp_org_id_fk") or payload.get("authenticated_org_id")
        supp_code = payload.get("supp_code")

        if not verify_supplier_organization_exists(supp_org_id_fk, supplier_engine):
            return {"error": "supp_org_id_fk not found in organization_master."}

        if duplicate_supplier_code_exists(
            supp_org_id_fk,
            supp_code,
            supplier_engine=supplier_engine
        ):
            return {
                "error": (
                    "Supplier already exists for this organization and supplier code: "
                    f"{supp_code}"
                )
            }

        insert_into_supplier_master = text("""
            insert into supplier_master(
                supp_org_id_fk,
                supp_code,
                supp_name,
                supp_category,
                supp_status,
                supp_contact_person_name,
                supp_contact_person_designation,
                supp_phone_primary,
                supp_phone_secondary,
                supp_email_primary,
                supp_email_secondary,
                supp_billing_address,
                supp_service_address,
                supp_tax_registration_number,
                supp_credit_period_days,
                supp_notes,
                created_by,
                updated_by
            ) values (
                :supp_org_id_fk,
                :supp_code,
                :supp_name,
                :supp_category,
                coalesce(:supp_status, 'ACTIVE'),
                :supp_contact_person_name,
                :supp_contact_person_designation,
                :supp_phone_primary,
                :supp_phone_secondary,
                :supp_email_primary,
                :supp_email_secondary,
                :supp_billing_address,
                :supp_service_address,
                :supp_tax_registration_number,
                coalesce(:supp_credit_period_days, 0),
                :supp_notes,
                :created_by,
                :updated_by
            )
            returning supp_id_pk
        """)

        params_insert = {
            **get_supplier_params(payload),
            "supp_org_id_fk": supp_org_id_fk
        }

        with supplier_engine.begin() as conn:
            supp_id = conn.execute(
                insert_into_supplier_master,
                params_insert
            ).scalar_one()

        return {
            "message": f"Successfully created supplier: {supp_code}",
            "supp_id_pk": supp_id,
            "supp_code": supp_code,
            "supp_name": payload.get("supp_name")
        }

    except IntegrityError as e:
        return {"error": f"Failed to create supplier due to database constraint. Error Message: {str(e.orig)}"}
    except Exception as e:
        return {"error": f"Failed to create supplier. Error Message: {str(e)}"}
    finally:
        if supplier_engine is not None:
            supplier_engine.dispose()


def create_supplier(payload: dict):
    return create_supplier_master(payload)
