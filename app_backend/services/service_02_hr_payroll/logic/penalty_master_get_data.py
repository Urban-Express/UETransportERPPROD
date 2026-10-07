import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.penalty_master_common import (
    PenaltyBusinessValidationError,
    penalty_identity,
)


logger = logging.getLogger(__name__)


def read_penalties(conn, params, *, detail=False):
    id_filter = "and p.penalty_id_pk = :penalty_id" if detail else ""
    rows = conn.execute(text(f"""
        select p.*, em.employee_id, em.employee_name
        from penalty_master p
        left join employee_master em on em.empl_id_pk = p.penalty_empl_id_fk
            and em.empl_org_id_fk = p.penalty_org_id_fk
        where p.penalty_org_id_fk = :authenticated_org_id {id_filter}
        order by p.penalty_date desc, p.penalty_id_pk desc
    """), params).mappings().all()
    if detail and not rows:
        raise PenaltyBusinessValidationError("Penalty not found.")
    result = [dict(row) for row in rows]
    for penalty in result:
        # Preserve NUMERIC precision through the shared API JSON encoder.
        for field in ("penalty_amount", "recovery_split_percentage"):
            if penalty[field] is not None:
                penalty[field] = str(penalty[field])
    return result[0] if detail else result


def get_penalty_master(payload):
    return _get_penalties(payload, detail=True)


def list_penalty_master(payload):
    return _get_penalties(payload, detail=False)


def _get_penalties(payload, *, detail):
    engine = None
    try:
        params = penalty_identity(payload, require_penalty=detail)
        engine = db_engine()
        with engine.begin() as conn:
            return read_penalties(conn, params, detail=detail)
    except PenaltyBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to retrieve penalties")
        return {"error": "Failed to retrieve penalties."}
    finally:
        if engine is not None:
            engine.dispose()
