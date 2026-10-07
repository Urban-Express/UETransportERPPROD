from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    approve_configured_or_legacy_workflow,
    ensure_legacy_workflow_table,
    get_legacy_workflow_requests,
    is_trusted_internal_workflow_payload,
    reject_configured_or_legacy_workflow,
    submit_configured_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import trusted_workflow_execution


WORKFLOW_CODE = "CONTRACTS_MANAGEMENT"
WORKFLOW_REQUEST_TABLE = "contracts_management_workflow_requests"
INTERNAL_WORKFLOW_APPROVAL_FLAG = "_contracts_management_workflow_approved"
SUPPORTED_ACTIONS = {"CREATE", "UPDATE"}
LEGACY_ORG_FIELDS = ("cont_org_id_fk",)


def verify_contracts_management_workflow_table():
    ensure_legacy_workflow_table(WORKFLOW_REQUEST_TABLE)


def trigger_contracts_management_workflow(payload: dict, workflow_action: str):
    workflow_action = (workflow_action or "").upper()
    user_principal_name = payload.get("user_principal_name")

    if is_trusted_internal_workflow_payload(payload, INTERNAL_WORKFLOW_APPROVAL_FLAG):
        return {
            "workflow_confirmation": "Y",
            "workflow_status": "APPROVED",
            "workflow_action": workflow_action,
            "user_principal_name": user_principal_name,
            "message": "Contracts workflow approved by trusted workflow engine execution.",
        }

    verify_contracts_management_workflow_table()

    if workflow_action not in SUPPORTED_ACTIONS:
        return {
            "workflow_confirmation": "N",
            "workflow_status": "REJECTED",
            "error": f"Unsupported contracts management workflow action: {workflow_action}",
        }

    return submit_configured_workflow(
        workflow_code=WORKFLOW_CODE,
        workflow_action=workflow_action,
        payload=payload,
        organization_id=payload.get("cont_org_id_fk"),
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
    )


def is_contracts_management_workflow_approved(payload: dict, workflow_action: str):
    workflow_response = trigger_contracts_management_workflow(payload, workflow_action)
    return workflow_response.get("workflow_confirmation") == "Y", workflow_response


def get_contracts_management_workflow_requests(payload: dict | None = None):
    return get_legacy_workflow_requests(WORKFLOW_REQUEST_TABLE, payload, LEGACY_ORG_FIELDS)


def execute_approved_contracts_action(workflow_action: str, request_payload: dict, conn=None):
    execution_payload = request_payload.copy()
    execution_payload[INTERNAL_WORKFLOW_APPROVAL_FLAG] = True
    with trusted_workflow_execution():
        if workflow_action == "CREATE":
            from app_backend.services.service_06_contracts_management.logic.contracts_management_create_data import create_contract

            return create_contract(payload=execution_payload, conn=conn)
        if workflow_action == "UPDATE":
            from app_backend.services.service_06_contracts_management.logic.contracts_management_update_data import update_contract

            return update_contract(payload=execution_payload, conn=conn)
    return {"error": f"Unsupported contracts management workflow action: {workflow_action}"}


def approve_contracts_management_workflow(payload: dict):
    return approve_configured_or_legacy_workflow(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
        execution_adapter=execute_approved_contracts_action,
        legacy_organization_field_names=LEGACY_ORG_FIELDS,
    )


def reject_contracts_management_workflow(payload: dict):
    return reject_configured_or_legacy_workflow(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
        legacy_organization_field_names=LEGACY_ORG_FIELDS,
    )
