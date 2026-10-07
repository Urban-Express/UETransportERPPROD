import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.fine_master_common import (
    FineBusinessValidationError,
    fine_identity,
    validate_fine_employee,
    validate_fine_fields,
)
from app_backend.services.service_02_hr_payroll.logic.fine_master_get_data import read_fines


logger = logging.getLogger(__name__)


def create_fine_master(payload):
    engine = None
    try:
        params = fine_identity(payload, require_fine=False, require_actor=True)
        fields = validate_fine_fields(payload)
        engine = db_engine()
        with engine.begin() as conn:
            validate_fine_employee(conn, fields["fine_empl_id_fk"], params["authenticated_org_id"])
            fine_id = conn.execute(text("""
                insert into fine_master (
                    fine_org_id_fk, fine_empl_id_fk, fine_date, fine_on,
                    fine_accountability, payment_authority, amount_paid,
                    recovery_split, fine_attachment_path, created_by, updated_by
                ) values (
                    :authenticated_org_id, :fine_empl_id_fk, :fine_date, :fine_on,
                    :fine_accountability, :payment_authority, :amount_paid,
                    :recovery_split, null, :actor, :actor
                ) returning fine_id_pk
            """), {**params, **fields}).scalar_one()
            result = read_fines(conn, {**params, "fine_id": fine_id}, detail=True)
        return {"message": "Successfully created Fine.", **result}
    except FineBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to create Fine")
        return {"error": "Failed to create Fine."}
    finally:
        if engine is not None:
            engine.dispose()
