import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.advance_master_create_data import (
    AdvanceBusinessValidationError,
    advance_identity,
    get_advance_params,
    validate_advance_employee,
)


logger = logging.getLogger(__name__)


def update_advance_master(payload: dict):
    engine = None
    try:
        params = advance_identity(payload, require_actor=True)
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
            fields = get_advance_params(payload)
            validate_advance_employee(conn, fields["advance_empl_id_fk"], params["authenticated_org_id"])
            conn.execute(text("""
                update advance_master set
                    advance_empl_id_fk = :advance_empl_id_fk,
                    advance_date = :advance_date,
                    advance_reason = :advance_reason,
                    advance_amount = :advance_amount,
                    recovery_split_percentage = :recovery_split_percentage,
                    updated_by = :actor, updated_at = current_timestamp
                where advance_id_pk = :advance_id
                    and exists (
                        select 1 from employee_master e
                        where e.empl_id_pk = advance_master.advance_empl_id_fk
                            and e.empl_org_id_fk = :authenticated_org_id
                    )
            """), {**params, **fields})
        return {
            "message": f"Successfully updated advance record: {params['advance_id']}",
            "advance_id_pk": params["advance_id"],
            "advance_empl_id_fk": fields["advance_empl_id_fk"],
        }
    except AdvanceBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to update advance record")
        return {"error": "Failed to update advance record."}
    finally:
        if engine is not None:
            engine.dispose()


def update_advance(payload: dict):
    return update_advance_master(payload)
