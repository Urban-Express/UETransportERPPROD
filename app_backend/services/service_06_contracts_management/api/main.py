from pathlib import Path
import json
import sys
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, Security, UploadFile
from fastapi import Path as ApiPath
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError


BASE_DIR = Path(__file__).resolve().parent
LOGIC_DIR = BASE_DIR.parent / "logic"
if str(LOGIC_DIR) not in sys.path:
    sys.path.insert(0, str(LOGIC_DIR))

from app_backend.services.service_06_contracts_management.logic.customer_master_create_data import create_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_get_data import get_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_update_data import update_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_delete_data import delete_customer_master
from app_backend.services.service_06_contracts_management.logic.contracts_management_create_data import create_contract
from app_backend.services.service_06_contracts_management.logic.contracts_management_get_data import get_contract, get_contract_by_id
from app_backend.services.service_06_contracts_management.logic.contracts_management_update_data import update_contract
from app_backend.services.service_06_contracts_management.logic.contracts_management_delete_data import delete_contract
from app_backend.services.service_06_contracts_management.integrations.firebase_contract_document_upload import upload_contract_document
from app_backend.services.service_06_contracts_management.integrations.firebase_contract_document_download import download_contract_document
from app_backend.services.auth_context import (
    bind_authenticated_payload,
    get_authenticated_context,
    raise_for_logic_error,
)


app = FastAPI(
    title="UETransportERP Contracts Management API",
    description="REST API for customer master and contracts management.",
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


class CustomerMasterPayload(BaseModel):
    cust_org_id_fk: int
    cust_code: str
    cust_name: str
    cust_category: str
    cust_status: Optional[str] = "ACTIVE"
    cust_contact_person_name: Optional[str] = None
    cust_contact_person_designation: Optional[str] = None
    cust_phone_primary: Optional[str] = None
    cust_phone_secondary: Optional[str] = None
    cust_email_primary: Optional[str] = None
    cust_email_secondary: Optional[str] = None
    cust_billing_address: Optional[str] = None
    cust_service_address: Optional[str] = None
    cust_tax_registration_number: Optional[str] = None
    cust_credit_period_days: Optional[int] = 0
    cust_notes: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class CustomerMasterUpdatePayload(CustomerMasterPayload):
    cust_id: Optional[int] = None
    cust_id_pk: Optional[int] = None


class CustomerMasterDeletePayload(BaseModel):
    cust_id: Optional[int] = None
    cust_id_pk: Optional[int] = None


class ContractsManagementPayload(BaseModel):
    user_principal_name: str
    cont_org_id_fk: int
    cont_cust_id_fk: int
    cont_dep_id_fk: int
    cont_contract_number: str
    cont_contract_name: Optional[str] = None
    cont_start_date: str
    cont_end_date: Optional[str] = None
    cont_status: Optional[str] = "DRAFT"
    cont_revenue_basis: str
    cont_currency_code: Optional[str] = "AED"
    cont_no_of_passengers: Optional[int] = 0
    cont_big_bus_count_gt_34: Optional[int] = 0
    cont_medium_bus_count_17_34: Optional[int] = 0
    cont_small_bus_count_lt_17: Optional[int] = 0
    cont_per_passenger_rate_pm: Optional[float] = None
    cont_big_bus_rate_pm: Optional[float] = None
    cont_medium_bus_rate_pm: Optional[float] = None
    cont_small_bus_rate_pm: Optional[float] = None
    cont_no_of_billing_months: int
    cont_no_of_work_days_per_week: int
    cont_no_of_round_trips_per_day: int
    cont_driver_responsibility_party: Optional[str] = "NOT_SPECIFIED"
    cont_driver_accommodation_resp_party: Optional[str] = "NOT_SPECIFIED"
    cont_fuel_responsibility_party: Optional[str] = "NOT_SPECIFIED"
    cont_salik_responsibility_party: Optional[str] = "NOT_SPECIFIED"
    cont_permit_responsibility_party: Optional[str] = "NOT_SPECIFIED"
    cont_no_of_free_trips_per_month: Optional[int] = 0
    cont_extra_trip_charge: Optional[float] = 0
    cont_km_cap_pm_per_bus: Optional[float] = None
    cont_extra_km_charge_per_km: Optional[float] = None
    total_contract_value: Optional[float] = None
    cont_notes: Optional[str] = None
    cont_link_path: Optional[str] = None
    cont_approval_status: Optional[str] = None
    file_path: Optional[str] = None
    contract_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class ContractsManagementUpdatePayload(ContractsManagementPayload):
    cont_id: Optional[int] = None
    cont_id_pk: Optional[int] = None


class ContractsManagementDeletePayload(BaseModel):
    cont_id: Optional[int] = None
    cont_id_pk: Optional[int] = None


class ContractDocumentUploadPayload(BaseModel):
    cont_id: Optional[int] = None
    cont_id_pk: Optional[int] = None
    cont_contract_number: Optional[str] = None
    cont_org_id_fk: Optional[int] = None
    file_path: Optional[str] = None
    contract_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    updated_by: Optional[str] = None
    created_by: Optional[str] = None


class ContractDocumentDownloadPayload(BaseModel):
    cont_id: Optional[int] = None
    cont_id_pk: Optional[int] = None
    cont_contract_number: Optional[str] = None
    cont_org_id_fk: Optional[int] = None


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


def handle_logic_file_call(
    func: Callable[..., Any],
    payload: dict[str, Any],
    file: Optional[UploadFile] = None,
) -> dict[str, Any]:
    try:
        if file is None:
            result = func(payload)
        else:
            result = func(payload, file.file, file.filename, file.content_type)
        raise_for_logic_error(result)
        return success_response(_to_json_safe(result))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def parse_json_form_payload(
    payload_json: str,
    payload_model: type[BaseModel],
) -> BaseModel:
    try:
        payload_data = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="payload must be valid JSON.",
        ) from exc

    if not isinstance(payload_data, dict):
        raise HTTPException(
            status_code=422,
            detail="payload must be a JSON object.",
        )

    try:
        if hasattr(payload_model, "model_validate"):
            return payload_model.model_validate(payload_data)
        return payload_model.parse_obj(payload_data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@app.post("/api/v1/customers/create", tags=["Customer Master"])
def create_customer_endpoint(
    payload: CustomerMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_customer_master,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cust_org_id_fk",),
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/customers/update", tags=["Customer Master"])
def update_customer_endpoint(
    payload: CustomerMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_customer_master,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cust_org_id_fk",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/customers/delete", tags=["Customer Master"])
def delete_customer_endpoint(
    payload: CustomerMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_customer_master,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cust_org_id_fk",),
        ),
    )


@app.get("/api/v1/customers", tags=["Customer Master"])
def get_customer_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_customer_master,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("cust_org_id_fk",),
        ),
    )


@app.post("/api/v1/contracts/create", tags=["Contracts Management"])
def create_contract_endpoint(
    payload: ContractsManagementPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_contract,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cont_org_id_fk",),
            principal_fields=("user_principal_name",),
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/contracts/create-with-document", tags=["Contracts Management"])
def create_contract_with_document_endpoint(
    payload_json: str = Form(
        ...,
        alias="payload",
        description="JSON object matching ContractsManagementPayload.",
    ),
    file: Optional[UploadFile] = File(
        None,
        description="Optional contract document to stage for workflow approval.",
    ),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = parse_json_form_payload(payload_json, ContractsManagementPayload)
    return handle_logic_file_call(
        create_contract,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cont_org_id_fk",),
            principal_fields=("user_principal_name",),
            set_created_by=True,
            set_updated_by=True,
        ),
        file,
    )


@app.post("/api/v1/contracts/update", tags=["Contracts Management"])
def update_contract_endpoint(
    payload: ContractsManagementUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_contract,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cont_org_id_fk",),
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/contracts/update-with-document", tags=["Contracts Management"])
def update_contract_with_document_endpoint(
    payload_json: str = Form(
        ...,
        alias="payload",
        description="JSON object matching ContractsManagementUpdatePayload.",
    ),
    file: Optional[UploadFile] = File(
        None,
        description="Optional contract document to stage for workflow approval.",
    ),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = parse_json_form_payload(payload_json, ContractsManagementUpdatePayload)
    return handle_logic_file_call(
        update_contract,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cont_org_id_fk",),
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
        file,
    )


@app.post("/api/v1/contracts/delete", tags=["Contracts Management"])
def delete_contract_endpoint(
    payload: ContractsManagementDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_contract,
        bind_authenticated_payload(
            payload,
            auth_context,
            org_fields=("cont_org_id_fk",),
        ),
    )


@app.get("/api/v1/contracts", tags=["Contracts Management"])
def get_contract_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_contract,
        bind_authenticated_payload(
            {},
            auth_context,
            org_fields=("cont_org_id_fk",),
        ),
    )


@app.post("/api/v1/contracts/documents/upload", tags=["Contract Document Integration"])
def upload_contract_document_endpoint(
    cont_id: Optional[int] = Form(None),
    cont_id_pk: Optional[int] = Form(None),
    cont_contract_number: Optional[str] = Form(None),
    cont_org_id_fk: Optional[int] = Form(None),
    updated_by: Optional[str] = Form(None),
    created_by: Optional[str] = Form(None),
    storage_folder: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),
    file: UploadFile = File(...),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = {
        "cont_id": cont_id,
        "cont_id_pk": cont_id_pk,
        "cont_contract_number": cont_contract_number,
        "cont_org_id_fk": cont_org_id_fk,
        "updated_by": updated_by,
        "created_by": created_by,
        "storage_folder": storage_folder,
        "content_type": content_type,
    }
    payload = bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("cont_org_id_fk",),
        set_updated_by=True,
    )
    return handle_integration_result(
        upload_contract_document(payload, file.file, file.filename, file.content_type)
    )


@app.post("/api/v1/contracts/documents/download", tags=["Contract Document Integration"])
def download_contract_document_endpoint(
    payload: ContractDocumentDownloadPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_integration_result(
        download_contract_document(
            bind_authenticated_payload(
                payload,
                auth_context,
                org_fields=("cont_org_id_fk",),
            )
        )
    )


@app.get("/api/v1/contracts/{cont_id_pk}", tags=["Contracts Management"])
def get_contracts_management_by_id_endpoint(
    cont_id_pk: int = ApiPath(gt=0),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_contract_by_id,
        bind_authenticated_payload(
            {"cont_id_pk": cont_id_pk}, auth_context, org_fields=("cont_org_id_fk",),
        ),
    )


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_06_contracts_management.api.main:app",
        host="0.0.0.0",
        port=8006,
        reload=True,
    )
