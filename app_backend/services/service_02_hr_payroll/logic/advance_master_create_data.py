import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


logger = logging.getLogger(__name__)


class AdvanceBusinessValidationError(Exception):
    pass


def positive_id(value, field_name):
    if value is None or value == "":
        raise AdvanceBusinessValidationError(f"{field_name} is required.")
    try:
        result = int(value)
        if isinstance(value, bool) or Decimal(str(value)) != result or result <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise AdvanceBusinessValidationError(f"Invalid {field_name}.") from None


def advance_identity(payload, *, require_advance=True, require_actor=False):
    params = {
        "authenticated_org_id": positive_id(payload.get("authenticated_org_id"), "authenticated_org_id"),
    }
    if require_advance:
        params["advance_id"] = positive_id(
            payload.get("advance_id") or payload.get("advance_id_pk"), "advance_id",
        )
    if require_actor:
        principal = payload.get("authenticated_user_principal_name")
        if not isinstance(principal, str) or not principal.strip():
            raise AdvanceBusinessValidationError("authenticated_user_principal_name is required.")
        if len(principal) > 100:
            raise AdvanceBusinessValidationError("Invalid authenticated user principal length.")
        params["actor"] = principal
    return params


def advance_decimal(value, field_name):
    if value is None or value == "":
        raise AdvanceBusinessValidationError(f"{field_name} is required.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        extra_places = -amount.as_tuple().exponent - 2
        if extra_places > 0 and any(amount.as_tuple().digits[-extra_places:]):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise AdvanceBusinessValidationError(
            f"Invalid {field_name}; at most two decimal places are supported."
        ) from None
    if field_name == "advance_amount" and not Decimal("0") < amount < Decimal("1000000000000"):
        raise AdvanceBusinessValidationError("advance_amount must be greater than 0 and fit NUMERIC(14,2).")
    if field_name == "recovery_split_percentage" and not Decimal("0") <= amount <= Decimal("100"):
        raise AdvanceBusinessValidationError("recovery_split_percentage must be between 0 and 100.")
    return amount


def get_advance_params(payload: dict):
    fields = {
        "advance_empl_id_fk": positive_id(payload.get("advance_empl_id_fk"), "advance_empl_id_fk"),
    }
    value = payload.get("advance_date")
    if not value:
        raise AdvanceBusinessValidationError("advance_date is required.")
    try:
        if isinstance(value, datetime):
            raise ValueError
        fields["advance_date"] = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise AdvanceBusinessValidationError("Invalid advance_date; use YYYY-MM-DD.") from None
    reason = payload.get("advance_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise AdvanceBusinessValidationError("advance_reason is required and must be non-blank text.")
    fields["advance_reason"] = reason
    for field in ("advance_amount", "recovery_split_percentage"):
        fields[field] = advance_decimal(payload.get(field), field)
    return fields


def validate_advance_employee(conn, employee_id, org_id):
    employee = conn.execute(text("""
        select empl_id_pk from employee_master
        where empl_id_pk = :employee_id and empl_org_id_fk = :org_id
        for share
    """), {"employee_id": employee_id, "org_id": org_id}).first()
    if employee is None:
        raise AdvanceBusinessValidationError("Employee not found in authenticated organization.")


def create_advance_master(payload: dict):
    engine = None
    try:
        params = advance_identity(payload, require_advance=False, require_actor=True)
        fields = get_advance_params(payload)
        engine = db_engine()
        with engine.begin() as conn:
            validate_advance_employee(conn, fields["advance_empl_id_fk"], params["authenticated_org_id"])
            advance_id = conn.execute(text("""
                insert into advance_master (
                    advance_empl_id_fk, advance_date, advance_reason, advance_amount,
                    recovery_split_percentage, created_by, updated_by
                ) values (
                    :advance_empl_id_fk, :advance_date, :advance_reason, :advance_amount,
                    :recovery_split_percentage, :actor, :actor
                ) returning advance_id_pk
            """), {**params, **fields}).scalar_one()
        return {
            "message": f"Successfully created advance record: {advance_id}",
            "advance_id_pk": advance_id,
            "advance_empl_id_fk": fields["advance_empl_id_fk"],
        }
    except AdvanceBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to create advance record")
        return {"error": "Failed to create advance record."}
    finally:
        if engine is not None:
            engine.dispose()


def create_advance(payload: dict):
    return create_advance_master(payload)
