import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.fine_master_common import (
    FINE_BUSINESS_FIELDS,
    FineBusinessValidationError,
    fine_identity,
    get_fine_row,
    validate_fine_employee,
    validate_fine_fields,
)
from app_backend.services.service_02_hr_payroll.logic.fine_master_get_data import read_fines


logger = logging.getLogger(__name__)


def update_fine_master(payload):
    engine = None
    try:
        params = fine_identity(payload, require_actor=True)
        changes = {key: payload[key] for key in FINE_BUSINESS_FIELDS if key in payload}
        if not changes:
            return {"error": "At least one editable Fine field is required."}
        engine = db_engine()
        with engine.begin() as conn:
            fine = get_fine_row(conn, params, for_update=True)
            fields = validate_fine_fields({**fine, **changes})
            validate_fine_employee(conn, fields["fine_empl_id_fk"], params["authenticated_org_id"])
            # File pointers are updated exclusively by the Firebase integration.
            conn.execute(text("""
                update fine_master set
                    fine_empl_id_fk = :fine_empl_id_fk, fine_date = :fine_date,
                    fine_on = :fine_on, fine_accountability = :fine_accountability,
                    payment_authority = :payment_authority, amount_paid = :amount_paid,
                    recovery_split = :recovery_split,
                    updated_by = :actor, updated_at = current_timestamp
                where fine_id_pk = :fine_id and fine_org_id_fk = :authenticated_org_id
            """), {**params, **fields})
            result = read_fines(conn, params, detail=True)
        return {"message": "Successfully updated Fine.", **result}
    except FineBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to update Fine")
        return {"error": "Failed to update Fine."}
    finally:
        if engine is not None:
            engine.dispose()
