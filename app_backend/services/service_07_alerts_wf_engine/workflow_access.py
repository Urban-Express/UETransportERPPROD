from sqlalchemy import text


WORKFLOW_MODULE_NAME = "WORKFLOW"
WORKFLOW_SUBMIT_ACTION = "SUBMIT"
WORKFLOW_APPROVE_ACTION = "APPROVE"
WORKFLOW_ADMIN_ACTION = "ADMIN"


def _normalise(value: str | None) -> str | None:
    return value.lower() if isinstance(value, str) else value


def get_user_workflow_actions(
    conn,
    user_principal_name: str,
    organization_id: int | None = None,
) -> set[str]:
    rows = conn.execute(
        text("""
            select distinct upper(vuar.action_name) as action_name
            from v_user_access_rights vuar
            join user_master um
                on lower(um.user_principal_name) = lower(vuar.user_principal_name)
            where lower(vuar.user_principal_name) = lower(:user_principal_name)
            and upper(vuar.module_name) = :module_name
            and upper(vuar.action_name) in (:submit_action, :approve_action, :admin_action)
            and coalesce(um.is_active, true) = true
            and coalesce(um.is_deleted, false) = false
            and (:organization_id is null or um.user_org_id_fk = :organization_id)
        """),
        {
            "user_principal_name": user_principal_name,
            "organization_id": organization_id,
            "module_name": WORKFLOW_MODULE_NAME,
            "submit_action": WORKFLOW_SUBMIT_ACTION,
            "approve_action": WORKFLOW_APPROVE_ACTION,
            "admin_action": WORKFLOW_ADMIN_ACTION,
        },
    ).mappings()
    return {row["action_name"] for row in rows}


def user_has_workflow_action(
    conn,
    user_principal_name: str,
    action_name: str,
    organization_id: int | None = None,
) -> bool:
    return action_name.upper() in get_user_workflow_actions(
        conn,
        user_principal_name,
        organization_id,
    )


def get_active_user(
    conn,
    user_principal_name: str,
    organization_id: int | None = None,
) -> dict | None:
    row = conn.execute(
        text("""
            select
                user_id_pk,
                user_principal_name,
                display_name,
                email,
                user_org_id_fk
            from user_master
            where lower(user_principal_name) = lower(:user_principal_name)
            and coalesce(is_active, true) = true
            and coalesce(is_deleted, false) = false
            and (:organization_id is null or user_org_id_fk = :organization_id)
            limit 1
        """),
        {
            "user_principal_name": user_principal_name,
            "organization_id": organization_id,
        },
    ).mappings().first()
    return dict(row) if row else None


def list_eligible_users(conn, organization_id: int | None = None) -> dict[str, list[dict]]:
    rows = conn.execute(
        text("""
            select
                um.user_principal_name,
                coalesce(um.display_name, um.user_principal_name) as display_name,
                um.email,
                um.user_org_id_fk,
                array_remove(array_agg(distinct vuar.role_name), null) as roles,
                array_remove(array_agg(distinct upper(vuar.action_name)), null) as workflow_permissions
            from user_master um
            join v_user_access_rights vuar
                on lower(vuar.user_principal_name) = lower(um.user_principal_name)
            where coalesce(um.is_active, true) = true
            and coalesce(um.is_deleted, false) = false
            and upper(vuar.module_name) = :module_name
            and upper(vuar.action_name) in (:submit_action, :approve_action, :admin_action)
            and (:organization_id is null or um.user_org_id_fk = :organization_id)
            group by
                um.user_principal_name,
                um.display_name,
                um.email,
                um.user_org_id_fk
            order by display_name, um.user_principal_name
        """),
        {
            "organization_id": organization_id,
            "module_name": WORKFLOW_MODULE_NAME,
            "submit_action": WORKFLOW_SUBMIT_ACTION,
            "approve_action": WORKFLOW_APPROVE_ACTION,
            "admin_action": WORKFLOW_ADMIN_ACTION,
        },
    ).mappings()

    requesters: list[dict] = []
    approvers: list[dict] = []
    for row in rows:
        user = dict(row)
        permissions = {permission.upper() for permission in user.get("workflow_permissions") or []}
        if WORKFLOW_SUBMIT_ACTION in permissions:
            requesters.append(user)
        if WORKFLOW_APPROVE_ACTION in permissions:
            approvers.append(user)

    return {"requesters": requesters, "approvers": approvers}


def organization_exists(conn, organization_id: int | None) -> bool:
    if organization_id is None:
        return False
    row = conn.execute(
        text("select org_id_pk from organization_master where org_id_pk = :organization_id"),
        {"organization_id": organization_id},
    ).first()
    return row is not None
