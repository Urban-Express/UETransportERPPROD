import logging

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_common import (
    business_error,
    resolve_allocation_id,
    resolve_org_id,
)


logger = logging.getLogger(__name__)


def delete_fleet_driver_allocation(payload: dict):
    payload = dict(payload or {})
    fleet_org_id_fk, error = resolve_org_id(payload)
    if error:
        return error

    fleet_driver_allocation_id, error = resolve_allocation_id(payload)
    if error:
        return error

    fleet_engine = db_engine()
    try:
        delete_stmt = text("""
            delete from fleet_driver_allocation
            where fleet_driver_allocation_id_pk = :fleet_driver_allocation_id
              and fleet_org_id_fk = :fleet_org_id_fk
            returning fleet_driver_allocation_id_pk
        """)

        with fleet_engine.begin() as conn:
            deleted_row = conn.execute(
                delete_stmt,
                {
                    "fleet_driver_allocation_id": fleet_driver_allocation_id,
                    "fleet_org_id_fk": fleet_org_id_fk,
                },
            ).first()

        if not deleted_row:
            return business_error("Fleet driver allocation ID not found.")

        return {
            "message": (
                "Successfully deleted fleet driver allocation ID: "
                f"{fleet_driver_allocation_id}"
            ),
            "fleet_driver_allocation_id_pk": fleet_driver_allocation_id,
        }

    except Exception:
        logger.exception(
            "Failed to delete fleet driver allocation",
            extra={
                "fleet_org_id_fk": fleet_org_id_fk,
                "fleet_driver_allocation_id_pk": fleet_driver_allocation_id,
            },
        )
        return {"error": "Failed to delete fleet driver allocation."}
    finally:
        fleet_engine.dispose()


def delete_driver_allocation(payload: dict):
    return delete_fleet_driver_allocation(payload)
