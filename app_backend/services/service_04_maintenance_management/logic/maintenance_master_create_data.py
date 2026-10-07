from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def get_maintenance_params(payload: dict):
    return {
        "maint_fleet_vehicle_id_fk": payload.get("maint_fleet_vehicle_id_fk"),
        "maint_image_path": payload.get("maint_image_path"),
        "preventive_maintenance_date": payload.get("preventive_maintenance_date"),
        "preventive_maintenance_job_work": payload.get("preventive_maintenance_job_work"),
        "preventive_maintenance_workshop": payload.get("preventive_maintenance_workshop"),
        "preventive_maintenance_amount": payload.get("preventive_maintenance_amount"),
        "breakdown_date": payload.get("breakdown_date"),
        "breakdown_job_work": payload.get("breakdown_job_work"),
        "breakdown_workshop": payload.get("breakdown_workshop"),
        "breakdown_amount": payload.get("breakdown_amount"),
        "accident_date": payload.get("accident_date"),
        "accident_job_work": payload.get("accident_job_work"),
        "accident_workshop": payload.get("accident_workshop"),
        "accident_amount": payload.get("accident_amount"),
        "deployment_type": payload.get("deployment_type"),
        "deployment_client_name": payload.get("deployment_client_name"),
        "deployment_from_date": payload.get("deployment_from_date"),
        "created_by": payload.get("created_by"),
        "updated_by": payload.get("updated_by")
    }


def create_maintenance_master(payload: dict):
    maintenance_engine = None
    try:
        maintenance_engine = db_engine()
        maint_fleet_vehicle_id_fk = payload.get("maint_fleet_vehicle_id_fk")
        authenticated_org_id = payload.get("authenticated_org_id")

        if not maint_fleet_vehicle_id_fk:
            return {"error": "maint_fleet_vehicle_id_fk is required."}
        if not authenticated_org_id:
            return {"error": "authenticated_org_id is required."}

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
                    "maint_fleet_vehicle_id_fk": maint_fleet_vehicle_id_fk,
                    "authenticated_org_id": authenticated_org_id
                }
            )

        if df_fleet_vehicle.empty:
            return {"error": "Fleet vehicle ID not found."}

        payload = {
            **payload,
            "maint_image_path": None,
        }

        insert_maintenance_master = text("""
            insert into maintenance_master(
                maint_fleet_vehicle_id_fk,
                maint_image_path,
                preventive_maintenance_date,
                preventive_maintenance_job_work,
                preventive_maintenance_workshop,
                preventive_maintenance_amount,
                breakdown_date,
                breakdown_job_work,
                breakdown_workshop,
                breakdown_amount,
                accident_date,
                accident_job_work,
                accident_workshop,
                accident_amount,
                deployment_type,
                deployment_client_name,
                deployment_from_date,
                created_by,
                updated_by
            ) values (
                :maint_fleet_vehicle_id_fk,
                :maint_image_path,
                :preventive_maintenance_date,
                :preventive_maintenance_job_work,
                :preventive_maintenance_workshop,
                :preventive_maintenance_amount,
                :breakdown_date,
                :breakdown_job_work,
                :breakdown_workshop,
                :breakdown_amount,
                :accident_date,
                :accident_job_work,
                :accident_workshop,
                :accident_amount,
                :deployment_type,
                :deployment_client_name,
                :deployment_from_date,
                :created_by,
                :updated_by
            )
            returning maint_id_pk
        """)

        params_insert = get_maintenance_params(payload)

        with maintenance_engine.begin() as conn:
            maint_id = conn.execute(
                insert_maintenance_master,
                params_insert
            ).scalar_one()

        return {
            "message": f"Successfully created maintenance record: {maint_id}",
            "maint_id_pk": maint_id,
            "maint_fleet_vehicle_id_fk": maint_fleet_vehicle_id_fk,
            "maint_image_path": params_insert.get("maint_image_path")
        }

    except Exception as e:
        return {"error": f"Failed to create maintenance record. Error Message: {str(e)}"}
    finally:
        if maintenance_engine is not None:
            maintenance_engine.dispose()


def create_maintenance(payload: dict):
    return create_maintenance_master(payload)
