from pathlib import Path
import json
import sys
from typing import Any, Callable, Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
LOGIC_DIR = BASE_DIR.parent / "logic"
if str(LOGIC_DIR) not in sys.path:
    sys.path.insert(0, str(LOGIC_DIR))

# Update function name if different in actual logic file.
from app_backend.services.service_01_organization_management.logic.department_master_create_data import create_department
from app_backend.services.service_01_organization_management.logic.department_master_update_data import update_department
from app_backend.services.service_01_organization_management.logic.department_master_delete_data import delete_department
from app_backend.services.service_01_organization_management.logic.department_master_get_data import get_department as get_department_data

# Update function name if different in actual logic file.
from app_backend.services.service_01_organization_management.logic.organization_master_create_data import create_organization
from app_backend.services.service_01_organization_management.logic.organization_master_update_data import update_organization
from app_backend.services.service_01_organization_management.logic.organization_master_delete_data import delete_organization
from app_backend.services.service_01_organization_management.logic.organization_master_get_data import get_organization as get_organization_data

# Update function name if different in actual logic file.
from app_backend.services.service_01_organization_management.logic.organization_license_master_get_data import (
    get_organization_license as get_organization_license_data,
)

# Update function name if different in actual logic file.
from app_backend.services.service_01_organization_management.logic.role_master_get_data import get_role as get_role_master_data
from app_backend.services.service_01_organization_management.logic.permissions_master_get_data import get_permissions as get_permissions_master_data
from app_backend.services.service_01_organization_management.logic.user_role_assignment_master import assign_user_role
from app_backend.services.service_01_organization_management.logic.user_role_get_data import get_user_role as get_user_role_data
from app_backend.services.service_01_organization_management.logic.user_access_rights_data import get_user_access as get_user_access

# Update function name if different in actual logic file.
from app_backend.services.service_01_organization_management.logic.user_master_create_data import create_user
from app_backend.services.service_01_organization_management.logic.user_master_update_data import update_user
from app_backend.services.service_01_organization_management.logic.user_master_delete_data import delete_user
from app_backend.services.service_01_organization_management.logic.user_master_get_data import get_user as get_user_data
from app_backend.services.auth_context import (
    bind_authenticated_payload,
    get_authenticated_context,
    raise_for_logic_error,
)


app = FastAPI(
    title="UETransportERP Organization Management API",
    description="REST API for Organization, User, Role, and Permission management.",
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


class DepartmentCreatePayload(BaseModel):
    dep_org_id_fk: int
    department_name: str
    cost_center_flag: Optional[str] = ""
    profit_center_flag: Optional[str] = ""
    created_by: str


class DepartmentUpdatePayload(BaseModel):
    dep_id: int
    dep_org_id_fk: int
    department_name: str
    cost_center_flag: Optional[str] = ""
    profit_center_flag: Optional[str] = ""
    created_by: str


class DepartmentDeletePayload(BaseModel):
    dep_id: int


class OrganizationCreatePayload(BaseModel):
    org_name: str
    org_address: Optional[str] = ""
    org_phone_primary: Optional[str] = ""
    org_phone_secondary: Optional[str] = ""
    org_email_primary: Optional[str] = ""
    org_email_secondary: Optional[str] = ""
    company_registration_number: Optional[str] = ""
    created_by: str


class OrganizationUpdatePayload(BaseModel):
    org_id: int
    org_name: str
    org_address: Optional[str] = ""
    org_phone_primary: Optional[str] = ""
    org_phone_secondary: Optional[str] = ""
    org_email_primary: Optional[str] = ""
    org_email_secondary: Optional[str] = ""
    company_registration_number: Optional[str] = ""
    created_by: str


class OrganizationDeletePayload(BaseModel):
    org_id: int


class OrganizationLicenseGetPayload(BaseModel):
    org_id: int


class AssignUserRolePayload(BaseModel):
    user_id: int
    role_id: int


class UserCreatePayload(BaseModel):
    auth_provider: Optional[Literal["LOCAL", "ENTRA"]] = None
    entra_object_id: Optional[str] = None
    user_principal_name: str
    password: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    user_org_id_fk: int
    user_department_id_fk: Optional[int] = None
    is_active: Optional[bool] = True
    is_deleted: Optional[bool] = False
    created_by: str


class UserUpdatePayload(BaseModel):
    user_id: int
    auth_provider: Optional[Literal["LOCAL", "ENTRA"]] = None
    entra_object_id: Optional[str] = None
    user_principal_name: str
    password: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    user_org_id_fk: int
    user_department_id_fk: Optional[int] = None
    is_active: Optional[bool] = True
    is_deleted: Optional[bool] = False
    updated_by: str


class UserDeletePayload(BaseModel):
    user_id: int

class UserAccessPayload(BaseModel):
    user_principal_name: str

def success_response(result: Any) -> dict[str, Any]:
    return {"success": True, "data": result}


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


def handle_logic_call(func: Callable[..., Any], payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
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
    error_message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": error_message},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": str(exc)},
    )


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, Any]:
    return success_response({"status": "healthy"})


@app.post("/api/v1/departments/create", tags=["Department Master"])
def create_department_endpoint(
    payload: DepartmentCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/department_master_create_data.py
    return handle_logic_call(
        create_department,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("dep_org_id_fk",),
            set_created_by=True,
        ),
    )


@app.post("/api/v1/departments/update", tags=["Department Master"])
def update_department_endpoint(
    payload: DepartmentUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/department_master_update_data.py
    return handle_logic_call(
        update_department,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("dep_org_id_fk",),
        ),
    )


@app.post("/api/v1/departments/delete", tags=["Department Master"])
def delete_department_endpoint(
    payload: DepartmentDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/department_master_delete_data.py
    return handle_logic_call(
        delete_department,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("dep_org_id_fk",),
        ),
    )


@app.get("/api/v1/departments", tags=["Department Master"])
def get_departments_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/department_master_get_data.py
    return handle_logic_call(
        get_department_data,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("dep_org_id_fk",),
        ),
    )


@app.post("/api/v1/organizations/create", tags=["Organization Master"])
def create_organization_endpoint(payload: OrganizationCreatePayload) -> dict[str, Any]:
    # Calls logic/organization_master_create_data.py
    return handle_logic_call(create_organization, payload.model_dump(exclude_none=False))


@app.post("/api/v1/organizations/update", tags=["Organization Master"])
def update_organization_endpoint(payload: OrganizationUpdatePayload) -> dict[str, Any]:
    # Calls logic/organization_master_update_data.py
    return handle_logic_call(update_organization, payload.model_dump(exclude_none=False))


@app.post("/api/v1/organizations/delete", tags=["Organization Master"])
def delete_organization_endpoint(payload: OrganizationDeletePayload) -> dict[str, Any]:
    # Calls logic/organization_master_delete_data.py
    return handle_logic_call(delete_organization, payload.model_dump(exclude_none=False))


@app.get("/api/v1/organizations", tags=["Organization Master"])
def get_organizations_endpoint() -> dict[str, Any]:
    # Calls logic/organization_master_get_data.py
    return handle_logic_call(get_organization_data)


@app.post("/api/v1/organization-licenses/get", tags=["Organization License"])
def get_organization_licenses_endpoint(
    payload: OrganizationLicenseGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/organization_license_master_get_data.py
    return handle_logic_call(
        get_organization_license_data,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("org_id",),
        ),
    )


@app.get("/api/v1/roles", tags=["Roles and Permissions"])
def get_roles_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/role_master_get_data.py
    bind_authenticated_payload({}, auth_context)
    return handle_logic_call(get_role_master_data)


@app.get("/api/v1/permissions", tags=["Roles and Permissions"])
def get_permissions_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/permissions_master_get_data.py
    bind_authenticated_payload({}, auth_context)
    return handle_logic_call(get_permissions_master_data)


@app.get("/api/v1/user-roles", tags=["Roles and Permissions"])
def get_user_roles_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/user_role_get_data.py
    return handle_logic_call(
        get_user_role_data,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("user_org_id_fk",),
        ),
    )

@app.get("/api/v1/user-access", tags=["Roles and Permissions"])
def get_user_access_endpoint(
    user_principal_name: str,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/user_access_rights_data
    return handle_logic_call(
        get_user_access,
        bind_authenticated_payload(
            {"user_principal_name": user_principal_name},
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/user-roles/assign", tags=["Roles and Permissions"])
def assign_user_role_endpoint(
    payload: AssignUserRolePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/user_role_assignment_master.py
    return handle_logic_call(
        assign_user_role,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("user_org_id_fk",),
        ),
    )


@app.post("/api/v1/users/create", tags=["User Master"])
def create_user_endpoint(
    payload: UserCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/user_master_create_data.py
    return handle_logic_call(
        create_user,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("user_org_id_fk",),
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/users/update", tags=["User Master"])
def update_user_endpoint(
    payload: UserUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/user_master_update_data.py
    return handle_logic_call(
        update_user,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("user_org_id_fk",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/users/delete", tags=["User Master"])
def delete_user_endpoint(
    payload: UserDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/user_master_delete_data.py
    return handle_logic_call(
        delete_user,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("user_org_id_fk",),
        ),
    )


@app.get("/api/v1/users", tags=["User Master"])
def get_users_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/user_master_get_data.py
    return handle_logic_call(
        get_user_data,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("user_org_id_fk",),
        ),
    )


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_01_organization_management.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
