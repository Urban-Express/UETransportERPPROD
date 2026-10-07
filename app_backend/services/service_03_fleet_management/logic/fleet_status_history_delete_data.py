from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Delete fleet status history
def delete_fleet_status_history(payload: dict):
    try:
        fleet_engine = db_engine()
        fleet_status_history_id = (
            payload.get("fleet_status_history_id")
            or payload.get("fleet_status_history_id_pk")
        )
        fleet_vehicle_id = (
            payload.get("fleet_vehicle_id")
            or payload.get("fleet_vehicle_id_fk")
        )
        fleet_org_id_fk = payload.get("fleet_org_id_fk") or payload.get("authenticated_org_id")

        if not fleet_status_history_id and not fleet_vehicle_id:
            return {
                "error": "fleet_status_history_id or fleet_vehicle_id is required."
            }

        get_fleet_status_history_id = text("""
            select fleet_status_history_id_pk
            from fleet_status_history
            where (
                fleet_status_history_id_pk = :fleet_status_history_id
                or (
                    :fleet_status_history_id is null
                    and fleet_vehicle_id_fk = :fleet_vehicle_id
                )
            )
            and fleet_org_id_fk = :fleet_org_id_fk
        """)

        with fleet_engine.begin() as conn:
            df_fleet_status_history_id = pd.read_sql(
                sql=get_fleet_status_history_id,
                con=conn,
                params={
                    "fleet_status_history_id": fleet_status_history_id,
                    "fleet_vehicle_id": fleet_vehicle_id,
                    "fleet_org_id_fk": fleet_org_id_fk
                }
            )

        if df_fleet_status_history_id.empty:
            return {"error": "Fleet status history ID not found."}

        delete_fleet_status_history_master = text("""
            delete from fleet_status_history
            where (
                fleet_status_history_id_pk = :fleet_status_history_id
                or (
                    :fleet_status_history_id is null
                    and fleet_vehicle_id_fk = :fleet_vehicle_id
                )
            )
            and fleet_org_id_fk = :fleet_org_id_fk
        """)

        params_delete = {
            "fleet_status_history_id": fleet_status_history_id,
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_org_id_fk": fleet_org_id_fk
        }

        with fleet_engine.begin() as conn:
            conn.execute(delete_fleet_status_history_master, params_delete)

        return {
            "message": (
                "Successfully deleted fleet status history ID: "
                f"{fleet_status_history_id or fleet_vehicle_id}"
            )
        }

    except Exception as e:
        return {
            "error": (
                "Failed to delete fleet status history. "
                f"Error Message: {str(e)}"
            )
        }


def delete_status_history(payload: dict):
    return delete_fleet_status_history(payload)
