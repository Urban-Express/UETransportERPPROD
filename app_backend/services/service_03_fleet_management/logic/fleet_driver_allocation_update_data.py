import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_common import (
    DEALLOCATION_STATUSES,
    DRIVER_ALLOCATION_COLUMNS,
    EDITABLE_DRIVER_ALLOCATION_FIELDS,
    business_error,
    coerce_positive_int,
    normalize_optional_fk,
    normalize_status,
    parse_datetime_field,
    resolve_actor_id,
    resolve_allocation_id,
    resolve_org_id,
    validate_date_status_rules,
    validate_driver_allocation_references_for_organization,
)


logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return dict(row._mapping)


def _normalise_update_payload(
    payload: dict,
    existing_row: dict,
) -> tuple[dict | None, dict | None, bool | None]:
    normalized_payload = dict(payload)

    actor_id, error = resolve_actor_id(normalized_payload)
    if error:
        return None, error, None

    updates: dict = {}
    for field_name in EDITABLE_DRIVER_ALLOCATION_FIELDS:
        if field_name in normalized_payload:
            updates[field_name] = normalized_payload[field_name]

    if not updates:
        return (
            None,
            business_error(
                "At least one editable fleet driver allocation field is required."
            ),
            None,
        )

    for field_name in ("fleet_vehicle_id_fk", "fleet_driver_empl_id_fk"):
        if field_name in updates:
            value, error = coerce_positive_int(updates[field_name], field_name)
            if error:
                return None, error, None
            updates[field_name] = value

    for field_name in ("route_id_fk", "shift_id_fk"):
        if field_name in updates:
            value, error = normalize_optional_fk(updates[field_name], field_name)
            if error:
                return None, error, None
            updates[field_name] = value

    if "allocation_status" in updates:
        allocation_status, error = normalize_status(
            updates["allocation_status"],
            default=None,
        )
        if error:
            return None, error, None
        updates["allocation_status"] = allocation_status

    if "allocation_start_datetime" in updates:
        value, error = parse_datetime_field(
            updates["allocation_start_datetime"],
            "allocation_start_datetime",
            required=True,
        )
        if error:
            return None, error, None
        updates["allocation_start_datetime"] = value

    if "allocation_end_datetime" in updates:
        value, error = parse_datetime_field(
            updates["allocation_end_datetime"],
            "allocation_end_datetime",
            required=False,
        )
        if error:
            return None, error, None
        updates["allocation_end_datetime"] = value

    effective_start = updates.get(
        "allocation_start_datetime",
        existing_row["allocation_start_datetime"],
    )
    effective_end = updates.get(
        "allocation_end_datetime",
        existing_row["allocation_end_datetime"],
    )
    effective_status = updates.get(
        "allocation_status",
        existing_row["allocation_status"],
    )

    date_error = validate_date_status_rules(
        effective_start,
        effective_end,
        effective_status,
    )
    if date_error:
        return None, date_error, None

    existing_status = str(existing_row["allocation_status"]).lower()
    transition_to_deallocated = (
        existing_status not in DEALLOCATION_STATUSES
        and effective_status in DEALLOCATION_STATUSES
    )

    updates["updated_by"] = actor_id
    if transition_to_deallocated:
        updates["deallocated_by"] = actor_id

    return updates, None, transition_to_deallocated


def update_fleet_driver_allocation(payload: dict):
    payload = dict(payload or {})
    fleet_org_id_fk, error = resolve_org_id(payload)
    if error:
        return error

    fleet_driver_allocation_id, error = resolve_allocation_id(payload)
    if error:
        return error

    select_columns = ", ".join(DRIVER_ALLOCATION_COLUMNS)
    fleet_engine = db_engine()
    try:
        with fleet_engine.begin() as conn:
            existing_row = conn.execute(
                text(f"""
                    select {select_columns}
                    from fleet_driver_allocation
                    where fleet_driver_allocation_id_pk = :fleet_driver_allocation_id
                      and fleet_org_id_fk = :fleet_org_id_fk
                    for update
                """),
                {
                    "fleet_driver_allocation_id": fleet_driver_allocation_id,
                    "fleet_org_id_fk": fleet_org_id_fk,
                },
            ).first()
            if not existing_row:
                return business_error("Fleet driver allocation ID not found.")

            existing = _row_to_dict(existing_row)
            updates, error, transition_to_deallocated = _normalise_update_payload(
                payload,
                existing,
            )
            if error:
                return error

            reference_payload = {}
            if "fleet_vehicle_id_fk" in updates:
                reference_payload["fleet_vehicle_id_fk"] = updates["fleet_vehicle_id_fk"]
            if "fleet_driver_empl_id_fk" in updates:
                reference_payload["fleet_driver_empl_id_fk"] = updates[
                    "fleet_driver_empl_id_fk"
                ]
            if reference_payload:
                reference_error = validate_driver_allocation_references_for_organization(
                    reference_payload,
                    fleet_org_id_fk,
                    conn,
                    require_vehicle=False,
                    require_driver=False,
                )
                if reference_error:
                    return reference_error

            set_clauses = []
            params = {
                "fleet_driver_allocation_id": fleet_driver_allocation_id,
                "fleet_org_id_fk": fleet_org_id_fk,
            }
            for field_name, value in updates.items():
                set_clauses.append(f"{field_name} = :{field_name}")
                params[field_name] = value

            if transition_to_deallocated:
                set_clauses.append("deallocated_at = current_timestamp")
            set_clauses.append("updated_at = current_timestamp")

            update_stmt = text(f"""
                update fleet_driver_allocation
                set {", ".join(set_clauses)}
                where fleet_driver_allocation_id_pk = :fleet_driver_allocation_id
                  and fleet_org_id_fk = :fleet_org_id_fk
                returning fleet_driver_allocation_id_pk
            """)

            updated_row = conn.execute(update_stmt, params).first()
            if not updated_row:
                return business_error("Fleet driver allocation ID not found.")

        return {
            "message": (
                "Successfully updated fleet driver allocation ID: "
                f"{fleet_driver_allocation_id}"
            ),
            "fleet_driver_allocation_id_pk": fleet_driver_allocation_id,
        }

    except Exception:
        logger.exception(
            "Failed to update fleet driver allocation",
            extra={
                "fleet_org_id_fk": fleet_org_id_fk,
                "fleet_driver_allocation_id_pk": fleet_driver_allocation_id,
            },
        )
        return {"error": "Failed to update fleet driver allocation."}
    finally:
        fleet_engine.dispose()


def update_driver_allocation(payload: dict):
    return update_fleet_driver_allocation(payload)
