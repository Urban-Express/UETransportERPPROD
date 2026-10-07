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
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    cleanup_staged_workflow_document,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    is_trusted_workflow_execution,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_create_data import (
    stage_ar_invoice_document_for_workflow,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_common import (
    duplicate_ar_invoice_exists,
    get_ar_by_id,
    get_ar_id_from_payload,
    get_ar_params,
    validate_ar_payload,
    verify_ar_organization_exists,
    verify_customer_for_organization,
)


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
