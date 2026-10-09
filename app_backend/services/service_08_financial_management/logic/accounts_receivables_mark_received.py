"""Atomic, workflow-free full receipt of an authenticated organization's AR invoice."""
import logging
from decimal import Decimal

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    _legacy_payload_org_predicate,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine import (
    _find_pending_domain_conflict,
    _lock_pending_domain_conflict,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import (
    ARValidationError,
    ar_json_safe,
    decimal_value,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_update_data import (
    _ar_collection_status_license_error,
)


class _MarkReceivedError(ValueError):
    def __init__(self, message, code, status_code):
        super().__init__(message)
        self.response = {"error": message, "error_code": code, "status_code": status_code}


def _payment_amounts(invoice):
    try:
        amount, received, balance = (
            decimal_value(invoice.get(field), field)
            for field in ("ar_invoice_amount", "ar_received_amount", "ar_balance_amount")
        )
        if received > amount or balance != amount - received:
            raise ARValidationError("Inconsistent AR payment amounts.")
    except ARValidationError:
        raise _MarkReceivedError(
            "Saved AR payment amounts are missing, invalid or inconsistent; resolve them before settlement.",
            "AR_PAYMENT_DATA_INCONSISTENT", 409,
        ) from None
    return amount, received, balance


def _success(invoice, *, no_change):
    return ar_json_safe({
        "message": "AR invoice is already fully received." if no_change else "AR invoice marked as fully received.",
        **{field: invoice[field] for field in (
            "ar_id_pk", "ar_invoice_number", "ar_invoice_amount", "ar_received_amount",
            "ar_balance_amount", "ar_collection_status", "ar_revision",
        )},
        "workflow_required": False,
        "business_operation_executed": not no_change,
        "no_change": no_change,
    })


def mark_ar_invoice_received(payload: dict, conn=None):
    """Set status and received amount together using API-bound identity.

    Own the transaction when no active transaction is supplied; otherwise use
    a savepoint without committing or rolling back the caller's outer work.
    """
    allowed_fields = {
        "ar_id_pk", "ar_org_id_fk", "ar_expected_revision",
        "authenticated_org_id", "authenticated_user_id",
        "authenticated_user_principal_name", "user_principal_name", "updated_by",
    }
    if not isinstance(payload, dict) or set(payload) - allowed_fields:
        return {"error": "Invalid Mark as Received request fields.", "error_code": "AR_INVALID_MARK_RECEIVED_REQUEST", "status_code": 422}
    org_id = payload.get("authenticated_org_id")
    user_id = payload.get("authenticated_user_id")
    principal = payload.get("authenticated_user_principal_name")
    if (type(org_id) is not int or org_id <= 0 or type(user_id) is not int or user_id <= 0
            or not isinstance(principal, str) or not principal.strip()):
        return {"error": "Authenticated user and organization context is required.", "error_code": "AR_AUTHENTICATION_REQUIRED", "status_code": 401}
    if type(payload.get("ar_org_id_fk")) is not int or payload["ar_org_id_fk"] != org_id:
        return {"error": "ar_org_id_fk does not match the authenticated organization.", "error_code": "AR_ORGANIZATION_MISMATCH", "status_code": 403}
    ar_id = payload.get("ar_id_pk")
    if type(ar_id) is not int or ar_id <= 0:
        return {"error": "ar_id_pk must be a positive integer.", "error_code": "AR_INVALID_INVOICE_ID", "status_code": 422}
    revision = payload.get("ar_expected_revision")
    if type(revision) is not int or revision < 0:
        return {"error": "ar_expected_revision must be a nonnegative integer.", "error_code": "AR_INVALID_REVISION", "status_code": 422}
    principal = principal.strip()

    def execute_update(connection):
        # Match Collection Status's FIN_UPDATE grant and active-user/role rules.
        permitted = connection.execute(text("""
            SELECT 1
            FROM v_user_access_rights rights
            JOIN user_master usr
              ON lower(usr.user_principal_name) = lower(rights.user_principal_name)
            JOIN role_master role ON role.role_name = rights.role_name
            JOIN organization_master org ON org.org_id_pk = usr.user_org_id_fk
            WHERE usr.user_id_pk = :user_id
              AND lower(usr.user_principal_name) = lower(:principal)
              AND usr.user_org_id_fk = :org_id
              AND coalesce(usr.is_active, true) = true
              AND coalesce(usr.is_deleted, false) = false
              AND coalesce(role.is_active, true) = true
              AND upper(rights.module_name) = 'FINANCE'
              AND upper(rights.action_name) = 'UPDATE'
              AND upper(rights.permission_code) = 'FIN_UPDATE'
            LIMIT 1
        """), {"user_id": user_id, "principal": principal, "org_id": org_id}).first()
        if not permitted:
            raise _MarkReceivedError(
                "FIN_UPDATE permission is required for the authenticated organization.",
                "AR_UPDATE_PERMISSION_REQUIRED", 403,
            )
        license_error = _ar_collection_status_license_error(connection, org_id)
        if license_error is not None:
            raise _MarkReceivedError(
                license_error["error"], license_error["error_code"], license_error["status_code"],
            )

        # Keep the existing submission guard and workflow-before-invoice lock
        # order, including pending requests still handled by the legacy adapter.
        identity = {"domain_reference_id": ar_id}
        _lock_pending_domain_conflict(connection, "ACCOUNTS_RECEIVABLE", "UPDATE", org_id, identity)
        pending = _find_pending_domain_conflict(connection, "ACCOUNTS_RECEIVABLE", "UPDATE", org_id, identity)
        if not pending:
            org_predicate = _legacy_payload_org_predicate(("ar_org_id_fk",))
            pending = connection.execute(text(f"""
                SELECT legacy.workflow_request_id_pk
                FROM accounts_receivables_workflow_requests legacy
                WHERE legacy.workflow_instance_id_fk IS NULL
                  AND legacy.workflow_action = 'UPDATE'
                  AND legacy.workflow_status = 'PENDING_APPROVAL'
                  AND {org_predicate}
                  AND (legacy.request_payload ->> 'ar_id' = :ar_id_text
                       OR legacy.request_payload ->> 'ar_id_pk' = :ar_id_text)
                ORDER BY legacy.workflow_request_id_pk
                LIMIT 1
                FOR UPDATE
            """), {"organization_id": org_id, "ar_id_text": str(ar_id)}).first()

        current = connection.execute(text("""
            SELECT ar_id_pk, ar_org_id_fk, ar_invoice_number, ar_invoice_amount,
                   ar_received_amount, ar_balance_amount, ar_collection_status,
                   coalesce(ar_revision, 0) AS ar_revision
            FROM accounts_receivables
            WHERE ar_id_pk = :ar_id AND ar_org_id_fk = :org_id
            FOR UPDATE
        """), {"ar_id": ar_id, "org_id": org_id}).mappings().one_or_none()
        if current is None:
            raise _MarkReceivedError(
                "AR invoice ID not found for the authenticated organization.", "AR_INVOICE_NOT_FOUND", 404,
            )
        if current["ar_revision"] != revision:
            raise _MarkReceivedError(
                "AR_STALE_INVOICE: reload the current invoice before updating.", "AR_STALE_INVOICE", 409,
            )

        amount, received, balance = _payment_amounts(current)
        if current["ar_collection_status"] == "RECEIVED":
            if received != amount:
                raise _MarkReceivedError(
                    "A RECEIVED invoice must already be fully paid.", "AR_PAYMENT_DATA_INCONSISTENT", 409,
                )
            # A validated no-op leaves any pending proposal and revision intact.
            return _success(current, no_change=True)
        if current["ar_collection_status"] not in ("OUTSTANDING", "PARTIALLY_RECEIVED"):
            raise _MarkReceivedError(
                "Saved Collection Status does not allow Mark as Received.", "AR_INVALID_SETTLEMENT_STATUS", 409,
            )
        if pending:
            raise _MarkReceivedError(
                "A pending AR UPDATE approval conflicts with this settlement; resolve it and reload the invoice.",
                "AR_PENDING_UPDATE_WORKFLOW", 409,
            )

        # PostgreSQL generates the balance and the existing trigger owns updated_at.
        updated = connection.execute(text("""
            UPDATE accounts_receivables
            SET ar_collection_status = 'RECEIVED',
                ar_received_amount = ar_invoice_amount,
                updated_by = :principal,
                ar_revision = coalesce(ar_revision, 0) + 1
            WHERE ar_id_pk = :ar_id AND ar_org_id_fk = :org_id
              AND coalesce(ar_revision, 0) = :revision
            RETURNING ar_id_pk, ar_invoice_number, ar_invoice_amount,
                      ar_received_amount, ar_balance_amount, ar_collection_status, ar_revision
        """), {"principal": principal, "ar_id": ar_id, "org_id": org_id, "revision": revision}).mappings().one_or_none()
        if updated is None:
            raise _MarkReceivedError(
                "AR_STALE_INVOICE: reload the current invoice before updating.", "AR_STALE_INVOICE", 409,
            )
        try:
            saved_amount, saved_received, saved_balance = _payment_amounts(updated)
        except _MarkReceivedError:
            raise RuntimeError("AR settlement returned invalid payment amounts.") from None
        if (updated["ar_collection_status"] != "RECEIVED" or saved_received != saved_amount
                or saved_balance != Decimal("0.00") or saved_amount != amount
                or updated["ar_revision"] != revision + 1):
            raise RuntimeError("AR settlement persisted values failed verification.")
        return _success(updated, no_change=False)

    try:
        if conn is not None:
            with conn.begin_nested() if conn.in_transaction() else conn.begin():
                return execute_update(conn)
        engine = db_engine()
        try:
            with engine.begin() as connection:
                return execute_update(connection)
        finally:
            engine.dispose()
    except _MarkReceivedError as exc:
        return exc.response
    except Exception:
        logging.getLogger(__name__).exception("AR Mark as Received failed.")
        return {
            "error": "Failed to mark AR invoice as received; the operation was rolled back.",
            "error_code": "AR_MARK_RECEIVED_FAILED", "status_code": 500,
        }
