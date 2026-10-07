"""Organization-bound, read-only configuration and immutable invoice identity."""
from contextlib import contextmanager
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import ARValidationError, ar_json_safe, date_value


@contextmanager
def ar_connection(conn=None):
    if conn is not None:
        yield conn
        return
    engine = db_engine()
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def tax_options(conn, org_id, invoice_date):
    return [dict(row) for row in conn.execute(text("""
        SELECT tax_config_id_pk,tax_code,tax_name,tax_treatment,tax_rate,is_default,effective_from,effective_to
        FROM organization_tax_configuration
        WHERE tax_org_id_fk=:org AND is_active
          AND (effective_from IS NULL OR effective_from<=:day)
          AND (effective_to IS NULL OR effective_to>=:day)
        ORDER BY tax_code
    """), {"org": org_id, "day": invoice_date}).mappings()]


def resolve_tax(conn, org_id, invoice_date, tax_code=None):
    rows = [row for row in tax_options(conn, org_id, invoice_date)
            if row["tax_code"] == tax_code] if tax_code else [row for row in tax_options(conn, org_id, invoice_date) if row["is_default"]]
    if len(rows) != 1:
        raise ARValidationError("Invalid tax selection: one active, invoice-date-valid organization tax configuration is required.", "AR_TAX_CONFIGURATION_REQUIRED")
    row = rows[0]
    return {"ar_line_tax_config_id_fk": row["tax_config_id_pk"], "ar_line_tax_code": row["tax_code"],
            "ar_line_tax_treatment": row["tax_treatment"], "ar_line_tax_rate": row["tax_rate"]}


def get_accounts_receivables_tax_options(payload):
    try:
        org = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")
        if not org:
            raise ARValidationError("ar_org_id_fk is required.")
        day = date_value(payload.get("ar_invoice_date"), "ar_invoice_date", required=True)
        with ar_connection() as conn:
            return ar_json_safe({"tax_options": tax_options(conn, org, day)})
    except ARValidationError as exc:
        return exc.response()


def customer_identity(conn, org_id, customer_id):
    row = conn.execute(text("""
        SELECT cust_id_pk,cust_name,cust_billing_address,cust_phone_primary,cust_phone_secondary,cust_tax_registration_number
        FROM customer_master WHERE cust_id_pk=:customer AND cust_org_id_fk=:org
    """), {"customer": customer_id, "org": org_id}).mappings().one_or_none()
    if not row:
        raise ARValidationError("Customer not found for the authenticated organization.")
    if not row["cust_name"] or not row["cust_name"].strip():
        raise ARValidationError("Customer legal invoice name is required.")
    return dict(row)


def invoice_identity(conn, org_id, customer_id, existing=None):
    customer = customer_identity(conn, org_id, customer_id)
    if existing:
        # Same customer/issuer: do not silently adopt later master-data edits.
        return {**existing, "customer": existing["customer"] if existing["customer"]["cust_id_pk"] == customer_id else customer}
    organization = conn.execute(text("""
        SELECT org_id_pk,org_name,org_address,org_phone_primary,org_phone_secondary,
               org_email_primary,org_email_secondary,company_registration_number
        FROM organization_master WHERE org_id_pk=:org
    """), {"org": org_id}).mappings().one_or_none()
    config = conn.execute(text("""
        SELECT invoice_config_id_pk,organization_trn,bank_name,bank_account_name,bank_account_number,iban,
               signatory_name,signatory_designation
        FROM organization_invoice_configuration WHERE invoice_org_id_fk=:org AND is_active
    """), {"org": org_id}).mappings().one_or_none()
    if not organization or not organization["org_name"] or not config or any(value is None or value == "" for value in config.values()):
        raise ARValidationError("Invalid organization invoice configuration: an active configuration and company name are required.", "AR_INVOICE_CONFIGURATION_REQUIRED")
    return {"organization": dict(organization), "invoice_configuration": dict(config), "customer": customer}
