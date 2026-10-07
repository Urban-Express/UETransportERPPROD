from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text


PENALTY_BUSINESS_FIELDS = (
    "penalty_empl_id_fk", "penalty_date", "penalty_reason",
    "warning_letter_issued", "warning_letter_date", "warning_letter_accepted",
    "financial_implication", "penalty_amount", "recovery_split_percentage",
)


class PenaltyBusinessValidationError(Exception):
    pass


def positive_id(value, field_name):
    if value is None or value == "":
        raise PenaltyBusinessValidationError(f"{field_name} is required.")
    try:
        result = int(value)
        if isinstance(value, bool) or Decimal(str(value)) != result or result <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise PenaltyBusinessValidationError(f"Invalid {field_name}.") from None


def penalty_identity(payload, *, require_penalty=True, require_actor=False):
    params = {
        "authenticated_org_id": positive_id(payload.get("authenticated_org_id"), "authenticated_org_id"),
    }
    if require_penalty:
        params["penalty_id"] = positive_id(
            payload.get("penalty_id") or payload.get("penalty_id_pk"), "penalty_id",
        )
    if require_actor:
        principal = payload.get("authenticated_user_principal_name")
        if not principal:
            raise PenaltyBusinessValidationError("authenticated_user_principal_name is required.")
        if len(str(principal)) > 100:
            raise PenaltyBusinessValidationError("Invalid authenticated user principal length.")
        params["actor"] = str(principal)
    return params


def penalty_date_value(value, field_name):
    if not value:
        raise PenaltyBusinessValidationError(f"{field_name} is required.")
    try:
        if isinstance(value, datetime):
            raise ValueError
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise PenaltyBusinessValidationError(f"Invalid {field_name}; use YYYY-MM-DD.") from None


def penalty_decimal(value, field_name):
    if value is None or value == "":
        raise PenaltyBusinessValidationError(f"{field_name} is required when financial_implication is true.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        extra_places = -amount.as_tuple().exponent - 2
        if extra_places > 0 and any(amount.as_tuple().digits[-extra_places:]):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise PenaltyBusinessValidationError(
            f"Invalid {field_name}; at most two decimal places are supported."
        ) from None
    if field_name == "penalty_amount" and (amount <= 0 or amount >= Decimal("10000000000000000")):
        raise PenaltyBusinessValidationError("penalty_amount must be greater than 0 and fit NUMERIC(18,2).")
    if field_name == "recovery_split_percentage" and not Decimal("0") <= amount <= Decimal("100"):
        raise PenaltyBusinessValidationError("recovery_split_percentage must be between 0 and 100.")
    return amount


def validate_penalty_fields(payload):
    fields = {key: payload.get(key) for key in PENALTY_BUSINESS_FIELDS}
    fields["penalty_empl_id_fk"] = positive_id(fields["penalty_empl_id_fk"], "penalty_empl_id_fk")
    fields["penalty_date"] = penalty_date_value(fields["penalty_date"], "penalty_date")
    reason = fields["penalty_reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise PenaltyBusinessValidationError("A non-empty penalty_reason is required.")
    for key in ("warning_letter_issued", "financial_implication"):
        if not isinstance(fields[key], bool):
            raise PenaltyBusinessValidationError(f"Invalid {key}; use true or false.")

    if fields["warning_letter_issued"]:
        fields["warning_letter_date"] = penalty_date_value(fields["warning_letter_date"], "warning_letter_date")
        if not isinstance(fields["warning_letter_accepted"], bool):
            raise PenaltyBusinessValidationError("warning_letter_accepted is required; use true or false.")
    else:
        fields["warning_letter_date"] = None
        fields["warning_letter_accepted"] = None

    if fields["financial_implication"]:
        for key in ("penalty_amount", "recovery_split_percentage"):
            fields[key] = penalty_decimal(fields[key], key)
    else:
        fields["penalty_amount"] = None
        fields["recovery_split_percentage"] = None
    return fields


def get_penalty_row(conn, params, *, for_update=False):
    lock_clause = "for update" if for_update else ""
    row = conn.execute(text(f"""
        select * from penalty_master
        where penalty_id_pk = :penalty_id and penalty_org_id_fk = :authenticated_org_id
        {lock_clause}
    """), params).mappings().one_or_none()
    if row is None:
        raise PenaltyBusinessValidationError("Penalty not found.")
    return dict(row)


def validate_penalty_employee(conn, employee_id, org_id):
    row = conn.execute(text("""
        select em.empl_id_pk from employee_master em
        join organization_master org on org.org_id_pk = em.empl_org_id_fk
        where em.empl_id_pk = :employee_id and em.empl_org_id_fk = :org_id
        for share of em, org
    """), {"employee_id": employee_id, "org_id": org_id}).first()
    if row is None:
        raise PenaltyBusinessValidationError("Employee not found in authenticated organization.")
