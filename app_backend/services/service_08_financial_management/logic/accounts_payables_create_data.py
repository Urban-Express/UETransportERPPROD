from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    has_trusted_workflow_document_path,
)
from app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf import (
    is_accounts_payables_workflow_approved,
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
from app_backend.services.service_08_financial_management.integrations.firebase_ap_invoice_document_upload import (
    DEFAULT_AP_STORAGE_FOLDER,
    upload_ap_invoice_document_to_firebase,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_common import (
    duplicate_ap_invoice_exists,
    get_ap_params,
    validate_ap_payload,
    verify_organization_exists,
    verify_supplier_for_organization,
)


AP_EPHEMERAL_DOCUMENT_FIELDS = ("file_path", "ap_invoice_local_file_path")


def stage_ap_invoice_document_for_workflow(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    require_ap_invoice: bool = False,
    workflow_action: str = "CREATE",
) -> tuple[dict | None, dict | None]:
    if file_stream:
        ap_org_id_fk = payload.get("ap_org_id_fk") or payload.get("authenticated_org_id")
        staging_payload = {
            **payload,
            "storage_folder": workflow_document_staging_storage_folder(
                DEFAULT_AP_STORAGE_FOLDER,
                "ACCOUNTS_PAYABLE",
                workflow_action,
                ap_org_id_fk,
                payload,
            ),
        }
        upload_response = upload_ap_invoice_document_to_firebase(
            payload=staging_payload,
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            update_ap_document_link=False,
            require_ap_invoice=require_ap_invoice,
        )
        if upload_response.get("error"):
            return None, upload_response
        return workflow_staged_document_payload(
            payload,
            "ap_invoice_file_path",
            upload_response.get("ap_invoice_file_path"),
            upload_response,
            AP_EPHEMERAL_DOCUMENT_FIELDS,
        ), None

    stream_error = workflow_document_stream_required_error(
        payload,
        "ap_invoice_file_path",
        AP_EPHEMERAL_DOCUMENT_FIELDS,
        "AP invoice document",
    )
    if stream_error:
        return None, stream_error
    return strip_ephemeral_document_fields(payload, AP_EPHEMERAL_DOCUMENT_FIELDS), None


def create_accounts_payables(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    ap_engine = None
    try:
        workflow_pending_created = False
        validation_error = validate_ap_payload(payload)
        if validation_error:
            return validation_error

        ap_org_id_fk = payload.get("ap_org_id_fk") or payload.get("authenticated_org_id")
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
            conn=conn,
        ):
            return {
                "error": (
                    "Accounts payable invoice already exists for this "
                    "organization, supplier, and invoice number."
                )
            }

        payload, staging_error = stage_ap_invoice_document_for_workflow(
            payload,
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            require_ap_invoice=False,
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
            "ap_invoice_file_path",
            staged_in_current_request=staged_document_uploaded,
            trusted_workflow_execution=is_trusted_workflow_execution(),
        ):
            payload = {
                **payload,
                "ap_invoice_file_path": None,
            }

        workflow_approved, workflow_response = is_accounts_payables_workflow_approved(
            payload=payload,
            workflow_action="CREATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                workflow_pending_created = True
                return pending_workflow_submission_response(
                    workflow_response,
                    "Accounts payable creation submitted for approval.",
                )
            cleanup_result = cleanup_staged_workflow_document(payload, force=True)
            return {
                "error": "Accounts payable creation blocked by workflow.",
                "workflow": workflow_response,
                "document_cleanup": cleanup_result,
            }

        insert_ap = text("""
            insert into accounts_payables (
                ap_org_id_fk,
                ap_supp_id_fk,
                ap_invoice_number,
                ap_invoice_date,
                ap_due_date,
                ap_description,
                ap_currency_code,
                ap_invoice_amount,
                ap_tax_amount,
                ap_paid_amount,
                ap_approval_status,
                ap_payment_status,
                ap_approval_comments,
                ap_approved_by,
                ap_approved_at,
                ap_notes,
                ap_invoice_file_path,
                created_by,
                updated_by
            ) values (
                :ap_org_id_fk,
                :ap_supp_id_fk,
                :ap_invoice_number,
                :ap_invoice_date,
                :ap_due_date,
                :ap_description,
                :ap_currency_code,
                :ap_invoice_amount,
                :ap_tax_amount,
                :ap_paid_amount,
                :ap_approval_status,
                :ap_payment_status,
                :ap_approval_comments,
                :ap_approved_by,
                coalesce(:ap_approved_at, CURRENT_TIMESTAMP),
                :ap_notes,
                :ap_invoice_file_path,
                :created_by,
                :updated_by
            )
            returning ap_id_pk, ap_balance_amount, ap_invoice_file_path
        """)

        payload = {
            **payload,
            "ap_approval_status": workflow_response.get("workflow_status", "APPROVED"),
            "ap_approved_by": payload.get("ap_approved_by") or payload.get("user_principal_name")
        }
        params_insert = {
            **get_ap_params(payload),
            "ap_org_id_fk": ap_org_id_fk
        }
        if conn is not None:
            ap_row = conn.execute(insert_ap, params_insert).one()
        else:
            ap_engine = db_engine()
            try:
                with ap_engine.begin() as ap_conn:
                    ap_row = ap_conn.execute(insert_ap, params_insert).one()
            finally:
                ap_engine.dispose()
                ap_engine = None

        return {
            "message": f"Successfully created AP invoice: {ap_invoice_number}",
            "ap_id_pk": ap_row.ap_id_pk,
            "ap_invoice_number": ap_invoice_number,
            "ap_balance_amount": ap_row.ap_balance_amount,
            "ap_approval_status": params_insert.get("ap_approval_status"),
            "ap_invoice_file_path": ap_row.ap_invoice_file_path,
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        response = {"error": f"Failed to create AP invoice. Error Message: {str(e)}"}
        if not locals().get("workflow_pending_created"):
            cleanup_result = cleanup_staged_workflow_document(locals().get("payload"), force=True)
            if cleanup_result.get("cleanup_attempted"):
                response["document_cleanup"] = cleanup_result
        return response
    finally:
        if ap_engine is not None:
            ap_engine.dispose()


def create_accounts_payable(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    return create_accounts_payables(payload, file_stream, file_name, content_type, conn)
