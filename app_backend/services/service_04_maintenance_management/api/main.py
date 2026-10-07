from pathlib import Path
import json
import sys
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, Security, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
LOGIC_DIR = BASE_DIR.parent / "logic"
INTEGRATIONS_DIR = BASE_DIR.parent / "integrations"
for import_dir in (LOGIC_DIR, INTEGRATIONS_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

from app_backend.services.service_04_maintenance_management.logic.maintenance_master_create_data import create_maintenance
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_get_data import get_maintenance
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_update_data import update_maintenance
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_delete_data import delete_maintenance
from app_backend.services.service_04_maintenance_management.integrations.firebase_maintenance_image_upload import upload_maintenance_image
from app_backend.services.service_04_maintenance_management.integrations.firebase_maintenance_image_download import download_maintenance_image
from app_backend.services.auth_context import (
    bind_authenticated_payload,
    get_authenticated_context,
    raise_for_logic_error,
)


app = FastAPI(
    title="UETransportERP Maintenance Management API",
    description="REST API for vehicle maintenance master and Firebase maintenance image integration.",
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


class MaintenanceMasterPayload(BaseModel):
    maint_fleet_vehicle_id_fk: int
    maint_image_path: Optional[str] = None
    preventive_maintenance_date: Optional[str] = None
    preventive_maintenance_job_work: Optional[str] = None
    preventive_maintenance_workshop: Optional[str] = None
    preventive_maintenance_amount: Optional[float] = None
    breakdown_date: Optional[str] = None
    breakdown_job_work: Optional[str] = None
    breakdown_workshop: Optional[str] = None
    breakdown_amount: Optional[float] = None
    accident_date: Optional[str] = None
    accident_job_work: Optional[str] = None
    accident_workshop: Optional[str] = None
    accident_amount: Optional[float] = None
    deployment_type: Optional[str] = None
    deployment_client_name: Optional[str] = None
    deployment_from_date: Optional[str] = None
    file_path: Optional[str] = None
    maintenance_image_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class MaintenanceMasterUpdatePayload(MaintenanceMasterPayload):
    maint_id: Optional[int] = None
    maint_id_pk: Optional[int] = None


class MaintenanceMasterDeletePayload(BaseModel):
    maint_id: Optional[int] = None
    maint_id_pk: Optional[int] = None


class MaintenanceImageUploadPayload(BaseModel):
    maint_id: Optional[int] = None
    maint_id_pk: Optional[int] = None
    maint_fleet_vehicle_id_fk: Optional[int] = None
    file_path: Optional[str] = None
    maintenance_image_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    updated_by: Optional[str] = None
    created_by: Optional[str] = None


class MaintenanceImageDownloadPayload(BaseModel):
    maint_id: Optional[int] = None
    maint_id_pk: Optional[int] = None


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


def integration_error_status(error_message: str) -> int:
    normalized = error_message.lower()
    if "not found" in normalized:
        return 404
    if (
        " is required" in normalized
        or " is empty" in normalized
        or "missing" in normalized
        or "invalid" in normalized
    ):
        return 400
    if "authorization" in normalized or "forbidden" in normalized:
        return 403
    if "authentication" in normalized or "unauthorized" in normalized:
        return 401
    return 500


def handle_integration_result(result: Any) -> dict[str, Any]:
    safe_result = _to_json_safe(result)
    if isinstance(safe_result, dict) and safe_result.get("error"):
        raise HTTPException(
            status_code=integration_error_status(str(safe_result["error"])),
            detail=str(safe_result["error"])
        )
    return success_response(safe_result)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
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


@app.post("/api/v1/maintenance/create", tags=["Maintenance Master"])
def create_maintenance_endpoint(
    payload: MaintenanceMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_maintenance,
        bind_authenticated_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/maintenance/update", tags=["Maintenance Master"])
def update_maintenance_endpoint(
    payload: MaintenanceMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_maintenance,
        bind_authenticated_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/maintenance/delete", tags=["Maintenance Master"])
def delete_maintenance_endpoint(
    payload: MaintenanceMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_maintenance,
        bind_authenticated_payload(payload, auth_context),
    )


@app.get("/api/v1/maintenance", tags=["Maintenance Master"])
def get_maintenance_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_maintenance,
        bind_authenticated_payload({}, auth_context),
    )


@app.post("/api/v1/maintenance/images/upload", tags=["Maintenance Image Integration"])
def upload_maintenance_image_endpoint(
    maint_id: Optional[int] = Form(None),
    maint_id_pk: Optional[int] = Form(None),
    maint_fleet_vehicle_id_fk: Optional[int] = Form(None),
    updated_by: Optional[str] = Form(None),
    created_by: Optional[str] = Form(None),
    storage_folder: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),
    file: UploadFile = File(...),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = {
        "maint_id": maint_id,
        "maint_id_pk": maint_id_pk,
        "maint_fleet_vehicle_id_fk": maint_fleet_vehicle_id_fk,
        "updated_by": updated_by,
        "created_by": created_by,
        "storage_folder": storage_folder,
        "content_type": content_type,
    }
    payload = bind_authenticated_payload(
        payload,
        auth_context,
        set_updated_by=True,
    )
    return handle_integration_result(
        upload_maintenance_image(payload, file.file, file.filename, file.content_type)
    )


@app.post("/api/v1/maintenance/images/download", tags=["Maintenance Image Integration"])
def download_maintenance_image_endpoint(
    payload: MaintenanceImageDownloadPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_integration_result(
        download_maintenance_image(bind_authenticated_payload(payload, auth_context))
    )


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_04_maintenance_management.api.main:app",
        host="0.0.0.0",
        port=8004,
        reload=True,
    )
