from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


AP_INSERT_UPDATE_FIELDS = [
    "ap_org_id_fk",
    "ap_supp_id_fk",
    "ap_invoice_number",
    "ap_invoice_date",
    "ap_due_date",
    "ap_description",
    "ap_currency_code",
    "ap_invoice_amount",
    "ap_tax_amount",
    "ap_paid_amount",
    "ap_approval_status",
    "ap_payment_status",
    "ap_approval_comments",
    "ap_approved_by",
    "ap_approved_at",
    "ap_notes",
    "ap_invoice_file_path",
    "created_by",
    "updated_by",
]


def get_ap_id_from_payload(payload: dict):
    return payload.get("ap_id") or payload.get("ap_id_pk")


def get_ap_params(payload: dict):
    return {
        "ap_org_id_fk": payload.get("ap_org_id_fk") or payload.get("authenticated_org_id"),
        "ap_supp_id_fk": payload.get("ap_supp_id_fk"),
        "ap_invoice_number": payload.get("ap_invoice_number"),
        "ap_invoice_date": payload.get("ap_invoice_date"),
        "ap_due_date": payload.get("ap_due_date"),
        "ap_description": payload.get("ap_description"),
        "ap_currency_code": payload.get("ap_currency_code", "AED"),
        "ap_invoice_amount": payload.get("ap_invoice_amount"),
        "ap_tax_amount": payload.get("ap_tax_amount", 0),
        "ap_paid_amount": payload.get("ap_paid_amount", 0),
        "ap_approval_status": payload.get("ap_approval_status", "DRAFT"),
        "ap_payment_status": payload.get("ap_payment_status", "UNPAID"),
        "ap_approval_comments": payload.get("ap_approval_comments"),
        "ap_approved_by": payload.get("ap_approved_by"),
        "ap_approved_at": payload.get("ap_approved_at"),
        "ap_notes": payload.get("ap_notes"),
        "ap_invoice_file_path": payload.get("ap_invoice_file_path"),
        "created_by": payload.get("created_by"),
        "updated_by": payload.get("updated_by"),
    }


def validate_ap_payload(payload: dict, require_id: bool = False):
    if require_id and not get_ap_id_from_payload(payload):
        return {"error": "ap_id is required."}

    required_fields = [
        "ap_org_id_fk",
        "ap_supp_id_fk",
        "ap_invoice_number",
        "ap_invoice_date",
        "ap_currency_code",
        "ap_invoice_amount",
    ]
    for field_name in required_fields:
        if payload.get(field_name) is None or payload.get(field_name) == "":
            return {"error": f"{field_name} is required."}

    if payload.get("ap_tax_amount") is not None and payload.get("ap_tax_amount") < 0:
        return {"error": "ap_tax_amount must be greater than or equal to zero."}

    if payload.get("ap_paid_amount") is not None and payload.get("ap_paid_amount") < 0:
        return {"error": "ap_paid_amount must be greater than or equal to zero."}

    if payload.get("ap_invoice_amount") is not None and payload.get("ap_invoice_amount") < 0:
        return {"error": "ap_invoice_amount must be greater than or equal to zero."}

    return None


def get_ap_by_id(ap_id, ap_org_id_fk=None, conn=None):
    get_ap_query = text("""
        select *
        from accounts_payables
        where ap_id_pk = :ap_id
        and (:ap_org_id_fk is null or ap_org_id_fk = :ap_org_id_fk)
    """)
    params = {
        "ap_id": ap_id,
        "ap_org_id_fk": ap_org_id_fk
    }
    if conn is not None:
        df_ap = pd.read_sql(sql=get_ap_query, con=conn, params=params)
    else:
        ap_engine = db_engine()
        try:
            with ap_engine.begin() as ap_conn:
                df_ap = pd.read_sql(sql=get_ap_query, con=ap_conn, params=params)
        finally:
            ap_engine.dispose()

    if df_ap.empty:
        return None

    return df_ap.iloc[0].to_dict()


def verify_organization_exists(ap_org_id_fk, conn=None):
    get_org_query = text("""
        select org_id_pk
        from organization_master
        where org_id_pk = :ap_org_id_fk
    """)
    if conn is not None:
        df_org = pd.read_sql(
            sql=get_org_query,
            con=conn,
            params={"ap_org_id_fk": ap_org_id_fk}
        )
    else:
        ap_engine = db_engine()
        try:
            with ap_engine.begin() as ap_conn:
                df_org = pd.read_sql(
                    sql=get_org_query,
                    con=ap_conn,
                    params={"ap_org_id_fk": ap_org_id_fk}
                )
        finally:
            ap_engine.dispose()

    return not df_org.empty


def verify_supplier_for_organization(ap_org_id_fk, ap_supp_id_fk, conn=None):
    get_supplier_query = text("""
        select supp_id_pk
        from supplier_master
        where supp_id_pk = :ap_supp_id_fk
        and supp_org_id_fk = :ap_org_id_fk
    """)
    params = {
        "ap_org_id_fk": ap_org_id_fk,
        "ap_supp_id_fk": ap_supp_id_fk
    }
    if conn is not None:
        df_supplier = pd.read_sql(sql=get_supplier_query, con=conn, params=params)
    else:
        ap_engine = db_engine()
        try:
            with ap_engine.begin() as ap_conn:
                df_supplier = pd.read_sql(sql=get_supplier_query, con=ap_conn, params=params)
        finally:
            ap_engine.dispose()

    return not df_supplier.empty


def duplicate_ap_invoice_exists(
    ap_org_id_fk,
    ap_supp_id_fk,
    ap_invoice_number,
    exclude_ap_id=None,
    conn=None,
):
    duplicate_query = text("""
        select ap_id_pk
        from accounts_payables
        where ap_org_id_fk = :ap_org_id_fk
        and ap_supp_id_fk = :ap_supp_id_fk
        and lower(ap_invoice_number) = lower(:ap_invoice_number)
        and (:exclude_ap_id is null or ap_id_pk <> :exclude_ap_id)
    """)
    params = {
        "ap_org_id_fk": ap_org_id_fk,
        "ap_supp_id_fk": ap_supp_id_fk,
        "ap_invoice_number": ap_invoice_number,
        "exclude_ap_id": exclude_ap_id
    }
    if conn is not None:
        df_duplicate = pd.read_sql(sql=duplicate_query, con=conn, params=params)
    else:
        ap_engine = db_engine()
        try:
            with ap_engine.begin() as ap_conn:
                df_duplicate = pd.read_sql(sql=duplicate_query, con=ap_conn, params=params)
        finally:
            ap_engine.dispose()

    return not df_duplicate.empty


def payload_has_invoice_document(payload: dict):
    return bool(payload.get("file_path") or payload.get("ap_invoice_local_file_path"))
