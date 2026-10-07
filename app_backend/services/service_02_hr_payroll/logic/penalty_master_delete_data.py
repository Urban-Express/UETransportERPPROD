import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.penalty_master_common import (
    PenaltyBusinessValidationError,
    penalty_identity,
)


logger = logging.getLogger(__name__)


def delete_penalty_master(payload):
    engine = None
    try:
        params = penalty_identity(payload)
        engine = db_engine()
        with engine.begin() as conn:
            penalty_id = conn.execute(text("""
                delete from penalty_master
                where penalty_id_pk = :penalty_id and penalty_org_id_fk = :authenticated_org_id
                returning penalty_id_pk
            """), params).scalar_one_or_none()
            if penalty_id is None:
                raise PenaltyBusinessValidationError("Penalty not found.")
        return {"message": "Successfully deleted Penalty.", "penalty_id_pk": penalty_id}
    except PenaltyBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to delete Penalty")
        return {"error": "Failed to delete Penalty."}
    finally:
        if engine is not None:
            engine.dispose()
