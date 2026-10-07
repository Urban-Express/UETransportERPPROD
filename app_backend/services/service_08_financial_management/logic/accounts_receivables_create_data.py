from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    has_trusted_workflow_document_path,
)
from app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf import (
    is_accounts_receivables_workflow_approved,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
    strip_ephemeral_document_fields,
    workflow_document_staging_storage_folder,
    workflow_document_stream_required_error,
    workflow_staged_document_payload,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    cleanup_staged_workflow_document,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    is_trusted_workflow_execution,
)
from app_backend.services.service_08_financial_management.integrations.firebase_ar_invoice_document_upload import (
    DEFAULT_AR_STORAGE_FOLDER,
    upload_ar_invoice_document_to_firebase,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_common import (
    duplicate_ar_invoice_exists,
    get_ar_params,
    validate_ar_payload,
    verify_ar_organization_exists,
    verify_customer_for_organization,
)


AR_EPHEMERAL_DOCUMENT_FIELDS = ("file_path", "ar_invoice_local_file_path")


def stage_ar_invoice_document_for_workflow(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    require_ar_invoice: bool = False,
    workflow_action: str = "CREATE",
) -> tuple[dict | None, dict | None]:
    if file_stream:
        ar_org_id_fk = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")
        staging_payload = {
            **payload,
            "storage_folder": workflow_document_staging_storage_folder(
                DEFAULT_AR_STORAGE_FOLDER,
                "ACCOUNTS_RECEIVABLE",
                workflow_action,
                ar_org_id_fk,
                payload,
            ),
        }
        upload_response = upload_ar_invoice_document_to_firebase(
            payload=staging_payload,
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            update_ar_document_link=False,
            require_ar_invoice=require_ar_invoice,
        )
        if upload_response.get("error"):
            return None, upload_response
        return workflow_staged_document_payload(
            payload,
            "ar_invoice_file_path",
            upload_response.get("ar_invoice_file_path"),
            upload_response,
            AR_EPHEMERAL_DOCUMENT_FIELDS,
        ), None

    stream_error = workflow_document_stream_required_error(
        payload,
        "ar_invoice_file_path",
        AR_EPHEMERAL_DOCUMENT_FIELDS,
        "AR invoice document",
    )
    if stream_error:
        return None, stream_error
    return strip_ephemeral_document_fields(payload, AR_EPHEMERAL_DOCUMENT_FIELDS), None


def create_accounts_receivables(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    if "lines" in payload:
        from app_backend.services.service_08_financial_management.logic.accounts_receivables_aggregate import process_line_invoice
        return process_line_invoice(payload, "CREATE", file_stream=file_stream, conn=conn)
    if any(payload.get(key) is not None for key in ("ar_contract_id_fk", "ar_billing_period_start", "ar_billing_period_end")):
        return {"error": "lines is required when using the new contract/billing-period fields."}
    try:
        workflow_pending_created = False
        validation_error = validate_ar_payload(payload)
        if validation_error:
            return validation_error

        ar_org_id_fk = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")
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

        if duplicate_ar_invoice_exists(ar_org_id_fk, ar_invoice_number, conn=conn):
            return {
                "error": (
                    "Accounts receivable invoice already exists for this "
                    "organization and invoice number."
                )
            }

        payload, staging_error = stage_ar_invoice_document_for_workflow(
            payload,
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            require_ar_invoice=False,
            workflow_action="CREATE",
        )
        if staging_error:
            return staging_error

        staged_document_uploaded = bool(
            file_stream
            and payload.get("workflow_document_staging_status") == "STAGED"
        )
        if not has_trusted_workflow_document_path(
            payload,
            "ar_invoice_file_path",
            staged_in_current_request=staged_document_uploaded,
            trusted_workflow_execution=is_trusted_workflow_execution(),
        ):
            payload = {
                **payload,
                "ar_invoice_file_path": None,
            }

        workflow_approved, workflow_response = is_accounts_receivables_workflow_approved(
            payload=payload,
            workflow_action="CREATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                workflow_pending_created = True
                return pending_workflow_submission_response(
                    workflow_response,
                    "Accounts receivable creation submitted for approval.",
                )
            cleanup_result = cleanup_staged_workflow_document(payload, force=True)
            return {
                "error": "Accounts receivable creation blocked by workflow.",
                "workflow": workflow_response,
                "document_cleanup": cleanup_result,
            }

        payload = {
            **payload,
            "ar_approval_status": workflow_response.get("workflow_status", "APPROVED"),
            "ar_approved_by": payload.get("ar_approved_by") or payload.get("user_principal_name")
        }
        params_insert = {
            **get_ar_params(payload),
            "ar_org_id_fk": ar_org_id_fk
        }

        insert_ar = text("""
            insert into accounts_receivables (
                ar_org_id_fk,
                ar_cust_id_fk,
                ar_invoice_number,
                ar_invoice_date,
                ar_due_date,
                ar_contract,
                ar_description,
                ar_currency_code,
                ar_invoice_amount,
                ar_tax_amount,
                ar_received_amount,
                ar_approval_status,
                ar_collection_status,
                ar_approval_comments,
                ar_approved_by,
                ar_approved_at,
                ar_notes,
                ar_invoice_file_path,
                created_by,
                updated_by
            ) values (
                :ar_org_id_fk,
                :ar_cust_id_fk,
                :ar_invoice_number,
                :ar_invoice_date,
                :ar_due_date,
                :ar_contract,
                :ar_description,
                :ar_currency_code,
                :ar_invoice_amount,
                :ar_tax_amount,
                :ar_received_amount,
                :ar_approval_status,
                :ar_collection_status,
                :ar_approval_comments,
                :ar_approved_by,
                coalesce(:ar_approved_at, CURRENT_TIMESTAMP),
                :ar_notes,
                :ar_invoice_file_path,
                :created_by,
                :updated_by
            )
            returning ar_id_pk, ar_balance_amount, ar_invoice_file_path
        """)

        if conn is not None:
            ar_row = conn.execute(insert_ar, params_insert).one()
        else:
            ar_engine = db_engine()
            try:
                with ar_engine.begin() as ar_conn:
                    ar_row = ar_conn.execute(insert_ar, params_insert).one()
            finally:
                ar_engine.dispose()

        return {
            "message": f"Successfully created AR invoice: {ar_invoice_number}",
            "ar_id_pk": ar_row.ar_id_pk,
            "ar_invoice_number": ar_invoice_number,
            "ar_balance_amount": ar_row.ar_balance_amount,
            "ar_approval_status": params_insert.get("ar_approval_status"),
            "ar_invoice_file_path": ar_row.ar_invoice_file_path,
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        response = {"error": f"Failed to create AR invoice. Error Message: {str(e)}"}
        if not locals().get("workflow_pending_created"):
            cleanup_result = cleanup_staged_workflow_document(locals().get("payload"), force=True)
            if cleanup_result.get("cleanup_attempted"):
                response["document_cleanup"] = cleanup_result
        return response


def create_accounts_receivable(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    return create_accounts_receivables(payload, file_stream, file_name, content_type, conn)
