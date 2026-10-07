import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.advance_master_create_data import (
    AdvanceBusinessValidationError,
    advance_identity,
)


logger = logging.getLogger(__name__)


def delete_advance_master(payload: dict):
    engine = None
    try:
        params = advance_identity(payload)
        engine = db_engine()
        with engine.begin() as conn:
            advance = conn.execute(text("""
                select a.advance_id_pk
                from advance_master a
                join employee_master e on e.empl_id_pk = a.advance_empl_id_fk
                where a.advance_id_pk = :advance_id
                    and e.empl_org_id_fk = :authenticated_org_id
                for update of a for share of e
            """), params).first()
            if advance is None:
                raise AdvanceBusinessValidationError("Advance not found in authenticated organization.")
            conn.execute(text("""
                delete from advance_master
                where advance_id_pk = :advance_id
                    and exists (
                        select 1 from employee_master e
                        where e.empl_id_pk = advance_master.advance_empl_id_fk
                            and e.empl_org_id_fk = :authenticated_org_id
                    )
            """), params)
        return {
            "message": f"Successfully deleted advance record: {params['advance_id']}",
            "advance_id_pk": params["advance_id"],
        }
    except AdvanceBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to delete advance record")
        return {"error": "Failed to delete advance record."}
    finally:
        if engine is not None:
            engine.dispose()


def delete_advance(payload: dict):
    return delete_advance_master(payload)
