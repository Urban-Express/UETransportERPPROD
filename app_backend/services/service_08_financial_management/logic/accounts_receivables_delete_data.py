from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf import (
    is_accounts_receivables_workflow_approved,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_common import (
    get_ar_by_id,
    get_ar_id_from_payload,
)


def delete_accounts_receivables(payload: dict, conn=None):
    try:
        ar_id = get_ar_id_from_payload(payload)
        ar_org_id_fk = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")

        if not ar_id:
            return {"error": "ar_id is required."}
        if not ar_org_id_fk:
            return {"error": "ar_org_id_fk is required."}

        existing_ar = get_ar_by_id(ar_id, ar_org_id_fk, conn=conn)
        if not existing_ar:
            return {"error": "AR invoice ID not found."}

        workflow_payload = {
            **payload,
            "ar_id": ar_id,
            "ar_id_pk": ar_id,
            "ar_org_id_fk": existing_ar.get("ar_org_id_fk"),
            "ar_cust_id_fk": existing_ar.get("ar_cust_id_fk"),
            "ar_invoice_number": existing_ar.get("ar_invoice_number"),
        }

        workflow_approved, workflow_response = is_accounts_receivables_workflow_approved(
            payload=workflow_payload,
            workflow_action="DELETE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Accounts receivable deletion submitted for approval.",
                    domain_reference_id=ar_id,
                )
            return {
                "error": "Accounts receivable deletion blocked by workflow.",
                "workflow": workflow_response
            }

        delete_ar_query = text("""
            delete from accounts_receivables
            where ar_id_pk = :ar_id
            and ar_org_id_fk = :ar_org_id_fk
        """)

        if conn is not None:
            result = conn.execute(
                delete_ar_query,
                {
                    "ar_id": ar_id,
                    "ar_org_id_fk": ar_org_id_fk
                }
            )
        else:
            ar_engine = db_engine()
            try:
                with ar_engine.begin() as ar_conn:
                    result = ar_conn.execute(
                        delete_ar_query,
                        {
                            "ar_id": ar_id,
                            "ar_org_id_fk": ar_org_id_fk
                        }
                    )
            finally:
                ar_engine.dispose()

        if result.rowcount == 0:
            return {"error": "AR invoice ID not found at execution time."}

        return {
            "message": f"Successfully deleted AR invoice ID: {ar_id}",
            "ar_id_pk": ar_id,
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        return {"error": f"Failed to delete AR invoice. Error Message: {str(e)}"}


def delete_accounts_receivable(payload: dict, conn=None):
    return delete_accounts_receivables(payload, conn=conn)
