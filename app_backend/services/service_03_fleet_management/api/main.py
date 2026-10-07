from pathlib import Path
import json
import sys
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi import Path as ApiPath
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


BASE_DIR = Path(__file__).resolve().parent
LOGIC_DIR = BASE_DIR.parent / "logic"
if str(LOGIC_DIR) not in sys.path:
    sys.path.insert(0, str(LOGIC_DIR))

from app_backend.services.service_03_fleet_management.logic.fleet_master_create_data import create_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_master_get_data import get_fleet_vehicle, get_fleet_vehicle_by_id
from app_backend.services.service_03_fleet_management.logic.fleet_master_update_data import update_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_master_delete_data import delete_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_create_data import create_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_get_data import get_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_update_data import update_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_delete_data import delete_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_create_data import create_fleet_status_history
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_get_data import get_fleet_status_history
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_update_data import update_fleet_status_history
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_delete_data import delete_fleet_status_history
from app_backend.services.auth_context import (
    bind_authenticated_payload,
    get_authenticated_context,
    raise_for_logic_error,
)


app = FastAPI(
    title="UETransportERP Fleet Management API",
    description="REST API for Fleet Master, Driver Allocation, and Status History management.",
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


class FleetMasterCreatePayload(BaseModel):
    user_principal_name: str
    fleet_org_id_fk: int
    vehicle_code: str
    fleet_type: str
    fleet_category: str
    vehicle_brand_name: str
    vehicle_model_name: Optional[str] = None
    vehicle_model_year: Optional[int] = None
    vehicle_color: Optional[str] = None
    vehicle_total_seats_including_driver: int
    vehicle_plate_number: str
    vehicle_plate_emirate: Optional[str] = None
    vehicle_chassis_number: str
    vehicle_engine_number: Optional[str] = None
    mulkiya_number: str
    mulkiya_expiry_date: str
    salik_tag_number: Optional[str] = None
    vehicle_insurance_provider: Optional[str] = None
    vehicle_insurance_number: Optional[str] = None
    vehicle_insurance_type: Optional[str] = None
    vehicle_insurance_start_date: Optional[str] = None
    vehicle_insurance_expiry_date: Optional[str] = None
    current_odometer_km: Optional[float] = 0
    odometer_last_updated_at: Optional[str] = None
    vehicle_operational_status: Optional[str] = "available"
    vehicle_deployment_status: Optional[str] = "unassigned"
    vehicle_ownership_type: Optional[str] = "owned"
    vehicle_owner_legal_entity: Optional[str] = None
    vehicle_acquisition_date: Optional[str] = None
    vehicle_acquisition_cost_aed: Optional[float] = None
    depreciation_start_date: Optional[str] = None
    depreciation_method: Optional[str] = "straight_line"
    useful_life_months: Optional[int] = None
    residual_value_aed: Optional[float] = 0
    field_flex_field_1: Optional[str] = None
    field_flex_field_2: Optional[str] = None
    field_flex_field_3: Optional[str] = None
    field_flex_field_4: Optional[str] = None


class FleetMasterUpdatePayload(FleetMasterCreatePayload):
    fleet_vehicle_id: Optional[int] = None
    fleet_vehicle_id_pk: Optional[int] = None


class FleetMasterDeletePayload(BaseModel):
    user_principal_name: str
    fleet_vehicle_id: Optional[int] = None
    fleet_vehicle_id_pk: Optional[int] = None


class FleetDriverAllocationCreatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fleet_org_id_fk: int
    fleet_vehicle_id_fk: int
    fleet_driver_empl_id_fk: int
    allocation_start_datetime: str
    allocation_end_datetime: Optional[str] = None
    allocation_status: Optional[str] = "scheduled"
    route_id_fk: Optional[int] = None
    shift_id_fk: Optional[int] = None
    allocation_reason: Optional[str] = None
    deallocation_reason: Optional[str] = None


class FleetDriverAllocationUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fleet_org_id_fk: Optional[int] = None
    fleet_driver_allocation_id: Optional[int] = None
    fleet_driver_allocation_id_pk: Optional[int] = None
    fleet_vehicle_id_fk: Optional[int] = None
    fleet_driver_empl_id_fk: Optional[int] = None
    allocation_start_datetime: Optional[str] = None
    allocation_end_datetime: Optional[str] = None
    allocation_status: Optional[str] = None
    route_id_fk: Optional[int] = None
    shift_id_fk: Optional[int] = None
    allocation_reason: Optional[str] = None
    deallocation_reason: Optional[str] = None


class FleetDriverAllocationDeletePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fleet_org_id_fk: Optional[int] = None
    fleet_driver_allocation_id: Optional[int] = None
    fleet_driver_allocation_id_pk: Optional[int] = None


class FleetStatusHistoryCreatePayload(BaseModel):
    fleet_org_id_fk: int
    fleet_vehicle_id_fk: int
    previous_vehicle_status: Optional[str] = None
    new_vehicle_status: str
    status_effective_from: Optional[str] = None
    status_effective_to: Optional[str] = None
    status_change_source: Optional[str] = "manual"
    status_change_reason: Optional[str] = None
    status_change_remarks: Optional[str] = None
    related_maintenance_order_id_fk: Optional[int] = None
    changed_by: Optional[int] = None
    changed_at: Optional[str] = None
    field_flex_field_1: Optional[str] = None
    field_flex_field_2: Optional[str] = None
    field_flex_field_3: Optional[str] = None
    field_flex_field_4: Optional[str] = None
    created_by: Optional[int] = None
    updated_by: Optional[int] = None


class FleetStatusHistoryUpdatePayload(FleetStatusHistoryCreatePayload):
    fleet_status_history_id: Optional[int] = None
    fleet_status_history_id_pk: Optional[int] = None
    fleet_vehicle_id: Optional[int] = None


class FleetStatusHistoryDeletePayload(BaseModel):
    fleet_status_history_id: Optional[int] = None
    fleet_status_history_id_pk: Optional[int] = None
    fleet_vehicle_id: Optional[int] = None
    fleet_vehicle_id_fk: Optional[int] = None


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


@app.post("/api/v1/fleet-master/create", tags=["Fleet Master"])
def create_fleet_master_endpoint(
    payload: FleetMasterCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_master_create_data.py
    return handle_logic_call(
        create_fleet_vehicle,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
            principal_fields=("user_principal_name",),
            audit_actor="user_id",
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/fleet-master/update", tags=["Fleet Master"])
def update_fleet_master_endpoint(
    payload: FleetMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_master_update_data.py
    return handle_logic_call(
        update_fleet_vehicle,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
            principal_fields=("user_principal_name",),
            audit_actor="user_id",
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/fleet-master/delete", tags=["Fleet Master"])
def delete_fleet_master_endpoint(
    payload: FleetMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_master_delete_data.py
    return handle_logic_call(
        delete_fleet_vehicle,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
            principal_fields=("user_principal_name",),
        ),
    )


@app.get("/api/v1/fleet-master", tags=["Fleet Master"])
def get_fleet_master_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_master_get_data.py
    return handle_logic_call(
        get_fleet_vehicle,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("fleet_org_id_fk",),
        ),
    )


@app.post("/api/v1/fleet-driver-allocations/create", tags=["Fleet Driver Allocation"])
def create_driver_allocation_endpoint(
    payload: FleetDriverAllocationCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_driver_allocation_create_data.py
    return handle_logic_call(
        create_fleet_driver_allocation,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
            audit_actor="user_id",
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/fleet-driver-allocations/update", tags=["Fleet Driver Allocation"])
def update_driver_allocation_endpoint(
    payload: FleetDriverAllocationUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_driver_allocation_update_data.py
    return handle_logic_call(
        update_fleet_driver_allocation,
        bind_authenticated_payload(
            payload.model_dump(exclude_unset=True),
            auth_context,
            org_fields=("fleet_org_id_fk",),
            audit_actor="user_id",
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/fleet-driver-allocations/delete", tags=["Fleet Driver Allocation"])
def delete_driver_allocation_endpoint(
    payload: FleetDriverAllocationDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_driver_allocation_delete_data.py
    return handle_logic_call(
        delete_fleet_driver_allocation,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
        ),
    )


@app.get("/api/v1/fleet-driver-allocations", tags=["Fleet Driver Allocation"])
def get_driver_allocations_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_driver_allocation_get_data.py
    return handle_logic_call(
        get_fleet_driver_allocation,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("fleet_org_id_fk",),
        ),
    )


@app.post("/api/v1/fleet-status-history/create", tags=["Fleet Status History"])
def create_status_history_endpoint(
    payload: FleetStatusHistoryCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_status_history_create_data.py
    return handle_logic_call(
        create_fleet_status_history,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
            audit_actor="user_id",
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/fleet-status-history/update", tags=["Fleet Status History"])
def update_status_history_endpoint(
    payload: FleetStatusHistoryUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_status_history_update_data.py
    return handle_logic_call(
        update_fleet_status_history,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
            audit_actor="user_id",
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/fleet-status-history/delete", tags=["Fleet Status History"])
def delete_status_history_endpoint(
    payload: FleetStatusHistoryDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_status_history_delete_data.py
    return handle_logic_call(
        delete_fleet_status_history,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("fleet_org_id_fk",),
        ),
    )


@app.get("/api/v1/fleet-status-history", tags=["Fleet Status History"])
def get_status_history_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    # Calls logic/fleet_status_history_get_data.py
    return handle_logic_call(
        get_fleet_status_history,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("fleet_org_id_fk",),
        ),
    )


@app.get("/api/v1/fleet-master/{fleet_vehicle_id_pk}", tags=["Fleet Master"])
def get_fleet_master_by_id_endpoint(
    fleet_vehicle_id_pk: int = ApiPath(gt=0),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_fleet_vehicle_by_id,
        bind_authenticated_payload(
            {"fleet_vehicle_id_pk": fleet_vehicle_id_pk}, auth_context, org_fields=("fleet_org_id_fk",),
        ),
    )


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_03_fleet_management.api.main:app",
        host="0.0.0.0",
        port=8003,
        reload=True,
    )
