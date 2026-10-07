from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    list_eligible_users,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_catalog import (
    WORKFLOW_ALL_ACTION,
    get_workflow_catalog_entry,
    list_workflows,
    validate_workflow_action,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    dumps_json,
    ensure_workflow_engine_schema,
    get_edges,
    get_nodes,
    get_published_version,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_validation import (
    validate_workflow_graph,
)


def get_workflow_catalog() -> list[dict]:
    return list_workflows()


def get_workflow_eligible_users(workflow_code: str, organization_id: int) -> dict:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            entry = get_workflow_catalog_entry(workflow_code.upper())
            if not entry:
                return {"requesters": [], "approvers": [], "error": "WORKFLOW_CODE_NOT_FOUND"}
            return list_eligible_users(conn, organization_id)
    finally:
        engine.dispose()


def get_workflow_definition_payload(
    workflow_code: str,
    organization_id: int,
    include_draft: bool = True,
    applies_to_action: str | None = None,
) -> dict:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            definition = _get_workflow_definition_any(conn, workflow_code.upper(), organization_id)
            if not definition:
                return {"workflow_code": workflow_code.upper(), "organization_id": organization_id, "published_version": None, "draft_version": None}

            response = {
                "definition": definition,
                "published_version": None,
                "draft_version": None,
            }
            published = get_published_version(
                conn,
                definition["workflow_definition_id_pk"],
                (applies_to_action or WORKFLOW_ALL_ACTION).upper(),
            )
            if published:
                published["nodes"] = get_nodes(conn, published["workflow_version_id_pk"])
                published["edges"] = get_edges(conn, published["workflow_version_id_pk"])
                response["published_version"] = published
            if include_draft:
                draft = _get_draft_version(
                    conn,
                    definition["workflow_definition_id_pk"],
                    (applies_to_action or WORKFLOW_ALL_ACTION).upper(),
                )
                if draft:
                    draft["nodes"] = get_nodes(conn, draft["workflow_version_id_pk"])
                    draft["edges"] = get_edges(conn, draft["workflow_version_id_pk"])
                    response["draft_version"] = draft
            return response
    finally:
        engine.dispose()


def save_workflow_draft(workflow_code: str, payload: dict) -> dict:
    engine = db_engine()
    workflow_code = workflow_code.upper()
    organization_id = payload.get("organization_id") or payload.get("organization_id_fk")
    applies_to_action = (
        payload.get("workflow_action")
        or payload.get("applies_to_action")
        or WORKFLOW_ALL_ACTION
    ).upper()
    allow_self_approval = bool(payload.get("allow_self_approval", False))
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    created_by = payload.get("created_by") or payload.get("user_principal_name")
    canvas_metadata = payload.get("canvas_metadata") or {}
    node_keys = {node.get("node_key") for node in nodes}
    invalid_edges = [
        edge for edge in edges
        if edge.get("source_node_key") not in node_keys or edge.get("target_node_key") not in node_keys
    ]
    if invalid_edges:
        return {
            "success": False,
            "error": "WORKFLOW_DRAFT_EDGE_NODE_INVALID",
            "validation_errors": [
                {
                    "code": "EDGE_NODE_INVALID",
                    "edge": edge,
                    "message": "Edge source and target must reference nodes in the draft.",
                }
                for edge in invalid_edges
            ],
        }

    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            entry = get_workflow_catalog_entry(workflow_code)
            if not entry:
                return {"success": False, "error": "WORKFLOW_CODE_NOT_FOUND"}
            action_valid, action_error = validate_workflow_action(
                workflow_code,
                applies_to_action,
                allow_all_scope=True,
            )
            if not action_valid:
                return {"success": False, "error": action_error or "INVALID_WORKFLOW_ACTION"}
            definition = _get_workflow_definition_any(conn, workflow_code, organization_id)
            if not definition:
                definition_id = conn.execute(
                    text("""
                        insert into workflow_definitions (
                            workflow_code,
                            workflow_name,
                            service_name,
                            entity_name,
                            organization_id_fk,
                            is_active,
                            created_by
                        ) values (
                            :workflow_code,
                            :workflow_name,
                            :service_name,
                            :entity_name,
                            :organization_id,
                            true,
                            :created_by
                        )
                        on conflict (organization_id_fk, workflow_code) do update
                        set
                            workflow_name = excluded.workflow_name,
                            service_name = excluded.service_name,
                            entity_name = excluded.entity_name,
                            updated_at = current_timestamp,
                            updated_by = excluded.created_by
                        returning workflow_definition_id_pk
                    """),
                    {
                        "workflow_code": entry.workflow_code,
                        "workflow_name": entry.workflow_name,
                        "service_name": entry.service_name,
                        "entity_name": entry.entity_name,
                        "organization_id": organization_id,
                        "created_by": created_by,
                    },
                ).scalar_one()
                definition = _get_workflow_definition_any(conn, workflow_code, organization_id)
                if not definition:
                    definition = {"workflow_definition_id_pk": definition_id}
            conn.execute(
                text("""
                    select workflow_definition_id_pk
                    from workflow_definitions
                    where workflow_definition_id_pk = :definition_id
                    for update
                """),
                {"definition_id": definition["workflow_definition_id_pk"]},
            ).first()

            existing_draft = _get_draft_version(
                conn,
                definition["workflow_definition_id_pk"],
                applies_to_action,
                for_update=True,
            )
            if existing_draft:
                version_id = existing_draft["workflow_version_id_pk"]
                version_number = existing_draft["version_number"]
                conn.execute(
                    text("delete from workflow_edges where workflow_version_id_fk = :version_id"),
                    {"version_id": version_id},
                )
                conn.execute(
                    text("delete from workflow_nodes where workflow_version_id_fk = :version_id"),
                    {"version_id": version_id},
                )
                conn.execute(
                    text("""
                        update workflow_definition_versions
                        set
                            allow_self_approval = :allow_self_approval,
                            canvas_metadata = cast(:canvas_metadata as jsonb),
                            created_by = coalesce(created_by, :created_by)
                        where workflow_version_id_pk = :version_id
                    """),
                    {
                        "version_id": version_id,
                        "allow_self_approval": allow_self_approval,
                        "canvas_metadata": dumps_json(canvas_metadata),
                        "created_by": created_by,
                    },
                )
            else:
                latest_number = conn.execute(
                text("""
                    select coalesce(max(version_number), 0)
                    from workflow_definition_versions
                    where workflow_definition_id_fk = :definition_id
                """),
                    {"definition_id": definition["workflow_definition_id_pk"]},
                ).scalar_one()
                version_number = latest_number + 1
                version_id = conn.execute(
                    text("""
                        insert into workflow_definition_versions (
                            workflow_definition_id_fk,
                            version_number,
                            version_status,
                            applies_to_action,
                            allow_self_approval,
                            canvas_metadata,
                            created_by
                        ) values (
                            :definition_id,
                            :version_number,
                            'DRAFT',
                            :applies_to_action,
                            :allow_self_approval,
                            cast(:canvas_metadata as jsonb),
                            :created_by
                        )
                        returning workflow_version_id_pk
                    """),
                    {
                        "definition_id": definition["workflow_definition_id_pk"],
                        "version_number": version_number,
                        "applies_to_action": applies_to_action,
                        "allow_self_approval": allow_self_approval,
                        "canvas_metadata": dumps_json(canvas_metadata),
                        "created_by": created_by,
                    },
                ).scalar_one()

            node_id_by_key = {}
            for node in nodes:
                node_id = conn.execute(
                    text("""
                        insert into workflow_nodes (
                            workflow_version_id_fk,
                            node_key,
                            node_type,
                            user_principal_name,
                            sequence_hint,
                            canvas_x,
                            canvas_y,
                            node_metadata
                        ) values (
                            :version_id,
                            :node_key,
                            :node_type,
                            :user_principal_name,
                            :sequence_hint,
                            :canvas_x,
                            :canvas_y,
                            cast(:node_metadata as jsonb)
                        )
                        returning workflow_node_id_pk
                    """),
                    {
                        "version_id": version_id,
                        "node_key": node.get("node_key"),
                        "node_type": (node.get("node_type") or "").upper(),
                        "user_principal_name": node.get("user_principal_name"),
                        "sequence_hint": node.get("sequence_hint"),
                        "canvas_x": node.get("canvas_x"),
                        "canvas_y": node.get("canvas_y"),
                        "node_metadata": dumps_json(node.get("node_metadata") or {}),
                    },
                ).scalar_one()
                node_id_by_key[node.get("node_key")] = node_id

            for edge in edges:
                conn.execute(
                    text("""
                        insert into workflow_edges (
                            workflow_version_id_fk,
                            source_node_id_fk,
                            target_node_id_fk,
                            edge_sequence,
                            edge_type,
                            condition_json
                        ) values (
                            :version_id,
                            :source_node_id,
                            :target_node_id,
                            :edge_sequence,
                            :edge_type,
                            cast(:condition_json as jsonb)
                        )
                    """),
                    {
                        "version_id": version_id,
                        "source_node_id": node_id_by_key.get(edge.get("source_node_key")),
                        "target_node_id": node_id_by_key.get(edge.get("target_node_key")),
                        "edge_sequence": edge.get("edge_sequence"),
                        "edge_type": edge.get("edge_type") or "SEQUENTIAL",
                        "condition_json": dumps_json(edge.get("condition_json") or {}),
                    },
                )

            return {
                "success": True,
                "workflow_code": workflow_code,
                "workflow_version_id": version_id,
                "version_number": version_number,
                "version_status": "DRAFT",
            }
    finally:
        engine.dispose()


def publish_workflow_draft(workflow_code: str, payload: dict) -> dict:
    engine = db_engine()
    workflow_code = workflow_code.upper()
    workflow_version_id = payload.get("workflow_version_id") or payload.get("workflow_version_id_pk")
    organization_id = payload.get("organization_id") or payload.get("organization_id_fk")
    applies_to_action = (
        payload.get("workflow_action")
        or payload.get("applies_to_action")
        or WORKFLOW_ALL_ACTION
    ).upper()
    published_by = payload.get("published_by") or payload.get("user_principal_name")

    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            row = conn.execute(
                text("""
                    select
                        wdv.*,
                        wd.workflow_code,
                        wd.organization_id_fk
                    from workflow_definition_versions wdv
                    join workflow_definitions wd
                        on wd.workflow_definition_id_pk = wdv.workflow_definition_id_fk
                    where wd.workflow_code = :workflow_code
                    and wd.organization_id_fk = :organization_id
                    and wdv.version_status = 'DRAFT'
                    and wdv.applies_to_action = :applies_to_action
                    order by wdv.version_number desc, wdv.workflow_version_id_pk desc
                    limit 1
                    for update
                """),
                {
                    "workflow_code": workflow_code,
                    "organization_id": organization_id,
                    "applies_to_action": applies_to_action,
                },
            ).mappings().first()
            if not row:
                return {"success": False, "error": "WORKFLOW_DRAFT_NOT_FOUND"}
            version = dict(row)
            resolved_workflow_version_id = version["workflow_version_id_pk"]
            if workflow_version_id and int(workflow_version_id) != int(resolved_workflow_version_id):
                return {"success": False, "error": "WORKFLOW_VERSION_CONFLICT"}
            if version["workflow_code"] != workflow_code:
                return {"success": False, "error": "WORKFLOW_VERSION_CONFLICT"}
            if int(version["organization_id_fk"]) != int(organization_id):
                return {"success": False, "error": "WORKFLOW_VERSION_CONFLICT"}
            if version["applies_to_action"] != applies_to_action:
                return {"success": False, "error": "WORKFLOW_VERSION_CONFLICT"}
            if version["version_status"] != "DRAFT":
                return {"success": False, "error": "ONLY_DRAFT_WORKFLOWS_CAN_BE_PUBLISHED"}

            nodes = _nodes_for_validation(conn, resolved_workflow_version_id)
            edges = _edges_for_validation(conn, resolved_workflow_version_id)
            errors = validate_workflow_graph(
                conn=conn,
                workflow_code=workflow_code,
                organization_id=version["organization_id_fk"],
                applies_to_action=version["applies_to_action"],
                allow_self_approval=version["allow_self_approval"],
                nodes=nodes,
                edges=edges,
            )
            if errors:
                return {"success": False, "error": "WORKFLOW_VALIDATION_FAILED", "validation_errors": errors}

            conn.execute(
                text("""
                    update workflow_definition_versions
                    set version_status = 'RETIRED'
                    where workflow_definition_id_fk = :definition_id
                    and applies_to_action = :applies_to_action
                    and version_status = 'PUBLISHED'
                """),
                {
                    "definition_id": version["workflow_definition_id_fk"],
                    "applies_to_action": version["applies_to_action"],
                },
            )
            conn.execute(
                text("""
                    update workflow_definition_versions
                    set
                        version_status = 'PUBLISHED',
                        published_at = current_timestamp,
                        published_by = :published_by
                    where workflow_version_id_pk = :workflow_version_id
                """),
                {
                    "workflow_version_id": resolved_workflow_version_id,
                    "published_by": published_by,
                },
            )
            return {
                "success": True,
                "workflow_code": workflow_code,
                "workflow_version_id": resolved_workflow_version_id,
                "version_status": "PUBLISHED",
            }
    finally:
        engine.dispose()


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


def _get_draft_version(
    conn,
    workflow_definition_id: int,
    applies_to_action: str,
    for_update: bool = False,
) -> dict | None:
    query = """
        select *
        from workflow_definition_versions
        where workflow_definition_id_fk = :definition_id
        and version_status = 'DRAFT'
        and applies_to_action = :applies_to_action
        order by version_number desc, workflow_version_id_pk desc
        limit 1
    """
    if for_update:
        query += " for update"
    row = conn.execute(
        text(query),
        {
            "definition_id": workflow_definition_id,
            "applies_to_action": applies_to_action,
        },
    ).mappings().first()
    return dict(row) if row else None


def _get_workflow_definition_any(conn, workflow_code: str, organization_id: int) -> dict | None:
    row = conn.execute(
        text("""
            select *
            from workflow_definitions
            where workflow_code = :workflow_code
            and organization_id_fk = :organization_id
            limit 1
        """),
        {"workflow_code": workflow_code, "organization_id": organization_id},
    ).mappings().first()
    return dict(row) if row else None
