from pathlib import Path
import json
import sys
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
SERVICE_DIR = BASE_DIR.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app_backend.services.service_07_alerts_wf_engine.fleet_management_wf import (
    approve_fleet_management_workflow,
    get_fleet_management_workflow_requests,
    reject_fleet_management_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.contracts_management_wf import (
    approve_contracts_management_workflow,
    get_contracts_management_workflow_requests,
    reject_contracts_management_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.asset_master_wf import (
    approve_asset_master_workflow,
    get_asset_master_workflow_requests,
    reject_asset_master_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf import (
    approve_accounts_payables_workflow,
    get_accounts_payables_workflow_requests,
    reject_accounts_payables_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf import (
    approve_accounts_receivables_workflow,
    get_accounts_receivables_workflow_requests,
    reject_accounts_receivables_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.api.workflow_admin_api import (
    router as workflow_admin_router,
)
from app_backend.services.service_07_alerts_wf_engine.api.workflow_runtime_api import (
    router as workflow_runtime_router,
)
from app_backend.services.service_07_alerts_wf_engine.payroll_wf import (
    approve_payroll_workflow,
    get_payroll_workflow_requests,
    get_payroll_workflow_status,
    get_pending_payroll_approvals,
    reject_payroll_workflow,
    submit_payroll_run_for_approval,
)
from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    WORKFLOW_ADMIN_ACTION,
    user_has_workflow_action,
)
from app_backend.services.auth_context import (
    authenticated_organization_id as get_authenticated_organization_id,
    raise_for_logic_error,
)
from app_backend.services.service_09_user_authentication.logic.authentication_me import (
    get_authenticated_user,
)


app = FastAPI(
    title="UETransportERP Alerts and Workflow API",
    description="REST API for alerts and workflow approval handling.",
    version="1.0.0",
)

# Development CORS policy. Restrict allowed origins before production deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflow_admin_router)
app.include_router(workflow_runtime_router)

bearer_scheme = HTTPBearer(auto_error=False)


class WorkflowActionIdentityPayload(BaseModel):
    workflow_request_id: Optional[int] = None
    workflow_request_id_pk: Optional[int] = None
    workflow_instance_id: Optional[int] = None
    workflow_instance_id_pk: Optional[int] = None
    workflow_instance_step_id: Optional[int] = None
    workflow_instance_step_id_pk: Optional[int] = None
    approver_user_principal_name: Optional[str] = None


class FleetWorkflowApprovalPayload(WorkflowActionIdentityPayload):
    approval_comments: Optional[str] = None


class FleetWorkflowRejectPayload(WorkflowActionIdentityPayload):
    rejection_comments: Optional[str] = None


class FleetWorkflowGetPayload(BaseModel):
    workflow_status: Optional[str] = None
    user_principal_name: Optional[str] = None
    actionable_only: bool = False


class PayrollWorkflowSubmitPayload(BaseModel):
    payroll_run_id: Optional[int] = None
    payroll_run_id_pk: Optional[int] = None
    payroll_org_id_fk: Optional[int] = None
    user_principal_name: Optional[str] = None
    approval_comments: Optional[str] = None


class PayrollWorkflowApprovalPayload(WorkflowActionIdentityPayload):
    approval_comments: Optional[str] = None


class PayrollWorkflowRejectPayload(WorkflowActionIdentityPayload):
    rejection_comments: Optional[str] = None


class PayrollWorkflowGetPayload(BaseModel):
    payroll_run_id: Optional[int] = None
    payroll_run_id_pk: Optional[int] = None
    payroll_org_id_fk: Optional[int] = None
    workflow_status: Optional[str] = None
    user_principal_name: Optional[str] = None
    actionable_only: bool = False


class ContractsWorkflowApprovalPayload(WorkflowActionIdentityPayload):
    approval_comments: Optional[str] = None


class ContractsWorkflowRejectPayload(WorkflowActionIdentityPayload):
    rejection_comments: Optional[str] = None


class ContractsWorkflowGetPayload(BaseModel):
    workflow_status: Optional[str] = None
    user_principal_name: Optional[str] = None
    actionable_only: bool = False


class AssetMasterWorkflowApprovalPayload(WorkflowActionIdentityPayload):
    approval_comments: Optional[str] = None


class AssetMasterWorkflowRejectPayload(WorkflowActionIdentityPayload):
    rejection_comments: Optional[str] = None


class AssetMasterWorkflowGetPayload(BaseModel):
    workflow_status: Optional[str] = None
    user_principal_name: Optional[str] = None
    actionable_only: bool = False


class AccountsPayableWorkflowApprovalPayload(WorkflowActionIdentityPayload):
    approval_comments: Optional[str] = None


class AccountsPayableWorkflowRejectPayload(WorkflowActionIdentityPayload):
    rejection_comments: Optional[str] = None


class AccountsPayableWorkflowGetPayload(BaseModel):
    workflow_status: Optional[str] = None
    user_principal_name: Optional[str] = None
    actionable_only: bool = False


class AccountsReceivableWorkflowApprovalPayload(WorkflowActionIdentityPayload):
    approval_comments: Optional[str] = None


class AccountsReceivableWorkflowRejectPayload(WorkflowActionIdentityPayload):
    rejection_comments: Optional[str] = None


class AccountsReceivableWorkflowGetPayload(BaseModel):
    workflow_status: Optional[str] = None
    user_principal_name: Optional[str] = None
    actionable_only: bool = False


class WorkflowDefinitionQueryPayload(BaseModel):
    organization_id: int
    applies_to_action: Optional[str] = None
    include_draft: bool = True


class WorkflowEligibleUsersQueryPayload(BaseModel):
    organization_id: int


class WorkflowDraftNodePayload(BaseModel):
    node_key: str
    node_type: str
    user_principal_name: Optional[str] = None
    sequence_hint: Optional[int] = None
    canvas_x: Optional[float] = None
    canvas_y: Optional[float] = None
    node_metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowDraftEdgePayload(BaseModel):
    source_node_key: str
    target_node_key: str
    edge_sequence: Optional[int] = None
    edge_type: str = "SEQUENTIAL"
    condition_json: dict[str, Any] = Field(default_factory=dict)


class WorkflowDraftSavePayload(BaseModel):
    organization_id: int
    applies_to_action: str = "ALL"
    allow_self_approval: bool = False
    nodes: list[WorkflowDraftNodePayload]
    edges: list[WorkflowDraftEdgePayload]
    canvas_metadata: dict[str, Any] = Field(default_factory=dict)
    user_principal_name: Optional[str] = None
    created_by: Optional[str] = None


class WorkflowPublishPayload(BaseModel):
    workflow_version_id: Optional[int] = None
    workflow_version_id_pk: Optional[int] = None
    user_principal_name: Optional[str] = None
    published_by: Optional[str] = None


def success_response(result: Any) -> dict[str, Any]:
    return {"success": True, "data": result}


def get_authenticated_workflow_context(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication token is required.")
    auth_result = get_authenticated_user(credentials.credentials)
    if auth_result.get("authenticated") is not True:
        raise HTTPException(status_code=401, detail=auth_result.get("error") or "Authentication failed.")
    return auth_result


def authenticated_principal(auth_context: dict[str, Any]) -> str:
    user_principal_name = (auth_context.get("user") or {}).get("user_principal_name")
    if not user_principal_name:
        raise HTTPException(status_code=401, detail="Authenticated user principal is missing.")
    return user_principal_name


def require_workflow_admin(auth_context: dict[str, Any]) -> None:
    user = auth_context.get("user") or {}
    organization = auth_context.get("organization") or {}
    user_principal_name = user.get("user_principal_name")
    organization_id = organization.get("org_id") or user.get("user_org_id_fk")
    engine = db_engine()
    try:
        with engine.begin() as conn:
            if not user_has_workflow_action(
                conn,
                user_principal_name,
                WORKFLOW_ADMIN_ACTION,
                organization_id,
            ):
                raise HTTPException(
                    status_code=403,
                    detail="WORKFLOW_ADMIN_PERMISSION_REQUIRED",
                )
    finally:
        engine.dispose()


def payload_with_authenticated_user(
    payload: BaseModel | dict[str, Any],
    auth_context: dict[str, Any],
    organization_field_names: tuple[str, ...] = (),
) -> dict[str, Any]:
    user_principal_name = authenticated_principal(auth_context)
    organization_id = get_authenticated_organization_id(auth_context)
    if isinstance(payload, dict):
        data = dict(payload)
    else:
        data = payload.model_dump(exclude_none=False)
    for field_name in (
        "user_principal_name",
        "acting_user_principal_name",
        "approver_user_principal_name",
    ):
        field_value = data.get(field_name)
        if field_value and field_value.lower() != user_principal_name.lower():
            raise HTTPException(
                status_code=403,
                detail="AUTHENTICATED_PRINCIPAL_MISMATCH",
            )
    for field_name in organization_field_names:
        field_value = data.get(field_name)
        if field_value is not None and field_value != "" and int(field_value) != int(organization_id):
            raise HTTPException(
                status_code=403,
                detail="AUTHENTICATED_ORGANIZATION_MISMATCH",
            )
        data[field_name] = organization_id
    data["user_principal_name"] = user_principal_name
    data["acting_user_principal_name"] = user_principal_name
    data["approver_user_principal_name"] = user_principal_name
    data["authenticated_org_id"] = organization_id
    return data


def _to_json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict") and value.__class__.__name__ == "DataFrame":
        return json.loads(value.to_json(orient="records", date_format="iso"))

    if isinstance(value, tuple):
        if value and hasattr(value[0], "to_dict") and value[0].__class__.__name__ == "DataFrame":
            return _to_json_safe(value[0])

        return [_to_json_safe(item) for item in value]

    if isinstance(value, list):
        return [_to_json_safe(item) for item in value]

    if isinstance(value, dict):
        return {str(key): _to_json_safe(item) for key, item in value.items()}

    try:
        return jsonable_encoder(value)
    except Exception:
        return str(value)


def handle_logic_call(
    func: Callable[..., Any],
    payload: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    try:
        result = func(payload) if payload is not None else func()
        raise_for_logic_error(result)
        return success_response(_to_json_safe(result))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        content = {"success": False, **exc.detail}
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(content))
    error_message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": error_message},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": str(exc)},
    )


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, Any]:
    return success_response({"status": "healthy"})


@app.post("/api/v1/workflow/fleet-management/approve", tags=["Fleet Workflow"])
def approve_fleet_workflow_endpoint(
    payload: FleetWorkflowApprovalPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_fleet_management_workflow,
        payload_with_authenticated_user(payload, auth_context, ("fleet_org_id_fk",)),
    )


@app.post("/api/v1/workflow/fleet-management/reject", tags=["Fleet Workflow"])
def reject_fleet_workflow_endpoint(
    payload: FleetWorkflowRejectPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_fleet_management_workflow,
        payload_with_authenticated_user(payload, auth_context, ("fleet_org_id_fk",)),
    )


@app.post("/api/v1/workflow/fleet-management/requests", tags=["Fleet Workflow"])
def get_fleet_workflow_requests_endpoint(
    payload: FleetWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_fleet_management_workflow_requests,
        payload_with_authenticated_user(payload, auth_context, ("fleet_org_id_fk",)),
    )


@app.get("/api/v1/workflow/fleet-management/requests", tags=["Fleet Workflow"])
def get_all_fleet_workflow_requests_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_fleet_management_workflow_requests,
        payload_with_authenticated_user(
            {"user_principal_name": authenticated_principal(auth_context)},
            auth_context,
            ("fleet_org_id_fk",),
        ),
    )


@app.post("/api/v1/workflow/contracts-management/approve", tags=["Contracts Workflow"])
def approve_contracts_workflow_endpoint(
    payload: ContractsWorkflowApprovalPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_contracts_management_workflow,
        payload_with_authenticated_user(payload, auth_context, ("cont_org_id_fk",)),
    )


@app.post("/api/v1/workflow/contracts-management/reject", tags=["Contracts Workflow"])
def reject_contracts_workflow_endpoint(
    payload: ContractsWorkflowRejectPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_contracts_management_workflow,
        payload_with_authenticated_user(payload, auth_context, ("cont_org_id_fk",)),
    )


@app.post("/api/v1/workflow/contracts-management/requests", tags=["Contracts Workflow"])
def get_contracts_workflow_requests_endpoint(
    payload: ContractsWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_contracts_management_workflow_requests,
        payload_with_authenticated_user(payload, auth_context, ("cont_org_id_fk",)),
    )


@app.get("/api/v1/workflow/contracts-management/requests", tags=["Contracts Workflow"])
def get_all_contracts_workflow_requests_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_contracts_management_workflow_requests,
        payload_with_authenticated_user(
            {"user_principal_name": authenticated_principal(auth_context)},
            auth_context,
            ("cont_org_id_fk",),
        ),
    )


@app.post("/api/v1/workflow/asset-master/approve", tags=["Asset Master Workflow"])
def approve_asset_master_workflow_endpoint(
    payload: AssetMasterWorkflowApprovalPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_asset_master_workflow,
        payload_with_authenticated_user(payload, auth_context, ("asset_org_id_fk",)),
    )


@app.post("/api/v1/workflow/asset-master/reject", tags=["Asset Master Workflow"])
def reject_asset_master_workflow_endpoint(
    payload: AssetMasterWorkflowRejectPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_asset_master_workflow,
        payload_with_authenticated_user(payload, auth_context, ("asset_org_id_fk",)),
    )


@app.post("/api/v1/workflow/asset-master/requests", tags=["Asset Master Workflow"])
def get_asset_master_workflow_requests_endpoint(
    payload: AssetMasterWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_asset_master_workflow_requests,
        payload_with_authenticated_user(payload, auth_context, ("asset_org_id_fk",)),
    )


@app.get("/api/v1/workflow/asset-master/requests", tags=["Asset Master Workflow"])
def get_all_asset_master_workflow_requests_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_asset_master_workflow_requests,
        payload_with_authenticated_user(
            {"user_principal_name": authenticated_principal(auth_context)},
            auth_context,
            ("asset_org_id_fk",),
        ),
    )


@app.post("/api/v1/workflow/accounts-payables/approve", tags=["Accounts Payable Workflow"])
def approve_accounts_payable_workflow_endpoint(
    payload: AccountsPayableWorkflowApprovalPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_accounts_payables_workflow,
        payload_with_authenticated_user(payload, auth_context, ("ap_org_id_fk",)),
    )


@app.post("/api/v1/workflow/accounts-payables/reject", tags=["Accounts Payable Workflow"])
def reject_accounts_payable_workflow_endpoint(
    payload: AccountsPayableWorkflowRejectPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_accounts_payables_workflow,
        payload_with_authenticated_user(payload, auth_context, ("ap_org_id_fk",)),
    )


@app.post("/api/v1/workflow/accounts-payables/requests", tags=["Accounts Payable Workflow"])
def get_accounts_payable_workflow_requests_endpoint(
    payload: AccountsPayableWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_payables_workflow_requests,
        payload_with_authenticated_user(payload, auth_context, ("ap_org_id_fk",)),
    )


@app.get("/api/v1/workflow/accounts-payables/requests", tags=["Accounts Payable Workflow"])
def get_all_accounts_payable_workflow_requests_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_payables_workflow_requests,
        payload_with_authenticated_user(
            {"user_principal_name": authenticated_principal(auth_context)},
            auth_context,
            ("ap_org_id_fk",),
        ),
    )


@app.post("/api/v1/workflow/accounts-receivables/approve", tags=["Accounts Receivable Workflow"])
def approve_accounts_receivable_workflow_endpoint(
    payload: AccountsReceivableWorkflowApprovalPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_accounts_receivables_workflow,
        payload_with_authenticated_user(payload, auth_context, ("ar_org_id_fk",)),
    )


@app.post("/api/v1/workflow/accounts-receivables/reject", tags=["Accounts Receivable Workflow"])
def reject_accounts_receivable_workflow_endpoint(
    payload: AccountsReceivableWorkflowRejectPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_accounts_receivables_workflow,
        payload_with_authenticated_user(payload, auth_context, ("ar_org_id_fk",)),
    )


@app.post("/api/v1/workflow/accounts-receivables/requests", tags=["Accounts Receivable Workflow"])
def get_accounts_receivable_workflow_requests_endpoint(
    payload: AccountsReceivableWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_receivables_workflow_requests,
        payload_with_authenticated_user(payload, auth_context, ("ar_org_id_fk",)),
    )


@app.get("/api/v1/workflow/accounts-receivables/requests", tags=["Accounts Receivable Workflow"])
def get_all_accounts_receivable_workflow_requests_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_receivables_workflow_requests,
        payload_with_authenticated_user(
            {"user_principal_name": authenticated_principal(auth_context)},
            auth_context,
            ("ar_org_id_fk",),
        ),
    )


@app.post("/api/v1/workflow/payroll/submit", tags=["Payroll Workflow"])
def submit_payroll_workflow_endpoint(
    payload: PayrollWorkflowSubmitPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        submit_payroll_run_for_approval,
        payload_with_authenticated_user(payload, auth_context, ("payroll_org_id_fk",)),
    )


@app.post("/api/v1/workflow/payroll/approve", tags=["Payroll Workflow"])
def approve_payroll_workflow_endpoint(
    payload: PayrollWorkflowApprovalPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_payroll_workflow,
        payload_with_authenticated_user(payload, auth_context, ("payroll_org_id_fk",)),
    )


@app.post("/api/v1/workflow/payroll/reject", tags=["Payroll Workflow"])
def reject_payroll_workflow_endpoint(
    payload: PayrollWorkflowRejectPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_payroll_workflow,
        payload_with_authenticated_user(payload, auth_context, ("payroll_org_id_fk",)),
    )


@app.post("/api/v1/workflow/payroll/status", tags=["Payroll Workflow"])
def get_payroll_workflow_status_endpoint(
    payload: PayrollWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_payroll_workflow_status,
        payload_with_authenticated_user(payload, auth_context, ("payroll_org_id_fk",)),
    )


@app.post("/api/v1/workflow/payroll/requests", tags=["Payroll Workflow"])
def get_payroll_workflow_requests_endpoint(
    payload: PayrollWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_payroll_workflow_requests,
        payload_with_authenticated_user(payload, auth_context, ("payroll_org_id_fk",)),
    )


@app.get("/api/v1/workflow/payroll/requests", tags=["Payroll Workflow"])
def get_all_payroll_workflow_requests_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_payroll_workflow_requests,
        payload_with_authenticated_user(
            {"user_principal_name": authenticated_principal(auth_context)},
            auth_context,
            ("payroll_org_id_fk",),
        ),
    )


@app.post("/api/v1/workflow/payroll/pending-approvals", tags=["Payroll Workflow"])
def get_pending_payroll_approvals_endpoint(
    payload: PayrollWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_workflow_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_pending_payroll_approvals,
        payload_with_authenticated_user(payload, auth_context, ("payroll_org_id_fk",)),
    )


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_07_alerts_wf_engine.api.main:app",
        host="0.0.0.0",
        port=8007,
        reload=True,
    )
