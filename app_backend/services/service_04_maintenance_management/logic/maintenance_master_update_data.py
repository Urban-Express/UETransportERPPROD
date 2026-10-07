from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_create_data import (
    get_maintenance_params,
)


def update_maintenance_master(payload: dict):
    maintenance_engine = None
    try:
        maintenance_engine = db_engine()
        maint_id = (
            payload.get("maint_id")
            or payload.get("maint_id_pk")
        )
        authenticated_org_id = payload.get("authenticated_org_id")

        if not maint_id:
            return {"error": "maint_id is required."}
        if not authenticated_org_id:
            return {"error": "authenticated_org_id is required."}

        get_maint_id = text("""
            select m.maint_id_pk, m.maint_image_path
            from maintenance_master m
            join fleet_master f
                on f.fleet_vehicle_id_pk = m.maint_fleet_vehicle_id_fk
            where m.maint_id_pk = :maint_id
            and f.fleet_org_id_fk = :authenticated_org_id
        """)

        with maintenance_engine.begin() as conn:
            df_maint_id = pd.read_sql(
                sql=get_maint_id,
                con=conn,
                params={
                    "maint_id": maint_id,
                    "authenticated_org_id": authenticated_org_id
                }
            )

        if df_maint_id.empty:
            return {"error": "Maintenance record ID not found."}

        get_fleet_vehicle = text("""
            select fleet_vehicle_id_pk
            from fleet_master
            where fleet_vehicle_id_pk = :maint_fleet_vehicle_id_fk
            and fleet_org_id_fk = :authenticated_org_id
        """)
        with maintenance_engine.begin() as conn:
            df_fleet_vehicle = pd.read_sql(
                sql=get_fleet_vehicle,
                con=conn,
                params={
                    "maint_fleet_vehicle_id_fk": payload.get("maint_fleet_vehicle_id_fk"),
                    "authenticated_org_id": authenticated_org_id
                }
            )
        if df_fleet_vehicle.empty:
            return {"error": "Fleet vehicle ID not found."}

        update_maintenance_master_query = text("""
            update maintenance_master
            set
                maint_fleet_vehicle_id_fk = :maint_fleet_vehicle_id_fk,
                preventive_maintenance_date = :preventive_maintenance_date,
                preventive_maintenance_job_work = :preventive_maintenance_job_work,
                preventive_maintenance_workshop = :preventive_maintenance_workshop,
                preventive_maintenance_amount = :preventive_maintenance_amount,
                breakdown_date = :breakdown_date,
                breakdown_job_work = :breakdown_job_work,
                breakdown_workshop = :breakdown_workshop,
                breakdown_amount = :breakdown_amount,
                accident_date = :accident_date,
                accident_job_work = :accident_job_work,
                accident_workshop = :accident_workshop,
                accident_amount = :accident_amount,
                deployment_type = :deployment_type,
                deployment_client_name = :deployment_client_name,
                deployment_from_date = :deployment_from_date,
                updated_by = :updated_by,
                updated_at = CURRENT_TIMESTAMP
            where maint_id_pk = :maint_id
            and exists (
                select 1
                from fleet_master f
                where f.fleet_vehicle_id_pk = maintenance_master.maint_fleet_vehicle_id_fk
                and f.fleet_org_id_fk = :authenticated_org_id
            )
            returning maint_id_pk, maint_image_path
        """)

        params_update = {
            **get_maintenance_params(payload),
            "maint_id": maint_id,
            "authenticated_org_id": authenticated_org_id
        }
        params_update.pop("maint_image_path", None)

        with maintenance_engine.begin() as conn:
            updated_maintenance = conn.execute(
                update_maintenance_master_query,
                params_update,
            ).mappings().one_or_none()

        if not updated_maintenance:
            return {"error": "Maintenance record ID not found at execution time."}

        return {
            "message": f"Successfully updated maintenance record: {maint_id}",
            "maint_id_pk": maint_id,
            "maint_image_path": updated_maintenance["maint_image_path"]
        }

    except Exception as e:
        return {"error": f"Failed to update maintenance record. Error Message: {str(e)}"}
    finally:
        if maintenance_engine is not None:
            maintenance_engine.dispose()


def update_maintenance(payload: dict):
    return update_maintenance_master(payload)
