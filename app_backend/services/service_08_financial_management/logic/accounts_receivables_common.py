from sqlalchemy import text
import pandas as pd
from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import ARValidationError, decimal_value

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


AR_APPROVAL_STATUSES = {
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
}

AR_COLLECTION_STATUSES = {
    "OUTSTANDING",
    "PARTIALLY_RECEIVED",
    "RECEIVED",
}


def get_ar_id_from_payload(payload: dict):
    return payload.get("ar_id") or payload.get("ar_id_pk")


def get_ar_params(payload: dict):
    return {
        "ar_org_id_fk": payload.get("ar_org_id_fk") or payload.get("authenticated_org_id"),
        "ar_cust_id_fk": payload.get("ar_cust_id_fk"),
        "ar_invoice_number": payload.get("ar_invoice_number"),
        "ar_invoice_date": payload.get("ar_invoice_date"),
        "ar_due_date": payload.get("ar_due_date"),
        "ar_contract": payload.get("ar_contract"),
        "ar_description": payload.get("ar_description"),
        "ar_currency_code": payload.get("ar_currency_code", "AED"),
        "ar_invoice_amount": payload.get("ar_invoice_amount"),
        "ar_tax_amount": payload.get("ar_tax_amount", 0),
        "ar_received_amount": payload.get("ar_received_amount", 0),
        "ar_approval_status": payload.get("ar_approval_status", "DRAFT"),
        "ar_collection_status": payload.get("ar_collection_status", "OUTSTANDING"),
        "ar_approval_comments": payload.get("ar_approval_comments"),
        "ar_approved_by": payload.get("ar_approved_by"),
        "ar_approved_at": payload.get("ar_approved_at"),
        "ar_notes": payload.get("ar_notes"),
        "ar_invoice_file_path": payload.get("ar_invoice_file_path"),
        "created_by": payload.get("created_by"),
        "updated_by": payload.get("updated_by"),
    }


def validate_ar_payload(payload: dict, require_id: bool = False):
    # JSONB workflow serialization may return Decimal values as strings.
    # Normalize before validation without changing historical gross/tax semantics.
    payload = dict(payload)
    try:
        for field in ("ar_invoice_amount", "ar_tax_amount", "ar_received_amount"):
            if payload.get(field) is not None:
                payload[field] = decimal_value(payload[field], field)
    except ARValidationError as exc:
        return exc.response()
    if require_id and not get_ar_id_from_payload(payload):
        return {"error": "ar_id is required."}

    required_fields = [
        "ar_org_id_fk",
        "ar_cust_id_fk",
        "ar_invoice_number",
        "ar_invoice_date",
        "ar_currency_code",
        "ar_invoice_amount",
    ]
    for field_name in required_fields:
        if payload.get(field_name) is None or payload.get(field_name) == "":
            return {"error": f"{field_name} is required."}

    ar_approval_status = payload.get("ar_approval_status", "DRAFT")
    if ar_approval_status not in AR_APPROVAL_STATUSES:
        return {"error": f"Invalid ar_approval_status: {ar_approval_status}"}

    ar_collection_status = payload.get("ar_collection_status", "OUTSTANDING")
    if ar_collection_status not in AR_COLLECTION_STATUSES:
        return {"error": f"Invalid ar_collection_status: {ar_collection_status}"}

    if payload.get("ar_tax_amount") is not None and payload.get("ar_tax_amount") < 0:
        return {"error": "ar_tax_amount must be greater than or equal to zero."}

    if (
        payload.get("ar_received_amount") is not None
        and payload.get("ar_received_amount") < 0
    ):
        return {"error": "ar_received_amount must be greater than or equal to zero."}

    if (
        payload.get("ar_invoice_amount") is not None
        and payload.get("ar_invoice_amount") < 0
    ):
        return {"error": "ar_invoice_amount must be greater than or equal to zero."}

    return None


def get_ar_by_id(ar_id, ar_org_id_fk=None, conn=None):
    get_ar_query = text("""
        select *
        from accounts_receivables
        where ar_id_pk = :ar_id
        and (:ar_org_id_fk is null or ar_org_id_fk = :ar_org_id_fk)
    """)
    params = {
        "ar_id": ar_id,
        "ar_org_id_fk": ar_org_id_fk
    }
    if conn is not None:
        df_ar = pd.read_sql(sql=get_ar_query, con=conn, params=params, coerce_float=False)
    else:
        ar_engine = db_engine()
        try:
            with ar_engine.begin() as ar_conn:
                df_ar = pd.read_sql(sql=get_ar_query, con=ar_conn, params=params, coerce_float=False)
        finally:
            ar_engine.dispose()

    if df_ar.empty:
        return None

    return df_ar.iloc[0].to_dict()


def verify_ar_organization_exists(ar_org_id_fk, conn=None):
    get_org_query = text("""
        select org_id_pk
        from organization_master
        where org_id_pk = :ar_org_id_fk
    """)
    if conn is not None:
        df_org = pd.read_sql(
            sql=get_org_query,
            con=conn,
            params={"ar_org_id_fk": ar_org_id_fk}
        )
    else:
        ar_engine = db_engine()
        try:
            with ar_engine.begin() as ar_conn:
                df_org = pd.read_sql(
                    sql=get_org_query,
                    con=ar_conn,
                    params={"ar_org_id_fk": ar_org_id_fk}
                )
        finally:
            ar_engine.dispose()

    return not df_org.empty


def verify_customer_for_organization(ar_org_id_fk, ar_cust_id_fk, conn=None):
    get_customer_query = text("""
        select cust_id_pk
        from customer_master
        where cust_id_pk = :ar_cust_id_fk
        and cust_org_id_fk = :ar_org_id_fk
    """)
    params = {
        "ar_org_id_fk": ar_org_id_fk,
        "ar_cust_id_fk": ar_cust_id_fk
    }
    if conn is not None:
        df_customer = pd.read_sql(sql=get_customer_query, con=conn, params=params)
    else:
        ar_engine = db_engine()
        try:
            with ar_engine.begin() as ar_conn:
                df_customer = pd.read_sql(sql=get_customer_query, con=ar_conn, params=params)
        finally:
            ar_engine.dispose()

    return not df_customer.empty


def duplicate_ar_invoice_exists(ar_org_id_fk, ar_invoice_number, exclude_ar_id=None, conn=None):
    duplicate_query = text("""
        select ar_id_pk
        from accounts_receivables
        where ar_org_id_fk = :ar_org_id_fk
        and lower(ar_invoice_number) = lower(:ar_invoice_number)
        and (:exclude_ar_id is null or ar_id_pk <> :exclude_ar_id)
    """)
    params = {
        "ar_org_id_fk": ar_org_id_fk,
        "ar_invoice_number": ar_invoice_number,
        "exclude_ar_id": exclude_ar_id
    }
    if conn is not None:
        df_duplicate = pd.read_sql(sql=duplicate_query, con=conn, params=params)
    else:
        ar_engine = db_engine()
        try:
            with ar_engine.begin() as ar_conn:
                df_duplicate = pd.read_sql(sql=duplicate_query, con=ar_conn, params=params)
        finally:
            ar_engine.dispose()

    return not df_duplicate.empty


def payload_has_ar_invoice_document(payload: dict):
    return bool(payload.get("file_path") or payload.get("ar_invoice_local_file_path"))
