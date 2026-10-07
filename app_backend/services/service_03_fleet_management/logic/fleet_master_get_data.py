from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
from sqlalchemy.exc import MultipleResultsFound
import pandas as pd


# Get fleet vehicle data
def get_fleet_vehicle(payload: dict | None = None):
    try:
        payload = payload or {}
        fleet_org_id_fk = payload.get("fleet_org_id_fk") or payload.get("authenticated_org_id")
        if not fleet_org_id_fk:
            return {"error": "fleet_org_id_fk is required."}

        # Running select query and populating data into dataframe
        fleet_select_query = text("""
            select *
            from fleet_master
            where fleet_org_id_fk = :fleet_org_id_fk
        """)
        fleet_engine = db_engine()
        with fleet_engine.connect() as conn:
            df_fleet_master = pd.read_sql(
                sql=fleet_select_query,
                con=conn,
                params={"fleet_org_id_fk": fleet_org_id_fk}
            )

        # Changing date/time columns for better rendering
        date_columns = [
            "mulkiya_expiry_date",
            "vehicle_insurance_start_date",
            "vehicle_insurance_expiry_date",
            "odometer_last_updated_at",
            "vehicle_acquisition_date",
            "depreciation_start_date",
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_fleet_master.columns:
                df_fleet_master[date_column] = df_fleet_master[date_column].astype(str)

        # Returning the data
        return df_fleet_master, df_fleet_master.to_json(orient="records")
    except Exception as e:
        df_fleet_master = pd.DataFrame()
        error_message = {"Failed to get fleet vehicle data. Error Message: ": {e}}
        return df_fleet_master, error_message


def get_fleet(payload: dict | None = None):
    return get_fleet_vehicle(payload)


def get_fleet_vehicle_by_id(payload: dict):
    """Exact business DTO; organization is bound by the authenticated API."""
    record_id = payload.get("fleet_vehicle_id_pk")
    organization_id = payload.get("authenticated_org_id")
    if not record_id or int(record_id) <= 0 or not organization_id:
        return {"error": "A positive fleet_vehicle_id_pk and authenticated organization are required."}
    engine = db_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                select * from fleet_master
                where fleet_vehicle_id_pk = :record_id and fleet_org_id_fk = :organization_id
            """), {"record_id": record_id, "organization_id": organization_id}).mappings().one_or_none()
        if row is None:
            return {"error": "Fleet vehicle not found."}
        return dict(row)
    except MultipleResultsFound:
        return {"error": "EXACT_RECORD_INTEGRITY_ERROR"}
    except Exception:
        return {"error": "EXACT_RECORD_QUERY_FAILED"}
    finally:
        engine.dispose()
