import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.penalty_master_common import (
    PENALTY_BUSINESS_FIELDS,
    PenaltyBusinessValidationError,
    get_penalty_row,
    penalty_identity,
    validate_penalty_employee,
    validate_penalty_fields,
)
from app_backend.services.service_02_hr_payroll.logic.penalty_master_get_data import read_penalties


logger = logging.getLogger(__name__)


def update_penalty_master(payload):
    engine = None
    try:
        params = penalty_identity(payload, require_actor=True)
        changes = {key: payload[key] for key in PENALTY_BUSINESS_FIELDS if key in payload}
        if not changes:
            raise PenaltyBusinessValidationError("At least one editable Penalty field is required.")
        engine = db_engine()
        with engine.begin() as conn:
            penalty = get_penalty_row(conn, params, for_update=True)
            fields = validate_penalty_fields({**penalty, **changes})
            validate_penalty_employee(conn, fields["penalty_empl_id_fk"], params["authenticated_org_id"])
            # Preserve the uploaded pointer unless the warning letter is disabled.
            conn.execute(text("""
                update penalty_master set
                    penalty_empl_id_fk = :penalty_empl_id_fk,
                    penalty_date = :penalty_date, penalty_reason = :penalty_reason,
                    warning_letter_issued = :warning_letter_issued,
                    warning_letter_date = :warning_letter_date,
                    warning_letter_accepted = :warning_letter_accepted,
                    penalty_attachment_path = case when :warning_letter_issued
                        then penalty_attachment_path else null end,
                    financial_implication = :financial_implication,
                    penalty_amount = :penalty_amount,
                    recovery_split_percentage = :recovery_split_percentage,
                    updated_by = :actor, updated_at = current_timestamp
                where penalty_id_pk = :penalty_id and penalty_org_id_fk = :authenticated_org_id
            """), {**params, **fields})
            result = read_penalties(conn, params, detail=True)
        return {"message": "Successfully updated Penalty.", **result}
    except PenaltyBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to update Penalty")
        return {"error": "Failed to update Penalty."}
    finally:
        if engine is not None:
            engine.dispose()
