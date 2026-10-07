from datetime import date
import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Security
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app_backend.services.service_07_alerts_wf_engine.api.workflow_admin_api import (
    authenticated_organization_id,
    authenticated_principal,
    get_authenticated_workflow_context,
    has_workflow_admin,
    success_response,
)
from app_backend.services.service_07_alerts_wf_engine.logic import workflow_runtime_logic
from app_backend.services.service_07_alerts_wf_engine.logic.workflow_attachment_logic import public_instance
from app_backend.services.service_07_alerts_wf_engine.workflow_document_download import download_workflow_document


router = APIRouter(prefix="/api/v1/workflow-engine", tags=["Workflow Engine Runtime"])
logger = logging.getLogger(__name__)
WORKFLOW_RUNTIME_QUERY_FAILED = "WORKFLOW_RUNTIME_QUERY_FAILED"

WorkflowAction = Literal["CREATE", "UPDATE", "DELETE", "APPROVAL"]
WorkflowStatus = Literal[
    "PENDING_APPROVAL",
    "EXECUTED",
    "REJECTED",
    "EXECUTION_FAILED",
    "ERROR",
]


class WorkflowInstanceResponse(BaseModel):
    workflow_instance_id: int
    workflow_code: str
    organization_id: int
    workflow_action: str
    requester_user_principal_name: str
    workflow_status: str


class WorkflowInstanceStepResponse(BaseModel):
    workflow_instance_step_id: int
    workflow_instance_id: int
    step_sequence: int
    assigned_approver_user_principal_name: str
    step_status: str


class WorkflowAttachmentDownloadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


def _execute_runtime_query(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Workflow runtime query failed.")
        raise HTTPException(
            status_code=500,
            detail=WORKFLOW_RUNTIME_QUERY_FAILED,
        ) from exc


@router.get("/instances")
def get_workflow_instances_endpoint(
    workflow_code: Optional[str] = None,
    organization_id: Optional[int] = None,
    workflow_action: Optional[WorkflowAction] = None,
    workflow_status: Optional[WorkflowStatus] = None,
    requester_user_principal_name: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    principal_org_id = authenticated_organization_id(auth_context)
    if organization_id is not None and int(organization_id) != principal_org_id:
        raise HTTPException(status_code=403, detail="WORKFLOW_ORGANIZATION_ACCESS_DENIED")
    return success_response(
        _execute_runtime_query(
            workflow_runtime_logic.get_instances,
            principal=authenticated_principal(auth_context),
            principal_organization_id=principal_org_id,
            is_admin=has_workflow_admin(auth_context),
            filters={
                "workflow_code": workflow_code,
                "organization_id": organization_id,
                "workflow_action": workflow_action,
                "workflow_status": workflow_status,
                "requester_user_principal_name": requester_user_principal_name,
                "date_from": date_from,
                "date_to": date_to,
            },
        )
    )


@router.get("/instances/{workflow_instance_id}")
def get_workflow_instance_endpoint(
    workflow_instance_id: int,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    result = _execute_runtime_query(
        workflow_runtime_logic.get_instance,
        workflow_instance_id=workflow_instance_id,
        principal=authenticated_principal(auth_context),
        principal_organization_id=authenticated_organization_id(auth_context),
        is_admin=has_workflow_admin(auth_context),
    )
    if not result:
        raise HTTPException(status_code=404, detail="WORKFLOW_INSTANCE_NOT_FOUND")
    return success_response(public_instance(result))


@router.post("/instances/{workflow_instance_id}/attachments/{attachment_id}/download")
def download_workflow_attachment_endpoint(
    payload: WorkflowAttachmentDownloadPayload,
    workflow_instance_id: int = Path(gt=0),
    attachment_id: str = Path(min_length=1, max_length=100),
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
):
    try:
        instance = _execute_runtime_query(
            workflow_runtime_logic.get_instance,
            workflow_instance_id=workflow_instance_id,
            principal=authenticated_principal(auth_context),
            principal_organization_id=authenticated_organization_id(auth_context),
            is_admin=has_workflow_admin(auth_context),
        )
        if not instance:
            raise HTTPException(status_code=404, detail="WORKFLOW_INSTANCE_NOT_FOUND")
        result = download_workflow_document(instance, attachment_id, payload.version_id)
        return JSONResponse(content=success_response(result), headers={"Cache-Control": "no-store"})
    except HTTPException as exc:
        # Return the same envelope in standalone and consolidated applications.
        return JSONResponse(status_code=exc.status_code,
                            content={"success": False, "error": exc.detail},
                            headers={"Cache-Control": "no-store"})


@router.get("/instances/{workflow_instance_id}/steps")
def get_workflow_instance_steps_endpoint(
    workflow_instance_id: int,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    result = _execute_runtime_query(
        workflow_runtime_logic.get_steps,
        workflow_instance_id=workflow_instance_id,
        principal=authenticated_principal(auth_context),
        principal_organization_id=authenticated_organization_id(auth_context),
        is_admin=has_workflow_admin(auth_context),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="WORKFLOW_INSTANCE_NOT_FOUND")
    return success_response(result)


@router.get("/inbox")
def get_workflow_inbox_endpoint(
    workflow_code: Optional[str] = None,
    workflow_action: Optional[WorkflowAction] = None,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return success_response(
        _execute_runtime_query(
            workflow_runtime_logic.get_inbox,
            principal=authenticated_principal(auth_context),
            principal_organization_id=authenticated_organization_id(auth_context),
            filters={
                "workflow_code": workflow_code,
                "workflow_action": workflow_action,
            },
        )
    )
