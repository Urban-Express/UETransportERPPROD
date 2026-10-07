from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    ensure_workflow_engine_schema,
)


def get_instances(
    principal: str,
    principal_organization_id: int,
    is_admin: bool,
    filters: dict[str, Any],
) -> list[dict]:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            statement, params = _instance_query(
                principal,
                principal_organization_id,
                is_admin,
                filters,
            )
            rows = conn.execute(
                text(statement),
                params,
            ).mappings()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def _instance_query(
    principal: str,
    principal_organization_id: int,
    is_admin: bool,
    filters: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    params = {
        "principal": principal,
        "principal_organization_id": principal_organization_id,
        "is_admin": is_admin,
    }
    predicates = [
        "wi.organization_id_fk = :principal_organization_id",
    ]

    workflow_code = _upper(filters.get("workflow_code"))
    if workflow_code is not None:
        predicates.append("wi.workflow_code = :workflow_code")
        params["workflow_code"] = workflow_code

    organization_id = filters.get("organization_id")
    if organization_id is not None:
        predicates.append("wi.organization_id_fk = :organization_id")
        params["organization_id"] = organization_id

    workflow_action = _upper(filters.get("workflow_action"))
    if workflow_action is not None:
        predicates.append("wi.workflow_action = :workflow_action")
        params["workflow_action"] = workflow_action

    workflow_status = _upper(filters.get("workflow_status"))
    if workflow_status is not None:
        predicates.append("wi.workflow_status = :workflow_status")
        params["workflow_status"] = workflow_status

    requester_user_principal_name = filters.get("requester_user_principal_name")
    if requester_user_principal_name is not None:
        predicates.append(
            "lower(wi.requester_user_principal_name) = "
            "lower(:requester_user_principal_name)"
        )
        params["requester_user_principal_name"] = requester_user_principal_name

    date_from = _date_param(filters.get("date_from"))
    if date_from is not None:
        predicates.append("wi.started_at >= :date_from")
        params["date_from"] = date_from

    date_to = _date_param(filters.get("date_to"))
    if date_to is not None:
        predicates.append("wi.started_at < :date_to_exclusive")
        params["date_to_exclusive"] = date_to + timedelta(days=1)

    predicates.append("""
        (
            :is_admin = true
            or lower(wi.requester_user_principal_name) = lower(:principal)
            or exists (
                select 1
                from workflow_instance_steps vis
                where vis.workflow_instance_id_fk = wi.workflow_instance_id_pk
                and (
                    lower(vis.assigned_approver_user_principal_name) = lower(:principal)
                    or lower(coalesce(vis.acted_by_user_principal_name, '')) = lower(:principal)
                )
            )
        )
    """)

    where_clause = "\n                    and ".join(
        predicate.strip() for predicate in predicates
    )
    return (
        f"""
                    select
                        wi.workflow_instance_id_pk as workflow_instance_id,
                        wi.workflow_definition_id_fk as workflow_definition_id,
                        wi.workflow_version_id_fk as workflow_version_id,
                        wi.workflow_code,
                        wi.organization_id_fk as organization_id,
                        wi.workflow_action,
                        wi.requester_user_principal_name,
                        wi.current_node_id_fk as current_node_id,
                        current_step.assigned_approver_user_principal_name
                            as current_approver_user_principal_name,
                        wi.workflow_status,
                        wi.started_at,
                        wi.completed_at,
                        wi.routing_source,
                        wi.legacy_request_table,
                        wi.legacy_workflow_request_id
                    from workflow_instances wi
                    left join workflow_instance_steps current_step
                        on current_step.workflow_instance_id_fk = wi.workflow_instance_id_pk
                        and current_step.workflow_node_id_fk = wi.current_node_id_fk
                        and current_step.step_status = 'PENDING'
                    where {where_clause}
                    order by wi.started_at desc, wi.workflow_instance_id_pk desc
                """,
        params,
    )


def get_instance(
    workflow_instance_id: int,
    principal: str,
    principal_organization_id: int,
    is_admin: bool,
) -> dict | None:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            row = conn.execute(
                text("""
                    select
                        wi.workflow_instance_id_pk as workflow_instance_id,
                        wi.workflow_definition_id_fk as workflow_definition_id,
                        wi.workflow_version_id_fk as workflow_version_id,
                        wi.workflow_code,
                        wi.organization_id_fk as organization_id,
                        wi.workflow_action,
                        wi.requester_user_principal_name,
                        wi.current_node_id_fk as current_node_id,
                        current_step.assigned_approver_user_principal_name
                            as current_approver_user_principal_name,
                        wi.workflow_status,
                        wi.request_payload,
                        wi.execution_result,
                        wi.started_at,
                        wi.completed_at,
                        wi.routing_source,
                        wi.route_snapshot,
                        wi.legacy_request_table,
                        wi.legacy_workflow_request_id
                    from workflow_instances wi
                    left join workflow_instance_steps current_step
                        on current_step.workflow_instance_id_fk = wi.workflow_instance_id_pk
                        and current_step.workflow_node_id_fk = wi.current_node_id_fk
                        and current_step.step_status = 'PENDING'
                    where wi.workflow_instance_id_pk = :workflow_instance_id
                    and wi.organization_id_fk = :principal_organization_id
                    and (
                        :is_admin = true
                        or lower(wi.requester_user_principal_name) = lower(:principal)
                        or exists (
                            select 1
                            from workflow_instance_steps vis
                            where vis.workflow_instance_id_fk = wi.workflow_instance_id_pk
                            and (
                                lower(vis.assigned_approver_user_principal_name) = lower(:principal)
                                or lower(coalesce(vis.acted_by_user_principal_name, '')) = lower(:principal)
                            )
                        )
                    )
                    limit 1
                """),
                {
                    "workflow_instance_id": workflow_instance_id,
                    "principal": principal,
                    "principal_organization_id": principal_organization_id,
                    "is_admin": is_admin,
                },
            ).mappings().first()
            return dict(row) if row else None
    finally:
        engine.dispose()


def get_steps(
    workflow_instance_id: int,
    principal: str,
    principal_organization_id: int,
    is_admin: bool,
) -> list[dict] | None:
    if not get_instance(workflow_instance_id, principal, principal_organization_id, is_admin):
        return None
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            rows = conn.execute(
                text("""
                    select
                        workflow_instance_step_id_pk as workflow_instance_step_id,
                        workflow_instance_id_fk as workflow_instance_id,
                        workflow_node_id_fk as workflow_node_id,
                        step_sequence,
                        assigned_approver_user_principal_name,
                        step_status,
                        acted_by_user_principal_name,
                        approval_comments,
                        rejection_comments,
                        assigned_at,
                        acted_at
                    from workflow_instance_steps
                    where workflow_instance_id_fk = :workflow_instance_id
                    order by step_sequence, workflow_instance_step_id_pk
                """),
                {"workflow_instance_id": workflow_instance_id},
            ).mappings()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def get_inbox(
    principal: str,
    principal_organization_id: int,
    filters: dict[str, Any],
) -> list[dict]:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            ensure_workflow_engine_schema(conn)
            rows = conn.execute(
                text("""
                    select
                        wis.workflow_instance_step_id_pk as workflow_instance_step_id,
                        wi.workflow_instance_id_pk as workflow_instance_id,
                        wi.workflow_definition_id_fk as workflow_definition_id,
                        wi.workflow_version_id_fk as workflow_version_id,
                        wi.workflow_code,
                        wi.organization_id_fk as organization_id,
                        wi.workflow_action,
                        wi.requester_user_principal_name,
                        wi.workflow_status,
                        wis.step_sequence,
                        wis.assigned_approver_user_principal_name,
                        wis.assigned_at,
                        wi.started_at,
                        wi.legacy_request_table,
                        wi.legacy_workflow_request_id
                    from workflow_instance_steps wis
                    join workflow_instances wi
                        on wi.workflow_instance_id_pk = wis.workflow_instance_id_fk
                    where wi.organization_id_fk = :principal_organization_id
                    and wis.step_status = 'PENDING'
                    and wi.workflow_status = 'PENDING_APPROVAL'
                    and lower(wis.assigned_approver_user_principal_name) = lower(:principal)
                    and (:workflow_code is null or wi.workflow_code = :workflow_code)
                    and (:workflow_action is null or wi.workflow_action = :workflow_action)
                    order by wis.assigned_at, wis.workflow_instance_step_id_pk
                """),
                {
                    "principal": principal,
                    "principal_organization_id": principal_organization_id,
                    "workflow_code": _upper(filters.get("workflow_code")),
                    "workflow_action": _upper(filters.get("workflow_action")),
                },
            ).mappings()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def _upper(value: str | None) -> str | None:
    return value.upper() if isinstance(value, str) and value else None


def _date_param(value: date | datetime | None) -> date | datetime | None:
    return value
