import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_common import (
    DEALLOCATION_STATUSES,
    coerce_positive_int,
    normalize_optional_fk_fields,
    normalize_status,
    parse_datetime_field,
    resolve_actor_id,
    resolve_org_id,
    validate_date_status_rules,
    validate_driver_allocation_references_for_organization as _validate_references,
)


logger = logging.getLogger(__name__)


def validate_driver_allocation_references_for_organization(
    payload: dict,
    fleet_org_id_fk,
    fleet_engine=None,
    require_vehicle: bool = True,
    require_driver: bool = True,
):
    owns_engine = fleet_engine is None
    fleet_engine = fleet_engine or db_engine()
    try:
        with fleet_engine.begin() as conn:
            return _validate_references(
                payload,
                fleet_org_id_fk,
                conn,
                require_vehicle=require_vehicle,
                require_driver=require_driver,
            )
    finally:
        if owns_engine:
            fleet_engine.dispose()


def _normalise_create_payload(payload: dict) -> tuple[dict | None, dict | None]:
    normalized_payload, error = normalize_optional_fk_fields(payload)
    if error:
        return None, error

    fleet_org_id_fk, error = resolve_org_id(normalized_payload)
    if error:
        return None, error

    fleet_vehicle_id_fk, error = coerce_positive_int(
        normalized_payload.get("fleet_vehicle_id_fk"),
        "fleet_vehicle_id_fk",
    )
    if error:
        return None, error

    fleet_driver_empl_id_fk, error = coerce_positive_int(
        normalized_payload.get("fleet_driver_empl_id_fk"),
        "fleet_driver_empl_id_fk",
    )
    if error:
        return None, error

    allocation_status, error = normalize_status(
        normalized_payload.get("allocation_status"),
        default="scheduled",
    )
    if error:
        return None, error

    allocation_start_datetime, error = parse_datetime_field(
        normalized_payload.get("allocation_start_datetime"),
        "allocation_start_datetime",
        required=True,
    )
    if error:
        return None, error

    allocation_end_datetime, error = parse_datetime_field(
        normalized_payload.get("allocation_end_datetime"),
        "allocation_end_datetime",
        required=False,
    )
    if error:
        return None, error

    date_error = validate_date_status_rules(
        allocation_start_datetime,
        allocation_end_datetime,
        allocation_status,
    )
    if date_error:
        return None, date_error

    actor_id, error = resolve_actor_id(normalized_payload)
    if error:
        return None, error

    return {
        "fleet_org_id_fk": fleet_org_id_fk,
        "fleet_vehicle_id_fk": fleet_vehicle_id_fk,
        "fleet_driver_empl_id_fk": fleet_driver_empl_id_fk,
        "allocation_start_datetime": allocation_start_datetime,
        "allocation_end_datetime": allocation_end_datetime,
        "allocation_status": allocation_status,
        "route_id_fk": normalized_payload.get("route_id_fk"),
        "shift_id_fk": normalized_payload.get("shift_id_fk"),
        "allocation_reason": normalized_payload.get("allocation_reason"),
        "allocated_by": actor_id,
        "deallocation_reason": normalized_payload.get("deallocation_reason"),
        "deallocated_by": (
            actor_id if allocation_status in DEALLOCATION_STATUSES else None
        ),
        "created_by": actor_id,
        "updated_by": actor_id,
    }, None


def create_fleet_driver_allocation(payload: dict):
    payload = dict(payload or {})
    normalized_payload, error = _normalise_create_payload(payload)
    if error:
        return error

    fleet_engine = db_engine()
    try:
        insert_stmt = text("""
            insert into fleet_driver_allocation(
                fleet_org_id_fk,
                fleet_vehicle_id_fk,
                fleet_driver_empl_id_fk,
                allocation_start_datetime,
                allocation_end_datetime,
                allocation_status,
                route_id_fk,
                shift_id_fk,
                allocation_reason,
                allocated_by,
                deallocation_reason,
                deallocated_by,
                deallocated_at,
                created_by,
                updated_by
            ) values (
                :fleet_org_id_fk,
                :fleet_vehicle_id_fk,
                :fleet_driver_empl_id_fk,
                :allocation_start_datetime,
                :allocation_end_datetime,
                :allocation_status,
                :route_id_fk,
                :shift_id_fk,
                :allocation_reason,
                :allocated_by,
                :deallocation_reason,
                :deallocated_by,
                case
                    when :deallocated_by is null then null
                    else current_timestamp
                end,
                :created_by,
                :updated_by
            )
            returning fleet_driver_allocation_id_pk
        """)

        with fleet_engine.begin() as conn:
            reference_error = _validate_references(
                normalized_payload,
                normalized_payload["fleet_org_id_fk"],
                conn,
                require_vehicle=True,
                require_driver=True,
            )
            if reference_error:
                return reference_error

            created_row = conn.execute(insert_stmt, normalized_payload).first()

        return {
            "message": (
                "Successfully created fleet driver allocation for vehicle ID: "
                f"{normalized_payload['fleet_vehicle_id_fk']}"
            ),
            "fleet_driver_allocation_id_pk": (
                int(created_row.fleet_driver_allocation_id_pk)
                if created_row is not None
                else None
            ),
        }

    except Exception:
        logger.exception(
            "Failed to create fleet driver allocation",
            extra={
                "fleet_org_id_fk": normalized_payload.get("fleet_org_id_fk"),
                "fleet_vehicle_id_fk": normalized_payload.get("fleet_vehicle_id_fk"),
                "fleet_driver_empl_id_fk": normalized_payload.get(
                    "fleet_driver_empl_id_fk"
                ),
            },
        )
        return {"error": "Failed to create fleet driver allocation."}
    finally:
        fleet_engine.dispose()


def create_driver_allocation(payload: dict):
    return create_fleet_driver_allocation(payload)
