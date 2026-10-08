import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    has_trusted_workflow_document_path,
    strip_untrusted_file_pointer_fields,
)
from app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf import (
    is_accounts_receivables_workflow_approved,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    _legacy_payload_org_predicate,
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    cleanup_staged_workflow_document,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    is_trusted_workflow_execution,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine import (
    _find_pending_domain_conflict,
    _lock_pending_domain_conflict,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_create_data import (
    stage_ar_invoice_document_for_workflow,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_common import (
    AR_COLLECTION_STATUSES,
    duplicate_ar_invoice_exists,
    get_ar_by_id,
    get_ar_id_from_payload,
    get_ar_params,
    validate_ar_payload,
    verify_ar_organization_exists,
    verify_customer_for_organization,
)


def _ar_collection_status_license_error(conn, organization_id):
    """Require at least one organization license and every finance flag TRUE."""
    licenses = conn.execute(text("""
        SELECT financial_management_module
        FROM organization_license_master
        WHERE org_id_fk = :organization_id
    """), {"organization_id": organization_id}).mappings().all()
    if licenses and all(row["financial_management_module"] is True for row in licenses):
        return None
    if any(row["financial_management_module"] is True for row in licenses):
        return {
            "error": "Conflicting financial_management_module license records for the authenticated organization.",
            "error_code": "AR_MODULE_LICENSE_AMBIGUOUS",
            "status_code": 403,
        }
    return {
        "error": (
            "financial_management_module must be enabled on every license record "
            "and at least one license record must exist for the authenticated organization."
        ),
        "error_code": "AR_MODULE_LICENSE_REQUIRED",
        "status_code": 403,
    }


def update_ar_collection_status(payload: dict, conn=None):
    """Update status only, using identity bound by the authenticated API.

    A supplied connection participates in its caller's transaction using a
    savepoint; otherwise this operation owns and commits its transaction.
    Module access requires one or more organization licenses, all finance-enabled.
    """
    allowed_fields = {
        "ar_id_pk", "ar_org_id_fk", "ar_collection_status", "ar_expected_revision",
        "authenticated_org_id", "authenticated_user_id",
        "authenticated_user_principal_name", "user_principal_name", "updated_by",
    }
    if not isinstance(payload, dict) or set(payload) - allowed_fields:
        return {"error": "Invalid Collection Status request fields.", "error_code": "AR_INVALID_COLLECTION_STATUS_REQUEST", "status_code": 422}
    ar_id = payload.get("ar_id_pk")
    if type(ar_id) is not int or ar_id <= 0:
        return {"error": "ar_id_pk must be a positive integer.", "error_code": "AR_INVALID_INVOICE_ID", "status_code": 422}
    org_id = payload.get("authenticated_org_id")
    user_id = payload.get("authenticated_user_id")
    principal = payload.get("authenticated_user_principal_name")
    if (type(org_id) is not int or org_id <= 0 or type(user_id) is not int or user_id <= 0
            or not isinstance(principal, str) or not principal.strip()):
        return {"error": "Authenticated user and organization context is required.", "error_code": "AR_AUTHENTICATION_REQUIRED", "status_code": 401}
    if type(payload.get("ar_org_id_fk")) is not int or payload["ar_org_id_fk"] != org_id:
        return {"error": "ar_org_id_fk does not match the authenticated organization.", "error_code": "AR_ORGANIZATION_MISMATCH", "status_code": 403}
    principal = principal.strip()

    def execute_update(connection):
        # The existing permission view supplies role-based grants. Exclude
        # inactive roles, as authentication's role-context lookup does.
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
            return {"error": "FIN_UPDATE permission is required for the authenticated organization.", "error_code": "AR_UPDATE_PERMISSION_REQUIRED", "status_code": 403}

        license_error = _ar_collection_status_license_error(connection, org_id)
        if license_error is not None:
            return license_error

        status = payload.get("ar_collection_status")
        if not isinstance(status, str) or status not in AR_COLLECTION_STATUSES:
            return {"error": "Invalid ar_collection_status.", "error_code": "AR_INVALID_COLLECTION_STATUS", "status_code": 422}
        revision = payload.get("ar_expected_revision")
        if type(revision) is not int or revision < 0:
            return {"error": "ar_expected_revision must be a nonnegative integer.", "error_code": "AR_INVALID_REVISION", "status_code": 422}

        # Serialize with existing UPDATE submissions. Lock workflow records
        # before the invoice, matching approval execution's lock order.
        identity = {"domain_reference_id": ar_id}
        _lock_pending_domain_conflict(connection, "ACCOUNTS_RECEIVABLE", "UPDATE", org_id, identity)
        pending = _find_pending_domain_conflict(connection, "ACCOUNTS_RECEIVABLE", "UPDATE", org_id, identity)
        if not pending:
            # The runtime lookup covers configured instances. Also protect
            # older unlinked requests still executable by the legacy adapter.
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
            SELECT ar_id_pk, ar_collection_status, coalesce(ar_revision, 0) AS ar_revision
            FROM accounts_receivables
            WHERE ar_id_pk = :ar_id AND ar_org_id_fk = :org_id
            FOR UPDATE
        """), {"ar_id": ar_id, "org_id": org_id}).mappings().one_or_none()
        if current is None:
            return {"error": "AR invoice ID not found for the authenticated organization.", "error_code": "AR_INVOICE_NOT_FOUND", "status_code": 404}
        if current["ar_revision"] != revision:
            return {"error": "AR_STALE_INVOICE: reload the current invoice before updating.", "error_code": "AR_STALE_INVOICE", "status_code": 409}
        if current["ar_collection_status"] == status:
            return {
                "message": "Collection Status is unchanged.", "ar_id_pk": ar_id,
                "ar_collection_status": status, "ar_revision": current["ar_revision"],
                "workflow_required": False, "business_operation_executed": False,
                "no_change": True,
            }
        if pending:
            return {
                "error": "A pending AR UPDATE approval conflicts with this Collection Status change; resolve it and reload the invoice.",
                "error_code": "AR_PENDING_UPDATE_WORKFLOW", "status_code": 409,
            }

        # updated_at remains trigger-owned. Never write amounts, approvals,
        # document pointers, lines, or any other invoice attributes.
        updated = connection.execute(text("""
            UPDATE accounts_receivables
            SET ar_collection_status = :status,
                updated_by = :principal,
                ar_revision = coalesce(ar_revision, 0) + 1
            WHERE ar_id_pk = :ar_id AND ar_org_id_fk = :org_id
              AND coalesce(ar_revision, 0) = :revision
            RETURNING ar_id_pk, ar_collection_status, ar_revision
        """), {"status": status, "principal": principal, "ar_id": ar_id, "org_id": org_id, "revision": revision}).mappings().one()
        return {
            "message": "Successfully updated AR Collection Status.",
            **dict(updated), "workflow_required": False,
            "business_operation_executed": True, "no_change": False,
        }

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
    except Exception:
        logging.getLogger(__name__).exception("AR Collection Status update failed.")
        return {"error": "Failed to update AR Collection Status; the operation was rolled back.", "error_code": "AR_COLLECTION_STATUS_UPDATE_FAILED", "status_code": 500}


def update_accounts_receivables(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    if "lines" in payload:
        from app_backend.services.service_08_financial_management.logic.accounts_receivables_aggregate import process_line_invoice
        return process_line_invoice(payload, "UPDATE", file_stream=file_stream, conn=conn)
    try:
        workflow_pending_created = False
        ar_id = get_ar_id_from_payload(payload)
        ar_org_id_fk = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")
        validation_error = validate_ar_payload(payload, require_id=True)
        if validation_error:
            return validation_error

        existing_ar = get_ar_by_id(ar_id, ar_org_id_fk, conn=conn)
        if not existing_ar:
            return {"error": "AR invoice ID not found."}
        if existing_ar.get("ar_subtotal_amount") is not None:
            return {"error": "lines is required to update a line-based invoice; submit its complete proposed line set."}
        if any(payload.get(key) is not None for key in ("ar_contract_id_fk", "ar_billing_period_start", "ar_billing_period_end")):
            return {"error": "lines is required when using the new contract/billing-period fields."}

        ar_cust_id_fk = payload.get("ar_cust_id_fk")
        ar_invoice_number = payload.get("ar_invoice_number")

        if not verify_ar_organization_exists(ar_org_id_fk, conn=conn):
            return {"error": "ar_org_id_fk not found in organization_master."}

        if not verify_customer_for_organization(ar_org_id_fk, ar_cust_id_fk, conn=conn):
            return {
                "error": (
                    "ar_cust_id_fk not found for ar_org_id_fk in customer_master."
                )
            }

        if duplicate_ar_invoice_exists(
            ar_org_id_fk,
            ar_invoice_number,
            exclude_ar_id=ar_id,
            conn=conn,
        ):
            return {
                "error": (
                    "Accounts receivable invoice already exists for this "
                    "organization and invoice number."
                )
            }

        payload, staging_error = stage_ar_invoice_document_for_workflow(
            {**payload, "ar_id": ar_id, "ar_id_pk": ar_id},
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            require_ar_invoice=True,
            workflow_action="UPDATE",
        )
        if staging_error:
            return staging_error
        staged_document_uploaded = bool(
            file_stream
            and payload.get("workflow_document_staging_status") == "STAGED"
        )
        update_document_pointer = has_trusted_workflow_document_path(
            payload,
            "ar_invoice_file_path",
            staged_in_current_request=staged_document_uploaded,
            trusted_workflow_execution=is_trusted_workflow_execution(),
        )
        if not update_document_pointer:
            payload = strip_untrusted_file_pointer_fields(payload, "ar_invoice_file_path")

        workflow_payload = {
            **payload,
            "ar_id": ar_id,
            "ar_id_pk": ar_id,
            "ar_org_id_fk": existing_ar.get("ar_org_id_fk"),
        }
        workflow_approved, workflow_response = is_accounts_receivables_workflow_approved(
            payload=workflow_payload,
            workflow_action="UPDATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                workflow_pending_created = True
                return pending_workflow_submission_response(
                    workflow_response,
                    "Accounts receivable update submitted for approval.",
                    domain_reference_id=ar_id,
                )
            cleanup_result = cleanup_staged_workflow_document(payload, force=True)
            return {
                "error": "Accounts receivable update blocked by workflow.",
                "workflow": workflow_response,
                "document_cleanup": cleanup_result,
            }

        payload = {
            **payload,
            "ar_approval_status": workflow_response.get("workflow_status", "APPROVED"),
            "ar_approved_by": payload.get("ar_approved_by") or payload.get("user_principal_name")
        }
        params_update = {
            **get_ar_params(payload),
            "ar_id": ar_id,
            "ar_org_id_fk": ar_org_id_fk
        }
        if not update_document_pointer:
            params_update.pop("ar_invoice_file_path", None)

        document_pointer_set_clause = (
            "                ar_invoice_file_path = :ar_invoice_file_path,\n"
            if update_document_pointer
            else ""
        )

        update_ar_query = text(f"""
            update accounts_receivables
            set
                ar_org_id_fk = :ar_org_id_fk,
                ar_cust_id_fk = :ar_cust_id_fk,
                ar_invoice_number = :ar_invoice_number,
                ar_invoice_date = :ar_invoice_date,
                ar_due_date = :ar_due_date,
                ar_contract = :ar_contract,
                ar_description = :ar_description,
                ar_currency_code = :ar_currency_code,
                ar_invoice_amount = :ar_invoice_amount,
                ar_tax_amount = :ar_tax_amount,
                ar_received_amount = :ar_received_amount,
                ar_approval_status = :ar_approval_status,
                ar_collection_status = :ar_collection_status,
                ar_approval_comments = :ar_approval_comments,
                ar_approved_by = :ar_approved_by,
                ar_approved_at = coalesce(:ar_approved_at, CURRENT_TIMESTAMP),
                ar_notes = :ar_notes,
{document_pointer_set_clause}\
                updated_by = :updated_by
            where ar_id_pk = :ar_id
            and ar_org_id_fk = :ar_org_id_fk
            returning ar_balance_amount, ar_invoice_file_path
        """)

        if conn is not None:
            updated_ar = conn.execute(
                update_ar_query,
                params_update,
            ).mappings().one_or_none()
        else:
            ar_engine = db_engine()
            try:
                with ar_engine.begin() as ar_conn:
                    updated_ar = ar_conn.execute(
                        update_ar_query,
                        params_update,
                    ).mappings().one_or_none()
            finally:
                ar_engine.dispose()

        if not updated_ar:
            return {"error": "AR invoice ID not found at execution time."}

        return {
            "message": f"Successfully updated AR invoice: {ar_invoice_number}",
            "ar_id_pk": ar_id,
            "ar_invoice_number": ar_invoice_number,
            "ar_balance_amount": updated_ar["ar_balance_amount"],
            "ar_approval_status": params_update.get("ar_approval_status"),
            "ar_invoice_file_path": updated_ar["ar_invoice_file_path"],
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        response = {"error": f"Failed to update AR invoice. Error Message: {str(e)}"}
        if not locals().get("workflow_pending_created"):
            cleanup_result = cleanup_staged_workflow_document(locals().get("payload"), force=True)
            if cleanup_result.get("cleanup_attempted"):
                response["document_cleanup"] = cleanup_result
        return response


def update_accounts_receivable(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    return update_accounts_receivables(payload, file_stream, file_name, content_type, conn)
