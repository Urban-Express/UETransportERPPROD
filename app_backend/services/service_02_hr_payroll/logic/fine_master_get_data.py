import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.fine_master_common import (
    FineBusinessValidationError,
    fine_identity,
)


logger = logging.getLogger(__name__)


def read_fines(conn, params, *, detail=False):
    id_filter = "and f.fine_id_pk = :fine_id" if detail else ""
    rows = conn.execute(text(f"""
        select f.*, em.employee_id, em.employee_name
        from fine_master f
        left join employee_master em on em.empl_id_pk = f.fine_empl_id_fk
            and em.empl_org_id_fk = f.fine_org_id_fk
        where f.fine_org_id_fk = :authenticated_org_id {id_filter}
        order by f.fine_date desc, f.fine_id_pk desc
    """), params).mappings().all()
    if detail and not rows:
        raise FineBusinessValidationError("Fine not found.")
    result = [dict(row) for row in rows]
    for fine in result:
        # Keep NUMERIC values exact through JSON, including amounts above the
        # binary-float precision limit. Pydantic also accepts decimal strings.
        for field in ("amount_paid", "recovery_split"):
            if fine[field] is not None:
                fine[field] = str(fine[field])
    return result[0] if detail else result


def get_fine_master(payload):
    return _get_fines(payload, detail=True)


def list_fine_master(payload):
    return _get_fines(payload, detail=False)


def _get_fines(payload, *, detail):
    engine = None
    try:
        params = fine_identity(payload, require_fine=detail)
        engine = db_engine()
        with engine.begin() as conn:
            return read_fines(conn, params, detail=detail)
    except FineBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to retrieve fines")
        return {"error": "Failed to retrieve fines."}
    finally:
        if engine is not None:
            engine.dispose()
