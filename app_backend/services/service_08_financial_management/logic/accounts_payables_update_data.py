from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    has_trusted_workflow_document_path,
    strip_untrusted_file_pointer_fields,
)
from app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf import (
    is_accounts_payables_workflow_approved,
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
from app_backend.services.service_08_financial_management.logic.accounts_payables_create_data import (
    stage_ap_invoice_document_for_workflow,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_common import (
    duplicate_ap_invoice_exists,
    get_ap_by_id,
    get_ap_id_from_payload,
    get_ap_params,
    validate_ap_payload,
    verify_organization_exists,
    verify_supplier_for_organization,
)


def update_accounts_payables(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    ap_engine = None
    try:
        workflow_pending_created = False
        ap_id = get_ap_id_from_payload(payload)
        ap_org_id_fk = payload.get("ap_org_id_fk") or payload.get("authenticated_org_id")
        validation_error = validate_ap_payload(payload, require_id=True)
        if validation_error:
            return validation_error

        existing_ap = get_ap_by_id(ap_id, ap_org_id_fk, conn=conn)
        if not existing_ap:
            return {"error": "AP invoice ID not found."}

        ap_supp_id_fk = payload.get("ap_supp_id_fk")
        ap_invoice_number = payload.get("ap_invoice_number")

        if not verify_organization_exists(ap_org_id_fk, conn=conn):
            return {"error": "ap_org_id_fk not found in organization_master."}

        if not verify_supplier_for_organization(ap_org_id_fk, ap_supp_id_fk, conn=conn):
            return {
                "error": (
                    "ap_supp_id_fk not found for ap_org_id_fk in supplier_master."
                )
            }

        if duplicate_ap_invoice_exists(
            ap_org_id_fk,
            ap_supp_id_fk,
            ap_invoice_number,
            exclude_ap_id=ap_id,
            conn=conn,
        ):
            return {
                "error": (
                    "Accounts payable invoice already exists for this "
                    "organization, supplier, and invoice number."
                )
            }

        payload, staging_error = stage_ap_invoice_document_for_workflow(
            {**payload, "ap_id": ap_id, "ap_id_pk": ap_id},
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            require_ap_invoice=True,
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
            "ap_invoice_file_path",
            staged_in_current_request=staged_document_uploaded,
            trusted_workflow_execution=is_trusted_workflow_execution(),
        )
        if not update_document_pointer:
            payload = strip_untrusted_file_pointer_fields(payload, "ap_invoice_file_path")

        workflow_payload = {
            **payload,
            "ap_id": ap_id,
            "ap_id_pk": ap_id,
            "ap_org_id_fk": existing_ap.get("ap_org_id_fk"),
        }
        workflow_approved, workflow_response = is_accounts_payables_workflow_approved(
            payload=workflow_payload,
            workflow_action="UPDATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                workflow_pending_created = True
                return pending_workflow_submission_response(
                    workflow_response,
                    "Accounts payable update submitted for approval.",
                    domain_reference_id=ap_id,
                )
            cleanup_result = cleanup_staged_workflow_document(payload, force=True)
            return {
                "error": "Accounts payable update blocked by workflow.",
                "workflow": workflow_response,
                "document_cleanup": cleanup_result,
            }

        payload = {
            **payload,
            "ap_approval_status": workflow_response.get("workflow_status", "APPROVED"),
            "ap_approved_by": payload.get("ap_approved_by") or payload.get("user_principal_name")
        }
        params_update = {
            **get_ap_params(payload),
            "ap_id": ap_id,
            "ap_org_id_fk": ap_org_id_fk
        }
        if not update_document_pointer:
            params_update.pop("ap_invoice_file_path", None)

        document_pointer_set_clause = (
            "                ap_invoice_file_path = :ap_invoice_file_path,\n"
            if update_document_pointer
            else ""
        )

        update_ap_query = text(f"""
            update accounts_payables
            set
                ap_org_id_fk = :ap_org_id_fk,
                ap_supp_id_fk = :ap_supp_id_fk,
                ap_invoice_number = :ap_invoice_number,
                ap_invoice_date = :ap_invoice_date,
                ap_due_date = :ap_due_date,
                ap_description = :ap_description,
                ap_currency_code = :ap_currency_code,
                ap_invoice_amount = :ap_invoice_amount,
                ap_tax_amount = :ap_tax_amount,
                ap_paid_amount = :ap_paid_amount,
                ap_approval_status = :ap_approval_status,
                ap_payment_status = :ap_payment_status,
                ap_approval_comments = :ap_approval_comments,
                ap_approved_by = :ap_approved_by,
                ap_approved_at = coalesce(:ap_approved_at, CURRENT_TIMESTAMP),
                ap_notes = :ap_notes,
{document_pointer_set_clause}\
                updated_by = :updated_by
            where ap_id_pk = :ap_id
            and ap_org_id_fk = :ap_org_id_fk
            returning ap_balance_amount, ap_invoice_file_path
        """)

        if conn is not None:
            updated_ap = conn.execute(
                update_ap_query,
                params_update,
            ).mappings().one_or_none()
        else:
            ap_engine = db_engine()
            try:
                with ap_engine.begin() as ap_conn:
                    updated_ap = ap_conn.execute(
                        update_ap_query,
                        params_update,
                    ).mappings().one_or_none()
            finally:
                ap_engine.dispose()
                ap_engine = None

        if not updated_ap:
            return {"error": "AP invoice ID not found at execution time."}

        return {
            "message": f"Successfully updated AP invoice: {ap_invoice_number}",
            "ap_id_pk": ap_id,
            "ap_invoice_number": ap_invoice_number,
            "ap_balance_amount": updated_ap["ap_balance_amount"],
            "ap_approval_status": params_update.get("ap_approval_status"),
            "ap_invoice_file_path": updated_ap["ap_invoice_file_path"],
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        response = {"error": f"Failed to update AP invoice. Error Message: {str(e)}"}
        if not locals().get("workflow_pending_created"):
            cleanup_result = cleanup_staged_workflow_document(locals().get("payload"), force=True)
            if cleanup_result.get("cleanup_attempted"):
                response["document_cleanup"] = cleanup_result
        return response
    finally:
        if ap_engine is not None:
            ap_engine.dispose()


def update_accounts_payable(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    return update_accounts_payables(payload, file_stream, file_name, content_type, conn)
