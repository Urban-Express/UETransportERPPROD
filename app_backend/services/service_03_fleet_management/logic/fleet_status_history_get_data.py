from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Get fleet status history data
def get_fleet_status_history(payload: dict | None = None):
    try:
        payload = payload or {}
        fleet_org_id_fk = payload.get("fleet_org_id_fk") or payload.get("authenticated_org_id")
        if not fleet_org_id_fk:
            return {"error": "fleet_org_id_fk is required."}

        # Running select query and populating data into dataframe
        status_history_select_query = text("""
            select *
            from fleet_status_history
            where fleet_org_id_fk = :fleet_org_id_fk
        """)
        fleet_engine = db_engine()
        with fleet_engine.connect() as conn:
            df_fleet_status_history = pd.read_sql(
                sql=status_history_select_query,
                con=conn,
                params={"fleet_org_id_fk": fleet_org_id_fk}
            )

        # Changing date/time columns for better rendering
        date_columns = [
            "status_effective_from",
            "status_effective_to",
            "changed_at",
            "created_at",
            "updated_at"
        ]
        for date_column in date_columns:
            if date_column in df_fleet_status_history.columns:
                df_fleet_status_history[date_column] = (
                    df_fleet_status_history[date_column].astype(str)
                )

        # Returning the data
        return (
            df_fleet_status_history,
            df_fleet_status_history.to_json(orient="records")
        )
    except Exception as e:
        df_fleet_status_history = pd.DataFrame()
        error_message = {
            "Failed to get fleet status history data. Error Message: ": {e}
        }
        return df_fleet_status_history, error_message


def get_status_history(payload: dict | None = None):
    return get_fleet_status_history(payload)
