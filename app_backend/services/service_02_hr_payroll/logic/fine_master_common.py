from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text


FINE_BUSINESS_FIELDS = (
    "fine_date", "fine_on", "fine_empl_id_fk", "fine_accountability",
    "payment_authority", "amount_paid", "recovery_split",
)


class FineBusinessValidationError(Exception):
    pass


def positive_id(value, field_name):
    if value is None or value == "":
        raise FineBusinessValidationError(f"{field_name} is required.")
    try:
        result = int(value)
        if isinstance(value, bool) or Decimal(str(value)) != result or result <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise FineBusinessValidationError(f"Invalid {field_name}.") from None


def fine_identity(payload, *, require_fine=True, require_actor=False):
    params = {
        "authenticated_org_id": positive_id(payload.get("authenticated_org_id"), "authenticated_org_id"),
    }
    if require_fine:
        params["fine_id"] = positive_id(payload.get("fine_id") or payload.get("fine_id_pk"), "fine_id")
    if require_actor:
        principal = payload.get("authenticated_user_principal_name")
        if not principal:
            raise FineBusinessValidationError("authenticated_user_principal_name is required.")
        if len(str(principal)) > 100:
            raise FineBusinessValidationError("Invalid authenticated user principal length.")
        params["actor"] = str(principal)
    return params


def has_payment_fields(fine):
    return (
        fine.get("fine_on") == "DRIVER"
        and fine.get("fine_accountability") == "DRIVER"
        and fine.get("payment_authority") == "URBAN_EXPRESS"
    )


def fine_decimal(value, field_name):
    if value is None or value == "":
        raise FineBusinessValidationError(f"{field_name} is required when Driver is accountable and Urban Express pays.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        # Validate storage precision without rounding or changing the value.
        extra_places = -amount.as_tuple().exponent - 2
        if extra_places > 0 and any(amount.as_tuple().digits[-extra_places:]):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise FineBusinessValidationError(f"Invalid {field_name}; at most two decimal places are supported.") from None
    if field_name == "amount_paid" and (amount <= 0 or amount >= Decimal("10000000000000000")):
        raise FineBusinessValidationError("amount_paid must be greater than 0 and fit NUMERIC(18,2).")
    if field_name == "recovery_split" and not Decimal("0") < amount <= Decimal("100"):
        raise FineBusinessValidationError("recovery_split must be greater than 0 and not exceed 100.")
    return amount


def validate_fine_fields(payload):
    fields = {key: payload.get(key) for key in FINE_BUSINESS_FIELDS}
    value = fields["fine_date"]
    if not value:
        raise FineBusinessValidationError("fine_date is required.")
    try:
        if isinstance(value, datetime):
            raise ValueError
        fields["fine_date"] = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise FineBusinessValidationError("Invalid fine_date; use YYYY-MM-DD.") from None
    if fields["fine_on"] not in ("DRIVER", "EMPLOYEE"):
        raise FineBusinessValidationError("Invalid fine_on; use DRIVER or EMPLOYEE.")
    fields["fine_empl_id_fk"] = positive_id(fields["fine_empl_id_fk"], "fine_empl_id_fk")
    driver_fields = ("fine_accountability", "payment_authority")
    if fields["fine_on"] == "DRIVER":
        for key in driver_fields:
            if fields[key] is None:
                raise FineBusinessValidationError(f"{key} is required for Driver fines.")
            if fields[key] not in ("DRIVER", "URBAN_EXPRESS"):
                raise FineBusinessValidationError(f"Invalid {key}.")
    elif any(fields[key] is not None for key in driver_fields):
        raise FineBusinessValidationError("Invalid Employee fine: Driver-specific fields must be empty.")
    if has_payment_fields(fields):
        for key in ("amount_paid", "recovery_split"):
            fields[key] = fine_decimal(fields[key], key)
    elif fields["amount_paid"] is not None or fields["recovery_split"] is not None:
        raise FineBusinessValidationError("amount_paid and recovery_split must be empty for this Fine combination.")
    return fields


def get_fine_row(conn, params, *, for_update=False):
    lock_clause = "for update" if for_update else ""
    row = conn.execute(text(f"""
        select * from fine_master
        where fine_id_pk = :fine_id and fine_org_id_fk = :authenticated_org_id
        {lock_clause}
    """), params).mappings().one_or_none()
    if row is None:
        raise FineBusinessValidationError("Fine not found.")
    return dict(row)


def validate_fine_employee(conn, employee_id, org_id):
    # All employees qualify; no Fleet allocation, designation, or name filter.
    row = conn.execute(text("""
        select empl_id_pk from employee_master
        where empl_id_pk = :employee_id and empl_org_id_fk = :org_id
        for share
    """), {"employee_id": employee_id, "org_id": org_id}).first()
    if row is None:
        raise FineBusinessValidationError("Employee not found in authenticated organization.")
