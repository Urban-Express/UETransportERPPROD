from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def get_maintenance_master(payload: dict | None = None):
    try:
        payload = payload or {}
        authenticated_org_id = payload.get("authenticated_org_id")
        if not authenticated_org_id:
            return {"error": "authenticated_org_id is required."}

        maintenance_select_query = text("""
            select m.*
            from maintenance_master m
            join fleet_master f
                on f.fleet_vehicle_id_pk = m.maint_fleet_vehicle_id_fk
            where f.fleet_org_id_fk = :authenticated_org_id
        """)
        maintenance_engine = db_engine()
        with maintenance_engine.connect() as conn:
            df_maintenance_master = pd.read_sql(
                sql=maintenance_select_query,
                con=conn,
                params={"authenticated_org_id": authenticated_org_id}
            )

        date_columns = [
            "preventive_maintenance_date",
            "breakdown_date",
            "accident_date",
            "deployment_from_date",
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_maintenance_master.columns:
                df_maintenance_master[date_column] = (
                    df_maintenance_master[date_column].astype(str)
                )

        return df_maintenance_master, df_maintenance_master.to_json(orient="records")
    except Exception as e:
        df_maintenance_master = pd.DataFrame()
        error_message = {"Failed to get maintenance data. Error Message: ": {e}}
        return df_maintenance_master, error_message


def get_maintenance(payload: dict | None = None):
    return get_maintenance_master(payload)
