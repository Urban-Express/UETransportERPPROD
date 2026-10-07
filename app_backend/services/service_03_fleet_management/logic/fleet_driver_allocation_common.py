from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text


DRIVER_ALLOCATION_COLUMNS = (
    "fleet_driver_allocation_id_pk",
    "fleet_org_id_fk",
    "fleet_vehicle_id_fk",
    "fleet_driver_empl_id_fk",
    "allocation_start_datetime",
    "allocation_end_datetime",
    "allocation_status",
    "route_id_fk",
    "shift_id_fk",
    "allocation_reason",
    "allocated_by",
    "allocated_at",
    "deallocation_reason",
    "deallocated_by",
    "deallocated_at",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
)

VALID_ALLOCATION_STATUSES = {"scheduled", "active", "completed", "cancelled"}
DEALLOCATION_STATUSES = {"completed", "cancelled"}
OPTIONAL_FK_FIELDS = {"route_id_fk", "shift_id_fk"}

EDITABLE_DRIVER_ALLOCATION_FIELDS = (
    "fleet_vehicle_id_fk",
    "fleet_driver_empl_id_fk",
    "allocation_start_datetime",
    "allocation_end_datetime",
    "allocation_status",
    "route_id_fk",
    "shift_id_fk",
    "allocation_reason",
    "deallocation_reason",
)


def business_error(message: str) -> dict[str, str]:
    return {"error": message}


def _empty(value: Any) -> bool:
    return value is None or value == "" or (
        isinstance(value, str) and not value.strip()
    )


def coerce_positive_int(value: Any, field_name: str) -> tuple[int | None, dict | None]:
    if _empty(value):
        return None, business_error(f"{field_name} is required.")
    try:
        integer_value = int(value)
    except (TypeError, ValueError):
        return None, business_error(f"Invalid {field_name}.")
    if integer_value <= 0:
        return None, business_error(f"Invalid {field_name}.")
    return integer_value, None


def resolve_org_id(payload: dict[str, Any]) -> tuple[int | None, dict | None]:
    return coerce_positive_int(
        payload.get("authenticated_org_id") or payload.get("fleet_org_id_fk"),
        "fleet_org_id_fk",
    )


def resolve_allocation_id(payload: dict[str, Any]) -> tuple[int | None, dict | None]:
    return coerce_positive_int(
        payload.get("fleet_driver_allocation_id")
        or payload.get("fleet_driver_allocation_id_pk"),
        "fleet_driver_allocation_id_pk",
    )


def resolve_actor_id(payload: dict[str, Any]) -> tuple[int | None, dict | None]:
    actor_value = payload.get("authenticated_user_id")
    if actor_value is not None and actor_value != "":
        return coerce_positive_int(actor_value, "authenticated_user_id")

    # Direct logic tests may not pass an auth context. Prefer the trusted value
    # above when it exists, then fall back to the existing audit payload fields.
    for field_name in ("updated_by", "created_by", "allocated_by"):
        value = payload.get(field_name)
        if value is not None and value != "":
            return coerce_positive_int(value, field_name)
    return None, business_error("authenticated_user_id is required.")


def normalize_optional_fk(value: Any, field_name: str) -> tuple[int | None, dict | None]:
    if _empty(value):
        return None, None
    if isinstance(value, str) and value.strip() == "0":
        return None, None
    if value == 0:
        return None, None
    try:
        integer_value = int(value)
    except (TypeError, ValueError):
        return None, business_error(f"Invalid {field_name}.")
    if integer_value <= 0:
        return None, business_error(f"Invalid {field_name}.")
    return integer_value, None


def normalize_optional_fk_fields(payload: dict[str, Any]) -> tuple[dict[str, Any], dict | None]:
    normalized_payload = dict(payload)
    for field_name in OPTIONAL_FK_FIELDS:
        if field_name in normalized_payload:
            normalized_value, error = normalize_optional_fk(
                normalized_payload.get(field_name),
                field_name,
            )
            if error:
                return normalized_payload, error
            normalized_payload[field_name] = normalized_value
    return normalized_payload, None


def normalize_status(
    value: Any,
    *,
    default: str | None,
) -> tuple[str | None, dict | None]:
    if _empty(value):
        if default is not None:
            return default, None
        return None, business_error("allocation_status is required.")

    status = str(value).strip().lower()
    if status not in VALID_ALLOCATION_STATUSES:
        return None, business_error("Invalid allocation_status.")
    return status, None


def parse_datetime_field(
    value: Any,
    field_name: str,
    *,
    required: bool,
) -> tuple[datetime | None, dict | None]:
    if _empty(value):
        if required:
            return None, business_error(f"{field_name} is required.")
        return None, None

    if isinstance(value, datetime):
        return value, None

    if not isinstance(value, str):
        return None, business_error(f"Invalid {field_name}.")

    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized), None
    except ValueError:
        return None, business_error(f"Invalid {field_name}.")


def validate_date_status_rules(
    start_datetime: datetime | None,
    end_datetime: datetime | None,
    allocation_status: str,
) -> dict | None:
    if start_datetime is None:
        return business_error("allocation_start_datetime is required.")

    if (
        end_datetime is not None
        and _comparison_datetime(end_datetime) < _comparison_datetime(start_datetime)
    ):
        return business_error(
            "Invalid allocation date range: allocation_end_datetime cannot be "
            "earlier than allocation_start_datetime."
        )

    if allocation_status == "active" and end_datetime is not None:
        return business_error(
            "Invalid allocation_status: active allocations must have "
            "allocation_end_datetime empty."
        )

    if allocation_status == "completed" and end_datetime is None:
        return business_error(
            "Invalid allocation_status: completed allocations must have "
            "allocation_end_datetime."
        )

    return None


def _comparison_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def validate_driver_allocation_references_for_organization(
    payload: dict[str, Any],
    fleet_org_id_fk: int | None,
    conn,
    *,
    require_vehicle: bool = True,
    require_driver: bool = True,
) -> dict | None:
    if not fleet_org_id_fk:
        return business_error("fleet_org_id_fk is required.")

    fleet_vehicle_id_fk = payload.get("fleet_vehicle_id_fk")
    if require_vehicle and not fleet_vehicle_id_fk:
        return business_error("fleet_vehicle_id_fk is required.")

    if fleet_vehicle_id_fk:
        fleet_row = conn.execute(
            text("""
                select fleet_vehicle_id_pk
                from fleet_master
                where fleet_vehicle_id_pk = :fleet_vehicle_id_fk
                  and fleet_org_id_fk = :fleet_org_id_fk
                limit 1
            """),
            {
                "fleet_vehicle_id_fk": fleet_vehicle_id_fk,
                "fleet_org_id_fk": fleet_org_id_fk,
            },
        ).first()
        if not fleet_row:
            return business_error("Fleet vehicle ID not found.")

    fleet_driver_empl_id_fk = payload.get("fleet_driver_empl_id_fk")
    if require_driver and not fleet_driver_empl_id_fk:
        return business_error("fleet_driver_empl_id_fk is required.")

    if fleet_driver_empl_id_fk:
        employee_row = conn.execute(
            text("""
                select empl_id_pk
                from employee_master
                where empl_id_pk = :fleet_driver_empl_id_fk
                  and empl_org_id_fk = :fleet_org_id_fk
                limit 1
            """),
            {
                "fleet_driver_empl_id_fk": fleet_driver_empl_id_fk,
                "fleet_org_id_fk": fleet_org_id_fk,
            },
        ).first()
        if not employee_row:
            return business_error("Driver employee ID not found.")

    return None
