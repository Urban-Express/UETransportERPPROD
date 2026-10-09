from pathlib import Path
from decimal import Decimal
import json
import sys
from typing import Any, Callable, Optional
from typing import Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, Security, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


BASE_DIR = Path(__file__).resolve().parent
LOGIC_DIR = BASE_DIR.parent / "logic"
if str(LOGIC_DIR) not in sys.path:
    sys.path.insert(0, str(LOGIC_DIR))

from app_backend.services.service_08_financial_management.logic.asset_master_create_data import create_asset
from app_backend.services.service_08_financial_management.logic.asset_master_get_data import get_asset
from app_backend.services.service_08_financial_management.logic.asset_master_update_data import update_asset
from app_backend.services.service_08_financial_management.logic.asset_master_delete_data import delete_asset
from app_backend.services.service_08_financial_management.logic.asset_master_depreciation_run import (
    run_asset_depreciation,
)
from app_backend.services.service_08_financial_management.logic.supplier_master_create_data import (
    create_supplier,
)
from app_backend.services.service_08_financial_management.logic.supplier_master_get_data import (
    get_supplier,
)
from app_backend.services.service_08_financial_management.logic.supplier_master_update_data import (
    update_supplier,
)
from app_backend.services.service_08_financial_management.logic.supplier_master_delete_data import (
    delete_supplier,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_create_data import (
    create_accounts_payable,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_get_data import (
    get_accounts_payable,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_update_data import (
    update_accounts_payable,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_delete_data import (
    delete_accounts_payable,
)
from app_backend.services.service_08_financial_management.integrations.firebase_ap_invoice_document_upload import (
    upload_ap_invoice_document,
)
from app_backend.services.service_08_financial_management.integrations.firebase_ap_invoice_document_download import (
    download_ap_invoice_document,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_create_data import (
    create_accounts_receivable,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_get_data import (
    get_accounts_receivable,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_update_data import (
    update_accounts_receivable,
    update_ar_collection_status,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_mark_received import (
    mark_ar_invoice_received,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_delete_data import (
    delete_accounts_receivable,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_contract_prefill import get_accounts_receivables_contract_prefill
from app_backend.services.service_08_financial_management.logic.accounts_receivables_configuration import get_accounts_receivables_tax_options
from app_backend.services.service_08_financial_management.integrations.firebase_ar_invoice_document_upload import (
    upload_ar_invoice_document,
)
from app_backend.services.service_08_financial_management.integrations.firebase_ar_invoice_document_download import (
    download_ar_invoice_document,
)
from app_backend.services.auth_context import (
    bind_authenticated_payload,
    get_authenticated_context,
    raise_for_logic_error,
)


app = FastAPI(
    title="UETransportERP Financial Management API",
    description="REST API for financial management.",
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


class AssetMasterPayload(BaseModel):
    user_principal_name: str
    asset_org_id_fk: int
    asset_location: Optional[str] = None
    asset_type: str
    asset_name: str
    asset_acquisition_date: Optional[str] = None
    depreciation_start_date: Optional[str] = None
    asset_acquisition_cost: Optional[float] = None
    asset_useful_life: Optional[float] = None
    asset_salvage_value: Optional[float] = None
    asset_nbv: Optional[float] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class AssetMasterUpdatePayload(AssetMasterPayload):
    asset_id: Optional[int] = None
    asset_id_pk: Optional[int] = None


class AssetMasterDeletePayload(BaseModel):
    user_principal_name: str
    asset_id: Optional[int] = None
    asset_id_pk: Optional[int] = None


class AssetDepreciationRunPayload(BaseModel):
    asset_id: Optional[int] = None
    asset_id_pk: Optional[int] = None
    run_date: Optional[str] = None
    user_principal_name: Optional[str] = None
    updated_by: Optional[str] = None


class SupplierMasterPayload(BaseModel):
    supp_org_id_fk: int
    supp_code: str
    supp_name: str
    supp_category: str
    supp_status: Optional[
        Literal["ACTIVE", "INACTIVE", "SUSPENDED", "BLACKLISTED"]
    ] = "ACTIVE"
    supp_contact_person_name: Optional[str] = None
    supp_contact_person_designation: Optional[str] = None
    supp_phone_primary: Optional[str] = None
    supp_phone_secondary: Optional[str] = None
    supp_email_primary: Optional[str] = None
    supp_email_secondary: Optional[str] = None
    supp_billing_address: Optional[str] = None
    supp_service_address: Optional[str] = None
    supp_tax_registration_number: Optional[str] = None
    supp_credit_period_days: Optional[int] = 0
    supp_notes: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class SupplierMasterUpdatePayload(SupplierMasterPayload):
    supp_id: Optional[int] = None
    supp_id_pk: Optional[int] = None


class SupplierMasterDeletePayload(BaseModel):
    supp_id: Optional[int] = None
    supp_id_pk: Optional[int] = None


class AccountsPayablePayload(BaseModel):
    user_principal_name: str
    ap_org_id_fk: int
    ap_supp_id_fk: int
    ap_invoice_number: str
    ap_invoice_date: str
    ap_due_date: Optional[str] = None
    ap_description: Optional[str] = None
    ap_currency_code: str
    ap_invoice_amount: float
    ap_tax_amount: Optional[float] = 0
    ap_paid_amount: Optional[float] = 0
    ap_approval_status: Optional[str] = "DRAFT"
    ap_payment_status: Optional[str] = "UNPAID"
    ap_approval_comments: Optional[str] = None
    ap_approved_by: Optional[str] = None
    ap_approved_at: Optional[str] = None
    ap_notes: Optional[str] = None
    ap_invoice_file_path: Optional[str] = None
    file_path: Optional[str] = None
    ap_invoice_local_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class AccountsPayableUpdatePayload(AccountsPayablePayload):
    ap_id: Optional[int] = None
    ap_id_pk: Optional[int] = None


class AccountsPayableDeletePayload(BaseModel):
    user_principal_name: str
    ap_id: Optional[int] = None
    ap_id_pk: Optional[int] = None


class AccountsPayableGetPayload(BaseModel):
    ap_id: Optional[int] = None
    ap_id_pk: Optional[int] = None
    ap_org_id_fk: Optional[int] = None
    ap_supp_id_fk: Optional[int] = None
    ap_approval_status: Optional[str] = None
    ap_payment_status: Optional[str] = None


class AccountsPayableDocumentUploadPayload(BaseModel):
    user_principal_name: Optional[str] = None
    ap_id: Optional[int] = None
    ap_id_pk: Optional[int] = None
    ap_invoice_number: Optional[str] = None
    ap_org_id_fk: Optional[int] = None
    ap_supp_id_fk: Optional[int] = None
    file_path: Optional[str] = None
    ap_invoice_file_path: Optional[str] = None
    ap_invoice_local_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    updated_by: Optional[str] = None
    created_by: Optional[str] = None


class AccountsPayableDocumentDownloadPayload(BaseModel):
    ap_id: Optional[int] = None
    ap_id_pk: Optional[int] = None
    ap_invoice_number: Optional[str] = None
    ap_org_id_fk: Optional[int] = None
    ap_supp_id_fk: Optional[int] = None


class AccountsReceivableLinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ar_line_id_pk: Optional[int] = Field(default=None, gt=0)
    ar_line_number: Optional[int] = Field(default=None, gt=0)
    ar_line_source_contract_id_fk: Optional[int] = Field(default=None, gt=0)
    ar_line_description: str = Field(min_length=1)
    ar_line_vehicle_description: Optional[str] = None
    ar_line_quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False)
    ar_line_uom: Optional[str] = Field(default=None, max_length=40)
    ar_line_unit_rate: Decimal = Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    ar_line_service_period_start: Optional[str] = None
    ar_line_service_period_end: Optional[str] = None
    ar_line_proration_method: Optional[Literal["CALENDAR_DAYS"]] = None
    ar_line_tax_code: Optional[str] = Field(default=None, max_length=40)
    ar_line_tax_rate: Optional[Decimal] = Field(default=None, ge=0, max_digits=9, decimal_places=4, allow_inf_nan=False)
    ar_line_notes: Optional[str] = None


class AccountsReceivableTaxOptionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ar_org_id_fk: Optional[int] = Field(default=None, gt=0)
    ar_invoice_date: str


class AccountsReceivableContractPrefillPayload(AccountsReceivableTaxOptionsPayload):
    ar_cust_id_fk: int = Field(gt=0)
    ar_contract_id_fk: int = Field(gt=0)
    ar_billing_period_start: str
    ar_billing_period_end: str


class AccountsReceivablePayload(BaseModel):
    # Preserve legacy callers' historical unknown-field handling. Line-mode inputs
    # are strict so posted calculation/snapshot fields cannot disappear silently.
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def reject_unknown_line_header_fields(cls, value):
        if isinstance(value, dict) and "lines" in value:
            unknown = set(value) - set(cls.model_fields)
            if unknown:
                raise ValueError("Invalid line invoice header fields: " + ", ".join(sorted(unknown)))
        return value
    user_principal_name: str
    ar_org_id_fk: int
    ar_cust_id_fk: int
    ar_invoice_number: str
    ar_invoice_date: str
    ar_due_date: Optional[str] = None
    ar_contract: Optional[str] = None
    ar_description: Optional[str] = None
    ar_currency_code: str
    # Gross includes tax. Required only for transitional header-only payloads.
    ar_invoice_amount: Optional[Decimal] = None
    ar_tax_amount: Optional[Decimal] = Decimal(0)
    ar_received_amount: Optional[Decimal] = Decimal(0)
    ar_contract_id_fk: Optional[int] = Field(default=None, gt=0)
    ar_billing_period_start: Optional[str] = None
    ar_billing_period_end: Optional[str] = None
    lines: Optional[list[AccountsReceivableLinePayload]] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)
    ar_approval_status: Optional[
        Literal["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "CANCELLED"]
    ] = Field(default="DRAFT", description="Legacy field. In line mode requester values are stripped; final status is workflow-owned.")
    ar_collection_status: Optional[
        Literal["OUTSTANDING", "PARTIALLY_RECEIVED", "RECEIVED"]
    ] = "OUTSTANDING"
    ar_approval_comments: Optional[str] = None
    ar_approved_by: Optional[str] = Field(default=None, description="Legacy field. In line mode requester values are stripped; the final workflow approver is persisted.")
    ar_approved_at: Optional[str] = Field(default=None, description="Legacy field. In line mode requester values are stripped; approved execution uses database CURRENT_TIMESTAMP.")
    ar_notes: Optional[str] = None
    ar_invoice_file_path: Optional[str] = None
    file_path: Optional[str] = None
    ar_invoice_local_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class AccountsReceivableUpdatePayload(AccountsReceivablePayload):
    ar_id: Optional[int] = None
    ar_id_pk: Optional[int] = None
    ar_expected_revision: Optional[int] = Field(default=None, ge=0)


class AccountsReceivableCollectionStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ar_id_pk: int = Field(gt=0)
    ar_org_id_fk: int = Field(gt=0)
    ar_collection_status: Literal["OUTSTANDING", "PARTIALLY_RECEIVED", "RECEIVED"]
    ar_expected_revision: int = Field(ge=0)


class AccountsReceivableMarkReceivedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ar_id_pk: int = Field(gt=0)
    ar_org_id_fk: int = Field(gt=0)
    ar_expected_revision: int = Field(ge=0)


class AccountsReceivableDeletePayload(BaseModel):
    user_principal_name: str
    ar_id: Optional[int] = None
    ar_id_pk: Optional[int] = None


class AccountsReceivableGetPayload(BaseModel):
    ar_id: Optional[int] = None
    ar_id_pk: Optional[int] = None
    ar_org_id_fk: Optional[int] = None
    ar_cust_id_fk: Optional[int] = None
    ar_approval_status: Optional[str] = None
    ar_collection_status: Optional[str] = None
    ar_contract_id_fk: Optional[int] = None
    include_lines: bool = False


class AccountsReceivableDocumentUploadPayload(BaseModel):
    """Existing legacy document upload requires ar_id or ar_id_pk, not invoice number alone.

    The public upload route uses individual multipart fields; its integration enforces the ID requirement.
    """
    user_principal_name: str
    ar_id: Optional[int] = None
    ar_id_pk: Optional[int] = None
    ar_invoice_number: Optional[str] = None
    ar_org_id_fk: Optional[int] = None
    file_path: Optional[str] = None
    ar_invoice_file_path: Optional[str] = None
    ar_invoice_local_file_path: Optional[str] = None
    storage_folder: Optional[str] = None
    content_type: Optional[str] = None
    updated_by: Optional[str] = None
    created_by: Optional[str] = None


class AccountsReceivableDocumentDownloadPayload(BaseModel):
    """Integration requires ar_id or ar_id_pk; ar_invoice_number alone cannot resolve a document.

    Both ID aliases remain optional individually for compatibility; at least one is required at execution.
    """
    ar_id: Optional[int] = None
    ar_id_pk: Optional[int] = None
    ar_invoice_number: Optional[str] = None
    ar_org_id_fk: Optional[int] = None


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
    if "blocked by workflow" in normalized:
        return 403
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


def bind_asset_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("asset_org_id_fk",),
        principal_fields=("user_principal_name",),
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


def bind_supplier_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("supp_org_id_fk",),
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


def bind_ap_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("ap_org_id_fk",),
        principal_fields=("user_principal_name",),
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


def bind_ar_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    if isinstance(payload, AccountsReceivablePayload):
        fields = payload.model_fields_set
        values = payload.model_dump()
        if "lines" not in fields:
            values.pop("lines", None)
            # Retain the legacy numeric workflow/API boundary and absent-field
            # shape; new line calculations never use these float values.
            for key in ("ar_invoice_amount", "ar_tax_amount", "ar_received_amount"):
                if isinstance(values.get(key), Decimal):
                    values[key] = float(values[key])
            for key in ("ar_contract_id_fk", "ar_billing_period_start", "ar_billing_period_end", "idempotency_key", "ar_expected_revision"):
                if key not in fields:
                    values.pop(key, None)
        elif payload.lines is not None:
            values["lines"] = [line.model_dump(exclude_unset=True) for line in payload.lines]
            for key in ("ar_invoice_amount", "ar_tax_amount"):
                if key not in fields:
                    values.pop(key, None)
        payload = values
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("ar_org_id_fk",),
        principal_fields=("user_principal_name",),
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


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


@app.post("/api/v1/assets/create", tags=["Asset Master"])
def create_asset_endpoint(
    payload: AssetMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_asset,
        bind_asset_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/assets/update", tags=["Asset Master"])
def update_asset_endpoint(
    payload: AssetMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_asset,
        bind_asset_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/assets/delete", tags=["Asset Master"])
def delete_asset_endpoint(
    payload: AssetMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_asset,
        bind_asset_payload(payload, auth_context),
    )


@app.get("/api/v1/assets", tags=["Asset Master"])
def get_asset_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_asset,
        bind_asset_payload({}, auth_context),
    )


@app.post("/api/v1/assets/depreciation/run", tags=["Asset Master"])
def run_asset_depreciation_endpoint(
    payload: AssetDepreciationRunPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        run_asset_depreciation,
        bind_asset_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/suppliers/create", tags=["Supplier Master"])
def create_supplier_endpoint(
    payload: SupplierMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_supplier,
        bind_supplier_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/suppliers/update", tags=["Supplier Master"])
def update_supplier_endpoint(
    payload: SupplierMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_supplier,
        bind_supplier_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/suppliers/delete", tags=["Supplier Master"])
def delete_supplier_endpoint(
    payload: SupplierMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_supplier,
        bind_supplier_payload(payload, auth_context),
    )


@app.get("/api/v1/suppliers", tags=["Supplier Master"])
def get_supplier_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_supplier,
        bind_supplier_payload({}, auth_context),
    )


@app.post("/api/v1/accounts-payables/create", tags=["Accounts Payable"])
def create_accounts_payable_endpoint(
    payload: AccountsPayablePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_accounts_payable,
        bind_ap_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/accounts-payables/create-with-document", tags=["Accounts Payable"])
def create_accounts_payable_with_document_endpoint(
    payload_json: str = Form(
        ...,
        alias="payload",
        description="JSON object matching AccountsPayablePayload.",
    ),
    file: Optional[UploadFile] = File(
        None,
        description="Optional AP invoice document to stage for workflow approval.",
    ),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = parse_json_form_payload(payload_json, AccountsPayablePayload)
    return handle_logic_file_call(
        create_accounts_payable,
        bind_ap_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
        file,
    )


@app.post("/api/v1/accounts-payables/update", tags=["Accounts Payable"])
def update_accounts_payable_endpoint(
    payload: AccountsPayableUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_accounts_payable,
        bind_ap_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/accounts-payables/update-with-document", tags=["Accounts Payable"])
def update_accounts_payable_with_document_endpoint(
    payload_json: str = Form(
        ...,
        alias="payload",
        description="JSON object matching AccountsPayableUpdatePayload.",
    ),
    file: Optional[UploadFile] = File(
        None,
        description="Optional AP invoice document to stage for workflow approval.",
    ),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = parse_json_form_payload(payload_json, AccountsPayableUpdatePayload)
    return handle_logic_file_call(
        update_accounts_payable,
        bind_ap_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
        file,
    )


@app.post("/api/v1/accounts-payables/delete", tags=["Accounts Payable"])
def delete_accounts_payable_endpoint(
    payload: AccountsPayableDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_accounts_payable,
        bind_ap_payload(payload, auth_context),
    )


@app.get("/api/v1/accounts-payables", tags=["Accounts Payable"])
def get_all_accounts_payable_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_payable,
        bind_ap_payload({}, auth_context),
    )


@app.post("/api/v1/accounts-payables", tags=["Accounts Payable"])
def get_accounts_payable_endpoint(
    payload: AccountsPayableGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_payable,
        bind_ap_payload(payload, auth_context),
    )


@app.post("/api/v1/accounts-payables/documents/upload", tags=["Accounts Payable Document Integration"])
def upload_accounts_payable_document_endpoint(
    user_principal_name: Optional[str] = Form(None),
    ap_id: Optional[int] = Form(None),
    ap_id_pk: Optional[int] = Form(None),
    ap_invoice_number: Optional[str] = Form(None),
    ap_org_id_fk: Optional[int] = Form(None),
    ap_supp_id_fk: Optional[int] = Form(None),
    updated_by: Optional[str] = Form(None),
    created_by: Optional[str] = Form(None),
    storage_folder: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),
    file: UploadFile = File(...),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = {
        "user_principal_name": user_principal_name,
        "ap_id": ap_id,
        "ap_id_pk": ap_id_pk,
        "ap_invoice_number": ap_invoice_number,
        "ap_org_id_fk": ap_org_id_fk,
        "ap_supp_id_fk": ap_supp_id_fk,
        "updated_by": updated_by,
        "created_by": created_by,
        "storage_folder": storage_folder,
        "content_type": content_type,
    }
    payload = bind_ap_payload(
        payload,
        auth_context,
        set_updated_by=True,
    )
    return handle_integration_result(
        upload_ap_invoice_document(payload, file.file, file.filename, file.content_type)
    )


@app.post("/api/v1/accounts-payables/documents/download", tags=["Accounts Payable Document Integration"])
def download_accounts_payable_document_endpoint(
    payload: AccountsPayableDocumentDownloadPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_integration_result(
        download_ap_invoice_document(bind_ap_payload(payload, auth_context))
    )


@app.post("/api/v1/accounts-receivables/contract-prefill", tags=["Accounts Receivable"])
def accounts_receivable_contract_prefill_endpoint(
    payload: AccountsReceivableContractPrefillPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(get_accounts_receivables_contract_prefill, bind_ar_payload(payload, auth_context))


@app.post("/api/v1/accounts-receivables/tax-options", tags=["Accounts Receivable"])
def accounts_receivable_tax_options_endpoint(
    payload: AccountsReceivableTaxOptionsPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(get_accounts_receivables_tax_options, bind_ar_payload(payload, auth_context))


@app.post("/api/v1/accounts-receivables/create", tags=["Accounts Receivable"])
def create_accounts_receivable_endpoint(
    payload: AccountsReceivablePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_accounts_receivable,
        bind_ar_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/accounts-receivables/create-with-document", tags=["Accounts Receivable"])
def create_accounts_receivable_with_document_endpoint(
    payload_json: str = Form(
        ...,
        alias="payload",
        description="JSON object matching AccountsReceivablePayload.",
    ),
    file: Optional[UploadFile] = File(
        None,
        description="Optional AR invoice document to stage for workflow approval.",
    ),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = parse_json_form_payload(payload_json, AccountsReceivablePayload)
    return handle_logic_file_call(
        create_accounts_receivable,
        bind_ar_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
        file,
    )


@app.post("/api/v1/accounts-receivables/update", tags=["Accounts Receivable"])
def update_accounts_receivable_endpoint(
    payload: AccountsReceivableUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_accounts_receivable,
        bind_ar_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/accounts-receivables/collection-status/update", tags=["Accounts Receivable"])
def update_ar_collection_status_endpoint(
    payload: AccountsReceivableCollectionStatusPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> Any:
    result = update_ar_collection_status(
        bind_ar_payload(payload, auth_context, set_updated_by=True)
    )
    if result.get("error"):
        return JSONResponse(
            status_code=result["status_code"],
            content={"success": False, "error": result["error"], "error_code": result["error_code"]},
        )
    return success_response(_to_json_safe(result))


@app.post("/api/v1/accounts-receivables/mark-received", tags=["Accounts Receivable"])
def mark_ar_invoice_received_endpoint(
    payload: AccountsReceivableMarkReceivedPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> Any:
    try:
        bound_payload = bind_ar_payload(payload, auth_context, set_updated_by=True)
    except HTTPException as exc:
        # This strict model has no client principal/audit fields, so a binding
        # 403 denotes the submitted organization failing the existing org check.
        if exc.status_code != 403:
            raise
        return JSONResponse(
            status_code=403,
            content={"success": False, "error": exc.detail, "error_code": "AR_ORGANIZATION_MISMATCH"},
        )
    result = mark_ar_invoice_received(bound_payload)
    if result.get("error"):
        return JSONResponse(
            status_code=result["status_code"],
            content={"success": False, "error": result["error"], "error_code": result["error_code"]},
        )
    return success_response(_to_json_safe(result))


@app.post("/api/v1/accounts-receivables/update-with-document", tags=["Accounts Receivable"])
def update_accounts_receivable_with_document_endpoint(
    payload_json: str = Form(
        ...,
        alias="payload",
        description="JSON object matching AccountsReceivableUpdatePayload.",
    ),
    file: Optional[UploadFile] = File(
        None,
        description="Optional AR invoice document to stage for workflow approval.",
    ),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = parse_json_form_payload(payload_json, AccountsReceivableUpdatePayload)
    return handle_logic_file_call(
        update_accounts_receivable,
        bind_ar_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
        file,
    )


@app.post("/api/v1/accounts-receivables/delete", tags=["Accounts Receivable"])
def delete_accounts_receivable_endpoint(
    payload: AccountsReceivableDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_accounts_receivable,
        bind_ar_payload(payload, auth_context),
    )


@app.get("/api/v1/accounts-receivables", tags=["Accounts Receivable"])
def get_all_accounts_receivable_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_receivable,
        bind_ar_payload({}, auth_context),
    )


@app.post("/api/v1/accounts-receivables", tags=["Accounts Receivable"])
def get_accounts_receivable_endpoint(
    payload: AccountsReceivableGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_accounts_receivable,
        bind_ar_payload(payload, auth_context),
    )


@app.post("/api/v1/accounts-receivables/documents/upload", tags=["Accounts Receivable Document Integration"])
def upload_accounts_receivable_document_endpoint(
    user_principal_name: str = Form(...),
    ar_id: Optional[int] = Form(None),
    ar_id_pk: Optional[int] = Form(None),
    ar_invoice_number: Optional[str] = Form(None),
    ar_org_id_fk: Optional[int] = Form(None),
    updated_by: Optional[str] = Form(None),
    created_by: Optional[str] = Form(None),
    storage_folder: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),
    file: UploadFile = File(...),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = {
        "user_principal_name": user_principal_name,
        "ar_id": ar_id,
        "ar_id_pk": ar_id_pk,
        "ar_invoice_number": ar_invoice_number,
        "ar_org_id_fk": ar_org_id_fk,
        "updated_by": updated_by,
        "created_by": created_by,
        "storage_folder": storage_folder,
        "content_type": content_type,
    }
    payload = bind_ar_payload(
        payload,
        auth_context,
        set_updated_by=True,
    )
    return handle_integration_result(
        upload_ar_invoice_document(payload, file.file, file.filename, file.content_type)
    )


@app.post("/api/v1/accounts-receivables/documents/download", tags=["Accounts Receivable Document Integration"])
def download_accounts_receivable_document_endpoint(
    payload: AccountsReceivableDocumentDownloadPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_integration_result(
        download_ar_invoice_document(bind_ar_payload(payload, auth_context))
    )


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_08_financial_management.api.main:app",
        host="0.0.0.0",
        port=8008,
        reload=True,
    )
