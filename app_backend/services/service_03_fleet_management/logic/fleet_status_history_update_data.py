from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd

from app_backend.services.service_03_fleet_management.logic.fleet_status_history_create_data import (
    validate_status_history_references_for_organization,
)


# Update existing fleet status history
def update_fleet_status_history(payload: dict):
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

        if (
            payload.get("fleet_vehicle_id_fk") is not None
            or payload.get("related_maintenance_order_id_fk") is not None
        ):
            reference_error = validate_status_history_references_for_organization(
                payload,
                fleet_org_id_fk,
                fleet_engine,
                require_vehicle=False,
            )
            if reference_error:
                return reference_error

        update_fleet_status_history_master = text("""
            update fleet_status_history
            set
                fleet_org_id_fk = :fleet_org_id_fk,
                fleet_vehicle_id_fk = :fleet_vehicle_id_fk,
                previous_vehicle_status = :previous_vehicle_status,
                new_vehicle_status = :new_vehicle_status,
                status_effective_from = coalesce(
                    :status_effective_from,
                    status_effective_from
                ),
                status_effective_to = :status_effective_to,
                status_change_source = :status_change_source,
                status_change_reason = :status_change_reason,
                status_change_remarks = :status_change_remarks,
                related_maintenance_order_id_fk = :related_maintenance_order_id_fk,
                changed_by = :changed_by,
                changed_at = coalesce(:changed_at, changed_at),
                field_flex_field_1 = :field_flex_field_1,
                field_flex_field_2 = :field_flex_field_2,
                field_flex_field_3 = :field_flex_field_3,
                field_flex_field_4 = :field_flex_field_4,
                updated_by = :updated_by,
                updated_at = current_timestamp
            where (
                fleet_status_history_id_pk = :fleet_status_history_id
                or (
                    :fleet_status_history_id is null
                    and fleet_vehicle_id_fk = :fleet_vehicle_id
                )
            )
            and fleet_org_id_fk = :fleet_org_id_fk
        """)

        params_update = {
            "fleet_status_history_id": fleet_status_history_id,
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_org_id_fk": fleet_org_id_fk,
            "fleet_vehicle_id_fk": payload.get("fleet_vehicle_id_fk"),
            "previous_vehicle_status": payload.get("previous_vehicle_status"),
            "new_vehicle_status": payload.get("new_vehicle_status"),
            "status_effective_from": payload.get("status_effective_from"),
            "status_effective_to": payload.get("status_effective_to"),
            "status_change_source": payload.get("status_change_source", "manual"),
            "status_change_reason": payload.get("status_change_reason"),
            "status_change_remarks": payload.get("status_change_remarks"),
            "related_maintenance_order_id_fk": payload.get(
                "related_maintenance_order_id_fk"
            ),
            "changed_by": payload.get("changed_by"),
            "changed_at": payload.get("changed_at"),
            "field_flex_field_1": payload.get("field_flex_field_1"),
            "field_flex_field_2": payload.get("field_flex_field_2"),
            "field_flex_field_3": payload.get("field_flex_field_3"),
            "field_flex_field_4": payload.get("field_flex_field_4"),
            "updated_by": payload.get("updated_by")
        }

        with fleet_engine.begin() as conn:
            conn.execute(update_fleet_status_history_master, params_update)

        return {
            "message": (
                "Successfully updated fleet status history for vehicle ID: "
                f"{fleet_vehicle_id or payload.get('fleet_vehicle_id_fk')}"
            )
        }

    except Exception as e:
        return {
            "error": (
                "Failed to update fleet status history. "
                f"Error Message: {str(e)}"
            )
        }


def update_status_history(payload: dict):
    return update_fleet_status_history(payload)
