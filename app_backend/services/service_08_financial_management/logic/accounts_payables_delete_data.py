from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf import (
    is_accounts_payables_workflow_approved,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_common import (
    get_ap_by_id,
    get_ap_id_from_payload,
)


def delete_accounts_payables(payload: dict, conn=None):
    ap_engine = None
    try:
        ap_id = get_ap_id_from_payload(payload)
        ap_org_id_fk = payload.get("ap_org_id_fk") or payload.get("authenticated_org_id")

        if not ap_id:
            return {"error": "ap_id is required."}
        if not ap_org_id_fk:
            return {"error": "ap_org_id_fk is required."}

        existing_ap = get_ap_by_id(ap_id, ap_org_id_fk, conn=conn)
        if not existing_ap:
            return {"error": "AP invoice ID not found."}

        workflow_payload = {
            **payload,
            "ap_id": ap_id,
            "ap_id_pk": ap_id,
            "ap_org_id_fk": existing_ap.get("ap_org_id_fk"),
            "ap_supp_id_fk": existing_ap.get("ap_supp_id_fk"),
            "ap_invoice_number": existing_ap.get("ap_invoice_number"),
        }

        workflow_approved, workflow_response = is_accounts_payables_workflow_approved(
            payload=workflow_payload,
            workflow_action="DELETE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Accounts payable deletion submitted for approval.",
                    domain_reference_id=ap_id,
                )
            return {
                "error": "Accounts payable deletion blocked by workflow.",
                "workflow": workflow_response
            }

        delete_ap_query = text("""
            delete from accounts_payables
            where ap_id_pk = :ap_id
            and ap_org_id_fk = :ap_org_id_fk
        """)

        if conn is not None:
            result = conn.execute(
                delete_ap_query,
                {
                    "ap_id": ap_id,
                    "ap_org_id_fk": ap_org_id_fk
                }
            )
        else:
            ap_engine = db_engine()
            try:
                with ap_engine.begin() as ap_conn:
                    result = ap_conn.execute(
                        delete_ap_query,
                        {
                            "ap_id": ap_id,
                            "ap_org_id_fk": ap_org_id_fk
                        }
                    )
            finally:
                ap_engine.dispose()
                ap_engine = None

        if result.rowcount == 0:
            return {"error": "AP invoice ID not found at execution time."}

        return {
            "message": f"Successfully deleted AP invoice ID: {ap_id}",
            "ap_id_pk": ap_id,
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        return {"error": f"Failed to delete AP invoice. Error Message: {str(e)}"}
    finally:
        if ap_engine is not None:
            ap_engine.dispose()


def delete_accounts_payable(payload: dict, conn=None):
    return delete_accounts_payables(payload, conn=conn)
