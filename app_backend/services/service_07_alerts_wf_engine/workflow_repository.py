import json
from typing import Any

from sqlalchemy import text

from app_backend.services.service_07_alerts_wf_engine.workflow_catalog import (
    WORKFLOW_CATALOG,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_errors import (
    LEGACY_WORKFLOW_TABLE_NOT_ALLOWED,
    WORKFLOW_ENGINE_MIGRATION_REQUIRED,
)


LEGACY_WORKFLOW_TABLES = (
    "asset_master_workflow_requests",
    "contracts_management_workflow_requests",
    "fleet_management_workflow_requests",
    "payroll_workflow_requests",
    "accounts_payables_workflow_requests",
    "accounts_receivables_workflow_requests",
)

REQUIRED_ENGINE_TABLES = (
    "workflow_definitions",
    "workflow_definition_versions",
    "workflow_nodes",
    "workflow_edges",
    "workflow_instances",
    "workflow_instance_steps",
)

REQUIRED_ENGINE_COLUMNS = {
    "workflow_instances": (
        "routing_source",
        "execution_key",
        "execution_started_at",
        "execution_completed_at",
    ),
    "workflow_edges": (
        "edge_type",
        "condition_json",
    ),
}

LEGACY_WORKFLOW_REQUIRED_COLUMNS = {
    table_name: (
        "workflow_instance_id_fk",
        "workflow_version_id_fk",
        "routing_source",
    )
    for table_name in LEGACY_WORKFLOW_TABLES
}


def dumps_json(value: Any) -> str:
    return json.dumps(value, default=str)


def apply_workflow_engine_migration(conn) -> None:
    conn.execute(text("""
        create table if not exists workflow_definitions (
            workflow_definition_id_pk bigserial primary key,
            workflow_code varchar(100) not null,
            workflow_name varchar(255) not null,
            service_name varchar(100) not null,
            entity_name varchar(100) not null,
            organization_id_fk bigint not null references organization_master(org_id_pk),
            is_active boolean not null default true,
            created_at timestamptz not null default current_timestamp,
            created_by varchar(255),
            updated_at timestamptz,
            updated_by varchar(255),
            constraint uq_workflow_definition_org_code unique (organization_id_fk, workflow_code)
        )
    """))
    conn.execute(text("""
        create table if not exists workflow_definition_versions (
            workflow_version_id_pk bigserial primary key,
            workflow_definition_id_fk bigint not null
                references workflow_definitions(workflow_definition_id_pk) on delete cascade,
            version_number integer not null,
            version_status varchar(30) not null default 'DRAFT',
            applies_to_action varchar(30) not null default 'ALL',
            allow_self_approval boolean not null default false,
            canvas_metadata jsonb not null default '{}'::jsonb,
            published_at timestamptz,
            published_by varchar(255),
            created_at timestamptz not null default current_timestamp,
            created_by varchar(255),
            constraint ck_workflow_version_status
                check (version_status in ('DRAFT', 'PUBLISHED', 'RETIRED')),
            constraint uq_workflow_definition_version_number
                unique (workflow_definition_id_fk, version_number)
        )
    """))
    conn.execute(text("""
        create table if not exists workflow_nodes (
            workflow_node_id_pk bigserial primary key,
            workflow_version_id_fk bigint not null
                references workflow_definition_versions(workflow_version_id_pk) on delete cascade,
            node_key varchar(100) not null,
            node_type varchar(30) not null,
            user_principal_name varchar(255),
            sequence_hint integer,
            canvas_x numeric,
            canvas_y numeric,
            node_metadata jsonb not null default '{}'::jsonb,
            created_at timestamptz not null default current_timestamp,
            constraint ck_workflow_node_type
                check (node_type in ('REQUESTER', 'APPROVER', 'END')),
            constraint uq_workflow_node_key unique (workflow_version_id_fk, node_key)
        )
    """))
    conn.execute(text("""
        create table if not exists workflow_edges (
            workflow_edge_id_pk bigserial primary key,
            workflow_version_id_fk bigint not null
                references workflow_definition_versions(workflow_version_id_pk) on delete cascade,
            source_node_id_fk bigint not null references workflow_nodes(workflow_node_id_pk) on delete cascade,
            target_node_id_fk bigint not null references workflow_nodes(workflow_node_id_pk) on delete cascade,
            edge_sequence integer,
            edge_type varchar(30) not null default 'SEQUENTIAL',
            condition_json jsonb not null default '{}'::jsonb,
            created_at timestamptz not null default current_timestamp,
            constraint ck_workflow_edge_type
                check (edge_type in ('SEQUENTIAL'))
        )
    """))
    conn.execute(text("""
        create table if not exists workflow_instances (
            workflow_instance_id_pk bigserial primary key,
            workflow_definition_id_fk bigint not null references workflow_definitions(workflow_definition_id_pk),
            workflow_version_id_fk bigint not null references workflow_definition_versions(workflow_version_id_pk),
            workflow_code varchar(100) not null,
            organization_id_fk bigint not null references organization_master(org_id_pk),
            workflow_action varchar(30) not null,
            requester_user_principal_name varchar(255) not null,
            current_node_id_fk bigint references workflow_nodes(workflow_node_id_pk),
            workflow_status varchar(30) not null default 'PENDING_APPROVAL',
            request_payload jsonb not null,
            execution_result jsonb,
            started_at timestamptz not null default current_timestamp,
            completed_at timestamptz,
            route_snapshot jsonb not null,
            legacy_request_table varchar(100),
            legacy_workflow_request_id bigint,
            routing_source varchar(50) not null default 'CONFIGURED_WORKFLOW',
            execution_key varchar(100),
            execution_started_at timestamptz,
            execution_completed_at timestamptz,
            constraint ck_workflow_instance_status
                check (workflow_status in ('PENDING_APPROVAL', 'EXECUTED', 'REJECTED', 'EXECUTION_FAILED', 'ERROR')),
            constraint ck_workflow_instance_routing_source
                check (routing_source in ('CONFIGURED_WORKFLOW', 'LEGACY_ASSIGNED_APPROVER'))
        )
    """))
    conn.execute(text("""
        create table if not exists workflow_instance_steps (
            workflow_instance_step_id_pk bigserial primary key,
            workflow_instance_id_fk bigint not null
                references workflow_instances(workflow_instance_id_pk) on delete cascade,
            workflow_node_id_fk bigint not null references workflow_nodes(workflow_node_id_pk),
            step_sequence integer not null,
            assigned_approver_user_principal_name varchar(255) not null,
            step_status varchar(30) not null default 'PENDING',
            acted_by_user_principal_name varchar(255),
            approval_comments text,
            rejection_comments text,
            assigned_at timestamptz not null default current_timestamp,
            acted_at timestamptz,
            constraint ck_workflow_step_status
                check (step_status in ('PENDING', 'APPROVED', 'REJECTED', 'SKIPPED')),
            constraint uq_workflow_instance_step_sequence unique (workflow_instance_id_fk, step_sequence)
        )
    """))
    conn.execute(text("""
        alter table if exists workflow_instances
            add column if not exists routing_source varchar(50) not null default 'CONFIGURED_WORKFLOW',
            add column if not exists execution_key varchar(100),
            add column if not exists execution_started_at timestamptz,
            add column if not exists execution_completed_at timestamptz
    """))
    conn.execute(text("""
        alter table if exists workflow_nodes
            drop constraint if exists ck_workflow_node_type,
            add constraint ck_workflow_node_type
                check (node_type in ('REQUESTER', 'APPROVER', 'END')) not valid
    """))
    conn.execute(text("""
        alter table if exists workflow_edges
            drop constraint if exists ck_workflow_edge_type,
            add constraint ck_workflow_edge_type
                check (edge_type in ('SEQUENTIAL')) not valid
    """))
    conn.execute(text("""
        alter table if exists workflow_instances
            drop constraint if exists ck_workflow_instance_routing_source,
            add constraint ck_workflow_instance_routing_source
                check (routing_source in ('CONFIGURED_WORKFLOW', 'LEGACY_ASSIGNED_APPROVER')) not valid
    """))
    conn.execute(text("""
        create unique index if not exists uq_workflow_published_action
        on workflow_definition_versions (workflow_definition_id_fk, applies_to_action)
        where version_status = 'PUBLISHED'
    """))
    conn.execute(text("""
        create unique index if not exists uq_workflow_execution_key
        on workflow_instances (execution_key)
        where execution_key is not null
    """))
    conn.execute(text("""
        create unique index if not exists uq_workflow_instance_pending_step
        on workflow_instance_steps(workflow_instance_id_fk)
        where step_status = 'PENDING'
    """))
    conn.execute(text("""
        do $$
        begin
            if to_regclass('public.payroll_workflow_requests') is not null then
                create unique index if not exists uq_payroll_pending_workflow
                on payroll_workflow_requests(payroll_run_id_fk)
                where workflow_status = 'PENDING_APPROVAL';
            end if;
        end $$;
    """))
    conn.execute(text("create index if not exists idx_workflow_instances_status on workflow_instances(workflow_status)"))
    conn.execute(text("create index if not exists idx_workflow_steps_pending on workflow_instance_steps(assigned_approver_user_principal_name, step_status)"))

    for table_name in LEGACY_WORKFLOW_TABLES:
        conn.execute(text(f"""
            do $$
            begin
                if to_regclass('public.{table_name}') is not null then
                    alter table {table_name}
                        add column if not exists workflow_instance_id_fk bigint,
                        add column if not exists workflow_version_id_fk bigint,
                        add column if not exists routing_source varchar(50) not null default 'LEGACY_ASSIGNED_APPROVER';
                    create index if not exists idx_{table_name}_instance
                        on {table_name}(workflow_instance_id_fk);
                    create index if not exists idx_{table_name}_version
                        on {table_name}(workflow_version_id_fk);
                end if;
            end $$;
        """))

    for entry in WORKFLOW_CATALOG.values():
        conn.execute(
            text("""
                insert into workflow_definitions (
                    workflow_code,
                    workflow_name,
                    service_name,
                    entity_name,
                    organization_id_fk,
                    is_active,
                    created_by
                )
                select
                    :workflow_code,
                    :workflow_name,
                    :service_name,
                    :entity_name,
                    org.org_id_pk,
                    :is_active,
                    'SYSTEM'
                from organization_master org
                where not exists (
                    select 1
                    from workflow_definitions existing
                    where existing.organization_id_fk = org.org_id_pk
                    and existing.workflow_code = :workflow_code
                )
            """),
            {
                "workflow_code": entry.workflow_code,
                "workflow_name": entry.workflow_name,
                "service_name": entry.service_name,
                "entity_name": entry.entity_name,
            "is_active": entry.is_active,
            },
        )
    conn.execute(text("""
        insert into permission_master (
            module_name,
            action_name,
            permission_code
        )
        select
            'WORKFLOW',
            'ADMIN',
            'WF_ADMIN'
        where exists (
            select 1
            from information_schema.tables
            where table_schema = 'public'
            and table_name = 'permission_master'
        )
        and not exists (
            select 1
            from permission_master
            where upper(module_name) = 'WORKFLOW'
            and upper(action_name) = 'ADMIN'
        )
    """))


def ensure_workflow_engine_schema(conn) -> None:
    missing_tables = []
    for table_name in REQUIRED_ENGINE_TABLES:
        exists = conn.execute(
            text("select to_regclass(:table_name)"),
            {"table_name": f"public.{table_name}"},
        ).scalar_one()
        if not exists:
            missing_tables.append(table_name)

    if missing_tables:
        raise RuntimeError(
            f"{WORKFLOW_ENGINE_MIGRATION_REQUIRED}: missing {', '.join(missing_tables)}"
        )

    missing_columns = []
    for table_name, column_names in REQUIRED_ENGINE_COLUMNS.items():
        rows = conn.execute(
            text("""
                select column_name
                from information_schema.columns
                where table_schema = 'public'
                and table_name = :table_name
            """),
            {"table_name": table_name},
        ).scalars().all()
        present = set(rows)
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in column_names
            if column_name not in present
        )

    if missing_columns:
        raise RuntimeError(
            f"{WORKFLOW_ENGINE_MIGRATION_REQUIRED}: missing {', '.join(missing_columns)}"
        )

    missing_legacy_columns = []
    for table_name in LEGACY_WORKFLOW_TABLES:
        table_exists = conn.execute(
            text("select to_regclass(:table_name)"),
            {"table_name": f"public.{table_name}"},
        ).scalar_one()
        if not table_exists:
            continue
        present_columns = _get_table_columns(conn, table_name)
        missing_legacy_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in LEGACY_WORKFLOW_REQUIRED_COLUMNS[table_name]
            if column_name not in present_columns
        )

    if missing_legacy_columns:
        raise RuntimeError(
            f"{WORKFLOW_ENGINE_MIGRATION_REQUIRED}: missing {', '.join(missing_legacy_columns)}"
        )


def assert_legacy_workflow_table_allowed(table_name: str) -> None:
    if table_name not in LEGACY_WORKFLOW_TABLES:
        raise ValueError(LEGACY_WORKFLOW_TABLE_NOT_ALLOWED)


def ensure_legacy_workflow_table_schema(conn, table_name: str) -> None:
    assert_legacy_workflow_table_allowed(table_name)
    table_exists = conn.execute(
        text("select to_regclass(:table_name)"),
        {"table_name": f"public.{table_name}"},
    ).scalar_one()
    if not table_exists:
        raise RuntimeError(f"{WORKFLOW_ENGINE_MIGRATION_REQUIRED}: missing {table_name}")

    present_columns = _get_table_columns(conn, table_name)
    missing_columns = [
        column_name
        for column_name in LEGACY_WORKFLOW_REQUIRED_COLUMNS[table_name]
        if column_name not in present_columns
    ]
    if missing_columns:
        raise RuntimeError(
            f"{WORKFLOW_ENGINE_MIGRATION_REQUIRED}: missing "
            f"{', '.join(f'{table_name}.{column_name}' for column_name in missing_columns)}"
        )


def _get_table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(
        text("""
            select column_name
            from information_schema.columns
            where table_schema = 'public'
            and table_name = :table_name
        """),
        {"table_name": table_name},
    ).scalars().all()
    return set(rows)


def resolve_effective_workflow_version(
    conn,
    workflow_definition_id: int,
    workflow_action: str,
) -> dict | None:
    rows = conn.execute(
        text("""
            select *
            from workflow_definition_versions
            where workflow_definition_id_fk = :workflow_definition_id
            and version_status = 'PUBLISHED'
            and applies_to_action in (:workflow_action, 'ALL')
            order by
                case when applies_to_action = :workflow_action then 0 else 1 end,
                version_number desc,
                workflow_version_id_pk desc
        """),
        {
            "workflow_definition_id": workflow_definition_id,
            "workflow_action": workflow_action,
        },
    ).mappings().all()
    if len(rows) > 1 and rows[0]["applies_to_action"] == rows[1]["applies_to_action"]:
        raise ValueError("DUPLICATE_PUBLISHED_WORKFLOW_VERSION")
    return dict(rows[0]) if rows else None


def get_workflow_definition(conn, workflow_code: str, organization_id: int) -> dict | None:
    row = conn.execute(
        text("""
            select *
            from workflow_definitions
            where workflow_code = :workflow_code
            and organization_id_fk = :organization_id
            and is_active = true
            limit 1
        """),
        {"workflow_code": workflow_code, "organization_id": organization_id},
    ).mappings().first()
    return dict(row) if row else None


def get_published_version(conn, workflow_definition_id: int, workflow_action: str) -> dict | None:
    return resolve_effective_workflow_version(conn, workflow_definition_id, workflow_action)


def get_nodes(conn, workflow_version_id: int) -> list[dict]:
    rows = conn.execute(
        text("""
            select *
            from workflow_nodes
            where workflow_version_id_fk = :workflow_version_id
            order by coalesce(sequence_hint, workflow_node_id_pk), workflow_node_id_pk
        """),
        {"workflow_version_id": workflow_version_id},
    ).mappings()
    return [dict(row) for row in rows]


def get_edges(conn, workflow_version_id: int) -> list[dict]:
    rows = conn.execute(
        text("""
            select *
            from workflow_edges
            where workflow_version_id_fk = :workflow_version_id
            order by coalesce(edge_sequence, workflow_edge_id_pk), workflow_edge_id_pk
        """),
        {"workflow_version_id": workflow_version_id},
    ).mappings()
    return [dict(row) for row in rows]
