from typing import Any

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    organization_exists,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_catalog import (
    get_workflow_catalog_entry,
    list_workflows,
    validate_workflow_action,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_definition_service import (
    get_workflow_definition_payload,
    get_workflow_eligible_users,
    publish_workflow_draft,
    save_workflow_draft,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    ensure_workflow_engine_schema,
    get_edges,
    get_nodes,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_validation import (
    validate_workflow_graph,
)


def get_catalog() -> list[dict]:
    return list_workflows()


def get_eligible_users(workflow_code: str, organization_id: int) -> dict:
    return get_workflow_eligible_users(workflow_code, organization_id)


def get_definition(workflow_code: str, organization_id: int, workflow_action: str) -> dict:
    applies_to_action = workflow_action.upper()
    payload = get_workflow_definition_payload(
        workflow_code=workflow_code,
        organization_id=organization_id,
        include_draft=True,
        applies_to_action=applies_to_action,
    )
    selected_version = payload.get("draft_version") or payload.get("published_version") or {}
    return {
        **payload,
        "nodes": selected_version.get("nodes") or [],
        "edges": selected_version.get("edges") or [],
    }


def save_draft(workflow_code: str, payload: dict[str, Any], principal: str) -> dict:
    applies_to_action = payload["workflow_action"].upper()
    draft_payload = {
        **payload,
        "workflow_action": applies_to_action,
        "applies_to_action": applies_to_action,
        "created_by": principal,
        "user_principal_name": principal,
    }
    return save_workflow_draft(workflow_code, draft_payload)


def validate_draft(workflow_code: str, payload: dict[str, Any]) -> dict:
    workflow_code = workflow_code.upper()
    organization_id = payload["organization_id"]
    applies_to_action = payload["workflow_action"].upper()
    if (payload.get("nodes") is None) != (payload.get("edges") is None):
        return {
            "success": False,
            "error": "WORKFLOW_VALIDATION_INPUT_INVALID",
            "is_valid": False,
            "validation_errors": [
                {
                    "code": "WORKFLOW_VALIDATION_INPUT_INVALID",
                    "message": "Validation requires both nodes and edges, or neither.",
                }
            ],
        }

    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            nodes = payload.get("nodes")
            edges = payload.get("edges")
            allow_self_approval = bool(payload.get("allow_self_approval", False))

            if nodes is None or edges is None:
                definition = _get_definition_any(conn, workflow_code, organization_id)
                if not definition:
                    return {
                        "success": False,
                        "error": "WORKFLOW_CONFIGURATION_NOT_FOUND",
                    }
                draft = _get_latest_draft(conn, definition["workflow_definition_id_pk"], applies_to_action)
                if not draft:
                    return {"success": False, "error": "WORKFLOW_DRAFT_NOT_FOUND"}
                nodes = _nodes_for_validation(conn, draft["workflow_version_id_pk"])
                edges = _edges_for_validation(conn, draft["workflow_version_id_pk"])
                allow_self_approval = bool(draft["allow_self_approval"])

            errors = validate_workflow_graph(
                conn=conn,
                workflow_code=workflow_code,
                organization_id=organization_id,
                applies_to_action=applies_to_action,
                allow_self_approval=allow_self_approval,
                nodes=nodes or [],
                edges=edges or [],
            )
            return {
                "success": not errors,
                "error": "WORKFLOW_VALIDATION_FAILED" if errors else None,
                "is_valid": not errors,
                "validation_errors": errors,
            }
    finally:
        engine.dispose()


def publish_draft(workflow_code: str, payload: dict[str, Any], principal: str) -> dict:
    applies_to_action = payload["workflow_action"].upper()
    publish_payload = {
        **payload,
        "workflow_action": applies_to_action,
        "applies_to_action": applies_to_action,
        "published_by": principal,
        "user_principal_name": principal,
    }
    return publish_workflow_draft(workflow_code, publish_payload)


def get_versions(
    workflow_code: str,
    organization_id: int,
    workflow_action: str | None = None,
) -> list[dict]:
    applies_to_action = workflow_action.upper() if workflow_action else None
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            definition = _get_definition_any(conn, workflow_code.upper(), organization_id)
            if not definition:
                return []
            rows = conn.execute(
                text("""
                    select
                        workflow_version_id_pk as workflow_version_id,
                        version_number,
                        version_status,
                        applies_to_action,
                        allow_self_approval,
                        canvas_metadata,
                        created_at,
                        created_by,
                        published_at,
                        published_by
                    from workflow_definition_versions
                    where workflow_definition_id_fk = :definition_id
                    and (:workflow_action is null or applies_to_action = :workflow_action)
                    order by version_number desc, workflow_version_id_pk desc
                """),
                {
                    "definition_id": definition["workflow_definition_id_pk"],
                    "workflow_action": applies_to_action,
                },
            ).mappings()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def get_version(workflow_code: str, organization_id: int, workflow_version_id: int) -> dict | None:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            row = conn.execute(
                text("""
                    select
                        wd.workflow_definition_id_pk as workflow_definition_id,
                        wd.workflow_code,
                        wd.workflow_name,
                        wd.service_name,
                        wd.entity_name,
                        wd.organization_id_fk as organization_id,
                        wd.is_active,
                        wdv.workflow_version_id_pk as workflow_version_id,
                        wdv.version_number,
                        wdv.version_status,
                        wdv.applies_to_action,
                        wdv.allow_self_approval,
                        wdv.canvas_metadata,
                        wdv.created_at,
                        wdv.created_by,
                        wdv.published_at,
                        wdv.published_by
                    from workflow_definition_versions wdv
                    join workflow_definitions wd
                        on wd.workflow_definition_id_pk = wdv.workflow_definition_id_fk
                    where wd.workflow_code = :workflow_code
                    and wd.organization_id_fk = :organization_id
                    and wdv.workflow_version_id_pk = :workflow_version_id
                    limit 1
                """),
                {
                    "workflow_code": workflow_code.upper(),
                    "organization_id": organization_id,
                    "workflow_version_id": workflow_version_id,
                },
            ).mappings().first()
            if not row:
                return None
            version = dict(row)
            version["nodes"] = get_nodes(conn, workflow_version_id)
            version["edges"] = get_edges(conn, workflow_version_id)
            return version
    finally:
        engine.dispose()


def update_status(
    workflow_code: str,
    organization_id: int,
    is_active: bool,
    principal: str,
) -> dict | None:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            if not get_workflow_catalog_entry(workflow_code.upper()):
                return None
            result = conn.execute(
                text("""
                    update workflow_definitions
                    set
                        is_active = :is_active,
                        updated_at = current_timestamp,
                        updated_by = :updated_by
                    where workflow_code = :workflow_code
                    and organization_id_fk = :organization_id
                    returning
                        workflow_definition_id_pk as workflow_definition_id,
                        workflow_code,
                        workflow_name,
                        organization_id_fk as organization_id,
                        is_active,
                        updated_at,
                        updated_by
                """),
                {
                    "workflow_code": workflow_code.upper(),
                    "organization_id": organization_id,
                    "is_active": is_active,
                    "updated_by": principal,
                },
            ).mappings().first()
            return dict(result) if result else None
    finally:
        engine.dispose()


def validate_request_context(
    workflow_code: str,
    organization_id: int,
    workflow_action: str | None = None,
    allow_all_scope: bool = False,
) -> str | None:
    if not get_workflow_catalog_entry(workflow_code.upper()):
        return "WORKFLOW_NOT_FOUND"
    if workflow_action:
        valid, error = validate_workflow_action(
            workflow_code.upper(),
            workflow_action.upper(),
            allow_all_scope=allow_all_scope,
        )
        if not valid:
            return error or "INVALID_WORKFLOW_ACTION"
    engine = db_engine()
    try:
        with engine.begin() as conn:
            if not organization_exists(conn, organization_id):
                return "INVALID_ORGANIZATION"
    finally:
        engine.dispose()
    return None


def _get_latest_draft(conn, workflow_definition_id: int, applies_to_action: str) -> dict | None:
    row = conn.execute(
        text("""
            select *
            from workflow_definition_versions
            where workflow_definition_id_fk = :definition_id
            and version_status = 'DRAFT'
            and applies_to_action = :applies_to_action
            order by version_number desc, workflow_version_id_pk desc
            limit 1
        """),
        {
            "definition_id": workflow_definition_id,
            "applies_to_action": applies_to_action,
        },
    ).mappings().first()
    return dict(row) if row else None


def _get_definition_any(conn, workflow_code: str, organization_id: int) -> dict | None:
    row = conn.execute(
        text("""
            select *
            from workflow_definitions
            where workflow_code = :workflow_code
            and organization_id_fk = :organization_id
            limit 1
        """),
        {
            "workflow_code": workflow_code,
            "organization_id": organization_id,
        },
    ).mappings().first()
    return dict(row) if row else None


def _nodes_for_validation(conn, workflow_version_id: int) -> list[dict]:
    return [
        {
            "node_key": row["node_key"],
            "node_type": row["node_type"],
            "user_principal_name": row["user_principal_name"],
        }
        for row in get_nodes(conn, workflow_version_id)
    ]


def _edges_for_validation(conn, workflow_version_id: int) -> list[dict]:
    rows = conn.execute(
        text("""
            select
                source_node.node_key as source_node_key,
                target_node.node_key as target_node_key,
                edge.edge_sequence,
                edge.edge_type,
                edge.condition_json
            from workflow_edges edge
            join workflow_nodes source_node
                on source_node.workflow_node_id_pk = edge.source_node_id_fk
            join workflow_nodes target_node
                on target_node.workflow_node_id_pk = edge.target_node_id_fk
            where edge.workflow_version_id_fk = :workflow_version_id
        """),
        {"workflow_version_id": workflow_version_id},
    ).mappings()
    return [dict(row) for row in rows]
