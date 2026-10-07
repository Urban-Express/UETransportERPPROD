from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text


def validate_status_history_references_for_organization(
    payload: dict,
    fleet_org_id_fk,
    fleet_engine=None,
    require_vehicle: bool = True,
):
    if not fleet_org_id_fk:
        return {"error": "fleet_org_id_fk is required."}

    fleet_vehicle_id_fk = payload.get("fleet_vehicle_id_fk")
    if require_vehicle and not fleet_vehicle_id_fk:
        return {"error": "fleet_vehicle_id_fk is required."}

    owns_engine = fleet_engine is None
    fleet_engine = fleet_engine or db_engine()
    try:
        with fleet_engine.begin() as conn:
            if fleet_vehicle_id_fk:
                fleet_row = conn.execute(
                    text("""
                        select fleet_vehicle_id_pk
                        from fleet_master
                        where fleet_vehicle_id_pk = :fleet_vehicle_id_fk
                        and fleet_org_id_fk = :fleet_org_id_fk
                        limit 1
                    """),
                    {
                        "fleet_vehicle_id_fk": fleet_vehicle_id_fk,
                        "fleet_org_id_fk": fleet_org_id_fk,
                    },
                ).first()
                if not fleet_row:
                    return {"error": "Fleet vehicle ID not found."}

            related_maintenance_order_id_fk = payload.get("related_maintenance_order_id_fk")
            if related_maintenance_order_id_fk:
                maintenance_row = conn.execute(
                    text("""
                        select m.maint_id_pk
                        from maintenance_master m
                        join fleet_master f
                            on f.fleet_vehicle_id_pk = m.maint_fleet_vehicle_id_fk
                        where m.maint_id_pk = :related_maintenance_order_id_fk
                        and f.fleet_org_id_fk = :fleet_org_id_fk
                        limit 1
                    """),
                    {
                        "related_maintenance_order_id_fk": related_maintenance_order_id_fk,
                        "fleet_org_id_fk": fleet_org_id_fk,
                    },
                ).first()
                if not maintenance_row:
                    return {"error": "Related maintenance order ID not found."}
    finally:
        if owns_engine:
            fleet_engine.dispose()

    return None


# Create new fleet status history
def create_fleet_status_history(payload: dict):
    try:
        fleet_engine = db_engine()
        fleet_org_id_fk = payload.get("fleet_org_id_fk") or payload.get("authenticated_org_id")

        reference_error = validate_status_history_references_for_organization(
            payload,
            fleet_org_id_fk,
            fleet_engine,
        )
        if reference_error:
            return reference_error

        insert_into_fleet_status_history = text("""
            insert into fleet_status_history(
                fleet_org_id_fk,
                fleet_vehicle_id_fk,
                previous_vehicle_status,
                new_vehicle_status,
                status_effective_from,
                status_effective_to,
                status_change_source,
                status_change_reason,
                status_change_remarks,
                related_maintenance_order_id_fk,
                changed_by,
                changed_at,
                field_flex_field_1,
                field_flex_field_2,
                field_flex_field_3,
                field_flex_field_4,
                created_by,
                updated_by
            ) values (
                :fleet_org_id_fk,
                :fleet_vehicle_id_fk,
                :previous_vehicle_status,
                :new_vehicle_status,
                coalesce(:status_effective_from, current_timestamp),
                :status_effective_to,
                :status_change_source,
                :status_change_reason,
                :status_change_remarks,
                :related_maintenance_order_id_fk,
                :changed_by,
                coalesce(:changed_at, current_timestamp),
                :field_flex_field_1,
                :field_flex_field_2,
                :field_flex_field_3,
                :field_flex_field_4,
                :created_by,
                :updated_by
            )
        """)

        params_insert = {
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
            "created_by": payload.get("created_by"),
            "updated_by": payload.get("updated_by")
        }

        with fleet_engine.begin() as conn:
            conn.execute(insert_into_fleet_status_history, params_insert)

        return {
            "message": (
                "Successfully created fleet status history for vehicle ID: "
                f"{payload.get('fleet_vehicle_id_fk')}"
            )
        }

    except Exception as e:
        return {
            "error": (
                "Failed to create fleet status history. "
                f"Error Message: {str(e)}"
            )
        }


def create_status_history(payload: dict):
    return create_fleet_status_history(payload)
