from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    approve_configured_or_legacy_workflow,
    ensure_legacy_workflow_table,
    get_legacy_workflow_requests,
    is_trusted_internal_workflow_payload,
    reject_configured_or_legacy_workflow,
    submit_configured_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import trusted_workflow_execution


WORKFLOW_CODE = "ASSET_MASTER"
WORKFLOW_REQUEST_TABLE = "asset_master_workflow_requests"
INTERNAL_WORKFLOW_APPROVAL_FLAG = "_asset_master_workflow_approved"
SUPPORTED_ACTIONS = {"CREATE", "UPDATE", "DELETE"}
LEGACY_ORG_FIELDS = ("asset_org_id_fk",)


def verify_asset_master_workflow_table():
    ensure_legacy_workflow_table(WORKFLOW_REQUEST_TABLE)


def trigger_asset_master_workflow(payload: dict, workflow_action: str):
    workflow_action = (workflow_action or "").upper()
    user_principal_name = payload.get("user_principal_name")

    if is_trusted_internal_workflow_payload(payload, INTERNAL_WORKFLOW_APPROVAL_FLAG):
        return {
            "workflow_confirmation": "Y",
            "workflow_status": "APPROVED",
            "workflow_action": workflow_action,
            "user_principal_name": user_principal_name,
            "message": "Asset master workflow approved by trusted workflow engine execution.",
        }

    verify_asset_master_workflow_table()

    if workflow_action not in SUPPORTED_ACTIONS:
        return {
            "workflow_confirmation": "N",
            "workflow_status": "REJECTED",
            "error": f"Unsupported asset master workflow action: {workflow_action}",
        }

    return submit_configured_workflow(
        workflow_code=WORKFLOW_CODE,
        workflow_action=workflow_action,
        payload=payload,
        organization_id=payload.get("asset_org_id_fk"),
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
    )


def is_asset_master_workflow_approved(payload: dict, workflow_action: str):
    workflow_response = trigger_asset_master_workflow(payload, workflow_action)
    return workflow_response.get("workflow_confirmation") == "Y", workflow_response


def get_asset_master_workflow_requests(payload: dict | None = None):
    return get_legacy_workflow_requests(WORKFLOW_REQUEST_TABLE, payload, LEGACY_ORG_FIELDS)


def execute_approved_asset_master_action(workflow_action: str, request_payload: dict, conn=None):
    execution_payload = request_payload.copy()
    execution_payload[INTERNAL_WORKFLOW_APPROVAL_FLAG] = True
    with trusted_workflow_execution():
        if workflow_action == "CREATE":
            from app_backend.services.service_08_financial_management.logic.asset_master_create_data import create_asset

            return create_asset(payload=execution_payload, conn=conn)
        if workflow_action == "UPDATE":
            from app_backend.services.service_08_financial_management.logic.asset_master_update_data import update_asset

            return update_asset(payload=execution_payload, conn=conn)
        if workflow_action == "DELETE":
            from app_backend.services.service_08_financial_management.logic.asset_master_delete_data import delete_asset

            return delete_asset(payload=execution_payload, conn=conn)
    return {"error": f"Unsupported asset master workflow action: {workflow_action}"}


def approve_asset_master_workflow(payload: dict):
    return approve_configured_or_legacy_workflow(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
        execution_adapter=execute_approved_asset_master_action,
        legacy_organization_field_names=LEGACY_ORG_FIELDS,
    )


def reject_asset_master_workflow(payload: dict):
    return reject_configured_or_legacy_workflow(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
        legacy_organization_field_names=LEGACY_ORG_FIELDS,
    )
