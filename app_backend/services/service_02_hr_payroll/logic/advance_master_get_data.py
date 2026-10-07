import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.advance_master_create_data import (
    AdvanceBusinessValidationError,
    advance_identity,
)


logger = logging.getLogger(__name__)


def get_advance_master(payload: dict | None = None):
    engine = None
    try:
        params = advance_identity(payload or {}, require_advance=False)
        engine = db_engine()
        with engine.begin() as conn:
            rows = conn.execute(text("""
                select a.*, e.empl_id_pk, e.employee_id, e.employee_name
                from advance_master a
                join employee_master e on e.empl_id_pk = a.advance_empl_id_fk
                where e.empl_org_id_fk = :authenticated_org_id
                order by a.advance_date desc, a.advance_id_pk desc
            """), params).mappings().all()
        result = [dict(row) for row in rows]
        for advance in result:
            # Match Fine/Penalty JSON values without losing NUMERIC precision.
            for field in ("advance_amount", "recovery_split_percentage"):
                advance[field] = str(advance[field])
        return result
    except AdvanceBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to retrieve advances")
        return {"error": "Failed to retrieve advances."}
    finally:
        if engine is not None:
            engine.dispose()


def get_advances(payload: dict | None = None):
    return get_advance_master(payload)
