import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.penalty_master_common import (
    PenaltyBusinessValidationError,
    penalty_identity,
    validate_penalty_employee,
    validate_penalty_fields,
)
from app_backend.services.service_02_hr_payroll.logic.penalty_master_get_data import read_penalties


logger = logging.getLogger(__name__)


def create_penalty_master(payload):
    engine = None
    try:
        params = penalty_identity(payload, require_penalty=False, require_actor=True)
        fields = validate_penalty_fields(payload)
        engine = db_engine()
        with engine.begin() as conn:
            validate_penalty_employee(conn, fields["penalty_empl_id_fk"], params["authenticated_org_id"])
            penalty_id = conn.execute(text("""
                insert into penalty_master (
                    penalty_org_id_fk, penalty_empl_id_fk, penalty_date, penalty_reason,
                    warning_letter_issued, warning_letter_date, warning_letter_accepted,
                    penalty_attachment_path, financial_implication, penalty_amount,
                    recovery_split_percentage, created_by, updated_by
                ) values (
                    :authenticated_org_id, :penalty_empl_id_fk, :penalty_date, :penalty_reason,
                    :warning_letter_issued, :warning_letter_date, :warning_letter_accepted,
                    null, :financial_implication, :penalty_amount,
                    :recovery_split_percentage, :actor, :actor
                ) returning penalty_id_pk
            """), {**params, **fields}).scalar_one()
            result = read_penalties(conn, {**params, "penalty_id": penalty_id}, detail=True)
        return {"message": "Successfully created Penalty.", **result}
    except PenaltyBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to create Penalty")
        return {"error": "Failed to create Penalty."}
    finally:
        if engine is not None:
            engine.dispose()
