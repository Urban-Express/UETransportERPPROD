import json
import logging

import pandas as pd
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    WORKFLOW_APPROVE_ACTION,
    WORKFLOW_MODULE_NAME,
    WORKFLOW_SUBMIT_ACTION,
    get_user_workflow_actions,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    approve_configured_or_legacy_workflow,
    ensure_legacy_workflow_table,
    reject_configured_or_legacy_workflow,
    submit_configured_workflow,
)


logger = logging.getLogger(__name__)

PAYROLL_WORKFLOW_REQUEST_TABLE = "payroll_workflow_requests"
WORKFLOW_CODE = "PAYROLL"
SUPPORTED_PAYROLL_RUN_TYPES = {"MONTHLY", "ONE_TIME", "CORRECTION", "FINAL_SETTLEMENT"}
LEGACY_ORG_FIELDS = ("payroll_org_id_fk",)
_PAYROLL_WORKFLOW_ENGINE = None


def _get_payroll_workflow_engine():
    global _PAYROLL_WORKFLOW_ENGINE
    if _PAYROLL_WORKFLOW_ENGINE is None:
        _PAYROLL_WORKFLOW_ENGINE = db_engine()
    return _PAYROLL_WORKFLOW_ENGINE


def ensure_payroll_workflow_table():
    ensure_legacy_workflow_table(PAYROLL_WORKFLOW_REQUEST_TABLE)


def get_payroll_user_workflow_actions(user_principal_name: str):
    workflow_engine = _get_payroll_workflow_engine()
    with workflow_engine.begin() as conn:
        return get_user_workflow_actions(conn, user_principal_name)


def get_payroll_employee_line_manager_user_principal_name(
    user_principal_name: str,
    payroll_org_id_fk: int | None = None,
):
    """Legacy utility retained for historical requests; new submissions do not call it."""
    workflow_engine = _get_payroll_workflow_engine()
    get_line_manager = text("""
        select
            manager_employee.email_id_company as approver_official_email,
            coalesce(
                workflow_user.user_principal_name,
                manager_employee.email_id_company,
                manager_employee.email_id_personal,
                manager_employee.employee_id
            ) as approver_user_principal_name
        from employee_master requester_employee
        join employee_master manager_employee
            on manager_employee.empl_org_id_fk = requester_employee.empl_org_id_fk
            and (
                lower(manager_employee.employee_id) = lower(requester_employee.reporting_to_employee_id)
                or manager_employee.empl_id_pk::text = requester_employee.reporting_to_employee_id
            )
        left join lateral (
            select user_principal_name
            from user_master
            where user_org_id_fk = manager_employee.empl_org_id_fk
            and coalesce(is_active, true) = true
            and coalesce(is_deleted, false) = false
            and (
                lower(user_principal_name) = lower(manager_employee.email_id_company)
                or lower(email) = lower(manager_employee.email_id_company)
                or lower(user_principal_name) = lower(manager_employee.email_id_personal)
                or lower(email) = lower(manager_employee.email_id_personal)
                or lower(user_principal_name) = lower(manager_employee.employee_id)
                or regexp_replace(lower(coalesce(display_name, '')), '\\s+', ' ', 'g')
                    = regexp_replace(lower(coalesce(manager_employee.employee_name, '')), '\\s+', ' ', 'g')
            )
            order by
                case
                    when lower(user_principal_name) = lower(manager_employee.email_id_company) then 1
                    when lower(email) = lower(manager_employee.email_id_company) then 2
                    when lower(user_principal_name) = lower(manager_employee.email_id_personal) then 3
                    when lower(email) = lower(manager_employee.email_id_personal) then 4
                    when lower(user_principal_name) = lower(manager_employee.employee_id) then 5
                    else 6
                end
            limit 1
        ) workflow_user on true
        where
            (:payroll_org_id_fk is null or requester_employee.empl_org_id_fk = :payroll_org_id_fk)
            and (
                lower(requester_employee.email_id_company) = lower(:user_principal_name)
                or lower(requester_employee.email_id_personal) = lower(:user_principal_name)
                or lower(requester_employee.employee_id) = lower(:user_principal_name)
            )
        limit 1
    """)

    with workflow_engine.begin() as conn:
        df_line_manager = pd.read_sql(
            sql=get_line_manager,
            con=conn,
            params={
                "user_principal_name": user_principal_name,
                "payroll_org_id_fk": payroll_org_id_fk,
            },
        )

    if df_line_manager.empty:
        return None

    return df_line_manager.iloc[0]["approver_user_principal_name"]


def get_payroll_employee_line_manager_workflow_identity(
    user_principal_name: str,
    payroll_org_id_fk: int | None = None,
):
    """Legacy utility retained for historical requests; new submissions do not call it."""
    workflow_engine = _get_payroll_workflow_engine()
    get_line_manager = text("""
        select
            manager_employee.email_id_company as approver_official_email,
            coalesce(
                workflow_user.user_principal_name,
                manager_employee.email_id_company,
                manager_employee.email_id_personal,
                manager_employee.employee_id
            ) as approver_user_principal_name
        from employee_master requester_employee
        join employee_master manager_employee
            on manager_employee.empl_org_id_fk = requester_employee.empl_org_id_fk
            and (
                lower(manager_employee.employee_id) = lower(requester_employee.reporting_to_employee_id)
                or manager_employee.empl_id_pk::text = requester_employee.reporting_to_employee_id
            )
        left join lateral (
            select user_principal_name
            from user_master
            where user_org_id_fk = manager_employee.empl_org_id_fk
            and coalesce(is_active, true) = true
            and coalesce(is_deleted, false) = false
            and (
                lower(user_principal_name) = lower(manager_employee.email_id_company)
                or lower(email) = lower(manager_employee.email_id_company)
                or lower(user_principal_name) = lower(manager_employee.email_id_personal)
                or lower(email) = lower(manager_employee.email_id_personal)
                or lower(user_principal_name) = lower(manager_employee.employee_id)
                or regexp_replace(lower(coalesce(display_name, '')), '\\s+', ' ', 'g')
                    = regexp_replace(lower(coalesce(manager_employee.employee_name, '')), '\\s+', ' ', 'g')
            )
            order by
                case
                    when lower(user_principal_name) = lower(manager_employee.email_id_company) then 1
                    when lower(email) = lower(manager_employee.email_id_company) then 2
                    when lower(user_principal_name) = lower(manager_employee.email_id_personal) then 3
                    when lower(email) = lower(manager_employee.email_id_personal) then 4
                    when lower(user_principal_name) = lower(manager_employee.employee_id) then 5
                    else 6
                end
            limit 1
        ) workflow_user on true
        where
            (:payroll_org_id_fk is null or requester_employee.empl_org_id_fk = :payroll_org_id_fk)
            and (
                lower(requester_employee.email_id_company) = lower(:user_principal_name)
                or lower(requester_employee.email_id_personal) = lower(:user_principal_name)
                or lower(requester_employee.employee_id) = lower(:user_principal_name)
            )
        limit 1
    """)

    with workflow_engine.begin() as conn:
        df_line_manager = pd.read_sql(
            sql=get_line_manager,
            con=conn,
            params={
                "user_principal_name": user_principal_name,
                "payroll_org_id_fk": payroll_org_id_fk,
            },
        )

    if df_line_manager.empty:
        return None

    return df_line_manager.iloc[0].to_dict()


def _dataframe_to_response(df_value: pd.DataFrame):
    date_columns = [
        "requested_at",
        "approved_at",
        "rejected_at",
        "executed_at",
        "payroll_period_start_date",
        "payroll_period_end_date",
        "payroll_payment_date",
        "created_at",
        "processed_at",
        "paid_at",
        "updated_at",
    ]
    for date_column in date_columns:
        if date_column in df_value.columns:
            df_value[date_column] = df_value[date_column].astype(str)

    return df_value, df_value.to_json(orient="records")


def _trusted_payroll_org_id(payload: dict | None) -> int | None:
    payload = payload or {}
    value = payload.get("payroll_org_id_fk") or payload.get("authenticated_org_id")
    if value is None or value == "":
        return None
    return int(value)


def _get_payroll_run_for_update(
    conn,
    payroll_run_id: int,
    payroll_org_id_fk: int | None = None,
):
    get_run = text("""
        select *
        from payroll_run
        where payroll_run_id_pk = :payroll_run_id
        and (:payroll_org_id_fk is null or payroll_org_id_fk = :payroll_org_id_fk)
        for update
    """)
    row = conn.execute(
        get_run,
        {
            "payroll_run_id": payroll_run_id,
            "payroll_org_id_fk": payroll_org_id_fk,
        },
    ).mappings().first()
    if not row:
        return None
    return dict(row)


def _get_latest_workflow_status(conn, payroll_run_id: int):
    get_latest = text("""
        select workflow_status
        from payroll_workflow_requests
        where payroll_run_id_fk = :payroll_run_id
        order by workflow_request_id_pk desc
        limit 1
    """)
    row = conn.execute(get_latest, {"payroll_run_id": payroll_run_id}).first()
    if not row:
        return None
    return row[0]


def _has_pending_payroll_workflow(conn, payroll_run_id: int):
    check_pending = text("""
        select
            pwr.workflow_request_id_pk,
            pwr.workflow_instance_id_fk,
            pwr.approver_user_principal_name,
            wi.workflow_code,
            wi.workflow_action,
            wis.workflow_instance_step_id_pk,
            wis.step_sequence,
            wis.assigned_approver_user_principal_name
        from payroll_workflow_requests pwr
        left join workflow_instances wi
            on wi.workflow_instance_id_pk = pwr.workflow_instance_id_fk
        left join workflow_instance_steps wis
            on wis.workflow_instance_id_fk = wi.workflow_instance_id_pk
            and wis.step_status = 'PENDING'
        where pwr.payroll_run_id_fk = :payroll_run_id
        and pwr.workflow_status = 'PENDING_APPROVAL'
        order by pwr.workflow_request_id_pk desc
        limit 1
    """)
    row = conn.execute(check_pending, {"payroll_run_id": payroll_run_id}).mappings().first()
    return dict(row) if row else None


def _update_payroll_run_approval_status(
    conn,
    payroll_run_id: int,
    approved_by: str,
    payroll_org_id_fk: int | None = None,
):
    update_run = text("""
        update payroll_run
        set
            payroll_status = 'APPROVED',
            approved_by = :approved_by,
            approved_at = current_timestamp,
            updated_by = :approved_by,
            updated_at = current_timestamp
        where payroll_run_id_pk = :payroll_run_id
        and (:payroll_org_id_fk is null or payroll_org_id_fk = :payroll_org_id_fk)
        and payroll_status = 'PROCESSED'
    """)
    update_details = text("""
        update payroll_employee_detail
        set
            payroll_detail_status = 'APPROVED',
            payment_status = 'READY',
            updated_by = :approved_by,
            updated_at = current_timestamp
        where payroll_run_id_fk = :payroll_run_id
        and payroll_detail_status = 'PROCESSED'
    """)
    result = conn.execute(
        update_run,
        {
            "payroll_run_id": payroll_run_id,
            "payroll_org_id_fk": payroll_org_id_fk,
            "approved_by": approved_by,
        },
    )
    if result.rowcount == 0:
        return False
    conn.execute(
        update_details,
        {
            "payroll_run_id": payroll_run_id,
            "approved_by": approved_by,
        },
    )
    return True


def _create_pending_payroll_workflow_request(
    conn,
    payload: dict,
    payroll_run: dict,
    approver_user_principal_name: str | None,
    approver_official_email: str | None = None,
):
    request_payload = payload.copy()
    if approver_official_email:
        request_payload["approver_official_email"] = approver_official_email

    insert_workflow = text("""
        insert into payroll_workflow_requests (
            payroll_run_id_fk,
            payroll_run_type,
            workflow_action,
            workflow_status,
            workflow_confirmation,
            requester_user_principal_name,
            approver_user_principal_name,
            request_payload
        ) values (
            :payroll_run_id,
            :payroll_run_type,
            'APPROVAL',
            'PENDING_APPROVAL',
            'N',
            :requester_user_principal_name,
            :approver_user_principal_name,
            cast(:request_payload as jsonb)
        )
        returning workflow_request_id_pk
    """)
    return conn.execute(
        insert_workflow,
        {
            "payroll_run_id": payroll_run.get("payroll_run_id_pk"),
            "payroll_run_type": payroll_run.get("payroll_run_type"),
            "requester_user_principal_name": payload.get("user_principal_name"),
            "approver_user_principal_name": approver_user_principal_name,
            "request_payload": json.dumps(request_payload),
        },
    ).scalar_one()


def submit_payroll_run_for_approval(payload: dict):
    try:
        ensure_payroll_workflow_table()

        payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
        payroll_org_id_fk = _trusted_payroll_org_id(payload)
        user_principal_name = payload.get("user_principal_name")

        if not payroll_run_id:
            return {"workflow_confirmation": "N", "error": "payroll_run_id is required."}
        if not payroll_org_id_fk:
            return {"workflow_confirmation": "N", "error": "payroll_org_id_fk is required."}
        if not user_principal_name:
            return {"workflow_confirmation": "N", "error": "user_principal_name is required."}

        workflow_engine = _get_payroll_workflow_engine()
        with workflow_engine.begin() as conn:
            payroll_run = _get_payroll_run_for_update(
                conn,
                payroll_run_id,
                payroll_org_id_fk,
            )
            if not payroll_run:
                return {"workflow_confirmation": "N", "error": "Payroll run not found."}
            if int(payroll_run.get("payroll_org_id_fk")) != int(payroll_org_id_fk):
                return {"workflow_confirmation": "N", "error": "Payroll run belongs to another organization."}
            if payroll_run.get("payroll_run_type") not in SUPPORTED_PAYROLL_RUN_TYPES:
                return {
                    "workflow_confirmation": "N",
                    "error": f"Unsupported payroll run type: {payroll_run.get('payroll_run_type')}",
                }
            if payroll_run.get("payroll_status") != "PROCESSED":
                return {
                    "workflow_confirmation": "N",
                    "payroll_status": payroll_run.get("payroll_status"),
                    "error": "Only processed payroll runs can be submitted for approval.",
                }

            pending_workflow = _has_pending_payroll_workflow(conn, payroll_run_id)
            if pending_workflow:
                return {
                    "workflow_confirmation": "N",
                    "workflow_status": "PENDING_APPROVAL",
                    "workflow_code": pending_workflow.get("workflow_code") or WORKFLOW_CODE,
                    "workflow_action": pending_workflow.get("workflow_action") or "APPROVAL",
                    "workflow_request_id": pending_workflow.get("workflow_request_id_pk"),
                    "workflow_instance_id": pending_workflow.get("workflow_instance_id_fk"),
                    "workflow_instance_step_id": pending_workflow.get("workflow_instance_step_id_pk"),
                    "domain_reference_id": payroll_run_id,
                    "current_step": pending_workflow.get("step_sequence"),
                    "approver_user_principal_name": (
                        pending_workflow.get("assigned_approver_user_principal_name")
                        or pending_workflow.get("approver_user_principal_name")
                    ),
                    "current_approver_user_principal_name": (
                        pending_workflow.get("assigned_approver_user_principal_name")
                        or pending_workflow.get("approver_user_principal_name")
                    ),
                    "payroll_status": payroll_run.get("payroll_status"),
                    "workflow_required": True,
                    "business_operation_executed": False,
                    "execution_result": None,
                    "idempotent_replay": True,
                    "message": "Existing pending payroll workflow returned for duplicate submission.",
                }

            effective_org_id = payroll_run.get("payroll_org_id_fk")
            payroll_run_type = payroll_run.get("payroll_run_type")

            workflow_response = submit_configured_workflow(
                workflow_code=WORKFLOW_CODE,
                workflow_action="APPROVAL",
                payload={
                    **payload,
                    "payroll_run_id": payroll_run_id,
                    "payroll_run_id_pk": payroll_run_id,
                    "payroll_org_id_fk": effective_org_id,
                },
                organization_id=effective_org_id,
                legacy_table_name=PAYROLL_WORKFLOW_REQUEST_TABLE,
                legacy_extra_values={
                    "payroll_run_id_fk": payroll_run_id,
                    "payroll_run_type": payroll_run_type,
                },
                conn=conn,
            )
        if workflow_response.get("workflow_status") == "PENDING_APPROVAL":
            workflow_response["payroll_status"] = "PROCESSED"
        return workflow_response
    except Exception as e:
        logger.exception("Failed to submit payroll workflow")
        return {
            "workflow_confirmation": "N",
            "workflow_status": "ERROR",
            "error": f"Failed to submit payroll workflow. Error Message: {str(e)}",
        }


def get_payroll_workflow_requests(payload: dict | None = None):
    try:
        ensure_payroll_workflow_table()
        payload = payload or {}
        payroll_org_id_fk = _trusted_payroll_org_id(payload)
        if not payroll_org_id_fk:
            return {"error": "payroll_org_id_fk is required."}
        actionable_only = bool(payload.get("actionable_only", False))
        get_requests = text("""
            select
                pwr.*,
                pr.payroll_status,
                pr.payroll_org_id_fk,
                pr.payroll_run_code,
                pr.payroll_year,
                pr.payroll_month
            from payroll_workflow_requests pwr
            join payroll_run pr
                on pr.payroll_run_id_pk = pwr.payroll_run_id_fk
            left join workflow_instances wi
                on wi.workflow_instance_id_pk = pwr.workflow_instance_id_fk
            left join workflow_instance_steps current_step
                on current_step.workflow_instance_id_fk = wi.workflow_instance_id_pk
                and current_step.step_status = 'PENDING'
            where (:payroll_run_id is null or pwr.payroll_run_id_fk = :payroll_run_id)
            and pr.payroll_org_id_fk = :payroll_org_id_fk
            and (:workflow_status is null or pwr.workflow_status = :workflow_status)
            and (:user_principal_name is null
                or lower(pwr.requester_user_principal_name) = lower(:user_principal_name)
                or lower(pwr.approver_user_principal_name) = lower(:user_principal_name)
                or lower(current_step.assigned_approver_user_principal_name) = lower(:user_principal_name)
                or exists (
                    select 1
                    from workflow_instance_steps history_step
                    where history_step.workflow_instance_id_fk = wi.workflow_instance_id_pk
                    and (
                        lower(history_step.assigned_approver_user_principal_name) = lower(:user_principal_name)
                        or lower(history_step.acted_by_user_principal_name) = lower(:user_principal_name)
                    )
                ))
            and (
                :actionable_only = false
                or (
                    pwr.workflow_status = 'PENDING_APPROVAL'
                    and lower(current_step.assigned_approver_user_principal_name) = lower(:user_principal_name)
                )
            )
            order by pwr.workflow_request_id_pk desc
        """)
        with _get_payroll_workflow_engine().begin() as conn:
            df_requests = pd.read_sql(
                sql=get_requests,
                con=conn,
                params={
                    "payroll_run_id": payload.get("payroll_run_id") or payload.get("payroll_run_id_pk"),
                    "payroll_org_id_fk": payroll_org_id_fk,
                    "workflow_status": payload.get("workflow_status"),
                    "user_principal_name": payload.get("user_principal_name"),
                    "actionable_only": actionable_only,
                },
            )
        return _dataframe_to_response(df_requests)
    except Exception as e:
        logger.exception("Failed to get payroll workflow requests")
        df_requests = pd.DataFrame()
        return df_requests, {"Failed to get payroll workflow requests. Error Message: ": {e}}


def get_pending_payroll_approvals(payload: dict):
    payload = {
        **payload,
        "workflow_status": "PENDING_APPROVAL",
        "actionable_only": True,
    }
    return get_payroll_workflow_requests(payload)


def get_payroll_workflow_status(payload: dict):
    try:
        ensure_payroll_workflow_table()
        payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
        payroll_org_id_fk = _trusted_payroll_org_id(payload)
        if not payroll_run_id:
            return {"error": "payroll_run_id is required."}
        if not payroll_org_id_fk:
            return {"error": "payroll_org_id_fk is required."}
        with _get_payroll_workflow_engine().begin() as conn:
            payroll_run = _get_payroll_run_for_update(
                conn,
                payroll_run_id,
                payroll_org_id_fk,
            )
            if not payroll_run:
                return {"error": "Payroll run not found."}
            return {
                "payroll_run_id_pk": payroll_run_id,
                "payroll_status": payroll_run.get("payroll_status"),
                "workflow_status": _get_latest_workflow_status(conn, payroll_run_id),
            }
    except Exception as e:
        logger.exception("Failed to get payroll workflow status")
        return {"error": f"Failed to get payroll workflow status. Error Message: {str(e)}"}


def execute_approved_payroll_action(workflow_action: str, request_payload: dict, conn=None):
    if workflow_action != "APPROVAL":
        return {"error": f"Unsupported payroll workflow action: {workflow_action}"}

    payroll_run_id = request_payload.get("payroll_run_id") or request_payload.get("payroll_run_id_pk")
    payroll_org_id_fk = _trusted_payroll_org_id(request_payload)
    approved_by = (
        request_payload.get("acting_user_principal_name")
        or request_payload.get("approver_user_principal_name")
        or request_payload.get("user_principal_name")
    )
    if not payroll_run_id:
        return {"error": "payroll_run_id is required."}
    if not approved_by:
        return {"error": "approved_by is required."}

    if conn is not None:
        return _execute_approved_payroll_action_in_conn(
            conn,
            request_payload,
            payroll_run_id,
            payroll_org_id_fk,
            approved_by,
        )

    with _get_payroll_workflow_engine().begin() as payroll_conn:
        return _execute_approved_payroll_action_in_conn(
            payroll_conn,
            request_payload,
            payroll_run_id,
            payroll_org_id_fk,
            approved_by,
        )


def _execute_approved_payroll_action_in_conn(
    conn,
    request_payload: dict,
    payroll_run_id,
    payroll_org_id_fk,
    approved_by: str,
):
    payroll_run = _get_payroll_run_for_update(
        conn,
        payroll_run_id,
        payroll_org_id_fk,
    )
    if not payroll_run:
        return {"error": "Payroll run not found."}
    if payroll_run.get("payroll_run_type") not in SUPPORTED_PAYROLL_RUN_TYPES:
        return {"error": f"Unsupported payroll run type: {payroll_run.get('payroll_run_type')}"}
    if payroll_run.get("payroll_status") == "APPROVED":
        return {
            "message": "Payroll run already approved.",
            "payroll_run_id_pk": payroll_run_id,
            "payroll_status": "APPROVED",
            "workflow_instance_id": request_payload.get("workflow_instance_id"),
            "idempotent_replay": True,
        }
    if payroll_run.get("payroll_status") != "PROCESSED":
        return {
            "error": "Only processed payroll runs can be approved.",
            "payroll_status": payroll_run.get("payroll_status"),
        }
    approved = _update_payroll_run_approval_status(
        conn,
        payroll_run_id=payroll_run_id,
        approved_by=approved_by,
        payroll_org_id_fk=payroll_org_id_fk,
    )
    if not approved:
        return {
            "error": "Payroll run could not be approved.",
            "payroll_run_id_pk": payroll_run_id,
            "payroll_status": payroll_run.get("payroll_status"),
        }
    return {
        "message": "Payroll run approved.",
        "payroll_run_id_pk": payroll_run_id,
        "payroll_status": "APPROVED",
    }


def _inject_final_payroll_approval_metadata(
    request_payload: dict,
    final_approver_user_principal_name: str,
    approval_comments: str | None,
) -> dict:
    return {
        **request_payload,
        "acting_user_principal_name": final_approver_user_principal_name,
        "approver_user_principal_name": final_approver_user_principal_name,
        "approval_comments": approval_comments,
    }


def _validate_payroll_workflow_request_org(payload: dict):
    workflow_request_id = payload.get("workflow_request_id") or payload.get("workflow_request_id_pk")
    workflow_instance_id = payload.get("workflow_instance_id") or payload.get("workflow_instance_id_pk")
    payroll_org_id_fk = _trusted_payroll_org_id(payload)
    if not workflow_request_id and not workflow_instance_id:
        return {"workflow_confirmation": "N", "error": "workflow_request_id or workflow_instance_id is required."}
    if not payroll_org_id_fk:
        return {"workflow_confirmation": "N", "error": "payroll_org_id_fk is required."}

    if workflow_instance_id:
        query = text("""
            select workflow_instance_id_pk
            from workflow_instances
            where workflow_instance_id_pk = :workflow_instance_id
            and workflow_code = 'PAYROLL'
            and organization_id_fk = :payroll_org_id_fk
            limit 1
        """)
        with _get_payroll_workflow_engine().begin() as conn:
            row = conn.execute(
                query,
                {
                    "workflow_instance_id": workflow_instance_id,
                    "payroll_org_id_fk": payroll_org_id_fk,
                },
            ).first()
        if not row:
            return {"workflow_confirmation": "N", "error": "Workflow instance not found."}
        return None

    query = text("""
        select pwr.workflow_request_id_pk
        from payroll_workflow_requests pwr
        join payroll_run pr
            on pr.payroll_run_id_pk = pwr.payroll_run_id_fk
        where pwr.workflow_request_id_pk = :workflow_request_id
        and pr.payroll_org_id_fk = :payroll_org_id_fk
        limit 1
    """)
    with _get_payroll_workflow_engine().begin() as conn:
        row = conn.execute(
            query,
            {
                "workflow_request_id": workflow_request_id,
                "payroll_org_id_fk": payroll_org_id_fk,
            },
        ).first()
    if not row:
        return {"workflow_confirmation": "N", "error": "Workflow request not found."}
    return None


def approve_payroll_workflow(payload: dict):
    try:
        ensure_payroll_workflow_table()
        org_error = _validate_payroll_workflow_request_org(payload)
        if org_error:
            return org_error
        result = approve_configured_or_legacy_workflow(
            workflow_code=WORKFLOW_CODE,
            payload=payload,
            legacy_table_name=PAYROLL_WORKFLOW_REQUEST_TABLE,
            execution_adapter=execute_approved_payroll_action,
            final_payload_enricher=_inject_final_payroll_approval_metadata,
            legacy_organization_field_names=LEGACY_ORG_FIELDS,
        )
        execution_result = result.get("execution_result")
        if isinstance(execution_result, dict) and execution_result.get("payroll_status"):
            result["payroll_status"] = execution_result.get("payroll_status")
        return result
    except Exception as e:
        logger.exception("Failed to approve payroll workflow")
        return {
            "workflow_confirmation": "N",
            "workflow_status": "ERROR",
            "error": f"Failed to approve payroll workflow. Error Message: {str(e)}",
        }


def reject_payroll_workflow(payload: dict):
    try:
        ensure_payroll_workflow_table()
        org_error = _validate_payroll_workflow_request_org(payload)
        if org_error:
            return org_error
        return reject_configured_or_legacy_workflow(
            workflow_code=WORKFLOW_CODE,
            payload=payload,
            legacy_table_name=PAYROLL_WORKFLOW_REQUEST_TABLE,
            legacy_organization_field_names=LEGACY_ORG_FIELDS,
        )
    except Exception as e:
        logger.exception("Failed to reject payroll workflow")
        return {
            "workflow_confirmation": "N",
            "workflow_status": "ERROR",
            "error": f"Failed to reject payroll workflow. Error Message: {str(e)}",
        }
