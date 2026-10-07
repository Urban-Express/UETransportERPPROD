from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def delete_maintenance_master(payload: dict):
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
            select m.maint_id_pk
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

        delete_maintenance_master_query = text("""
            delete from maintenance_master
            where maint_id_pk = :maint_id
            and exists (
                select 1
                from fleet_master f
                where f.fleet_vehicle_id_pk = maintenance_master.maint_fleet_vehicle_id_fk
                and f.fleet_org_id_fk = :authenticated_org_id
            )
        """)

        with maintenance_engine.begin() as conn:
            conn.execute(
                delete_maintenance_master_query,
                {
                    "maint_id": maint_id,
                    "authenticated_org_id": authenticated_org_id
                }
            )

        return {"message": f"Successfully deleted maintenance record ID: {maint_id}"}

    except Exception as e:
        return {"error": f"Failed to delete maintenance record. Error Message: {str(e)}"}


def delete_maintenance(payload: dict):
    return delete_maintenance_master(payload)
