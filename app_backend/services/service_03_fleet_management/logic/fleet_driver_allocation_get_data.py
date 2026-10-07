import logging

import pandas as pd
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_common import (
    DRIVER_ALLOCATION_COLUMNS,
    resolve_org_id,
)


logger = logging.getLogger(__name__)


def _empty_driver_allocation_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(DRIVER_ALLOCATION_COLUMNS))


def get_fleet_driver_allocation(payload: dict | None = None):
    payload = dict(payload or {})
    fleet_org_id_fk, error = resolve_org_id(payload)
    if error:
        return error

    select_columns = ", ".join(DRIVER_ALLOCATION_COLUMNS)
    allocation_select_query = text(f"""
        select {select_columns}
        from fleet_driver_allocation
        where fleet_org_id_fk = :fleet_org_id_fk
        order by fleet_driver_allocation_id_pk
    """)

    fleet_engine = db_engine()
    try:
        with fleet_engine.connect() as conn:
            df_fleet_driver_allocation = pd.read_sql(
                sql=allocation_select_query,
                con=conn,
                params={"fleet_org_id_fk": fleet_org_id_fk},
            )

        date_columns = [
            "allocation_start_datetime",
            "allocation_end_datetime",
            "allocated_at",
            "deallocated_at",
            "created_at",
            "updated_at",
        ]
        for date_column in date_columns:
            if date_column in df_fleet_driver_allocation.columns:
                df_fleet_driver_allocation[date_column] = (
                    df_fleet_driver_allocation[date_column].astype(str)
                )

        return (
            df_fleet_driver_allocation,
            df_fleet_driver_allocation.to_json(orient="records"),
        )
    except Exception:
        logger.exception(
            "Failed to retrieve fleet driver allocations",
            extra={"fleet_org_id_fk": fleet_org_id_fk},
        )
        return (
            _empty_driver_allocation_frame(),
            {"error": "Failed to retrieve fleet driver allocations."},
        )
    finally:
        fleet_engine.dispose()


def get_driver_allocation(payload: dict | None = None):
    return get_fleet_driver_allocation(payload)
