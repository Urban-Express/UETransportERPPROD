from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    WORKFLOW_APPROVE_ACTION,
    WORKFLOW_MODULE_NAME,
    WORKFLOW_SUBMIT_ACTION,
    get_user_workflow_actions as _get_user_workflow_actions,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    approve_configured_or_legacy_workflow,
    ensure_legacy_workflow_table,
    get_legacy_workflow_requests,
    is_trusted_internal_workflow_payload,
    reject_configured_or_legacy_workflow,
    submit_configured_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import trusted_workflow_execution


WORKFLOW_CODE = "FLEET_MANAGEMENT"
WORKFLOW_REQUEST_TABLE = "fleet_management_workflow_requests"
INTERNAL_WORKFLOW_APPROVAL_FLAG = "_fleet_management_workflow_approved"
SUPPORTED_ACTIONS = {"CREATE", "UPDATE", "DELETE"}
LEGACY_ORG_FIELDS = ("fleet_org_id_fk",)


def get_user_workflow_actions(user_principal_name: str):
    """Compatibility wrapper over the common workflow access module."""
    engine = db_engine()
    try:
        with engine.begin() as conn:
            return _get_user_workflow_actions(conn, user_principal_name)
    finally:
        engine.dispose()


def ensure_fleet_management_workflow_table():
    ensure_legacy_workflow_table(WORKFLOW_REQUEST_TABLE)


def verify_fleet_management_workflow_table():
    ensure_fleet_management_workflow_table()


def get_employee_line_manager_workflow_identity(
    user_principal_name: str,
    organization_id: int | None = None,
):
    """Legacy helper retained only for pre-engine compatibility; new submissions do not call it."""
    return None


def get_employee_line_manager_user_principal_name(
    user_principal_name: str,
    organization_id: int | None = None,
):
    """Legacy helper retained only for pre-engine compatibility; new submissions do not call it."""
    return None


def trigger_fleet_management_workflow(payload: dict, workflow_action: str):
    workflow_action = (workflow_action or "").upper()
    user_principal_name = payload.get("user_principal_name")

    if is_trusted_internal_workflow_payload(payload, INTERNAL_WORKFLOW_APPROVAL_FLAG):
        return {
            "workflow_confirmation": "Y",
            "workflow_status": "APPROVED",
            "workflow_action": workflow_action,
            "user_principal_name": user_principal_name,
            "message": "Fleet management workflow approved by trusted workflow engine execution.",
        }

    ensure_fleet_management_workflow_table()

    if workflow_action not in SUPPORTED_ACTIONS:
        return {
            "workflow_confirmation": "N",
            "workflow_status": "REJECTED",
            "error": f"Unsupported fleet management workflow action: {workflow_action}",
        }

    organization_id = _resolve_fleet_organization_id(payload)
    return submit_configured_workflow(
        workflow_code=WORKFLOW_CODE,
        workflow_action=workflow_action,
        payload=payload,
        organization_id=organization_id,
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
    )


def is_fleet_management_workflow_approved(payload: dict, workflow_action: str):
    workflow_response = trigger_fleet_management_workflow(payload, workflow_action)
    return workflow_response.get("workflow_confirmation") == "Y", workflow_response


def get_fleet_management_workflow_requests(payload: dict | None = None):
    return get_legacy_workflow_requests(WORKFLOW_REQUEST_TABLE, payload, LEGACY_ORG_FIELDS)


def execute_approved_fleet_action(workflow_action: str, request_payload: dict, conn=None):
    execution_payload = request_payload.copy()
    execution_payload[INTERNAL_WORKFLOW_APPROVAL_FLAG] = True
    with trusted_workflow_execution():
        if workflow_action == "CREATE":
            from app_backend.services.service_03_fleet_management.logic.fleet_master_create_data import create_fleet_vehicle

            return create_fleet_vehicle(payload=execution_payload, conn=conn)
        if workflow_action == "UPDATE":
            from app_backend.services.service_03_fleet_management.logic.fleet_master_update_data import update_fleet_vehicle

            return update_fleet_vehicle(payload=execution_payload, conn=conn)
        if workflow_action == "DELETE":
            from app_backend.services.service_03_fleet_management.logic.fleet_master_delete_data import delete_fleet_vehicle

            return delete_fleet_vehicle(payload=execution_payload, conn=conn)
    return {"error": f"Unsupported fleet management workflow action: {workflow_action}"}


def approve_fleet_management_workflow(payload: dict):
    return approve_configured_or_legacy_workflow(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
        execution_adapter=execute_approved_fleet_action,
        legacy_organization_field_names=LEGACY_ORG_FIELDS,
    )


def reject_fleet_management_workflow(payload: dict):
    return reject_configured_or_legacy_workflow(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        legacy_table_name=WORKFLOW_REQUEST_TABLE,
        legacy_organization_field_names=LEGACY_ORG_FIELDS,
    )


def _resolve_fleet_organization_id(payload: dict) -> int | None:
    if payload.get("fleet_org_id_fk"):
        return payload.get("fleet_org_id_fk")

    fleet_vehicle_id = payload.get("fleet_vehicle_id") or payload.get("fleet_vehicle_id_pk")
    if not fleet_vehicle_id:
        return None

    engine = db_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    select fleet_org_id_fk
                    from fleet_master
                    where fleet_vehicle_id_pk = :fleet_vehicle_id
                    limit 1
                """),
                {"fleet_vehicle_id": fleet_vehicle_id},
            ).first()
            return row[0] if row else None
    finally:
        engine.dispose()
