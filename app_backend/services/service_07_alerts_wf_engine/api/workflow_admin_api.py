from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Security
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.logic import workflow_admin_logic
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    WORKFLOW_ADMIN_ACTION,
    user_has_workflow_action,
)
from app_backend.services.service_09_user_authentication.logic.authentication_me import (
    get_authenticated_user,
)


router = APIRouter(prefix="/api/v1/workflow-engine", tags=["Workflow Engine Administration"])
bearer_scheme = HTTPBearer(auto_error=False)

WorkflowAction = Literal["ALL", "CREATE", "UPDATE", "DELETE", "APPROVAL"]
WorkflowNodeType = Literal["REQUESTER", "APPROVER", "END"]
WorkflowEdgeType = Literal["SEQUENTIAL"]


class WorkflowNodeRequest(BaseModel):
    node_key: str = Field(min_length=1)
    node_type: WorkflowNodeType
    user_principal_name: Optional[str] = None
    sequence_hint: Optional[int] = None
    canvas_x: float
    canvas_y: float
    node_metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdgeRequest(BaseModel):
    source_node_key: str = Field(min_length=1)
    target_node_key: str = Field(min_length=1)
    edge_sequence: Optional[int] = None
    edge_type: WorkflowEdgeType = "SEQUENTIAL"
    condition_json: dict[str, Any] = Field(default_factory=dict)


class WorkflowDraftRequest(BaseModel):
    organization_id: int
    workflow_action: WorkflowAction
    allow_self_approval: bool = False
    canvas_metadata: dict[str, Any] = Field(default_factory=dict)
    nodes: list[WorkflowNodeRequest]
    edges: list[WorkflowEdgeRequest]


class WorkflowValidateRequest(BaseModel):
    organization_id: int
    workflow_action: WorkflowAction
    allow_self_approval: bool = False
    canvas_metadata: dict[str, Any] = Field(default_factory=dict)
    nodes: Optional[list[WorkflowNodeRequest]] = None
    edges: Optional[list[WorkflowEdgeRequest]] = None

    @model_validator(mode="after")
    def require_complete_graph_or_stored_draft(self):
        if (self.nodes is None) != (self.edges is None):
            raise ValueError("Validation requires both nodes and edges, or neither.")
        return self


class WorkflowPublishRequest(BaseModel):
    organization_id: int
    workflow_action: WorkflowAction
    workflow_version_id: Optional[int] = None
    workflow_version_id_pk: Optional[int] = None


class WorkflowStatusRequest(BaseModel):
    organization_id: int
    is_active: bool


class WorkflowDefinitionResponse(BaseModel):
    definition: dict[str, Any] | None = None
    published_version: dict[str, Any] | None = None
    draft_version: dict[str, Any] | None = None


class WorkflowVersionResponse(BaseModel):
    workflow_version_id: int
    version_number: int
    version_status: str
    applies_to_action: str


class WorkflowValidationResponse(BaseModel):
    is_valid: bool
    validation_errors: list[dict[str, Any]]


def get_authenticated_workflow_context(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication token is required.")
    auth_result = get_authenticated_user(credentials.credentials)
    if auth_result.get("authenticated") is not True:
        raise HTTPException(
            status_code=401,
            detail=auth_result.get("error_code") or auth_result.get("error") or "Authentication failed.",
        )
    return auth_result


def authenticated_principal(auth_context: dict[str, Any]) -> str:
    user_principal_name = (auth_context.get("user") or {}).get("user_principal_name")
    if not user_principal_name:
        raise HTTPException(status_code=401, detail="Authenticated user principal is missing.")
    return user_principal_name


def authenticated_organization_id(auth_context: dict[str, Any]) -> int:
    user = auth_context.get("user") or {}
    organization = auth_context.get("organization") or {}
    organization_id = organization.get("org_id") or user.get("user_org_id_fk")
    if not organization_id:
        raise HTTPException(status_code=401, detail="Authenticated organization is missing.")
    return int(organization_id)


def require_same_organization(auth_context: dict[str, Any], organization_id: int) -> None:
    if authenticated_organization_id(auth_context) != int(organization_id):
        raise HTTPException(status_code=403, detail="WORKFLOW_ORGANIZATION_ACCESS_DENIED")


def has_workflow_admin(auth_context: dict[str, Any]) -> bool:
    principal = authenticated_principal(auth_context)
    organization_id = authenticated_organization_id(auth_context)
    engine = db_engine()
    try:
        with engine.begin() as conn:
            return user_has_workflow_action(
                conn,
                principal,
                WORKFLOW_ADMIN_ACTION,
                organization_id,
            )
    finally:
        engine.dispose()


def require_workflow_admin(auth_context: dict[str, Any]) -> None:
    if not has_workflow_admin(auth_context):
        raise HTTPException(status_code=403, detail="WORKFLOW_ADMIN_PERMISSION_REQUIRED")


def success_response(data: Any, status_code: int = 200) -> dict[str, Any]:
    return {"success": True, "data": jsonable_encoder(data)}


def fail_response(error_code: str, status_code: int = 400, data: Any = None) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "error_code": error_code,
            "error": error_code,
            "data": jsonable_encoder(data) if data is not None else {},
        },
    )


def require_valid_context(
    workflow_code: str,
    organization_id: int,
    workflow_action: str | None = None,
    allow_all_scope: bool = False,
) -> None:
    error = workflow_admin_logic.validate_request_context(
        workflow_code,
        organization_id,
        workflow_action,
        allow_all_scope=allow_all_scope,
    )
    if error == "WORKFLOW_NOT_FOUND":
        fail_response(error, 404)
    if error == "INVALID_ORGANIZATION":
        fail_response(error, 404)
    if error:
        fail_response(error, 400)


def normalize_logic_result(result: dict, success_status: int = 200) -> dict[str, Any]:
    if result.get("success") is False:
        error_code = result.get("error_code") or result.get("error") or "WORKFLOW_OPERATION_FAILED"
        if error_code in {"WORKFLOW_DRAFT_NOT_FOUND", "WORKFLOW_CONFIGURATION_NOT_FOUND"}:
            status_code = 404
        elif error_code in {"WORKFLOW_VALIDATION_FAILED", "WORKFLOW_VALIDATION_INPUT_INVALID"}:
            status_code = 422
        elif error_code in {"WORKFLOW_ALREADY_PUBLISHED", "WORKFLOW_VERSION_CONFLICT"}:
            status_code = 409
        else:
            status_code = 400
        data = {
            key: value
            for key, value in result.items()
            if key not in {"success", "error", "error_code"} and value is not None
        }
        fail_response(error_code, status_code, data)
    data = {
        key: value
        for key, value in result.items()
        if key not in {"success", "error", "error_code"} and value is not None
    }
    return success_response(data, success_status)


@router.get("/workflows")
def get_workflow_catalog_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_workflow_admin(auth_context)
    return success_response(workflow_admin_logic.get_catalog())


@router.get("/workflows/{workflow_code}/eligible-users")
def get_workflow_eligible_users_endpoint(
    workflow_code: str,
    organization_id: int,
    workflow_action: Optional[WorkflowAction] = None,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, organization_id, workflow_action, allow_all_scope=True)
    return success_response(workflow_admin_logic.get_eligible_users(workflow_code, organization_id))


@router.get("/workflows/{workflow_code}/definition")
def get_workflow_definition_endpoint(
    workflow_code: str,
    organization_id: int,
    workflow_action: WorkflowAction,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, organization_id, workflow_action, allow_all_scope=True)
    return success_response(workflow_admin_logic.get_definition(workflow_code, organization_id, workflow_action))


@router.put("/workflows/{workflow_code}/draft", status_code=201)
def save_workflow_draft_endpoint(
    workflow_code: str,
    payload: WorkflowDraftRequest,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, payload.organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, payload.organization_id, payload.workflow_action, allow_all_scope=True)
    result = workflow_admin_logic.save_draft(
        workflow_code,
        payload.model_dump(exclude_none=False),
        authenticated_principal(auth_context),
    )
    return normalize_logic_result(result, success_status=201)


@router.post("/workflows/{workflow_code}/validate")
def validate_workflow_draft_endpoint(
    workflow_code: str,
    payload: WorkflowValidateRequest,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, payload.organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, payload.organization_id, payload.workflow_action, allow_all_scope=True)
    result = workflow_admin_logic.validate_draft(workflow_code, payload.model_dump(exclude_none=False))
    if not result["is_valid"]:
        fail_response(
            "WORKFLOW_VALIDATION_FAILED",
            422,
            {
                "is_valid": False,
                "validation_errors": result["validation_errors"],
            },
        )
    return success_response({"is_valid": True, "validation_errors": []})


@router.post("/workflows/{workflow_code}/publish")
def publish_workflow_draft_endpoint(
    workflow_code: str,
    payload: WorkflowPublishRequest,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, payload.organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, payload.organization_id, payload.workflow_action, allow_all_scope=True)
    result = workflow_admin_logic.publish_draft(
        workflow_code,
        payload.model_dump(exclude_none=False),
        authenticated_principal(auth_context),
    )
    return normalize_logic_result(result)


@router.get("/workflows/{workflow_code}/versions")
def get_workflow_versions_endpoint(
    workflow_code: str,
    organization_id: int,
    workflow_action: Optional[WorkflowAction] = None,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, organization_id, workflow_action, allow_all_scope=True)
    return success_response(workflow_admin_logic.get_versions(workflow_code, organization_id, workflow_action))


@router.get("/workflows/{workflow_code}/versions/{workflow_version_id}")
def get_workflow_version_endpoint(
    workflow_code: str,
    workflow_version_id: int,
    organization_id: int,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, organization_id)
    result = workflow_admin_logic.get_version(workflow_code, organization_id, workflow_version_id)
    if not result:
        fail_response("WORKFLOW_VERSION_NOT_FOUND", 404)
    return success_response(result)


@router.patch("/workflows/{workflow_code}/status")
def update_workflow_status_endpoint(
    workflow_code: str,
    payload: WorkflowStatusRequest,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    require_same_organization(auth_context, payload.organization_id)
    require_workflow_admin(auth_context)
    require_valid_context(workflow_code, payload.organization_id)
    result = workflow_admin_logic.update_status(
        workflow_code,
        payload.organization_id,
        payload.is_active,
        authenticated_principal(auth_context),
    )
    if not result:
        fail_response("WORKFLOW_CONFIGURATION_NOT_FOUND", 404)
    return success_response(result)
