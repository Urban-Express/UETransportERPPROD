import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.logic.fine_master_common import (
    FineBusinessValidationError,
    fine_identity,
)


logger = logging.getLogger(__name__)


def delete_fine_master(payload):
    engine = None
    try:
        params = fine_identity(payload)
        engine = db_engine()
        with engine.begin() as conn:
            fine_id = conn.execute(text("""
                delete from fine_master
                where fine_id_pk = :fine_id and fine_org_id_fk = :authenticated_org_id
                returning fine_id_pk
            """), params).scalar_one_or_none()
            if fine_id is None:
                raise FineBusinessValidationError("Fine not found.")
        return {"message": "Successfully deleted Fine.", "fine_id_pk": fine_id}
    except FineBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to delete Fine")
        return {"error": "Failed to delete Fine."}
    finally:
        if engine is not None:
            engine.dispose()
