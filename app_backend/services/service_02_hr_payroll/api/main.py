from pathlib import Path
from decimal import Decimal
import json
import sys
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, Security, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
LOGIC_DIR = BASE_DIR.parent / "logic"
INTEGRATIONS_DIR = BASE_DIR.parent / "integrations"
for import_dir in (LOGIC_DIR, INTEGRATIONS_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

from app_backend.services.service_02_hr_payroll.logic.advance_master_create_data import create_advance_master
from app_backend.services.service_02_hr_payroll.logic.advance_master_get_data import get_advance_master
from app_backend.services.service_02_hr_payroll.logic.advance_master_update_data import update_advance_master
from app_backend.services.service_02_hr_payroll.logic.advance_master_delete_data import delete_advance_master
from app_backend.services.service_02_hr_payroll.logic.fine_master_create_data import create_fine_master
from app_backend.services.service_02_hr_payroll.logic.fine_master_get_data import get_fine_master, list_fine_master
from app_backend.services.service_02_hr_payroll.logic.fine_master_update_data import update_fine_master
from app_backend.services.service_02_hr_payroll.logic.fine_master_delete_data import delete_fine_master
from app_backend.services.service_02_hr_payroll.integrations.firebase_fine_attachment_upload import upload_fine_attachment
from app_backend.services.service_02_hr_payroll.integrations.firebase_fine_attachment_download import download_fine_attachment
from app_backend.services.service_02_hr_payroll.logic.penalty_master_create_data import create_penalty_master
from app_backend.services.service_02_hr_payroll.logic.penalty_master_get_data import get_penalty_master, list_penalty_master
from app_backend.services.service_02_hr_payroll.logic.penalty_master_update_data import update_penalty_master
from app_backend.services.service_02_hr_payroll.logic.penalty_master_delete_data import delete_penalty_master
from app_backend.services.service_02_hr_payroll.integrations.firebase_penalty_attachment_upload import upload_penalty_attachment
from app_backend.services.service_02_hr_payroll.integrations.firebase_penalty_attachment_download import download_penalty_attachment
from app_backend.services.service_02_hr_payroll.logic.employee_master_create_data import create_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_get_data import get_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_update_data import update_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_delete_data import delete_employee_master
from app_backend.services.service_02_hr_payroll.logic.payroll_module import (
    approve_payroll_adjustment,
    cancel_monthly_payroll_run,
    cancel_off_cycle_payroll_run,
    cancel_payroll_adjustment,
    create_monthly_payroll_run,
    create_off_cycle_payroll_run,
    create_payroll_adjustment,
    get_monthly_employee_payroll_detail,
    get_monthly_payroll_details,
    get_monthly_payroll_run,
    get_off_cycle_employee_payroll_detail,
    get_off_cycle_payroll_details,
    get_off_cycle_payroll_run,
    get_payroll_adjustment,
    get_pending_approved_payroll_adjustments,
    list_monthly_payroll_runs,
    list_off_cycle_payroll_runs,
    list_payroll_adjustments,
    process_monthly_employee_payroll,
    process_monthly_payroll_run,
    process_off_cycle_payroll_run,
    recalculate_monthly_payroll_run_totals,
    reject_payroll_adjustment,
    update_monthly_employee_payroll_detail,
    update_payroll_adjustment,
)
from app_backend.services.service_02_hr_payroll.integrations.firebase_employee_image_upload import upload_employee_image
from app_backend.services.service_02_hr_payroll.integrations.firebase_employee_image_download import download_employee_image
from app_backend.services.service_07_alerts_wf_engine.payroll_wf import (
    approve_payroll_workflow,
    get_payroll_workflow_requests,
    get_payroll_workflow_status,
    get_pending_payroll_approvals,
    reject_payroll_workflow,
    submit_payroll_run_for_approval,
)
from app_backend.services.auth_context import (
    bind_authenticated_payload,
    get_authenticated_context,
    raise_for_logic_error,
)


app = FastAPI(
    title="UETransportERP HR and Payroll API",
    description="REST API for HR employee master and employee image integration.",
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


class EmployeeMasterPayload(BaseModel):
    empl_org_id_fk: int
    secondary_key_dept: Optional[int] = None
    employee_id: str
    employee_name: str
    employee_designation: Optional[str] = None
    joining_date: Optional[str] = None
    nationality: Optional[str] = None
    passport_number: Optional[str] = None
    passport_expiry_date: Optional[str] = None
    passport_issuing_country: Optional[str] = None
    passport_with: Optional[str] = None
    visa_number: Optional[str] = None
    visa_expiry_date: Optional[str] = None
    emirates_id_number: Optional[str] = None
    emirates_id_expiry_date: Optional[str] = None
    phone_number_company: Optional[str] = None
    phone_number_personal: Optional[str] = None
    phone_number_home: Optional[str] = None
    email_id_company: Optional[str] = None
    email_id_personal: Optional[str] = None
    address_in_base_location: Optional[str] = None
    address_in_home_location: Optional[str] = None
    driver_licence_number: Optional[str] = None
    driver_licence_expiry_date: Optional[str] = None
    driver_licence_type: Optional[str] = None
    permit_number: Optional[str] = None
    permit_expiry_date: Optional[str] = None
    permit_type: Optional[str] = None
    insurance_number: Optional[str] = None
    insurance_expiry_date: Optional[str] = None
    bank_name: Optional[str] = None
    bank_address: Optional[str] = None
    bank_account_number: Optional[str] = None
    monthly_basic_salary: Optional[float] = 0
    monthly_allowance: Optional[float] = 0
    monthly_accomodation: Optional[float] = 0
    field_flex_field_1: Optional[str] = None
    field_flex_field_2: Optional[str] = None
    field_flex_field_3: Optional[str] = None
    field_flex_field_4: Optional[str] = None
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    employee_image_path: Optional[str] = None
    reporting_to_employee_id: Optional[str] = None


class EmployeeMasterUpdatePayload(EmployeeMasterPayload):
    empl_id: Optional[int] = None
    empl_id_pk: Optional[int] = None


class EmployeeMasterDeletePayload(BaseModel):
    empl_id: Optional[int] = None
    empl_id_pk: Optional[int] = None


class EmployeeImageUploadPayload(BaseModel):
    employee_id: str
    file_path: str
    storage_folder: Optional[str] = None
    updated_by: Optional[int] = None
    content_type: Optional[str] = None


class EmployeeImageDownloadPayload(BaseModel):
    employee_id: str


class PayrollRunCreatePayload(BaseModel):
    user_principal_name: Optional[str] = None
    payroll_org_id_fk: int
    payroll_run_code: str
    payroll_run_description: Optional[str] = None
    payroll_year: int
    payroll_month: int
    payroll_run_sequence: Optional[int] = 1
    payroll_run_type: Optional[str] = None
    payroll_period_start_date: str
    payroll_period_end_date: str
    payroll_payment_date: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    field_flex_field_1: Optional[str] = None
    field_flex_field_2: Optional[str] = None
    field_flex_field_3: Optional[str] = None
    field_flex_field_4: Optional[str] = None


class PayrollRunGetPayload(BaseModel):
    payroll_run_id: Optional[int] = None
    payroll_run_id_pk: Optional[int] = None
    payroll_org_id_fk: Optional[int] = None


class PayrollRunListPayload(BaseModel):
    payroll_org_id_fk: Optional[int] = None
    payroll_year: Optional[int] = None
    payroll_month: Optional[int] = None
    payroll_status: Optional[str] = None
    payroll_run_type: Optional[str] = None


class MonthlyPayrollProcessPayload(PayrollRunGetPayload):
    user_principal_name: Optional[str] = None
    processed_by: Optional[str] = None
    employee_ids: Optional[list[int]] = None
    total_period_days: Optional[float] = None
    payable_days: Optional[float] = None
    unpaid_leave_days: Optional[float] = 0
    payment_method: Optional[str] = "BANK_TRANSFER"
    remarks: Optional[str] = None


class EmployeePayrollProcessPayload(PayrollRunGetPayload):
    user_principal_name: Optional[str] = None
    payroll_employee_id_fk: Optional[int] = None
    empl_id_pk: Optional[int] = None
    total_period_days: Optional[float] = None
    payable_days: Optional[float] = None
    unpaid_leave_days: Optional[float] = 0
    payment_method: Optional[str] = "BANK_TRANSFER"
    remarks: Optional[str] = None


class OffCyclePayrollProcessPayload(PayrollRunGetPayload):
    user_principal_name: Optional[str] = None
    processed_by: Optional[str] = None
    employee_ids: list[int]
    adjustment_ids_by_employee: Optional[dict[str, list[int]]] = None
    source_payroll_run_id: Optional[int] = None
    source_payroll_run_id_fk: Optional[int] = None
    correction_source_payroll_run_id: Optional[int] = None
    source_payroll_run_ids_by_employee: Optional[dict[str, int]] = None
    correction_source_payroll_run_ids_by_employee: Optional[dict[str, int]] = None
    source_payroll_employee_detail_id: Optional[int] = None
    source_payroll_employee_detail_id_fk: Optional[int] = None
    source_payroll_detail_id: Optional[int] = None
    source_payroll_detail_ids_by_employee: Optional[dict[str, int]] = None
    source_payroll_employee_detail_ids_by_employee: Optional[dict[str, int]] = None
    correction_payroll_year: Optional[int] = None
    correction_payroll_month: Optional[int] = None
    source_payroll_year: Optional[int] = None
    source_payroll_month: Optional[int] = None
    payment_method: Optional[str] = "BANK_TRANSFER"
    remarks: Optional[str] = None


class PayrollDetailUpdatePayload(EmployeePayrollProcessPayload):
    basic_salary: Optional[float] = None
    monthly_allowance: Optional[float] = None
    accommodation_allowance: Optional[float] = None
    overtime_amount: Optional[float] = None
    bonus_amount: Optional[float] = None
    incentive_amount: Optional[float] = None
    reimbursement_amount: Optional[float] = None
    other_earning_amount: Optional[float] = None
    unpaid_leave_deduction: Optional[float] = None
    loan_deduction: Optional[float] = None
    advance_deduction: Optional[float] = None
    fine_deduction: Optional[float] = None
    fuel_deduction: Optional[float] = None
    salik_deduction: Optional[float] = None
    darb_deduction: Optional[float] = None
    charging_cost_deduction: Optional[float] = None
    other_deduction_amount: Optional[float] = None
    penalty_deduction: Optional[Decimal] = Field(
        default=None,
        description=(
            "Independent Payroll deduction. Omitted/null preserves the stored value; "
            "zero clears it. Direct edits on CORRECTION runs are rejected."
        ),
    )


# Document the existing open business DTO without filtering other detail columns.
PAYROLL_DETAIL_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "penalty_deduction": {
            "anyOf": [{"type": "number"}, {"type": "null"}],
            "description": "Stored independent deduction; null contributes zero to arithmetic.",
        },
    },
}
PAYROLL_DETAIL_RESPONSES = {
    200: {
        "description": "Complete employee Payroll detail, including penalty_deduction.",
        "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {"success": {"type": "boolean"}, "data": PAYROLL_DETAIL_RESPONSE_SCHEMA},
        }}},
    },
}
PAYROLL_DETAILS_RESPONSES = {
    200: {
        "description": "Complete employee Payroll details, including penalty_deduction on each row.",
        "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {"success": {"type": "boolean"}, "data": {
                "type": "array", "items": PAYROLL_DETAIL_RESPONSE_SCHEMA,
            }},
        }}},
    },
}


class PayrollRunCancelPayload(PayrollRunGetPayload):
    user_principal_name: Optional[str] = None
    cancellation_reason: Optional[str] = None
    updated_by: Optional[str] = None


class PayrollAdjustmentCreatePayload(BaseModel):
    user_principal_name: Optional[str] = None
    payroll_adjustment_org_id_fk: int
    payroll_adjustment_empl_id_fk: int
    payroll_year: int
    payroll_month: int
    adjustment_date: Optional[str] = None
    adjustment_type: str
    earning_deduction_flag: Optional[str] = None
    adjustment_description: Optional[str] = None
    adjustment_amount: float
    adjustment_status: Optional[str] = None
    external_reference: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    field_flex_field_1: Optional[str] = None
    field_flex_field_2: Optional[str] = None
    field_flex_field_3: Optional[str] = None
    field_flex_field_4: Optional[str] = None


class PayrollAdjustmentGetPayload(BaseModel):
    payroll_adjustment_id: Optional[int] = None
    payroll_adjustment_id_pk: Optional[int] = None


class PayrollAdjustmentListPayload(BaseModel):
    payroll_adjustment_org_id_fk: Optional[int] = None
    payroll_adjustment_empl_id_fk: Optional[int] = None
    payroll_year: Optional[int] = None
    payroll_month: Optional[int] = None
    adjustment_status: Optional[str] = None
    processed_flag: Optional[bool] = None


class PayrollAdjustmentUpdatePayload(PayrollAdjustmentGetPayload):
    user_principal_name: Optional[str] = None
    payroll_year: Optional[int] = None
    payroll_month: Optional[int] = None
    adjustment_date: Optional[str] = None
    adjustment_type: Optional[str] = None
    earning_deduction_flag: Optional[str] = None
    adjustment_description: Optional[str] = None
    adjustment_amount: Optional[float] = None
    external_reference: Optional[str] = None
    updated_by: Optional[str] = None
    field_flex_field_1: Optional[str] = None
    field_flex_field_2: Optional[str] = None
    field_flex_field_3: Optional[str] = None
    field_flex_field_4: Optional[str] = None


class PayrollAdjustmentStatusPayload(PayrollAdjustmentGetPayload):
    user_principal_name: Optional[str] = None
    updated_by: Optional[str] = None
    rejection_reason: Optional[str] = None


class PayrollWorkflowSubmitPayload(PayrollRunGetPayload):
    user_principal_name: str
    approval_comments: Optional[str] = None


class PayrollWorkflowActionIdentityPayload(BaseModel):
    workflow_request_id: Optional[int] = None
    workflow_request_id_pk: Optional[int] = None
    workflow_instance_id: Optional[int] = None
    workflow_instance_id_pk: Optional[int] = None
    workflow_instance_step_id: Optional[int] = None
    workflow_instance_step_id_pk: Optional[int] = None
    approver_user_principal_name: Optional[str] = None


class PayrollWorkflowApprovalPayload(PayrollWorkflowActionIdentityPayload):
    approval_comments: Optional[str] = None


class PayrollWorkflowRejectPayload(PayrollWorkflowActionIdentityPayload):
    rejection_comments: Optional[str] = None


class PayrollWorkflowGetPayload(BaseModel):
    payroll_run_id: Optional[int] = None
    payroll_run_id_pk: Optional[int] = None
    payroll_org_id_fk: Optional[int] = None
    workflow_status: Optional[str] = None
    user_principal_name: Optional[str] = None


class FineMasterPayload(BaseModel):
    fine_org_id_fk: Optional[int] = None
    fine_empl_id_fk: int
    fine_date: str
    fine_on: str
    fine_accountability: Optional[str] = None
    payment_authority: Optional[str] = None
    amount_paid: Optional[Decimal] = None
    recovery_split: Optional[Decimal] = None
    fine_attachment_path: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class FineMasterGetPayload(BaseModel):
    fine_id: Optional[int] = None
    fine_id_pk: Optional[int] = None
    fine_org_id_fk: Optional[int] = None


class FineMasterUpdatePayload(FineMasterGetPayload):
    fine_empl_id_fk: Optional[int] = None
    fine_date: Optional[str] = None
    fine_on: Optional[str] = None
    fine_accountability: Optional[str] = None
    payment_authority: Optional[str] = None
    amount_paid: Optional[Decimal] = None
    recovery_split: Optional[Decimal] = None
    fine_attachment_path: Optional[str] = None
    updated_by: Optional[str] = None


class FineMasterDeletePayload(FineMasterGetPayload):
    pass


class FineAttachmentDownloadPayload(FineMasterGetPayload):
    pass


class PenaltyMasterPayload(BaseModel):
    penalty_org_id_fk: Optional[int] = None
    penalty_empl_id_fk: int
    penalty_date: str
    penalty_reason: str
    warning_letter_issued: bool
    warning_letter_date: Optional[str] = None
    warning_letter_accepted: Optional[bool] = None
    financial_implication: bool
    penalty_amount: Optional[Decimal] = None
    recovery_split_percentage: Optional[Decimal] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class PenaltyMasterGetPayload(BaseModel):
    penalty_id: Optional[int] = None
    penalty_id_pk: Optional[int] = None
    penalty_org_id_fk: Optional[int] = None


class PenaltyMasterUpdatePayload(PenaltyMasterGetPayload):
    penalty_empl_id_fk: Optional[int] = None
    penalty_date: Optional[str] = None
    penalty_reason: Optional[str] = None
    warning_letter_issued: Optional[bool] = None
    warning_letter_date: Optional[str] = None
    warning_letter_accepted: Optional[bool] = None
    financial_implication: Optional[bool] = None
    penalty_amount: Optional[Decimal] = None
    recovery_split_percentage: Optional[Decimal] = None
    updated_by: Optional[str] = None


class PenaltyMasterDeletePayload(PenaltyMasterGetPayload):
    pass


class PenaltyAttachmentDownloadPayload(PenaltyMasterGetPayload):
    pass


class AdvanceMasterPayload(BaseModel):
    advance_empl_id_fk: int
    advance_date: str
    advance_reason: str
    advance_amount: Decimal
    recovery_split_percentage: Decimal
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class AdvanceMasterUpdatePayload(AdvanceMasterPayload):
    advance_id: Optional[int] = None
    advance_id_pk: Optional[int] = None


class AdvanceMasterDeletePayload(BaseModel):
    advance_id: Optional[int] = None
    advance_id_pk: Optional[int] = None


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


def bind_employee_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("empl_org_id_fk",),
        audit_actor="user_id",
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


def bind_payroll_run_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    principal_fields: tuple[str, ...] = (),
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("payroll_org_id_fk",),
        principal_fields=principal_fields,
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


def bind_payroll_adjustment_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    principal_fields: tuple[str, ...] = (),
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("payroll_adjustment_org_id_fk",),
        principal_fields=principal_fields,
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


def bind_fine_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    if isinstance(payload, FineMasterUpdatePayload):
        payload = payload.model_dump(exclude_unset=True)
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("fine_org_id_fk",),
        set_created_by=set_created_by,
        set_updated_by=set_updated_by,
    )


def bind_penalty_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    if isinstance(payload, PenaltyMasterUpdatePayload):
        payload = payload.model_dump(exclude_unset=True)
    return bind_authenticated_payload(
        payload,
        auth_context,
        org_fields=("penalty_org_id_fk",),
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


@app.post("/api/v1/employees/create", tags=["Employee Master"])
def create_employee_endpoint(
    payload: EmployeeMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_employee_master,
        bind_employee_payload(
            payload,
            auth_context,
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/employees/update", tags=["Employee Master"])
def update_employee_endpoint(
    payload: EmployeeMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_employee_master,
        bind_employee_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/employees/delete", tags=["Employee Master"])
def delete_employee_endpoint(
    payload: EmployeeMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_employee_master,
        bind_employee_payload(payload, auth_context),
    )


@app.get("/api/v1/employees", tags=["Employee Master"])
def get_employee_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_employee_master,
        bind_employee_payload({}, auth_context),
    )


@app.post("/api/v1/employees/images/upload", tags=["Employee Image Integration"])
def upload_employee_image_endpoint(
    employee_id: str = Form(...),
    updated_by: Optional[int] = Form(None),
    storage_folder: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),
    file: UploadFile = File(...),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = {
        "employee_id": employee_id,
        "updated_by": updated_by,
        "storage_folder": storage_folder,
        "content_type": content_type,
    }
    payload = bind_employee_payload(
        payload,
        auth_context,
        set_updated_by=True,
    )
    return handle_integration_result(
        upload_employee_image(payload, file.file, file.filename, file.content_type)
    )


@app.post("/api/v1/employees/images/download", tags=["Employee Image Integration"])
def download_employee_image_endpoint(
    payload: EmployeeImageDownloadPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_integration_result(
        download_employee_image(bind_employee_payload(payload, auth_context))
    )


@app.post("/api/v1/payroll/monthly/create", tags=["Monthly Payroll"])
def create_monthly_payroll_endpoint(
    payload: PayrollRunCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_monthly_payroll_run,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll/monthly/get", tags=["Monthly Payroll"])
def get_monthly_payroll_endpoint(
    payload: PayrollRunGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_monthly_payroll_run,
        bind_payroll_run_payload(payload, auth_context),
    )


@app.post("/api/v1/payroll/monthly/list", tags=["Monthly Payroll"])
def list_monthly_payroll_endpoint(
    payload: PayrollRunListPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        list_monthly_payroll_runs,
        bind_payroll_run_payload(payload, auth_context),
    )


@app.get("/api/v1/payroll/monthly", tags=["Monthly Payroll"])
def list_all_monthly_payroll_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        list_monthly_payroll_runs,
        bind_payroll_run_payload({}, auth_context),
    )


@app.post("/api/v1/payroll/monthly/process", tags=["Monthly Payroll"])
def process_monthly_payroll_endpoint(
    payload: MonthlyPayrollProcessPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        process_monthly_payroll_run,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name", "processed_by"),
        ),
    )


@app.post("/api/v1/payroll/monthly/process-employee", tags=["Monthly Payroll"])
def process_monthly_employee_payroll_endpoint(
    payload: EmployeePayrollProcessPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        process_monthly_employee_payroll,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/monthly/details", tags=["Monthly Payroll"], responses=PAYROLL_DETAILS_RESPONSES)
def get_monthly_payroll_details_endpoint(
    payload: PayrollRunGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_monthly_payroll_details,
        bind_payroll_run_payload(payload, auth_context),
    )


@app.post("/api/v1/payroll/monthly/details/employee", tags=["Monthly Payroll"], responses=PAYROLL_DETAIL_RESPONSES)
def get_monthly_employee_payroll_detail_endpoint(
    payload: EmployeePayrollProcessPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_monthly_employee_payroll_detail,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/monthly/details/update", tags=["Monthly Payroll"])
def update_monthly_employee_payroll_detail_endpoint(
    payload: PayrollDetailUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_monthly_employee_payroll_detail,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll/monthly/recalculate", tags=["Monthly Payroll"])
def recalculate_monthly_payroll_endpoint(
    payload: PayrollRunGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        recalculate_monthly_payroll_run_totals,
        bind_payroll_run_payload(
            payload,
            auth_context,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll/monthly/cancel", tags=["Monthly Payroll"])
def cancel_monthly_payroll_endpoint(
    payload: PayrollRunCancelPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        cancel_monthly_payroll_run,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll/monthly/submit-approval", tags=["Payroll Workflow"])
def submit_monthly_payroll_approval_endpoint(
    payload: PayrollWorkflowSubmitPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        submit_payroll_run_for_approval,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/off-cycle/create", tags=["Off-Cycle Payroll"])
def create_off_cycle_payroll_endpoint(
    payload: PayrollRunCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_off_cycle_payroll_run,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll/off-cycle/get", tags=["Off-Cycle Payroll"])
def get_off_cycle_payroll_endpoint(
    payload: PayrollRunGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_off_cycle_payroll_run,
        bind_payroll_run_payload(payload, auth_context),
    )


@app.post("/api/v1/payroll/off-cycle/list", tags=["Off-Cycle Payroll"])
def list_off_cycle_payroll_endpoint(
    payload: PayrollRunListPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        list_off_cycle_payroll_runs,
        bind_payroll_run_payload(payload, auth_context),
    )


@app.get("/api/v1/payroll/off-cycle", tags=["Off-Cycle Payroll"])
def list_all_off_cycle_payroll_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        list_off_cycle_payroll_runs,
        bind_payroll_run_payload({}, auth_context),
    )


@app.post("/api/v1/payroll/off-cycle/process", tags=["Off-Cycle Payroll"])
def process_off_cycle_payroll_endpoint(
    payload: OffCyclePayrollProcessPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        process_off_cycle_payroll_run,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name", "processed_by"),
        ),
    )


@app.post("/api/v1/payroll/off-cycle/details", tags=["Off-Cycle Payroll"], responses=PAYROLL_DETAILS_RESPONSES)
def get_off_cycle_payroll_details_endpoint(
    payload: PayrollRunGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_off_cycle_payroll_details,
        bind_payroll_run_payload(payload, auth_context),
    )


@app.post("/api/v1/payroll/off-cycle/details/employee", tags=["Off-Cycle Payroll"], responses=PAYROLL_DETAIL_RESPONSES)
def get_off_cycle_employee_payroll_detail_endpoint(
    payload: EmployeePayrollProcessPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_off_cycle_employee_payroll_detail,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/off-cycle/cancel", tags=["Off-Cycle Payroll"])
def cancel_off_cycle_payroll_endpoint(
    payload: PayrollRunCancelPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        cancel_off_cycle_payroll_run,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll/off-cycle/submit-approval", tags=["Payroll Workflow"])
def submit_off_cycle_payroll_approval_endpoint(
    payload: PayrollWorkflowSubmitPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        submit_payroll_run_for_approval,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll-adjustments/create", tags=["Payroll Adjustments"])
def create_payroll_adjustment_endpoint(
    payload: PayrollAdjustmentCreatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_payroll_adjustment,
        bind_payroll_adjustment_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_created_by=True,
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll-adjustments/get", tags=["Payroll Adjustments"])
def get_payroll_adjustment_endpoint(
    payload: PayrollAdjustmentGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_payroll_adjustment,
        bind_payroll_adjustment_payload(payload, auth_context),
    )


@app.post("/api/v1/payroll-adjustments/list", tags=["Payroll Adjustments"])
def list_payroll_adjustments_endpoint(
    payload: PayrollAdjustmentListPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        list_payroll_adjustments,
        bind_payroll_adjustment_payload(payload, auth_context),
    )


@app.get("/api/v1/payroll-adjustments", tags=["Payroll Adjustments"])
def list_all_payroll_adjustments_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        list_payroll_adjustments,
        bind_payroll_adjustment_payload({}, auth_context),
    )


@app.post("/api/v1/payroll-adjustments/update", tags=["Payroll Adjustments"])
def update_payroll_adjustment_endpoint(
    payload: PayrollAdjustmentUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_payroll_adjustment,
        bind_payroll_adjustment_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll-adjustments/approve", tags=["Payroll Adjustments"])
def approve_payroll_adjustment_endpoint(
    payload: PayrollAdjustmentStatusPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_payroll_adjustment,
        bind_payroll_adjustment_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll-adjustments/reject", tags=["Payroll Adjustments"])
def reject_payroll_adjustment_endpoint(
    payload: PayrollAdjustmentStatusPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_payroll_adjustment,
        bind_payroll_adjustment_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll-adjustments/cancel", tags=["Payroll Adjustments"])
def cancel_payroll_adjustment_endpoint(
    payload: PayrollAdjustmentStatusPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        cancel_payroll_adjustment,
        bind_payroll_adjustment_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
            set_updated_by=True,
        ),
    )


@app.post("/api/v1/payroll-adjustments/pending-approved", tags=["Payroll Adjustments"])
def get_pending_approved_payroll_adjustments_endpoint(
    payload: PayrollAdjustmentListPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_pending_approved_payroll_adjustments,
        bind_payroll_adjustment_payload(payload, auth_context),
    )


@app.post("/api/v1/payroll/workflow/submit", tags=["Payroll Workflow"])
def submit_payroll_workflow_endpoint(
    payload: PayrollWorkflowSubmitPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        submit_payroll_run_for_approval,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/workflow/approve", tags=["Payroll Workflow"])
def approve_payroll_workflow_endpoint(
    payload: PayrollWorkflowApprovalPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        approve_payroll_workflow,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("approver_user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/workflow/reject", tags=["Payroll Workflow"])
def reject_payroll_workflow_endpoint(
    payload: PayrollWorkflowRejectPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        reject_payroll_workflow,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("approver_user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/workflow/status", tags=["Payroll Workflow"])
def get_payroll_workflow_status_endpoint(
    payload: PayrollWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_payroll_workflow_status,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/payroll/workflow/requests", tags=["Payroll Workflow"])
def get_payroll_workflow_requests_endpoint(
    payload: PayrollWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_payroll_workflow_requests,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.get("/api/v1/payroll/workflow/requests", tags=["Payroll Workflow"])
def get_all_payroll_workflow_requests_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_payroll_workflow_requests,
        bind_payroll_run_payload({}, auth_context),
    )


@app.post("/api/v1/payroll/workflow/pending-approvals", tags=["Payroll Workflow"])
def get_pending_payroll_approvals_endpoint(
    payload: PayrollWorkflowGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        get_pending_payroll_approvals,
        bind_payroll_run_payload(
            payload,
            auth_context,
            principal_fields=("user_principal_name",),
        ),
    )


@app.post("/api/v1/fines/create", tags=["Fines"])
def create_fine_endpoint(
    payload: FineMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_fine_master,
        bind_fine_payload(payload, auth_context, set_created_by=True, set_updated_by=True),
    )


@app.post("/api/v1/fines/update", tags=["Fines"])
def update_fine_endpoint(
    payload: FineMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_fine_master,
        bind_fine_payload(payload, auth_context, set_updated_by=True),
    )


@app.post("/api/v1/fines/delete", tags=["Fines"])
def delete_fine_endpoint(
    payload: FineMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_fine_master,
        bind_fine_payload(payload, auth_context),
    )


@app.get("/api/v1/fines", tags=["Fines"])
def list_fine_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(list_fine_master, bind_fine_payload({}, auth_context))


@app.post("/api/v1/fines/get", tags=["Fines"])
def get_fine_endpoint(
    payload: FineMasterGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(get_fine_master, bind_fine_payload(payload, auth_context))


@app.post("/api/v1/fines/attachments/upload", tags=["Fine Attachment Integration"])
def upload_fine_attachment_endpoint(
    fine_id: Optional[int] = Form(None),
    fine_id_pk: Optional[int] = Form(None),
    file: UploadFile = File(...),
    storage_folder: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = bind_fine_payload(
        {"fine_id": fine_id, "fine_id_pk": fine_id_pk,
         "storage_folder": storage_folder, "content_type": content_type},
        auth_context, set_updated_by=True,
    )
    return handle_integration_result(
        upload_fine_attachment(payload, file.file, file.filename, file.content_type)
    )


@app.post("/api/v1/fines/attachments/download", tags=["Fine Attachment Integration"])
def download_fine_attachment_endpoint(
    payload: FineAttachmentDownloadPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_integration_result(
        download_fine_attachment(bind_fine_payload(payload, auth_context))
    )


@app.get("/api/v1/penalties", tags=["Penalties Management"])
def list_penalty_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(list_penalty_master, bind_penalty_payload({}, auth_context))


@app.post("/api/v1/penalties/get", tags=["Penalties Management"])
def get_penalty_endpoint(
    payload: PenaltyMasterGetPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(get_penalty_master, bind_penalty_payload(payload, auth_context))


@app.post("/api/v1/penalties/create", tags=["Penalties Management"])
def create_penalty_endpoint(
    payload: PenaltyMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_penalty_master,
        bind_penalty_payload(payload, auth_context, set_created_by=True, set_updated_by=True),
    )


@app.post("/api/v1/penalties/update", tags=["Penalties Management"])
def update_penalty_endpoint(
    payload: PenaltyMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_penalty_master,
        bind_penalty_payload(payload, auth_context, set_updated_by=True),
    )


@app.post("/api/v1/penalties/delete", tags=["Penalties Management"])
def delete_penalty_endpoint(
    payload: PenaltyMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(delete_penalty_master, bind_penalty_payload(payload, auth_context))


@app.post("/api/v1/penalties/attachments/upload", tags=["Penalty Attachment Integration"])
def upload_penalty_attachment_endpoint(
    penalty_id: Optional[int] = Form(None),
    penalty_id_pk: Optional[int] = Form(None),
    file: UploadFile = File(...),
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    payload = bind_penalty_payload(
        {"penalty_id": penalty_id, "penalty_id_pk": penalty_id_pk},
        auth_context,
        set_updated_by=True,
    )
    return handle_integration_result(
        upload_penalty_attachment(payload, file.file, file.filename, file.content_type)
    )


@app.post("/api/v1/penalties/attachments/download", tags=["Penalty Attachment Integration"])
def download_penalty_attachment_endpoint(
    payload: PenaltyAttachmentDownloadPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_integration_result(
        download_penalty_attachment(bind_penalty_payload(payload, auth_context))
    )


@app.post("/api/v1/advances/create", tags=["Advances Management"])
def create_advance_endpoint(
    payload: AdvanceMasterPayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        create_advance_master,
        bind_authenticated_payload(payload, auth_context, set_created_by=True, set_updated_by=True),
    )


@app.post("/api/v1/advances/update", tags=["Advances Management"])
def update_advance_endpoint(
    payload: AdvanceMasterUpdatePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        update_advance_master,
        bind_authenticated_payload(payload, auth_context, set_updated_by=True),
    )


@app.post("/api/v1/advances/delete", tags=["Advances Management"])
def delete_advance_endpoint(
    payload: AdvanceMasterDeletePayload,
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(
        delete_advance_master,
        bind_authenticated_payload(payload, auth_context),
    )


@app.get("/api/v1/advances", tags=["Advances Management"])
def get_advance_endpoint(
    auth_context: dict[str, Any] = Security(get_authenticated_context),
) -> dict[str, Any]:
    return handle_logic_call(get_advance_master, bind_authenticated_payload({}, auth_context))


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_02_hr_payroll.api.main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
    )
