import hashlib
import inspect
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    WORKFLOW_APPROVE_ACTION,
    WORKFLOW_SUBMIT_ACTION,
    get_active_user,
    user_has_workflow_action,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_catalog import (
    get_workflow_catalog_entry,
    validate_workflow_action,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    cleanup_staged_workflow_document,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    assert_legacy_workflow_table_allowed,
    dumps_json,
    ensure_legacy_workflow_table_schema,
    ensure_workflow_engine_schema,
    get_edges,
    get_nodes,
    get_published_version,
    get_workflow_definition,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_errors import (
    APPROVER_APPROVE_PERMISSION_MISSING,
    APPROVER_USER_INVALID,
    CURRENT_APPROVER_MISMATCH,
    ORGANIZATION_ID_REQUIRED,
    REQUESTER_SUBMIT_PERMISSION_MISSING,
    REQUESTER_USER_INVALID,
    REQUESTER_USER_REQUIRED,
    UNSUPPORTED_WORKFLOW_ACTION,
    WORKFLOW_CONFIGURATION_NOT_FOUND,
    WORKFLOW_EXECUTION_ALREADY_STARTED,
    WORKFLOW_IDENTIFIER_MISMATCH,
    WORKFLOW_INSTANCE_NOT_CONFIGURED,
    WORKFLOW_INSTANCE_NOT_FOUND,
    WORKFLOW_INSTANCE_STEP_ID_REQUIRED,
    WORKFLOW_INSTANCE_TYPE_MISMATCH,
    WORKFLOW_PENDING_CONFLICT,
    WORKFLOW_REQUEST_ID_REQUIRED,
    WORKFLOW_STEP_NOT_CURRENT,
    workflow_error_response,
)


TERMINAL_STATUSES = {"EXECUTED", "REJECTED", "EXECUTION_FAILED", "ERROR", "CANCELLED"}
EPHEMERAL_WORKFLOW_PAYLOAD_KEYS = {
    "file_path",
    "contract_file_path",
    "ap_invoice_local_file_path",
    "ar_invoice_local_file_path",
    "content_type",
}
DOMAIN_REFERENCE_FIELDS = {
    "ASSET_MASTER": ("asset_id", "asset_id_pk"),
    "CONTRACTS_MANAGEMENT": ("cont_id", "cont_id_pk"),
    "FLEET_MANAGEMENT": ("fleet_vehicle_id", "fleet_vehicle_id_pk"),
    "PAYROLL": ("payroll_run_id", "payroll_run_id_pk"),
    "ACCOUNTS_PAYABLE": ("ap_id", "ap_id_pk"),
    "ACCOUNTS_RECEIVABLE": ("ar_id", "ar_id_pk"),
}


def strip_internal_flags(payload: dict) -> dict:
    return {
        key: value
        for key, value in (payload or {}).items()
        if not (isinstance(key, str) and key.startswith("_") and key.endswith("_workflow_approved"))
    }


def _clean_workflow_payload(payload: dict) -> dict:
    return {
        key: value
        for key, value in strip_internal_flags(payload).items()
        if key not in EPHEMERAL_WORKFLOW_PAYLOAD_KEYS
    }


def start_workflow(
    workflow_code: str,
    workflow_action: str,
    organization_id: int,
    requester_user_principal_name: str,
    request_payload: dict,
    legacy_table_name: str,
    legacy_extra_values: dict[str, Any] | None = None,
    conn=None,
) -> dict:
    workflow_code = workflow_code.upper()
    workflow_action = workflow_action.upper()
    legacy_extra_values = legacy_extra_values or {}

    action_valid, action_error = validate_workflow_action(workflow_code, workflow_action)
    if not action_valid:
        return _error(action_error or UNSUPPORTED_WORKFLOW_ACTION, workflow_code, workflow_action, requester_user_principal_name)
    if not organization_id:
        return _error(ORGANIZATION_ID_REQUIRED, workflow_code, workflow_action, requester_user_principal_name)
    if not requester_user_principal_name:
        return _error(REQUESTER_USER_REQUIRED, workflow_code, workflow_action, requester_user_principal_name)

    assert_legacy_workflow_table_allowed(legacy_table_name)
    clean_payload = _clean_workflow_payload(request_payload)
    if conn is not None:
        return _start_workflow_in_conn(
            conn,
            workflow_code,
            workflow_action,
            organization_id,
            requester_user_principal_name,
            clean_payload,
            legacy_table_name,
            legacy_extra_values,
        )

    engine = db_engine()
    try:
        with engine.begin() as conn:
            return _start_workflow_in_conn(
                conn,
                workflow_code,
                workflow_action,
                organization_id,
                requester_user_principal_name,
                clean_payload,
                legacy_table_name,
                legacy_extra_values,
            )
    finally:
        engine.dispose()


def _start_workflow_in_conn(
    conn,
    workflow_code: str,
    workflow_action: str,
    organization_id: int,
    requester_user_principal_name: str,
    clean_payload: dict,
    legacy_table_name: str,
    legacy_extra_values: dict[str, Any],
) -> dict:
    ensure_workflow_engine_schema(conn)
    ensure_legacy_workflow_table_schema(conn, legacy_table_name)
    if not get_active_user(conn, requester_user_principal_name, organization_id):
        return _error(REQUESTER_USER_INVALID, workflow_code, workflow_action, requester_user_principal_name)
    has_submit = user_has_workflow_action(
        conn,
        requester_user_principal_name,
        WORKFLOW_SUBMIT_ACTION,
        organization_id,
    )
    if not has_submit:
        return _error(REQUESTER_SUBMIT_PERMISSION_MISSING, workflow_code, workflow_action, requester_user_principal_name)

    definition = get_workflow_definition(conn, workflow_code, organization_id)
    if not definition:
        return _error(WORKFLOW_CONFIGURATION_NOT_FOUND, workflow_code, workflow_action, requester_user_principal_name)

    version = get_published_version(
        conn,
        definition["workflow_definition_id_pk"],
        workflow_action,
    )
    if not version:
        return _error(WORKFLOW_CONFIGURATION_NOT_FOUND, workflow_code, workflow_action, requester_user_principal_name)

    nodes = get_nodes(conn, version["workflow_version_id_pk"])
    edges = get_edges(conn, version["workflow_version_id_pk"])
    route = _resolve_route_for_requester(
        nodes,
        edges,
        requester_user_principal_name,
        bool(version["allow_self_approval"]),
    )
    if "error" in route:
        return _error(route["error"], workflow_code, workflow_action, requester_user_principal_name)

    first_approver = route["approver_nodes"][0]
    first_approver_upn = first_approver["user_principal_name"]
    if not get_active_user(conn, first_approver_upn, organization_id):
        return _error(APPROVER_USER_INVALID, workflow_code, workflow_action, requester_user_principal_name, first_approver_upn)
    if not user_has_workflow_action(conn, first_approver_upn, WORKFLOW_APPROVE_ACTION, organization_id):
        return _error(APPROVER_APPROVE_PERMISSION_MISSING, workflow_code, workflow_action, requester_user_principal_name, first_approver_upn)

    idempotency_identity = _idempotency_identity(
        workflow_code,
        workflow_action,
        organization_id,
        requester_user_principal_name,
        clean_payload,
    )
    _lock_idempotent_submission(conn, idempotency_identity["lock_key"])
    existing_instance = _find_existing_pending_replay(
        conn,
        workflow_code,
        workflow_action,
        organization_id,
        requester_user_principal_name,
        clean_payload,
        idempotency_identity,
    )
    if existing_instance:
        return _pending_instance_response(
            conn,
            existing_instance,
            message="Existing pending workflow request returned for duplicate submission.",
            idempotent_replay=True,
        )
    _lock_pending_domain_conflict(conn, workflow_code, workflow_action, organization_id, idempotency_identity)
    pending_conflict = _find_pending_domain_conflict(
        conn,
        workflow_code,
        workflow_action,
        organization_id,
        idempotency_identity,
    )
    if pending_conflict:
        return _pending_conflict_response(
            conn,
            pending_conflict,
            idempotency_identity,
        )

    route_snapshot = {
        "workflow_version_id": version["workflow_version_id_pk"],
        "version_number": version["version_number"],
        "applies_to_action": version["applies_to_action"],
        "allow_self_approval": version["allow_self_approval"],
        "nodes": nodes,
        "edges": edges,
        "resolved_path_node_ids": [node["workflow_node_id_pk"] for node in route["path"]],
    }
    instance_id = conn.execute(
        text("""
            insert into workflow_instances (
                workflow_definition_id_fk,
                workflow_version_id_fk,
                workflow_code,
                organization_id_fk,
                workflow_action,
                requester_user_principal_name,
                current_node_id_fk,
                workflow_status,
                request_payload,
                route_snapshot,
                legacy_request_table
            ) values (
                :definition_id,
                :version_id,
                :workflow_code,
                :organization_id,
                :workflow_action,
                :requester,
                :current_node_id,
                'PENDING_APPROVAL',
                cast(:request_payload as jsonb),
                cast(:route_snapshot as jsonb),
                :legacy_table
            )
            returning workflow_instance_id_pk
        """),
        {
            "definition_id": definition["workflow_definition_id_pk"],
            "version_id": version["workflow_version_id_pk"],
            "workflow_code": workflow_code,
            "organization_id": organization_id,
            "workflow_action": workflow_action,
            "requester": requester_user_principal_name,
            "current_node_id": first_approver["workflow_node_id_pk"],
            "request_payload": dumps_json(clean_payload),
            "route_snapshot": dumps_json(route_snapshot),
            "legacy_table": legacy_table_name,
        },
    ).scalar_one()
    step_id = _insert_pending_step(
        conn,
        instance_id,
        first_approver["workflow_node_id_pk"],
        1,
        first_approver_upn,
    )
    legacy_request_id = _insert_legacy_request(
        conn,
        legacy_table_name,
        workflow_action,
        requester_user_principal_name,
        first_approver_upn,
        clean_payload,
        instance_id,
        version["workflow_version_id_pk"],
        legacy_extra_values,
    )
    conn.execute(
        text("""
            update workflow_instances
            set legacy_workflow_request_id = :legacy_request_id
            where workflow_instance_id_pk = :instance_id
        """),
        {"legacy_request_id": legacy_request_id, "instance_id": instance_id},
    )

    return {
        "workflow_confirmation": "N",
        "workflow_status": "PENDING_APPROVAL",
        "workflow_code": workflow_code,
        "workflow_action": workflow_action,
        "workflow_request_id": legacy_request_id,
        "workflow_instance_id": instance_id,
        "workflow_version_id": version["workflow_version_id_pk"],
        "workflow_version": version["version_number"],
        "current_step": 1,
        "workflow_instance_step_id": step_id,
        "domain_reference_id": _domain_reference_id_from_payload(workflow_code, workflow_action, clean_payload),
        "user_principal_name": requester_user_principal_name,
        "approver_user_principal_name": first_approver_upn,
        "current_approver_user_principal_name": first_approver_upn,
        "workflow_required": True,
        "business_operation_executed": False,
        "execution_result": None,
        "routing_source": "CONFIGURED_WORKFLOW",
        "message": "Workflow request submitted and pending approval.",
    }


def approve_workflow_step(
    workflow_code: str,
    payload: dict,
    execution_adapter: Callable[[str, dict], Any],
    legacy_table_name: str,
    final_payload_enricher: Callable[[dict, str, str | None], dict] | None = None,
    acting_user_principal_name: str | None = None,
    organization_id: int | None = None,
) -> dict:
    return _act_on_workflow_step(
        workflow_code=workflow_code,
        payload=payload,
        execution_adapter=execution_adapter,
        legacy_table_name=legacy_table_name,
        action="APPROVE",
        final_payload_enricher=final_payload_enricher,
        acting_user_principal_name=acting_user_principal_name,
        organization_id=organization_id,
    )


def reject_workflow_step(
    workflow_code: str,
    payload: dict,
    legacy_table_name: str,
    acting_user_principal_name: str | None = None,
    organization_id: int | None = None,
) -> dict:
    return _act_on_workflow_step(
        workflow_code=workflow_code,
        payload=payload,
        execution_adapter=None,
        legacy_table_name=legacy_table_name,
        action="REJECT",
        acting_user_principal_name=acting_user_principal_name,
        organization_id=organization_id,
    )


def _act_on_workflow_step(
    workflow_code: str,
    payload: dict,
    execution_adapter: Callable[[str, dict], Any] | None,
    legacy_table_name: str,
    action: str,
    final_payload_enricher: Callable[[dict, str, str | None], dict] | None = None,
    acting_user_principal_name: str | None = None,
    organization_id: int | None = None,
) -> dict:
    workflow_code = workflow_code.upper()
    workflow_request_id = payload.get("workflow_request_id") or payload.get("workflow_request_id_pk")
    workflow_instance_id = payload.get("workflow_instance_id") or payload.get("workflow_instance_id_pk")
    workflow_instance_step_id = payload.get("workflow_instance_step_id") or payload.get("workflow_instance_step_id_pk")
    acting_user = acting_user_principal_name or payload.get("acting_user_principal_name") or payload.get("approver_user_principal_name")
    comments = payload.get("approval_comments") if action == "APPROVE" else payload.get("rejection_comments")

    if not workflow_request_id and not workflow_instance_id:
        return workflow_error_response(
            WORKFLOW_REQUEST_ID_REQUIRED,
            "ERROR",
            error="workflow_request_id or workflow_instance_id is required.",
        )
    if not acting_user:
        return workflow_error_response("APPROVER_USER_INVALID", "ERROR")
    if workflow_instance_id and not workflow_instance_step_id:
        return workflow_error_response(
            WORKFLOW_INSTANCE_STEP_ID_REQUIRED,
            "ERROR",
            workflow_request_id=workflow_request_id,
            workflow_instance_id=workflow_instance_id,
        )

    assert_legacy_workflow_table_allowed(legacy_table_name)
    engine = db_engine()
    deferred_cleanup_payload = None
    deferred_cleanup_terminal_status = None
    deferred_response = None
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            ensure_legacy_workflow_table_schema(conn, legacy_table_name)
            instance = _get_instance_for_update(
                conn,
                workflow_instance_id,
                workflow_request_id,
                legacy_table_name,
                organization_id,
            )
            if not instance:
                return _legacy_not_found_or_unlinked(
                    conn,
                    workflow_request_id,
                    workflow_instance_id,
                    legacy_table_name,
                )

            identity_error = _validate_instance_identity(
                conn,
                instance,
                workflow_code,
                legacy_table_name,
                workflow_request_id,
            )
            if identity_error:
                return identity_error
            if not workflow_instance_id or not workflow_instance_step_id:
                return workflow_error_response(
                    WORKFLOW_INSTANCE_STEP_ID_REQUIRED,
                    "ERROR",
                    workflow_request_id=workflow_request_id,
                    workflow_instance_id=instance["workflow_instance_id_pk"],
                )

            if instance["workflow_status"] in TERMINAL_STATUSES:
                return _terminal_response(instance)
            if instance["workflow_status"] != "PENDING_APPROVAL":
                return {"workflow_confirmation": "N", "workflow_status": instance["workflow_status"], "error": "Workflow instance is not pending approval."}
            legacy_request_id = instance.get("legacy_workflow_request_id")

            step = _get_current_pending_step_for_update(conn, instance["workflow_instance_id_pk"])
            if not step:
                return {"workflow_confirmation": "N", "workflow_status": instance["workflow_status"], "error": "No pending workflow step found."}
            if workflow_instance_step_id:
                supplied_step = _get_step_for_update(conn, workflow_instance_step_id)
                if (
                    not supplied_step
                    or int(supplied_step["workflow_instance_id_fk"]) != int(instance["workflow_instance_id_pk"])
                    or int(supplied_step["workflow_instance_step_id_pk"]) != int(step["workflow_instance_step_id_pk"])
                    or supplied_step["step_status"] != "PENDING"
                ):
                    return workflow_error_response(
                        WORKFLOW_STEP_NOT_CURRENT,
                        "PENDING_APPROVAL",
                        workflow_request_id=legacy_request_id,
                        workflow_instance_id=instance["workflow_instance_id_pk"],
                        workflow_instance_step_id=workflow_instance_step_id,
                        current_workflow_instance_step_id=step["workflow_instance_step_id_pk"],
                        current_approver_user_principal_name=step["assigned_approver_user_principal_name"],
                    )

            expected_approver = step["assigned_approver_user_principal_name"]
            if expected_approver.lower() != acting_user.lower():
                return workflow_error_response(
                    CURRENT_APPROVER_MISMATCH,
                    "PENDING_APPROVAL",
                    workflow_request_id=legacy_request_id,
                    workflow_instance_id=instance["workflow_instance_id_pk"],
                    workflow_instance_step_id=step["workflow_instance_step_id_pk"],
                    expected_approver_user_principal_name=expected_approver,
                    approver_user_principal_name=acting_user,
                )

            if not user_has_workflow_action(conn, acting_user, WORKFLOW_APPROVE_ACTION, instance["organization_id_fk"]):
                return workflow_error_response(
                    APPROVER_APPROVE_PERMISSION_MISSING,
                    "PENDING_APPROVAL",
                    workflow_request_id=legacy_request_id,
                    workflow_instance_id=instance["workflow_instance_id_pk"],
                    workflow_instance_step_id=step["workflow_instance_step_id_pk"],
                    approver_user_principal_name=acting_user,
                )

            if action == "REJECT":
                request_payload = _dict_from_json_value(instance.get("request_payload"))
                _mark_step_rejected(conn, step["workflow_instance_step_id_pk"], acting_user, comments)
                _mark_instance_terminal(conn, instance["workflow_instance_id_pk"], "REJECTED", None)
                if legacy_request_id is not None:
                    _update_legacy_terminal(
                        conn,
                        legacy_table_name,
                        legacy_request_id,
                        "REJECTED",
                        "N",
                        acting_user,
                        rejection_comments=comments,
                    )
                deferred_cleanup_payload = request_payload
                deferred_cleanup_terminal_status = "REJECTED"
                deferred_response = {
                    "workflow_confirmation": "N",
                    "workflow_status": "REJECTED",
                    "workflow_code": instance["workflow_code"],
                    "workflow_action": instance["workflow_action"],
                    "workflow_request_id": legacy_request_id,
                    "workflow_instance_id": instance["workflow_instance_id_pk"],
                    "workflow_instance_step_id": step["workflow_instance_step_id_pk"],
                    "domain_reference_id": _domain_reference_id_from_payload(
                        instance["workflow_code"],
                        instance["workflow_action"],
                        request_payload,
                    ),
                    "current_approver_user_principal_name": None,
                    "workflow_required": True,
                    "business_operation_executed": False,
                    "execution_result": None,
                    "message": "Workflow request rejected.",
                }
            else:
                route_snapshot = _dict_from_json_value(instance.get("route_snapshot"))
                next_approver = _next_approver_from_snapshot(route_snapshot, step["workflow_node_id_fk"])
                if next_approver:
                    if not get_active_user(conn, next_approver["user_principal_name"], instance["organization_id_fk"]):
                        return {"workflow_confirmation": "N", "workflow_status": "PENDING_APPROVAL", "error": "APPROVER_USER_INVALID"}
                    if not user_has_workflow_action(conn, next_approver["user_principal_name"], WORKFLOW_APPROVE_ACTION, instance["organization_id_fk"]):
                        return {"workflow_confirmation": "N", "workflow_status": "PENDING_APPROVAL", "error": "APPROVER_APPROVE_PERMISSION_MISSING"}
                    _mark_step_approved(conn, step["workflow_instance_step_id_pk"], acting_user, comments)
                    next_sequence = int(step["step_sequence"]) + 1
                    next_step_id = _insert_pending_step(
                        conn,
                        instance["workflow_instance_id_pk"],
                        next_approver["workflow_node_id_pk"],
                        next_sequence,
                        next_approver["user_principal_name"],
                    )
                    conn.execute(
                        text("""
                            update workflow_instances
                            set current_node_id_fk = :current_node_id
                            where workflow_instance_id_pk = :instance_id
                        """),
                        {
                            "current_node_id": next_approver["workflow_node_id_pk"],
                            "instance_id": instance["workflow_instance_id_pk"],
                        },
                    )
                    if legacy_request_id is not None:
                        _update_legacy_current_approver(
                            conn,
                            legacy_table_name,
                            legacy_request_id,
                            next_approver["user_principal_name"],
                            comments,
                        )
                    return {
                        "workflow_confirmation": "N",
                        "workflow_status": "PENDING_APPROVAL",
                        "workflow_code": instance["workflow_code"],
                        "workflow_action": instance["workflow_action"],
                        "workflow_request_id": legacy_request_id,
                        "workflow_instance_id": instance["workflow_instance_id_pk"],
                        "workflow_instance_step_id": next_step_id,
                        "domain_reference_id": _domain_reference_id_from_payload(
                            instance["workflow_code"],
                            instance["workflow_action"],
                            _dict_from_json_value(instance.get("request_payload")),
                        ),
                        "current_step": next_sequence,
                        "approver_user_principal_name": next_approver["user_principal_name"],
                        "current_approver_user_principal_name": next_approver["user_principal_name"],
                        "workflow_required": True,
                        "business_operation_executed": False,
                        "execution_result": None,
                        "message": "Workflow step approved. Next approval step is pending.",
                    }

                execution_key = f"{workflow_code}:{instance['workflow_instance_id_pk']}"
                if not _claim_final_execution(conn, instance["workflow_instance_id_pk"], execution_key):
                    return workflow_error_response(
                        WORKFLOW_EXECUTION_ALREADY_STARTED,
                        "PENDING_APPROVAL",
                        workflow_request_id=legacy_request_id,
                        workflow_instance_id=instance["workflow_instance_id_pk"],
                    )
                _mark_step_approved(conn, step["workflow_instance_step_id_pk"], acting_user, comments)
                request_payload = _dict_from_json_value(instance.get("request_payload"))
                request_payload = {
                    **request_payload,
                    "workflow_instance_id": instance["workflow_instance_id_pk"],
                    "workflow_execution_key": execution_key,
                }
                if final_payload_enricher:
                    request_payload = final_payload_enricher(request_payload, acting_user, comments)
                execution_result = _execute_adapter(
                    execution_adapter,
                    instance["workflow_action"],
                    request_payload,
                    conn,
                ) if execution_adapter else None
                execution_failed = isinstance(execution_result, dict) and bool(execution_result.get("error"))
                workflow_status = "EXECUTION_FAILED" if execution_failed else "EXECUTED"
                workflow_confirmation = "N" if execution_failed else "Y"
                domain_reference_id = _domain_reference_id_from_execution_result(
                    instance["workflow_code"],
                    execution_result,
                ) or _domain_reference_id_from_payload(
                    instance["workflow_code"],
                    instance["workflow_action"],
                    request_payload,
                )
                _mark_instance_terminal(
                    conn,
                    instance["workflow_instance_id_pk"],
                    workflow_status,
                    execution_result,
                    current_node_id=_end_node_id_from_snapshot(route_snapshot),
                )
                if legacy_request_id is not None:
                    _update_legacy_terminal(
                        conn,
                        legacy_table_name,
                        legacy_request_id,
                        workflow_status,
                        workflow_confirmation,
                        acting_user,
                        approval_comments=comments,
                        execution_result=execution_result,
                    )
                return {
                    "workflow_confirmation": workflow_confirmation,
                    "workflow_status": workflow_status,
                    "workflow_code": instance["workflow_code"],
                    "workflow_action": instance["workflow_action"],
                    "workflow_request_id": legacy_request_id,
                    "workflow_instance_id": instance["workflow_instance_id_pk"],
                    "workflow_instance_step_id": step["workflow_instance_step_id_pk"],
                    "domain_reference_id": domain_reference_id,
                    "current_approver_user_principal_name": None,
                    "workflow_required": True,
                    "business_operation_executed": workflow_status == "EXECUTED",
                    "message": "Workflow approved and executed." if not execution_failed else "Workflow approved but execution failed.",
                    "execution_result": execution_result,
                }
    finally:
        engine.dispose()
    if deferred_response is not None:
        document_cleanup_result = cleanup_staged_workflow_document(
            deferred_cleanup_payload,
            terminal_status=deferred_cleanup_terminal_status,
        )
        if document_cleanup_result.get("cleanup_attempted"):
            deferred_response["document_cleanup"] = document_cleanup_result
    return deferred_response


def _resolve_route_for_requester(nodes: list[dict], edges: list[dict], requester: str, allow_self_approval: bool) -> dict:
    nodes_by_id = {node["workflow_node_id_pk"]: node for node in nodes}
    outgoing: dict[int, list[int]] = {}
    for edge in edges:
        if (edge.get("edge_type") or "SEQUENTIAL").upper() != "SEQUENTIAL":
            return {"error": "UNSUPPORTED_EDGE_TYPE"}
        if edge.get("condition_json"):
            return {"error": "UNSUPPORTED_EDGE_CONDITION"}
        outgoing.setdefault(edge["source_node_id_fk"], []).append(edge["target_node_id_fk"])
    exact_requester_nodes = [
        node for node in nodes
        if node["node_type"] == "REQUESTER" and (node.get("user_principal_name") or "").lower() == requester.lower()
    ]
    wildcard_requester_nodes = [
        node for node in nodes
        if node["node_type"] == "REQUESTER" and not node.get("user_principal_name")
    ]
    requester_nodes = exact_requester_nodes or wildcard_requester_nodes
    if len(requester_nodes) != 1:
        return {"error": "REQUESTER_ROUTE_NOT_FOUND"}
    path = [requester_nodes[0]]
    approvers = []
    seen = set()
    current_id = requester_nodes[0]["workflow_node_id_pk"]
    while True:
        if current_id in seen:
            return {"error": "UNSUPPORTED_CYCLE"}
        seen.add(current_id)
        targets = outgoing.get(current_id, [])
        if len(targets) != 1:
            return {"error": "REQUESTER_ROUTE_NOT_FOUND" if not targets else "UNSUPPORTED_BRANCHING"}
        next_node = nodes_by_id.get(targets[0])
        if not next_node:
            return {"error": "EDGE_TARGET_INVALID"}
        path.append(next_node)
        if next_node["node_type"] == "APPROVER":
            if not allow_self_approval and (next_node.get("user_principal_name") or "").lower() == requester.lower():
                return {"error": "SELF_APPROVAL_NOT_ALLOWED"}
            approvers.append(next_node)
        elif next_node["node_type"] == "END":
            if outgoing.get(next_node["workflow_node_id_pk"]):
                return {"error": "END_NODE_HAS_OUTGOING_EDGE"}
            break
        else:
            return {"error": "UNSUPPORTED_ROUTE_NODE_TYPE"}
        current_id = next_node["workflow_node_id_pk"]
    if not approvers:
        return {"error": "REQUESTER_PATH_HAS_NO_APPROVER"}
    return {"path": path, "approver_nodes": approvers}


def _next_approver_from_snapshot(route_snapshot: dict, current_node_id: int) -> dict | None:
    route_snapshot = _dict_from_json_value(route_snapshot)
    nodes = {node["workflow_node_id_pk"]: node for node in route_snapshot.get("nodes", [])}
    outgoing = {
        edge["source_node_id_fk"]: edge["target_node_id_fk"]
        for edge in route_snapshot.get("edges", [])
        if (edge.get("edge_type") or "SEQUENTIAL").upper() == "SEQUENTIAL"
        and not edge.get("condition_json")
    }
    next_id = outgoing.get(current_node_id)
    while next_id:
        node = nodes.get(next_id)
        if not node or node["node_type"] == "END":
            return None
        if node["node_type"] == "APPROVER":
            return node
        next_id = outgoing.get(next_id)
    return None


def _insert_pending_step(conn, instance_id: int, node_id: int, sequence: int, approver: str) -> int:
    return conn.execute(
        text("""
            insert into workflow_instance_steps (
                workflow_instance_id_fk,
                workflow_node_id_fk,
                step_sequence,
                assigned_approver_user_principal_name,
                step_status
            ) values (
                :instance_id,
                :node_id,
                :sequence,
                :approver,
                'PENDING'
            )
            returning workflow_instance_step_id_pk
        """),
        {
            "instance_id": instance_id,
            "node_id": node_id,
            "sequence": sequence,
            "approver": approver,
        },
    ).scalar_one()


def _insert_legacy_request(
    conn,
    table_name: str,
    workflow_action: str,
    requester: str,
    approver: str,
    request_payload: dict,
    instance_id: int,
    version_id: int,
    extra_values: dict[str, Any],
) -> int:
    ensure_legacy_workflow_table_schema(conn, table_name)
    columns = [
        "workflow_action",
        "workflow_status",
        "workflow_confirmation",
        "requester_user_principal_name",
        "approver_user_principal_name",
        "request_payload",
        "workflow_instance_id_fk",
        "workflow_version_id_fk",
        "routing_source",
    ]
    values = [
        ":workflow_action",
        "'PENDING_APPROVAL'",
        "'N'",
        ":requester",
        ":approver",
        "cast(:request_payload as jsonb)",
        ":instance_id",
        ":version_id",
        "'CONFIGURED_WORKFLOW'",
    ]
    params = {
        "workflow_action": workflow_action,
        "requester": requester,
        "approver": approver,
        "request_payload": dumps_json(request_payload),
        "instance_id": instance_id,
        "version_id": version_id,
    }
    for key, value in extra_values.items():
        columns.append(key)
        values.append(f":{key}")
        params[key] = value
    return conn.execute(
        text(f"""
            insert into {table_name} ({", ".join(columns)})
            values ({", ".join(values)})
            returning workflow_request_id_pk
        """),
        params,
    ).scalar_one()


def _get_instance_for_update(
    conn,
    instance_id: int | None,
    request_id: int | None,
    legacy_table_name: str,
    organization_id: int | None = None,
) -> dict | None:
    ensure_legacy_workflow_table_schema(conn, legacy_table_name)
    if instance_id:
        row = conn.execute(
            text("""
                select *
                from workflow_instances
                where workflow_instance_id_pk = :instance_id
                and (:organization_id is null or organization_id_fk = :organization_id)
                for update
            """),
            {
                "instance_id": instance_id,
                "organization_id": organization_id,
            },
        ).mappings().first()
    else:
        row = conn.execute(
            text("""
                select wi.*
                from workflow_instances wi
                join """ + legacy_table_name + """ legacy
                    on legacy.workflow_instance_id_fk = wi.workflow_instance_id_pk
                where legacy.workflow_request_id_pk = :request_id
                and (:organization_id is null or wi.organization_id_fk = :organization_id)
                for update of wi
            """),
            {
                "request_id": request_id,
                "organization_id": organization_id,
            },
        ).mappings().first()
    return dict(row) if row else None


def _get_current_pending_step_for_update(conn, instance_id: int) -> dict | None:
    row = conn.execute(
        text("""
            select *
            from workflow_instance_steps
            where workflow_instance_id_fk = :instance_id
            and step_status = 'PENDING'
            order by step_sequence
            limit 1
            for update
        """),
        {"instance_id": instance_id},
    ).mappings().first()
    return dict(row) if row else None


def _get_step_for_update(conn, step_id: int) -> dict | None:
    row = conn.execute(
        text("""
            select *
            from workflow_instance_steps
            where workflow_instance_step_id_pk = :step_id
            for update
        """),
        {"step_id": step_id},
    ).mappings().first()
    return dict(row) if row else None


def _mark_step_approved(conn, step_id: int, acting_user: str, comments: str | None) -> None:
    conn.execute(
        text("""
            update workflow_instance_steps
            set
                step_status = 'APPROVED',
                acted_by_user_principal_name = :acting_user,
                approval_comments = :comments,
                acted_at = current_timestamp
            where workflow_instance_step_id_pk = :step_id
            and step_status = 'PENDING'
        """),
        {"step_id": step_id, "acting_user": acting_user, "comments": comments},
    )


def _mark_step_rejected(conn, step_id: int, acting_user: str, comments: str | None) -> None:
    conn.execute(
        text("""
            update workflow_instance_steps
            set
                step_status = 'REJECTED',
                acted_by_user_principal_name = :acting_user,
                rejection_comments = :comments,
                acted_at = current_timestamp
            where workflow_instance_step_id_pk = :step_id
            and step_status = 'PENDING'
        """),
        {"step_id": step_id, "acting_user": acting_user, "comments": comments},
    )


def _mark_instance_terminal(
    conn,
    instance_id: int,
    status: str,
    execution_result: Any,
    current_node_id: int | None = None,
) -> None:
    conn.execute(
        text("""
            update workflow_instances
            set
                current_node_id_fk = :current_node_id,
                workflow_status = :status,
                execution_result = cast(:execution_result as jsonb),
                completed_at = current_timestamp,
                execution_completed_at = current_timestamp
            where workflow_instance_id_pk = :instance_id
            and workflow_status = 'PENDING_APPROVAL'
        """),
        {
            "instance_id": instance_id,
            "status": status,
            "execution_result": dumps_json(execution_result),
            "current_node_id": current_node_id,
        },
    )


def _claim_final_execution(conn, instance_id: int, execution_key: str) -> bool:
    result = conn.execute(
        text("""
            update workflow_instances
            set
                execution_key = :execution_key,
                execution_started_at = current_timestamp
            where workflow_instance_id_pk = :instance_id
            and workflow_status = 'PENDING_APPROVAL'
            and execution_started_at is null
        """),
        {"instance_id": instance_id, "execution_key": execution_key},
    )
    return result.rowcount == 1


def _update_legacy_current_approver(conn, table_name: str, request_id: int, approver: str, approval_comments: str | None) -> None:
    ensure_legacy_workflow_table_schema(conn, table_name)
    conn.execute(
        text(f"""
            update {table_name}
            set
                approver_user_principal_name = :approver,
                approval_comments = :approval_comments
            where workflow_request_id_pk = :request_id
        """),
        {"request_id": request_id, "approver": approver, "approval_comments": approval_comments},
    )


def _update_legacy_terminal(
    conn,
    table_name: str,
    request_id: int,
    status: str,
    confirmation: str,
    acting_user: str,
    approval_comments: str | None = None,
    rejection_comments: str | None = None,
    execution_result: Any = None,
) -> None:
    ensure_legacy_workflow_table_schema(conn, table_name)
    conn.execute(
        text(f"""
            update {table_name}
            set
                workflow_status = :status,
                workflow_confirmation = :confirmation,
                approver_user_principal_name = :acting_user,
                approved_at = case when :status in ('EXECUTED', 'EXECUTION_FAILED') then current_timestamp else approved_at end,
                rejected_at = case when :status = 'REJECTED' then current_timestamp else rejected_at end,
                executed_at = case when :status = 'EXECUTED' then current_timestamp else executed_at end,
                approval_comments = coalesce(:approval_comments, approval_comments),
                rejection_comments = coalesce(:rejection_comments, rejection_comments),
                execution_result = cast(:execution_result as jsonb)
            where workflow_request_id_pk = :request_id
        """),
        {
            "request_id": request_id,
            "status": status,
            "confirmation": confirmation,
            "acting_user": acting_user,
            "approval_comments": approval_comments,
            "rejection_comments": rejection_comments,
            "execution_result": dumps_json(execution_result),
        },
    )


def _terminal_response(instance: dict) -> dict:
    execution_result = instance.get("execution_result")
    request_payload = _dict_from_json_value(instance.get("request_payload"))
    return {
        "workflow_confirmation": "Y" if instance["workflow_status"] == "EXECUTED" else "N",
        "workflow_status": instance["workflow_status"],
        "workflow_code": instance.get("workflow_code"),
        "workflow_action": instance.get("workflow_action"),
        "workflow_request_id": instance.get("legacy_workflow_request_id"),
        "workflow_instance_id": instance["workflow_instance_id_pk"],
        "workflow_instance_step_id": None,
        "domain_reference_id": _domain_reference_id_from_execution_result(
            instance.get("workflow_code"),
            execution_result,
        ) or _domain_reference_id_from_payload(
            instance.get("workflow_code"),
            instance.get("workflow_action"),
            request_payload,
        ),
        "current_approver_user_principal_name": None,
        "workflow_required": True,
        "business_operation_executed": instance["workflow_status"] == "EXECUTED",
        "already_terminal": True,
        "message": "Workflow request is already terminal.",
        "execution_result": execution_result,
    }


def _legacy_not_found_or_unlinked(
    conn,
    workflow_request_id: int | None,
    workflow_instance_id: int | None,
    legacy_table_name: str,
) -> dict:
    if workflow_instance_id:
        return workflow_error_response(
            WORKFLOW_INSTANCE_NOT_FOUND,
            "ERROR",
            workflow_instance_id=workflow_instance_id,
            workflow_request_id=workflow_request_id,
        )

    legacy_request = _get_legacy_request_link_state(
        conn,
        workflow_request_id,
        legacy_table_name,
    )
    if not legacy_request:
        return workflow_error_response(
            WORKFLOW_INSTANCE_NOT_FOUND,
            "ERROR",
            workflow_request_id=workflow_request_id,
        )

    if legacy_request.get("workflow_instance_id_fk") is None:
        return workflow_error_response(
            WORKFLOW_INSTANCE_NOT_CONFIGURED,
            "ERROR",
            workflow_request_id=workflow_request_id,
            error="Workflow request is a legacy request and has no configured workflow instance.",
        )

    return workflow_error_response(
        WORKFLOW_INSTANCE_NOT_FOUND,
        "ERROR",
        workflow_instance_id=legacy_request.get("workflow_instance_id_fk"),
        workflow_request_id=workflow_request_id,
    )


def _get_legacy_request_link_state(
    conn,
    workflow_request_id: int | None,
    legacy_table_name: str,
) -> dict | None:
    if not workflow_request_id:
        return None
    ensure_legacy_workflow_table_schema(conn, legacy_table_name)
    row = conn.execute(
        text(f"""
            select
                workflow_request_id_pk,
                workflow_instance_id_fk
            from {legacy_table_name}
            where workflow_request_id_pk = :workflow_request_id
            limit 1
            for update
        """),
        {"workflow_request_id": workflow_request_id},
    ).mappings().first()
    return dict(row) if row else None


def _validate_instance_identity(
    conn,
    instance: dict,
    expected_workflow_code: str,
    expected_legacy_table: str,
    supplied_workflow_request_id: int | None,
) -> dict | None:
    actual_workflow_code = (instance.get("workflow_code") or "").upper()
    if actual_workflow_code != expected_workflow_code.upper():
        return workflow_error_response(
            WORKFLOW_INSTANCE_TYPE_MISMATCH,
            "ERROR",
            workflow_request_id=supplied_workflow_request_id,
            workflow_instance_id=instance.get("workflow_instance_id_pk"),
            expected_workflow_code=expected_workflow_code.upper(),
            actual_workflow_code=actual_workflow_code,
        )

    actual_legacy_table = instance.get("legacy_request_table")
    if actual_legacy_table and actual_legacy_table != expected_legacy_table:
        return workflow_error_response(
            WORKFLOW_INSTANCE_TYPE_MISMATCH,
            "ERROR",
            workflow_request_id=supplied_workflow_request_id,
            workflow_instance_id=instance.get("workflow_instance_id_pk"),
            expected_legacy_request_table=expected_legacy_table,
            actual_legacy_request_table=actual_legacy_table,
        )

    if not supplied_workflow_request_id:
        return None

    legacy_request_id = instance.get("legacy_workflow_request_id")
    if legacy_request_id is not None:
        if str(legacy_request_id) != str(supplied_workflow_request_id):
            return workflow_error_response(
                WORKFLOW_IDENTIFIER_MISMATCH,
                "ERROR",
                workflow_request_id=supplied_workflow_request_id,
                workflow_instance_id=instance.get("workflow_instance_id_pk"),
                expected_workflow_request_id=legacy_request_id,
            )
        return None

    row = conn.execute(
        text(f"""
            select workflow_request_id_pk
            from {expected_legacy_table}
            where workflow_request_id_pk = :workflow_request_id
            and workflow_instance_id_fk = :workflow_instance_id
            limit 1
            for update
        """),
        {
            "workflow_request_id": supplied_workflow_request_id,
            "workflow_instance_id": instance.get("workflow_instance_id_pk"),
        },
    ).first()
    if not row:
        return workflow_error_response(
            WORKFLOW_IDENTIFIER_MISMATCH,
            "ERROR",
            workflow_request_id=supplied_workflow_request_id,
            workflow_instance_id=instance.get("workflow_instance_id_pk"),
        )
    return None


def _execute_adapter(
    execution_adapter: Callable[[str, dict], Any],
    workflow_action: str,
    request_payload: dict,
    conn,
) -> Any:
    if _callable_accepts_conn(execution_adapter):
        return execution_adapter(workflow_action, request_payload, conn=conn)
    return execution_adapter(workflow_action, request_payload)


def _callable_accepts_conn(callback: Callable) -> bool:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return True
    return any(
        parameter_name == "conn"
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter_name, parameter in signature.parameters.items()
    )


def _error(
    code: str,
    workflow_code: str,
    workflow_action: str,
    requester: str | None,
    approver: str | None = None,
) -> dict:
    response = {
        "workflow_confirmation": "N",
        "workflow_status": "REJECTED",
        "workflow_code": workflow_code,
        "workflow_action": workflow_action,
        "user_principal_name": requester,
        "error_code": code,
        "error": code,
    }
    if approver:
        response["approver_user_principal_name"] = approver
    return response


def _idempotency_identity(
    workflow_code: str,
    workflow_action: str,
    organization_id: int,
    requester_user_principal_name: str,
    clean_payload: dict,
) -> dict[str, Any]:
    explicit_key = _payload_text_value(clean_payload, "service_request_id") or _payload_text_value(clean_payload, "idempotency_key")
    domain_reference_id = _domain_reference_id_from_payload(workflow_code, workflow_action, clean_payload)
    payload_fingerprint = hashlib.sha256(
        _canonical_json(clean_payload).encode("utf-8")
    ).hexdigest()
    key_parts = [
        workflow_code,
        workflow_action,
        str(organization_id),
    ]
    if explicit_key:
        key_parts.extend(("requester", requester_user_principal_name.lower(), "service_request", explicit_key))
    elif domain_reference_id is not None:
        key_parts.extend(("domain_reference", str(domain_reference_id)))
    else:
        key_parts.extend(("payload", payload_fingerprint))
    return {
        "explicit_key": explicit_key,
        "domain_reference_id": domain_reference_id,
        "payload_fingerprint": payload_fingerprint,
        "lock_key": ":".join(key_parts),
    }


def _payload_text_value(payload: dict, field_name: str) -> str | None:
    value = (payload or {}).get(field_name)
    if value is None or value == "":
        return None
    return str(value)


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload or {}, sort_keys=True, default=str, separators=(",", ":"))


def _domain_reference_id_from_payload(
    workflow_code: str | None,
    workflow_action: str | None,
    payload: dict,
) -> Any:
    if (workflow_action or "").upper() == "CREATE":
        return None
    for field_name in DOMAIN_REFERENCE_FIELDS.get((workflow_code or "").upper(), ()):
        value = (payload or {}).get(field_name)
        if value is not None and value != "":
            return value
    return None


def _domain_reference_id_from_execution_result(
    workflow_code: str | None,
    execution_result: Any,
) -> Any:
    if not isinstance(execution_result, dict):
        return None
    for field_name in DOMAIN_REFERENCE_FIELDS.get((workflow_code or "").upper(), ()):
        value = execution_result.get(field_name)
        if value is not None and value != "":
            return value
    return None


def _lock_idempotent_submission(conn, lock_key: str) -> None:
    conn.execute(
        text("select pg_advisory_xact_lock(hashtext(:lock_namespace), hashtext(:lock_key))"),
        {
            "lock_namespace": "UETransportERP:workflow_submission",
            "lock_key": lock_key,
        },
    )


def _lock_pending_domain_conflict(
    conn,
    workflow_code: str,
    workflow_action: str,
    organization_id: int,
    idempotency_identity: dict[str, Any],
) -> None:
    domain_reference_id = idempotency_identity.get("domain_reference_id")
    if domain_reference_id is None:
        return
    lock_key = ":".join(
        [
            workflow_code,
            workflow_action,
            str(organization_id),
            str(domain_reference_id),
        ]
    )
    conn.execute(
        text("select pg_advisory_xact_lock(hashtext(:lock_namespace), hashtext(:lock_key))"),
        {
            "lock_namespace": "UETransportERP:workflow_domain_conflict",
            "lock_key": lock_key,
        },
    )


def _find_existing_pending_replay(
    conn,
    workflow_code: str,
    workflow_action: str,
    organization_id: int,
    requester_user_principal_name: str,
    clean_payload: dict,
    idempotency_identity: dict[str, Any],
) -> dict | None:
    params: dict[str, Any] = {
        "workflow_code": workflow_code,
        "workflow_action": workflow_action,
        "organization_id": organization_id,
        "requester_user_principal_name": requester_user_principal_name,
        "request_payload": dumps_json(clean_payload),
    }
    if idempotency_identity.get("explicit_key"):
        row = conn.execute(
            text("""
                select wi.*
                from workflow_instances wi
                where wi.workflow_code = :workflow_code
                and wi.workflow_action = :workflow_action
                and wi.organization_id_fk = :organization_id
                and lower(wi.requester_user_principal_name) = lower(:requester_user_principal_name)
                and wi.workflow_status = 'PENDING_APPROVAL'
                and (
                    (wi.request_payload ->> 'service_request_id') = :explicit_key
                    or (wi.request_payload ->> 'idempotency_key') = :explicit_key
                )
                order by wi.started_at desc, wi.workflow_instance_id_pk desc
                limit 1
                for update
            """),
            {
                **params,
                "explicit_key": idempotency_identity["explicit_key"],
            },
        )
        row = row.mappings().first()
        return dict(row) if row else None

    if idempotency_identity.get("domain_reference_id") is not None:
        return None

    row = conn.execute(
        text("""
            select wi.*
            from workflow_instances wi
            where wi.workflow_code = :workflow_code
            and wi.workflow_action = :workflow_action
            and wi.organization_id_fk = :organization_id
            and lower(wi.requester_user_principal_name) = lower(:requester_user_principal_name)
            and wi.workflow_status = 'PENDING_APPROVAL'
            and wi.request_payload = cast(:request_payload as jsonb)
            order by wi.started_at desc, wi.workflow_instance_id_pk desc
            limit 1
            for update
        """),
        params,
    ).mappings().first()
    return dict(row) if row else None


def _find_pending_domain_conflict(
    conn,
    workflow_code: str,
    workflow_action: str,
    organization_id: int,
    idempotency_identity: dict[str, Any],
) -> dict | None:
    domain_reference_id = idempotency_identity.get("domain_reference_id")
    if domain_reference_id is None:
        return None

    domain_clauses = [
        f"(wi.request_payload ->> '{field_name}') = :domain_reference_id"
        for field_name in DOMAIN_REFERENCE_FIELDS.get(workflow_code, ())
    ]
    if not domain_clauses:
        return None

    params: dict[str, Any] = {
        "workflow_code": workflow_code,
        "workflow_action": workflow_action,
        "organization_id": organization_id,
        "domain_reference_id": str(domain_reference_id),
    }
    explicit_filter = ""
    explicit_key = idempotency_identity.get("explicit_key")
    if explicit_key:
        params["explicit_key"] = explicit_key
        explicit_filter = """
            and coalesce(wi.request_payload ->> 'service_request_id', '') <> :explicit_key
            and coalesce(wi.request_payload ->> 'idempotency_key', '') <> :explicit_key
        """

    row = conn.execute(
        text(f"""
            select wi.*
            from workflow_instances wi
            where wi.workflow_code = :workflow_code
            and wi.workflow_action = :workflow_action
            and wi.organization_id_fk = :organization_id
            and wi.workflow_status = 'PENDING_APPROVAL'
            and ({" or ".join(domain_clauses)})
            {explicit_filter}
            order by wi.started_at desc, wi.workflow_instance_id_pk desc
            limit 1
            for update
        """),
        params,
    ).mappings().first()
    return dict(row) if row else None


def _pending_conflict_response(
    conn,
    instance: dict,
    idempotency_identity: dict[str, Any],
) -> dict:
    current_step = _get_current_pending_step_for_update(conn, instance["workflow_instance_id_pk"])
    return workflow_error_response(
        WORKFLOW_PENDING_CONFLICT,
        "PENDING_APPROVAL",
        workflow_code=instance.get("workflow_code"),
        workflow_action=instance.get("workflow_action"),
        workflow_request_id=instance.get("legacy_workflow_request_id"),
        workflow_instance_id=instance["workflow_instance_id_pk"],
        workflow_instance_step_id=current_step.get("workflow_instance_step_id_pk") if current_step else None,
        domain_reference_id=idempotency_identity.get("domain_reference_id"),
        requester_user_principal_name=instance.get("requester_user_principal_name"),
        current_approver_user_principal_name=current_step.get("assigned_approver_user_principal_name") if current_step else None,
        workflow_required=True,
        business_operation_executed=False,
        execution_result=None,
    )


def _pending_instance_response(
    conn,
    instance: dict,
    message: str,
    idempotent_replay: bool = False,
) -> dict:
    current_step = _get_current_pending_step_for_update(conn, instance["workflow_instance_id_pk"])
    request_payload = _dict_from_json_value(instance.get("request_payload"))
    return {
        "workflow_confirmation": "N",
        "workflow_status": "PENDING_APPROVAL",
        "workflow_code": instance.get("workflow_code"),
        "workflow_action": instance.get("workflow_action"),
        "workflow_request_id": instance.get("legacy_workflow_request_id"),
        "workflow_instance_id": instance["workflow_instance_id_pk"],
        "workflow_instance_step_id": current_step.get("workflow_instance_step_id_pk") if current_step else None,
        "domain_reference_id": _domain_reference_id_from_payload(
            instance.get("workflow_code"),
            instance.get("workflow_action"),
            request_payload,
        ),
        "current_step": current_step.get("step_sequence") if current_step else None,
        "approver_user_principal_name": current_step.get("assigned_approver_user_principal_name") if current_step else None,
        "current_approver_user_principal_name": current_step.get("assigned_approver_user_principal_name") if current_step else None,
        "workflow_required": True,
        "business_operation_executed": False,
        "execution_result": None,
        "routing_source": instance.get("routing_source") or "CONFIGURED_WORKFLOW",
        "idempotent_replay": idempotent_replay,
        "message": message,
    }


def _end_node_id_from_snapshot(route_snapshot: dict | None) -> int | None:
    route_snapshot = _dict_from_json_value(route_snapshot)
    for node in (route_snapshot or {}).get("nodes", []):
        if node.get("node_type") == "END":
            return node.get("workflow_node_id_pk")
    return None


def _dict_from_json_value(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}
