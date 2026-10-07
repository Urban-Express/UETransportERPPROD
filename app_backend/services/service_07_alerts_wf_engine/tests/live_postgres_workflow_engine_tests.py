import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy import event


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_definition_service import (
    publish_workflow_draft,
    save_workflow_draft,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    ensure_workflow_engine_schema,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine import (
    approve_workflow_step,
    reject_workflow_step,
    start_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_catalog import (
    get_workflow_catalog_entry,
)
import app_backend.services.service_07_alerts_wf_engine.workflow_definition_service as definition_service
import app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine as runtime_engine


RUN_CODE = "WF_TEST_CONFIG_ENGINE_20260831"
LIVE_RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
AUDIT_USER = "WF_TEST_ENGINE"
ORG_NAME = f"{RUN_CODE}_ORG"
REQUESTER = f"{RUN_CODE}_REQUESTER"
APPROVER_X = f"{RUN_CODE}_APPROVER_X"
APPROVER_Y = f"{RUN_CODE}_APPROVER_Y"
MANAGER_M = f"{RUN_CODE}_MANAGER_M"
SUBMITTER_APPROVER = f"{RUN_CODE}_SUBMITTER_APPROVER"
LEGACY_TABLE = "accounts_payables_workflow_requests"
EXECUTION_TABLE = "workflow_engine_live_test_execution_log"
WORKFLOW_CODE = "ACCOUNTS_PAYABLE"
WORKFLOW_LEGACY_TABLES = {
    "ASSET_MASTER": "asset_master_workflow_requests",
    "CONTRACTS_MANAGEMENT": "contracts_management_workflow_requests",
    "FLEET_MANAGEMENT": "fleet_management_workflow_requests",
    "PAYROLL": "payroll_workflow_requests",
    "ACCOUNTS_PAYABLE": "accounts_payables_workflow_requests",
    "ACCOUNTS_RECEIVABLE": "accounts_receivables_workflow_requests",
}
WORKFLOW_ACTION_MATRIX = {
    "ASSET_MASTER": ("CREATE", "UPDATE", "DELETE"),
    "CONTRACTS_MANAGEMENT": ("CREATE", "UPDATE"),
    "FLEET_MANAGEMENT": ("CREATE", "UPDATE", "DELETE"),
    "PAYROLL": ("APPROVAL",),
    "ACCOUNTS_PAYABLE": ("CREATE", "UPDATE", "DELETE"),
    "ACCOUNTS_RECEIVABLE": ("CREATE", "UPDATE", "DELETE"),
}
WORKFLOW_ORG_FIELDS = {
    "ASSET_MASTER": "asset_org_id_fk",
    "CONTRACTS_MANAGEMENT": "cont_org_id_fk",
    "FLEET_MANAGEMENT": "fleet_org_id_fk",
    "PAYROLL": "payroll_org_id_fk",
    "ACCOUNTS_PAYABLE": "ap_org_id_fk",
    "ACCOUNTS_RECEIVABLE": "ar_org_id_fk",
}
WORKFLOW_DOMAIN_FIELDS = {
    "ASSET_MASTER": "asset_id",
    "CONTRACTS_MANAGEMENT": "cont_id",
    "FLEET_MANAGEMENT": "fleet_vehicle_id",
    "PAYROLL": "payroll_run_id",
    "ACCOUNTS_PAYABLE": "ap_id",
    "ACCOUNTS_RECEIVABLE": "ar_id",
}


RESULTS: list[dict] = []


def record(test_id, name, passed, details):
    RESULTS.append(
        {
            "test_id": test_id,
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "details": details,
        }
    )
    print(f"{test_id} {'PASS' if passed else 'FAIL'} - {name}: {details}")


def scalar(conn, sql, params=None):
    return conn.execute(text(sql), params or {}).scalar_one()


def setup_reference_data(conn):
    conn.execute(text("set local lock_timeout = '5s'"))
    conn.execute(text("set local statement_timeout = '30s'"))
    schema_exists = conn.execute(
        text("select to_regclass('public.workflow_instances')")
    ).scalar_one()
    if not schema_exists:
        ensure_workflow_engine_schema(conn)
    conn.execute(
        text("""
            create table if not exists workflow_engine_live_test_execution_log (
                execution_log_id_pk bigserial primary key,
                run_code varchar(100) not null,
                workflow_action varchar(30) not null,
                request_marker varchar(100),
                executed_by varchar(255),
                payload jsonb not null,
                created_at timestamptz not null default current_timestamp
            )
        """)
    )
    existing_org = conn.execute(
        text("""
            select min(org_id_pk)
            from organization_master
            where org_name = :org_name
        """),
        {"org_name": ORG_NAME},
    ).scalar_one()
    org_id = existing_org or conn.execute(
        text("""
            insert into organization_master (
                org_name,
                org_address,
                org_phone_primary,
                org_email_primary,
                created_by
            ) values (
                :org_name,
                'Workflow engine live test address',
                '+971000000000',
                :email,
                :created_by
            )
            returning org_id_pk
        """),
        {
            "org_name": ORG_NAME,
            "email": f"{RUN_CODE.lower()}@ue.local",
            "created_by": AUDIT_USER,
        },
    ).scalar_one()

    submit_role_id = upsert_role(conn, f"{RUN_CODE}_SUBMIT_ROLE")
    approve_role_id = upsert_role(conn, f"{RUN_CODE}_APPROVE_ROLE")
    submit_approve_role_id = upsert_role(conn, f"{RUN_CODE}_SUBMIT_APPROVE_ROLE")
    submit_permission_id = upsert_permission(conn, "WORKFLOW", "SUBMIT", f"{RUN_CODE}_WF_SUBMIT")
    approve_permission_id = upsert_permission(conn, "WORKFLOW", "APPROVE", f"{RUN_CODE}_WF_APPROVE")

    map_role_permission(conn, submit_role_id, submit_permission_id)
    map_role_permission(conn, approve_role_id, approve_permission_id)
    map_role_permission(conn, submit_approve_role_id, submit_permission_id)
    map_role_permission(conn, submit_approve_role_id, approve_permission_id)

    requester_id = upsert_user(conn, org_id, REQUESTER, "Requester")
    approver_x_id = upsert_user(conn, org_id, APPROVER_X, "Approver X")
    approver_y_id = upsert_user(conn, org_id, APPROVER_Y, "Approver Y")
    manager_m_id = upsert_user(conn, org_id, MANAGER_M, "Manager M")
    submitter_approver_id = upsert_user(conn, org_id, SUBMITTER_APPROVER, "Submitter Approver")

    map_user_role(conn, requester_id, submit_role_id)
    map_user_role(conn, approver_x_id, approve_role_id)
    map_user_role(conn, approver_y_id, approve_role_id)
    map_user_role(conn, manager_m_id, approve_role_id)
    map_user_role(conn, submitter_approver_id, submit_approve_role_id)

    for workflow_code in WORKFLOW_LEGACY_TABLES:
        ensure_test_workflow_definition(conn, org_id, workflow_code)
    return org_id


def ensure_test_workflow_definition(conn, org_id, workflow_code=WORKFLOW_CODE):
    entry = get_workflow_catalog_entry(workflow_code)
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
                is_active = true,
                updated_at = current_timestamp,
                updated_by = excluded.created_by
        """),
        {
            "workflow_code": entry.workflow_code,
            "workflow_name": entry.workflow_name,
            "service_name": entry.service_name,
            "entity_name": entry.entity_name,
            "organization_id": org_id,
            "created_by": AUDIT_USER,
        },
    )


def upsert_role(conn, role_name):
    return conn.execute(
        text("""
            insert into role_master (role_name, role_description, created_by)
            values (:role_name, :description, :created_by)
            on conflict (role_name) do update
            set role_description = excluded.role_description
            returning role_id_pk
        """),
        {
            "role_name": role_name,
            "description": "Workflow engine live test role",
            "created_by": AUDIT_USER,
        },
    ).scalar_one()


def upsert_permission(conn, module_name, action_name, permission_code):
    return conn.execute(
        text("""
            insert into permission_master (module_name, action_name, permission_code)
            values (:module_name, :action_name, :permission_code)
            on conflict (module_name, action_name) do update
            set permission_code = permission_master.permission_code
            returning permission_id_pk
        """),
        {
            "module_name": module_name,
            "action_name": action_name,
            "permission_code": permission_code,
        },
    ).scalar_one()


def upsert_user(conn, org_id, user_principal_name, display_suffix):
    return conn.execute(
        text("""
            insert into user_master (
                auth_provider,
                user_principal_name,
                password_hash,
                first_name,
                last_name,
                display_name,
                email,
                user_org_id_fk,
                is_active,
                is_deleted,
                created_by
            ) values (
                'LOCAL',
                :user_principal_name,
                '$2b$12$WF_TEST_DUMMY_HASH_ONLY',
                'WF',
                'Test',
                :display_name,
                :email,
                :org_id,
                true,
                false,
                :created_by
            )
            on conflict (email) do update
            set
                user_principal_name = excluded.user_principal_name,
                display_name = excluded.display_name,
                user_org_id_fk = excluded.user_org_id_fk,
                is_active = true,
                is_deleted = false
            returning user_id_pk
        """),
        {
            "user_principal_name": user_principal_name,
            "display_name": f"{RUN_CODE} {display_suffix}",
            "email": f"{user_principal_name.lower()}@ue.local",
            "org_id": org_id,
            "created_by": AUDIT_USER,
        },
    ).scalar_one()


def map_role_permission(conn, role_id, permission_id):
    conn.execute(
        text("""
            insert into role_permission_mapping (role_id_fk, permission_id_fk)
            values (:role_id, :permission_id)
            on conflict do nothing
        """),
        {"role_id": role_id, "permission_id": permission_id},
    )


def map_user_role(conn, user_id, role_id):
    conn.execute(
        text("""
            insert into user_role_mapping (user_id_fk, role_id_fk)
            values (:user_id, :role_id)
            on conflict do nothing
        """),
        {"user_id": user_id, "role_id": role_id},
    )


def dummy_execution_adapter(workflow_action, request_payload, conn=None):
    def execute(execution_conn):
        execution_id = execution_conn.execute(
            text(f"""
                insert into {EXECUTION_TABLE} (
                    run_code,
                    workflow_action,
                    request_marker,
                    executed_by,
                    payload
                ) values (
                    :run_code,
                    :workflow_action,
                    :request_marker,
                    :executed_by,
                    cast(:payload as jsonb)
                )
                returning execution_log_id_pk
            """),
            {
                "run_code": RUN_CODE,
                "workflow_action": workflow_action,
                "request_marker": request_payload.get("request_marker"),
                "executed_by": request_payload.get("approver_user_principal_name"),
                "payload": json.dumps(request_payload, default=str),
            },
        ).scalar_one()
        return {
            "message": "Dummy workflow test execution completed.",
            "execution_log_id_pk": execution_id,
            "request_marker": request_payload.get("request_marker"),
        }

    if conn is not None:
        return execute(conn)

    engine = db_engine()
    try:
        with engine.begin() as fallback_conn:
            return execute(fallback_conn)
    finally:
        engine.dispose()


def final_metadata(request_payload, final_approver, comments):
    return {
        **request_payload,
        "approver_user_principal_name": final_approver,
        "ap_approved_by": final_approver,
        "ap_approval_comments": comments,
    }


def save_and_publish(
    org_id,
    requester,
    approvers,
    action="CREATE",
    allow_self_approval=False,
    workflow_code=WORKFLOW_CODE,
):
    nodes = [
        {"node_key": "requester", "node_type": "REQUESTER", "user_principal_name": requester, "canvas_x": 10, "canvas_y": 20},
    ]
    edges = []
    previous = "requester"
    for index, approver in enumerate(approvers, start=1):
        key = f"approver_{index}"
        nodes.append({"node_key": key, "node_type": "APPROVER", "user_principal_name": approver, "canvas_x": 100 * index, "canvas_y": 20})
        edges.append({"source_node_key": previous, "target_node_key": key, "edge_sequence": index})
        previous = key
    nodes.append({"node_key": "end", "node_type": "END", "canvas_x": 500, "canvas_y": 20})
    edges.append({"source_node_key": previous, "target_node_key": "end", "edge_sequence": len(edges) + 1})

    print(f"Saving draft for {workflow_code} requester={requester} action={action} approvers={approvers}", flush=True)
    draft = save_workflow_draft(
        workflow_code,
        {
            "organization_id": org_id,
            "applies_to_action": action,
            "allow_self_approval": allow_self_approval,
            "nodes": nodes,
            "edges": edges,
            "canvas_metadata": {"run_code": RUN_CODE},
            "user_principal_name": RUN_CODE,
        },
    )
    if not draft.get("success"):
        raise AssertionError(f"Draft save failed: {draft}")
    print(f"Publishing draft version {draft['workflow_version_id']}", flush=True)
    published = publish_workflow_draft(
        workflow_code,
        {
            "workflow_version_id": draft["workflow_version_id"],
            "organization_id": org_id,
            "applies_to_action": action,
            "user_principal_name": RUN_CODE,
        },
    )
    if not published.get("success"):
        raise AssertionError(f"Publish failed: {published}")
    print(f"Published version {draft['workflow_version_id']}", flush=True)
    return published


def start_ap_workflow(org_id, requester, marker, action="CREATE"):
    return start_configured_workflow(
        WORKFLOW_CODE,
        action,
        org_id,
        requester,
        marker,
    )


def start_configured_workflow(
    workflow_code,
    action,
    org_id,
    requester,
    marker,
    payload_extra=None,
):
    payload = live_domain_payload(workflow_code, action, org_id, requester, marker)
    payload.update(payload_extra or {})
    legacy_extra_values = {}
    if workflow_code == "PAYROLL":
        payroll_run_id = payload["payroll_run_id"]
        legacy_extra_values = {
            "payroll_run_id_fk": payroll_run_id,
            "payroll_run_type": payload.get("payroll_run_type") or "MONTHLY",
        }
    return start_workflow(
        workflow_code=workflow_code,
        workflow_action=action,
        organization_id=org_id,
        requester_user_principal_name=requester,
        request_payload=payload,
        legacy_table_name=WORKFLOW_LEGACY_TABLES[workflow_code],
        legacy_extra_values=legacy_extra_values,
    )


def live_domain_payload(workflow_code, action, org_id, requester, marker):
    org_field = WORKFLOW_ORG_FIELDS[workflow_code]
    payload = {
        "user_principal_name": requester,
        org_field: org_id,
        "request_marker": marker,
        "service_request_id": f"{RUN_CODE}:{LIVE_RUN_ID}:{marker}",
        "_workflow_matrix_marker": True,
    }
    domain_id = stable_domain_id(marker)
    if action != "CREATE" or workflow_code == "PAYROLL":
        domain_field = WORKFLOW_DOMAIN_FIELDS[workflow_code]
        payload[domain_field] = domain_id
        payload[f"{domain_field}_pk"] = domain_id
    if workflow_code == "CONTRACTS_MANAGEMENT":
        payload["cont_contract_number"] = f"{RUN_CODE}-{LIVE_RUN_ID}-{marker}"[:80]
    if workflow_code == "FLEET_MANAGEMENT":
        payload["vehicle_code"] = f"VH-{LIVE_RUN_ID}-{marker}"[:80]
    if workflow_code == "ACCOUNTS_PAYABLE":
        payload["ap_invoice_number"] = f"AP-{LIVE_RUN_ID}-{marker}"[:80]
    if workflow_code == "ACCOUNTS_RECEIVABLE":
        payload["ar_invoice_number"] = f"AR-{LIVE_RUN_ID}-{marker}"[:80]
    if workflow_code == "PAYROLL":
        payload["payroll_run_type"] = "MONTHLY"
    return payload


def stable_domain_id(marker):
    digits = "".join(str(ord(character)).zfill(3) for character in marker)
    return int((digits or "1")[-12:])


def create_live_payroll_run(org_id, marker):
    payroll_run_code = f"{RUN_CODE}_{LIVE_RUN_ID}_{marker}"[:50]
    payroll_sequence = int(LIVE_RUN_ID[-6:]) + (stable_domain_id(marker) % 1000)
    engine = db_engine()
    try:
        with engine.begin() as conn:
            return conn.execute(
                text("""
                    insert into payroll_run (
                        payroll_org_id_fk,
                        payroll_run_code,
                        payroll_run_description,
                        payroll_year,
                        payroll_month,
                        payroll_run_sequence,
                        payroll_run_type,
                        payroll_period_start_date,
                        payroll_period_end_date,
                        payroll_payment_date,
                        payroll_status,
                        created_by,
                        updated_by
                    ) values (
                        :org_id,
                        :run_code,
                        :description,
                        2026,
                        9,
                        :sequence,
                        'MONTHLY',
                        date '2026-09-01',
                        date '2026-09-30',
                        date '2026-10-05',
                        'PROCESSED',
                        :audit_user,
                        :audit_user
                    )
                    on conflict (payroll_org_id_fk, payroll_run_code) do update
                    set
                        payroll_status = 'PROCESSED',
                        updated_by = excluded.updated_by,
                        updated_at = current_timestamp
                    returning payroll_run_id_pk
                """),
                {
                    "org_id": org_id,
                    "run_code": payroll_run_code,
                    "description": f"Workflow live verification {marker}",
                    "sequence": payroll_sequence,
                    "audit_user": AUDIT_USER,
                },
            ).scalar_one()
    finally:
        engine.dispose()


def step_payload(workflow_response, approver, **extra_values):
    return {
        "workflow_instance_id": workflow_response["workflow_instance_id"],
        "workflow_instance_step_id": workflow_response["workflow_instance_step_id"],
        "approver_user_principal_name": approver,
        **extra_values,
    }


def approve(payload):
    return approve_workflow_step(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        execution_adapter=dummy_execution_adapter,
        legacy_table_name=LEGACY_TABLE,
        final_payload_enricher=final_metadata,
    )


def reject(payload):
    return reject_workflow_step(
        workflow_code=WORKFLOW_CODE,
        payload=payload,
        legacy_table_name=LEGACY_TABLE,
    )


def approve_domain_workflow(workflow_code, payload):
    return approve_workflow_step(
        workflow_code=workflow_code,
        payload=payload,
        execution_adapter=dummy_execution_adapter,
        legacy_table_name=WORKFLOW_LEGACY_TABLES[workflow_code],
        final_payload_enricher=final_metadata,
    )


def reject_domain_workflow(workflow_code, payload):
    return reject_workflow_step(
        workflow_code=workflow_code,
        payload=payload,
        legacy_table_name=WORKFLOW_LEGACY_TABLES[workflow_code],
    )


def run_domain_matrix_tests(org_id):
    matrix_results = []
    for workflow_code, actions in WORKFLOW_ACTION_MATRIX.items():
        for action in actions:
            marker = f"matrix_{workflow_code.lower()}_{action.lower()}"
            save_and_publish(
                org_id,
                REQUESTER,
                [APPROVER_X],
                action,
                workflow_code=workflow_code,
            )
            payload_extra = {}
            if workflow_code == "PAYROLL":
                payload_extra["payroll_run_id"] = create_live_payroll_run(org_id, marker)
                payload_extra["payroll_run_id_pk"] = payload_extra["payroll_run_id"]
            started = start_configured_workflow(
                workflow_code,
                action,
                org_id,
                REQUESTER,
                marker,
                payload_extra=payload_extra,
            )
            approved = approve_domain_workflow(
                workflow_code,
                step_payload(started, APPROVER_X, approval_comments=f"{workflow_code} {action} live matrix"),
            )
            matrix_results.append(
                {
                    "workflow_code": workflow_code,
                    "workflow_action": action,
                    "workflow_request_id": started.get("workflow_request_id"),
                    "workflow_instance_id": started.get("workflow_instance_id"),
                    "workflow_instance_step_id": started.get("workflow_instance_step_id"),
                    "start_status": started.get("workflow_status"),
                    "final_status": approved.get("workflow_status"),
                    "business_operation_executed": approved.get("business_operation_executed"),
                }
            )
    passed = all(
        result["start_status"] == "PENDING_APPROVAL"
        and result["final_status"] == "EXECUTED"
        and result["business_operation_executed"] is True
        for result in matrix_results
    )
    record("LDB-16", "Six-domain workflow/action matrix executes", passed, json.dumps(matrix_results, default=str))


def run_hardening_live_tests(org_id):
    save_and_publish(org_id, REQUESTER, [APPROVER_X, APPROVER_Y], "UPDATE")
    stale = start_ap_workflow(org_id, REQUESTER, "hardening_stale_step", "UPDATE")
    first = approve(step_payload(stale, APPROVER_X, approval_comments="advance to step two"))
    stale_retry = approve(
        {
            "workflow_instance_id": stale["workflow_instance_id"],
            "workflow_instance_step_id": stale["workflow_instance_step_id"],
            "approver_user_principal_name": APPROVER_Y,
            "approval_comments": "stale step",
        }
    )
    record(
        "LDB-17",
        "Stale step ID is rejected",
        first.get("workflow_status") == "PENDING_APPROVAL"
        and stale_retry.get("error_code") == "WORKFLOW_STEP_NOT_CURRENT",
        str(stale_retry),
    )

    save_and_publish(org_id, REQUESTER, [APPROVER_X], "UPDATE", workflow_code="FLEET_MANAGEMENT")
    fleet_instance = start_configured_workflow(
        "FLEET_MANAGEMENT",
        "UPDATE",
        org_id,
        REQUESTER,
        "hardening_wrong_domain",
    )
    wrong_domain = approve_workflow_step(
        workflow_code="ASSET_MASTER",
        payload=step_payload(fleet_instance, APPROVER_X),
        execution_adapter=dummy_execution_adapter,
        legacy_table_name=WORKFLOW_LEGACY_TABLES["ASSET_MASTER"],
    )
    record(
        "LDB-18",
        "Wrong workflow instance/domain is rejected",
        wrong_domain.get("error_code") == "WORKFLOW_INSTANCE_TYPE_MISMATCH",
        str(wrong_domain),
    )

    save_and_publish(org_id, REQUESTER, [APPROVER_X], "CREATE")
    id_one = start_ap_workflow(org_id, REQUESTER, "hardening_mismatch_one", "CREATE")
    id_two = start_ap_workflow(org_id, REQUESTER, "hardening_mismatch_two", "CREATE")
    mismatch = approve(
        {
            "workflow_request_id": id_two["workflow_request_id"],
            "workflow_instance_id": id_one["workflow_instance_id"],
            "workflow_instance_step_id": id_one["workflow_instance_step_id"],
            "approver_user_principal_name": APPROVER_X,
        }
    )
    record(
        "LDB-19",
        "Mismatched request ID and instance ID are rejected",
        mismatch.get("error_code") == "WORKFLOW_IDENTIFIER_MISMATCH",
        str(mismatch),
    )

    save_and_publish(org_id, REQUESTER, [APPROVER_X], "UPDATE", workflow_code="ASSET_MASTER")
    duplicate_marker = "hardening_duplicate_retry"
    duplicate_first = start_configured_workflow(
        "ASSET_MASTER",
        "UPDATE",
        org_id,
        REQUESTER,
        duplicate_marker,
    )
    duplicate_second = start_configured_workflow(
        "ASSET_MASTER",
        "UPDATE",
        org_id,
        REQUESTER,
        duplicate_marker,
    )
    record(
        "LDB-20",
        "Duplicate retry returns idempotent replay",
        duplicate_first.get("workflow_status") == "PENDING_APPROVAL"
        and duplicate_second.get("idempotent_replay") is True
        and duplicate_second.get("workflow_instance_id") == duplicate_first.get("workflow_instance_id"),
        str(duplicate_second),
    )

    conflict_marker = "hardening_concurrent_conflict"
    conflict_domain_id = stable_domain_id(conflict_marker)

    def concurrent_submit(suffix):
        return start_configured_workflow(
            "ASSET_MASTER",
            "UPDATE",
            org_id,
            REQUESTER,
            f"{conflict_marker}_{suffix}",
            payload_extra={"asset_id": conflict_domain_id, "asset_id_pk": conflict_domain_id},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        conflict_results = list(executor.map(concurrent_submit, ("a", "b")))
    conflict_statuses = {result.get("workflow_status") for result in conflict_results}
    conflict_errors = {result.get("error_code") for result in conflict_results}
    record(
        "LDB-21",
        "Simultaneous conflicting UPDATE creates one pending workflow and one conflict",
        "PENDING_APPROVAL" in conflict_statuses
        and "WORKFLOW_PENDING_CONFLICT" in conflict_errors,
        json.dumps(conflict_results, default=str),
    )

    cross_org = approve_workflow_step(
        workflow_code="ASSET_MASTER",
        payload=step_payload(duplicate_first, APPROVER_X),
        execution_adapter=dummy_execution_adapter,
        legacy_table_name=WORKFLOW_LEGACY_TABLES["ASSET_MASTER"],
        organization_id=org_id + 999999,
    )
    record(
        "LDB-22",
        "Cross-organization approval is denied",
        cross_org.get("error_code") == "WORKFLOW_INSTANCE_NOT_FOUND",
        str(cross_org),
    )

    save_and_publish(org_id, None, [APPROVER_X], "DELETE", workflow_code="ASSET_MASTER")
    wildcard = start_configured_workflow(
        "ASSET_MASTER",
        "DELETE",
        org_id,
        REQUESTER,
        "hardening_wildcard_requester",
    )
    record(
        "LDB-23",
        "Wildcard requester route resolves for eligible requester",
        wildcard.get("workflow_status") == "PENDING_APPROVAL"
        and wildcard.get("current_approver_user_principal_name") == APPROVER_X,
        str(wildcard),
    )

    self_off = save_workflow_draft(
        WORKFLOW_CODE,
        {
            "organization_id": org_id,
            "applies_to_action": "DELETE",
            "allow_self_approval": False,
            "nodes": [
                {"node_key": "requester", "node_type": "REQUESTER", "user_principal_name": SUBMITTER_APPROVER, "canvas_x": 10, "canvas_y": 20},
                {"node_key": "approver", "node_type": "APPROVER", "user_principal_name": SUBMITTER_APPROVER, "canvas_x": 100, "canvas_y": 20},
                {"node_key": "end", "node_type": "END", "canvas_x": 200, "canvas_y": 20},
            ],
            "edges": [
                {"source_node_key": "requester", "target_node_key": "approver", "edge_sequence": 1},
                {"source_node_key": "approver", "target_node_key": "end", "edge_sequence": 2},
            ],
            "canvas_metadata": {"run_code": RUN_CODE, "self_approval": "off"},
            "user_principal_name": RUN_CODE,
        },
    )
    self_off_publish = self_off
    if self_off.get("success"):
        self_off_publish = publish_workflow_draft(
            WORKFLOW_CODE,
            {
                "workflow_version_id": self_off["workflow_version_id"],
                "organization_id": org_id,
                "applies_to_action": "DELETE",
                "user_principal_name": RUN_CODE,
            },
        )
    record(
        "LDB-24",
        "Self-approval OFF rejects explicit self-approval route",
        self_off_publish.get("error") == "WORKFLOW_VALIDATION_FAILED",
        str(self_off_publish),
    )

    save_and_publish(org_id, REQUESTER, [APPROVER_X], "CREATE")
    document_workflow = start_configured_workflow(
        WORKFLOW_CODE,
        "CREATE",
        org_id,
        REQUESTER,
        "hardening_document_rejection",
        payload_extra={
            "workflow_document_staging_status": "STAGED",
            "workflow_document_cleanup_policy": "DELETE_STAGED_DOCUMENT_IF_WORKFLOW_REJECTED_OR_CANCELLED",
            "workflow_document_staged_blob_path": f"workflow-live-test/{LIVE_RUN_ID}/document.pdf",
            "workflow_document_staged_bucket": "workflow-live-test-bucket",
        },
    )
    original_cleanup = runtime_engine.cleanup_staged_workflow_document
    try:
        runtime_engine.cleanup_staged_workflow_document = lambda payload, terminal_status=None, force=False: {
            "cleanup_required": True,
            "cleanup_attempted": True,
            "cleanup_completed": True,
            "terminal_status": terminal_status,
            "firebase_blob_path": payload.get("workflow_document_staged_blob_path"),
        }
        document_reject = reject(step_payload(document_workflow, APPROVER_X, rejection_comments="document cleanup"))
    finally:
        runtime_engine.cleanup_staged_workflow_document = original_cleanup
    record(
        "LDB-25",
        "Document workflow rejection invokes staged cleanup policy",
        document_reject.get("workflow_status") == "REJECTED"
        and document_reject.get("document_cleanup", {}).get("cleanup_completed") is True,
        str(document_reject),
    )


def run_tests(org_id):
    published_v1 = save_and_publish(org_id, REQUESTER, [APPROVER_X], "CREATE")
    record("LDB-01", "Draft and publish User A to Approver X", published_v1.get("success"), str(published_v1))

    basic = start_ap_workflow(org_id, REQUESTER, "basic_route")
    record(
        "LDB-02",
        "Submission creates central instance and assigns Approver X",
        basic.get("workflow_status") == "PENDING_APPROVAL" and basic.get("current_approver_user_principal_name") == APPROVER_X,
        str(basic),
    )

    record(
        "LDB-03",
        "No line-manager override",
        basic.get("current_approver_user_principal_name") == APPROVER_X and basic.get("current_approver_user_principal_name") != MANAGER_M,
        f"assigned={basic.get('current_approver_user_principal_name')} manager_candidate={MANAGER_M}",
    )

    wrong_approval = approve(step_payload(basic, APPROVER_Y))
    record("LDB-04", "Wrong approver approval denied", wrong_approval.get("workflow_confirmation") == "N" and "another approver" in wrong_approval.get("error", ""), str(wrong_approval))

    wrong_rejection = reject(step_payload(basic, APPROVER_Y))
    record("LDB-05", "Wrong approver rejection denied", wrong_rejection.get("workflow_confirmation") == "N" and "another approver" in wrong_rejection.get("error", ""), str(wrong_rejection))

    final_basic = approve(step_payload(basic, APPROVER_X, approval_comments="final basic approval"))
    record("LDB-06", "Correct approver final approval executes dummy adapter", final_basic.get("workflow_status") == "EXECUTED", str(final_basic))

    duplicate = approve(step_payload(basic, APPROVER_X, approval_comments="duplicate"))
    record("LDB-07", "Duplicate approval does not rerun execution", duplicate.get("workflow_status") == "EXECUTED" and "already terminal" in duplicate.get("message", ""), str(duplicate))

    published_v2 = save_and_publish(org_id, REQUESTER, [APPROVER_X, APPROVER_Y], "CREATE")
    seq = start_ap_workflow(org_id, REQUESTER, "sequential_route")
    first_step = approve(step_payload(seq, APPROVER_X, approval_comments="step 1"))
    record("LDB-08", "Sequential intermediate approval keeps workflow pending", first_step.get("workflow_status") == "PENDING_APPROVAL" and first_step.get("current_approver_user_principal_name") == APPROVER_Y, str(first_step))

    final_seq = approve(step_payload(first_step, APPROVER_Y, approval_comments="step 2 final"))
    record("LDB-09", "Sequential final approval executes dummy adapter", final_seq.get("workflow_status") == "EXECUTED", str(final_seq))

    published_v3 = save_and_publish(org_id, SUBMITTER_APPROVER, [APPROVER_X], "CREATE", False)
    submitter_has_both = start_ap_workflow(org_id, SUBMITTER_APPROVER, "submitter_has_both")
    record("LDB-10", "Submitter with SUBMIT and APPROVE does not self-approve", submitter_has_both.get("workflow_status") == "PENDING_APPROVAL" and submitter_has_both.get("current_approver_user_principal_name") == APPROVER_X, str(submitter_has_both))

    published_v4 = save_and_publish(org_id, SUBMITTER_APPROVER, [SUBMITTER_APPROVER], "UPDATE", True)
    self_approval = start_ap_workflow(org_id, SUBMITTER_APPROVER, "explicit_self_approval", "UPDATE")
    self_final = approve(step_payload(self_approval, SUBMITTER_APPROVER, approval_comments="explicit self approval"))
    record("LDB-11", "Explicit self-approval works only when configured", self_final.get("workflow_status") == "EXECUTED", str(self_final))

    no_config = start_ap_workflow(org_id, REQUESTER, "no_delete_config", "DELETE")
    record("LDB-12", "No published workflow fails closed", no_config.get("error") == "WORKFLOW_CONFIGURATION_NOT_FOUND", str(no_config))

    save_and_publish(org_id, REQUESTER, [APPROVER_X], "CREATE")
    snapshot_old = start_ap_workflow(org_id, REQUESTER, "snapshot_old")
    save_and_publish(org_id, REQUESTER, [APPROVER_Y], "CREATE")
    snapshot_new = start_ap_workflow(org_id, REQUESTER, "snapshot_new")
    record("LDB-13", "Workflow changed after submission keeps old request on old approver", snapshot_old.get("current_approver_user_principal_name") == APPROVER_X and snapshot_new.get("current_approver_user_principal_name") == APPROVER_Y, f"old={snapshot_old.get('current_approver_user_principal_name')} new={snapshot_new.get('current_approver_user_principal_name')}")

    rejected = reject(step_payload(snapshot_new, APPROVER_Y, rejection_comments="reject test"))
    record("LDB-14", "Correct approver can reject and no execution occurs", rejected.get("workflow_status") == "REJECTED", str(rejected))

    engine = db_engine()
    try:
        with engine.begin() as conn:
            exec_count = scalar(conn, f"select count(*) from {EXECUTION_TABLE} where run_code = :run_code", {"run_code": RUN_CODE})
            instance_count = scalar(conn, "select count(*) from workflow_instances where requester_user_principal_name in (:requester, :both)", {"requester": REQUESTER, "both": SUBMITTER_APPROVER})
            step_count = scalar(conn, "select count(*) from workflow_instance_steps wis join workflow_instances wi on wi.workflow_instance_id_pk = wis.workflow_instance_id_fk where wi.requester_user_principal_name in (:requester, :both)", {"requester": REQUESTER, "both": SUBMITTER_APPROVER})
            record("LDB-15", "Audit records exist in central runtime tables", exec_count >= 4 and instance_count >= 7 and step_count >= 8, f"execution_log={exec_count}, instances={instance_count}, steps={step_count}")
    finally:
        engine.dispose()

    return published_v2, published_v3, published_v4


def main():
    print(f"Starting live workflow engine integration tests: {RUN_CODE}", flush=True)
    print(f"Live request run id: {LIVE_RUN_ID}", flush=True)
    engine = db_engine()
    try:
        with engine.begin() as conn:
            print("Setting up dummy organization, users, roles, permissions, and workflow schema.", flush=True)
            org_id = setup_reference_data(conn)
    finally:
        engine.dispose()

    original_db_engine = db_engine

    def bounded_db_engine():
        engine = original_db_engine()

        @event.listens_for(engine, "connect")
        def set_timeouts(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("set lock_timeout = '5s'")
            cursor.execute("set statement_timeout = '30s'")
            cursor.close()

        return engine

    definition_service.db_engine = bounded_db_engine
    runtime_engine.db_engine = bounded_db_engine
    definition_service.ensure_workflow_engine_schema = lambda conn: None
    runtime_engine.ensure_workflow_engine_schema = lambda conn: None

    print(f"Dummy organization id: {org_id}", flush=True)
    run_tests(org_id)
    run_domain_matrix_tests(org_id)
    run_hardening_live_tests(org_id)
    passed = sum(1 for result in RESULTS if result["status"] == "PASS")
    failed = len(RESULTS) - passed
    print(json.dumps({"run_code": RUN_CODE, "live_run_id": LIVE_RUN_ID, "organization_name": ORG_NAME, "organization_id": org_id, "passed": passed, "failed": failed, "results": RESULTS}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
