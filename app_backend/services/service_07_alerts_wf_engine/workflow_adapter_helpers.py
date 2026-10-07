import hashlib
import json
from collections.abc import Callable
from typing import Any

import pandas as pd
from sqlalchemy import text

from app_backend.services.firebase_file_pointer_helpers import WORKFLOW_DOCUMENT_METADATA_FIELDS

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    WORKFLOW_APPROVE_ACTION,
    get_user_workflow_actions,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    assert_legacy_workflow_table_allowed,
    ensure_legacy_workflow_table_schema,
    ensure_workflow_engine_schema,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_errors import (
    AUTHENTICATED_PRINCIPAL_MISMATCH,
    APPROVER_USER_INVALID,
    CURRENT_APPROVER_MISMATCH,
    WORKFLOW_APPROVE_PERMISSION_REQUIRED,
    WORKFLOW_INSTANCE_NOT_FOUND,
    WORKFLOW_REQUEST_ID_REQUIRED,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    cleanup_staged_workflow_document,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine import (
    _execute_adapter,
    approve_workflow_step,
    reject_workflow_step,
    start_workflow,
    strip_internal_flags,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    is_trusted_workflow_execution,
)


def is_pending_workflow_submission(workflow_response: dict | None) -> bool:
    return (
        isinstance(workflow_response, dict)
        and workflow_response.get("workflow_status") == "PENDING_APPROVAL"
        and not workflow_response.get("error")
    )


def pending_workflow_submission_response(
    workflow_response: dict,
    message: str,
    domain_reference_id: Any = None,
) -> dict:
    response = {
        "message": message,
        "business_operation_executed": False,
        "workflow_required": True,
        "workflow_code": workflow_response.get("workflow_code"),
        "workflow_action": workflow_response.get("workflow_action"),
        "workflow_status": workflow_response.get("workflow_status"),
        "workflow_confirmation": workflow_response.get("workflow_confirmation"),
        "workflow_instance_id": workflow_response.get("workflow_instance_id")
        or workflow_response.get("workflow_instance_id_pk"),
        "workflow_instance_step_id": workflow_response.get("workflow_instance_step_id")
        or workflow_response.get("workflow_instance_step_id_pk"),
        "workflow_request_id": workflow_response.get("workflow_request_id")
        or workflow_response.get("workflow_request_id_pk"),
        "current_approver_user_principal_name": (
            workflow_response.get("current_approver_user_principal_name")
            or workflow_response.get("approver_user_principal_name")
        ),
        "execution_result": workflow_response.get("execution_result"),
        "workflow": workflow_response,
    }
    effective_domain_reference_id = domain_reference_id
    if effective_domain_reference_id is None:
        effective_domain_reference_id = workflow_response.get("domain_reference_id")
    if effective_domain_reference_id is not None:
        response["domain_reference_id"] = effective_domain_reference_id
    return response


WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY = (
    "DELETE_STAGED_DOCUMENT_IF_WORKFLOW_REJECTED_OR_CANCELLED"
)


def strip_ephemeral_document_fields(payload: dict, field_names: tuple[str, ...]) -> dict:
    return {
        key: value
        for key, value in (payload or {}).items()
        if key not in field_names and key != "content_type"
    }


def workflow_document_stream_required_error(
    payload: dict,
    durable_field_name: str,
    ephemeral_field_names: tuple[str, ...],
    document_label: str,
) -> dict | None:
    if payload.get(durable_field_name):
        return None
    if any(payload.get(field_name) for field_name in ephemeral_field_names):
        return {
            "error": (
                f"{document_label} must be uploaded to Firebase before workflow submission; "
                "local file path fields cannot be persisted in workflow requests."
            )
        }
    return None


def workflow_staged_document_payload(
    payload: dict,
    durable_field_name: str,
    durable_reference: str | None,
    upload_response: dict,
    ephemeral_field_names: tuple[str, ...],
) -> dict:
    staged_payload = strip_ephemeral_document_fields(payload, ephemeral_field_names)
    # Only the uploader may supply identity, provenance and integrity metadata.
    staged_payload = {key: value for key, value in staged_payload.items()
                      if key not in WORKFLOW_DOCUMENT_METADATA_FIELDS}
    if durable_reference:
        staged_payload[durable_field_name] = durable_reference
    staged_payload["workflow_document_staging_status"] = "STAGED"
    staged_payload["workflow_document_cleanup_policy"] = WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY
    if upload_response.get("firebase_blob_path"):
        staged_payload["workflow_document_staged_blob_path"] = upload_response.get("firebase_blob_path")
    if upload_response.get("firebase_bucket"):
        staged_payload["workflow_document_staged_bucket"] = upload_response.get("firebase_bucket")
    if upload_response.get("workflow_document_attachment"):
        staged_payload["workflow_document_attachment"] = dict(upload_response["workflow_document_attachment"])
    return staged_payload


def workflow_document_staging_storage_folder(
    base_storage_folder: str,
    workflow_code: str,
    workflow_action: str,
    organization_id: int | str | None,
    payload: dict,
) -> str:
    explicit_key = (
        payload.get("service_request_id")
        or payload.get("idempotency_key")
    )
    token_source = str(explicit_key) if explicit_key else json.dumps(
        payload or {},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    token = _safe_storage_path_component(token_source)
    if not explicit_key:
        token = hashlib.sha256(token_source.encode("utf-8")).hexdigest()
    org_component = _safe_storage_path_component(str(organization_id or "unknown_org"))
    return "/".join(
        [
            base_storage_folder.rstrip("/"),
            "_workflow_staging",
            org_component,
            _safe_storage_path_component(workflow_code.upper()),
            _safe_storage_path_component(workflow_action.upper()),
            token,
        ]
    )


def _safe_storage_path_component(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in ("-", "_", ".") else "_"
        for character in value
    ).strip("._")
    return (safe or "value")[:120]


def _organization_id_from_payload(
    payload: dict | None,
    organization_field_names: tuple[str, ...] = (),
    explicit_organization_id: int | None = None,
) -> int | None:
    if explicit_organization_id is not None:
        return int(explicit_organization_id)

    payload = payload or {}
    for field_name in ("authenticated_org_id", *organization_field_names):
        value = payload.get(field_name)
        if value is not None and value != "":
            return int(value)
    return None


def _validate_payload_field_name(field_name: str) -> str:
    if (
        not isinstance(field_name, str)
        or not field_name
        or field_name[0].isdigit()
        or not field_name.replace("_", "").isalnum()
    ):
        raise ValueError("Invalid workflow organization field name.")
    return field_name


def _legacy_payload_org_predicate(
    organization_field_names: tuple[str, ...],
    legacy_alias: str = "legacy",
) -> str:
    field_names = tuple(dict.fromkeys(("authenticated_org_id", *organization_field_names)))
    if not field_names:
        return "false"

    clauses = []
    for field_name in field_names:
        safe_field_name = _validate_payload_field_name(field_name)
        clauses.append(
            f"{legacy_alias}.request_payload ? '{safe_field_name}' "
            f"and {legacy_alias}.request_payload ->> '{safe_field_name}' = cast(:organization_id as text)"
        )
    return "(" + " or ".join(clauses) + ")"


def ensure_legacy_workflow_table(table_name: str) -> None:
    assert_legacy_workflow_table_allowed(table_name)
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            ensure_legacy_workflow_table_schema(conn, table_name)
    finally:
        engine.dispose()


def is_trusted_internal_workflow_payload(payload: dict, flag_name: str) -> bool:
    return payload.get(flag_name) is True and is_trusted_workflow_execution()


def submit_configured_workflow(
    workflow_code: str,
    workflow_action: str,
    payload: dict,
    organization_id: int | None,
    legacy_table_name: str,
    legacy_extra_values: dict[str, Any] | None = None,
    authenticated_user_principal_name: str | None = None,
    conn=None,
) -> dict:
    payload_user = payload.get("user_principal_name")
    requester = authenticated_user_principal_name or payload_user
    if authenticated_user_principal_name and payload_user and payload_user.lower() != authenticated_user_principal_name.lower():
        return {
            "workflow_confirmation": "N",
            "workflow_status": "ERROR",
            "error_code": AUTHENTICATED_PRINCIPAL_MISMATCH,
            "error": "Payload requester does not match authenticated user.",
        }
    return start_workflow(
        workflow_code=workflow_code,
        workflow_action=workflow_action,
        organization_id=organization_id,
        requester_user_principal_name=requester,
        request_payload=strip_internal_flags(payload),
        legacy_table_name=legacy_table_name,
        legacy_extra_values=legacy_extra_values,
        conn=conn,
    )


def approve_configured_or_legacy_workflow(
    workflow_code: str,
    payload: dict,
    legacy_table_name: str,
    execution_adapter: Callable[[str, dict], Any],
    final_payload_enricher: Callable[[dict, str, str | None], dict] | None = None,
    authenticated_user_principal_name: str | None = None,
    legacy_organization_field_names: tuple[str, ...] = (),
    authenticated_organization_id: int | None = None,
) -> dict:
    organization_id = _organization_id_from_payload(
        payload,
        legacy_organization_field_names,
        authenticated_organization_id,
    )
    engine_result = approve_workflow_step(
        workflow_code=workflow_code,
        payload=payload,
        execution_adapter=execution_adapter,
        legacy_table_name=legacy_table_name,
        final_payload_enricher=final_payload_enricher,
        acting_user_principal_name=authenticated_user_principal_name,
        organization_id=organization_id,
    )
    if engine_result.get("error_code") != "WORKFLOW_INSTANCE_NOT_CONFIGURED":
        return engine_result
    return approve_legacy_single_step_workflow(
        payload,
        legacy_table_name,
        execution_adapter,
        final_payload_enricher,
        authenticated_user_principal_name,
        organization_id=organization_id,
        organization_field_names=legacy_organization_field_names,
    )


def reject_configured_or_legacy_workflow(
    workflow_code: str,
    payload: dict,
    legacy_table_name: str,
    authenticated_user_principal_name: str | None = None,
    legacy_organization_field_names: tuple[str, ...] = (),
    authenticated_organization_id: int | None = None,
) -> dict:
    organization_id = _organization_id_from_payload(
        payload,
        legacy_organization_field_names,
        authenticated_organization_id,
    )
    engine_result = reject_workflow_step(
        workflow_code=workflow_code,
        payload=payload,
        legacy_table_name=legacy_table_name,
        acting_user_principal_name=authenticated_user_principal_name,
        organization_id=organization_id,
    )
    if engine_result.get("error_code") != "WORKFLOW_INSTANCE_NOT_CONFIGURED":
        return engine_result
    return reject_legacy_single_step_workflow(
        payload,
        legacy_table_name,
        authenticated_user_principal_name,
        organization_id=organization_id,
        organization_field_names=legacy_organization_field_names,
    )


def get_legacy_workflow_requests(
    table_name: str,
    payload: dict | None = None,
    organization_field_names: tuple[str, ...] = (),
):
    ensure_legacy_workflow_table(table_name)
    payload = payload or {}
    workflow_status = payload.get("workflow_status")
    user_principal_name = payload.get("user_principal_name")
    actionable_only = bool(payload.get("actionable_only", False))
    organization_id = _organization_id_from_payload(payload, organization_field_names)
    legacy_org_predicate = _legacy_payload_org_predicate(organization_field_names)

    engine = db_engine()
    try:
        with engine.begin() as conn:
            df_requests = pd.read_sql(
                sql=text(f"""
                    select legacy.*
                    from {table_name} legacy
                    left join workflow_instances wi
                        on wi.workflow_instance_id_pk = legacy.workflow_instance_id_fk
                    left join workflow_instance_steps current_step
                        on current_step.workflow_instance_id_fk = wi.workflow_instance_id_pk
                        and current_step.step_status = 'PENDING'
                    where (:workflow_status is null or legacy.workflow_status = :workflow_status)
                    and (
                        :organization_id is null
                        or (
                            wi.workflow_instance_id_pk is not null
                            and wi.organization_id_fk = :organization_id
                        )
                        or (
                            wi.workflow_instance_id_pk is null
                            and {legacy_org_predicate}
                        )
                    )
                    and (
                        :user_principal_name is null
                        or lower(legacy.requester_user_principal_name) = lower(:user_principal_name)
                        or lower(legacy.approver_user_principal_name) = lower(:user_principal_name)
                        or lower(current_step.assigned_approver_user_principal_name) = lower(:user_principal_name)
                        or exists (
                            select 1
                            from workflow_instance_steps history_step
                            where history_step.workflow_instance_id_fk = wi.workflow_instance_id_pk
                            and (
                                lower(history_step.assigned_approver_user_principal_name) = lower(:user_principal_name)
                                or lower(history_step.acted_by_user_principal_name) = lower(:user_principal_name)
                            )
                        )
                    )
                    and (
                        :actionable_only = false
                        or (
                            legacy.workflow_status = 'PENDING_APPROVAL'
                            and lower(current_step.assigned_approver_user_principal_name) = lower(:user_principal_name)
                        )
                    )
                    order by legacy.workflow_request_id_pk desc
                """),
                con=conn,
                params={
                    "workflow_status": workflow_status,
                    "user_principal_name": user_principal_name,
                    "actionable_only": actionable_only,
                    "organization_id": organization_id,
                },
            )
    finally:
        engine.dispose()

    for date_column in ["requested_at", "approved_at", "rejected_at", "executed_at"]:
        if date_column in df_requests.columns:
            df_requests[date_column] = df_requests[date_column].astype(str)
    return df_requests, df_requests.to_json(orient="records")


def approve_legacy_single_step_workflow(
    payload: dict,
    table_name: str,
    execution_adapter: Callable[[str, dict], Any],
    final_payload_enricher: Callable[[dict, str, str | None], dict] | None = None,
    authenticated_user_principal_name: str | None = None,
    organization_id: int | None = None,
    organization_field_names: tuple[str, ...] = (),
) -> dict:
    workflow_request_id = payload.get("workflow_request_id") or payload.get("workflow_request_id_pk")
    acting_user = authenticated_user_principal_name or payload.get("acting_user_principal_name") or payload.get("approver_user_principal_name")
    approval_comments = payload.get("approval_comments")
    organization_id = _organization_id_from_payload(
        payload,
        organization_field_names,
        organization_id,
    )
    legacy_org_predicate = _legacy_payload_org_predicate(organization_field_names)
    if not workflow_request_id:
        return {"workflow_confirmation": "N", "error_code": WORKFLOW_REQUEST_ID_REQUIRED, "error": "workflow_request_id is required."}
    if not acting_user:
        return {"workflow_confirmation": "N", "error_code": APPROVER_USER_INVALID, "error": "approver_user_principal_name is required."}

    engine = db_engine()
    try:
        with engine.begin() as conn:
            assert_legacy_workflow_table_allowed(table_name)
            workflow_actions = get_user_workflow_actions(conn, acting_user, organization_id)
            if WORKFLOW_APPROVE_ACTION not in workflow_actions:
                return {"workflow_confirmation": "N", "error_code": WORKFLOW_APPROVE_PERMISSION_REQUIRED, "error": "Approver does not have WORKFLOW/APPROVE rights.", "approver_user_principal_name": acting_user}
            row = conn.execute(
                text(f"""
                    select legacy.*
                    from {table_name} legacy
                    where legacy.workflow_request_id_pk = :workflow_request_id
                    and (
                        :organization_id is null
                        or {legacy_org_predicate}
                    )
                    for update
                """),
                {
                    "workflow_request_id": workflow_request_id,
                    "organization_id": organization_id,
                },
            ).mappings().first()
            if not row:
                return {"workflow_confirmation": "N", "error_code": WORKFLOW_INSTANCE_NOT_FOUND, "error": "Workflow request not found."}
            request = dict(row)
            if request.get("workflow_instance_id_fk"):
                return {"workflow_confirmation": "N", "error_code": WORKFLOW_INSTANCE_NOT_FOUND, "error": "Configured workflow instance was not found."}
            if request.get("workflow_status") != "PENDING_APPROVAL":
                return {
                    "workflow_confirmation": request.get("workflow_confirmation"),
                    "workflow_status": request.get("workflow_status"),
                    "error": "Workflow request is not pending approval.",
                }
            expected = request.get("approver_user_principal_name")
            if expected and expected.lower() != acting_user.lower():
                return {
                    "workflow_confirmation": "N",
                    "error_code": CURRENT_APPROVER_MISMATCH,
                    "error": "Workflow request is assigned to another approver.",
                    "expected_approver_user_principal_name": expected,
                    "approver_user_principal_name": acting_user,
                }
            request_payload = request.get("request_payload")
            if isinstance(request_payload, str):
                request_payload = json.loads(request_payload)
            if final_payload_enricher:
                request_payload = final_payload_enricher(request_payload, acting_user, approval_comments)
            execution_result = _execute_adapter(
                execution_adapter,
                request.get("workflow_action"),
                request_payload,
                conn,
            )
            execution_failed = isinstance(execution_result, dict) and execution_result.get("error")
            status = "EXECUTION_FAILED" if execution_failed else "EXECUTED"
            confirmation = "N" if execution_failed else "Y"
            conn.execute(
                text(f"""
                    update {table_name}
                    set
                        workflow_status = :status,
                        workflow_confirmation = :confirmation,
                        approver_user_principal_name = :acting_user,
                        approved_at = current_timestamp,
                        executed_at = case when :status = 'EXECUTED' then current_timestamp else executed_at end,
                        approval_comments = :approval_comments,
                        execution_result = cast(:execution_result as jsonb)
                    where workflow_request_id_pk = :workflow_request_id
                """),
                {
                    "workflow_request_id": workflow_request_id,
                    "status": status,
                    "confirmation": confirmation,
                    "acting_user": acting_user,
                    "approval_comments": approval_comments,
                    "execution_result": json.dumps(execution_result, default=str),
                },
            )
            return {
                "workflow_confirmation": confirmation,
                "workflow_status": status,
                "workflow_request_id": workflow_request_id,
                "routing_source": request.get("routing_source") or "LEGACY_ASSIGNED_APPROVER",
                "message": "Legacy workflow approved and executed." if not execution_failed else "Legacy workflow approved but execution failed.",
                "execution_result": execution_result,
            }
    finally:
        engine.dispose()


def reject_legacy_single_step_workflow(
    payload: dict,
    table_name: str,
    authenticated_user_principal_name: str | None = None,
    organization_id: int | None = None,
    organization_field_names: tuple[str, ...] = (),
) -> dict:
    workflow_request_id = payload.get("workflow_request_id") or payload.get("workflow_request_id_pk")
    acting_user = authenticated_user_principal_name or payload.get("acting_user_principal_name") or payload.get("approver_user_principal_name")
    rejection_comments = payload.get("rejection_comments")
    organization_id = _organization_id_from_payload(
        payload,
        organization_field_names,
        organization_id,
    )
    legacy_org_predicate = _legacy_payload_org_predicate(organization_field_names)
    if not workflow_request_id:
        return {"workflow_confirmation": "N", "error_code": WORKFLOW_REQUEST_ID_REQUIRED, "error": "workflow_request_id is required."}
    if not acting_user:
        return {"workflow_confirmation": "N", "error_code": APPROVER_USER_INVALID, "error": "approver_user_principal_name is required."}

    engine = db_engine()
    try:
        with engine.begin() as conn:
            assert_legacy_workflow_table_allowed(table_name)
            workflow_actions = get_user_workflow_actions(conn, acting_user, organization_id)
            if WORKFLOW_APPROVE_ACTION not in workflow_actions:
                return {"workflow_confirmation": "N", "error_code": WORKFLOW_APPROVE_PERMISSION_REQUIRED, "error": "Rejecting user does not have WORKFLOW/APPROVE rights.", "approver_user_principal_name": acting_user}
            row = conn.execute(
                text(f"""
                    select legacy.*
                    from {table_name} legacy
                    where legacy.workflow_request_id_pk = :workflow_request_id
                    and (
                        :organization_id is null
                        or {legacy_org_predicate}
                    )
                    for update
                """),
                {
                    "workflow_request_id": workflow_request_id,
                    "organization_id": organization_id,
                },
            ).mappings().first()
            if not row:
                return {"workflow_confirmation": "N", "error_code": WORKFLOW_INSTANCE_NOT_FOUND, "error": "Workflow request not found."}
            request = dict(row)
            if request.get("workflow_instance_id_fk"):
                return {"workflow_confirmation": "N", "error_code": WORKFLOW_INSTANCE_NOT_FOUND, "error": "Configured workflow instance was not found."}
            if request.get("workflow_status") != "PENDING_APPROVAL":
                return {
                    "workflow_confirmation": request.get("workflow_confirmation"),
                    "workflow_status": request.get("workflow_status"),
                    "error": "Workflow request is not pending approval.",
                }
            expected = request.get("approver_user_principal_name")
            if expected and expected.lower() != acting_user.lower():
                return {
                    "workflow_confirmation": "N",
                    "error_code": CURRENT_APPROVER_MISMATCH,
                    "error": "Workflow request is assigned to another approver.",
                    "expected_approver_user_principal_name": expected,
                    "approver_user_principal_name": acting_user,
                }
            request_payload = request.get("request_payload")
            if isinstance(request_payload, str):
                request_payload = json.loads(request_payload)
            conn.execute(
                text(f"""
                    update {table_name}
                    set
                        workflow_status = 'REJECTED',
                        workflow_confirmation = 'N',
                        approver_user_principal_name = :acting_user,
                        rejected_at = current_timestamp,
                        rejection_comments = :rejection_comments
                    where workflow_request_id_pk = :workflow_request_id
                """),
                {
                    "workflow_request_id": workflow_request_id,
                    "acting_user": acting_user,
                    "rejection_comments": rejection_comments,
                },
            )
            document_cleanup_result = cleanup_staged_workflow_document(
                request_payload,
                terminal_status="REJECTED",
            )
            response = {
                "workflow_confirmation": "N",
                "workflow_status": "REJECTED",
                "workflow_request_id": workflow_request_id,
                "routing_source": request.get("routing_source") or "LEGACY_ASSIGNED_APPROVER",
                "message": "Legacy workflow rejected.",
            }
            if document_cleanup_result.get("cleanup_attempted"):
                response["document_cleanup"] = document_cleanup_result
            return response
    finally:
        engine.dispose()
