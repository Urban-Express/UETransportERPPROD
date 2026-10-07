from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.fleet_management_wf import is_fleet_management_workflow_approved
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from sqlalchemy import text
import pandas as pd


# Delete fleet vehicle
def delete_fleet_vehicle(payload: dict, conn=None):
    try:
        fleet_vehicle_id = (
            payload.get("fleet_vehicle_id")
            or payload.get("fleet_vehicle_id_pk")
        )
        fleet_org_id_fk = payload.get("fleet_org_id_fk") or payload.get("authenticated_org_id")

        if not fleet_vehicle_id:
            return {"error": "fleet_vehicle_id is required."}

        get_fleet_vehicle_id = text("""
            select fleet_vehicle_id_pk, fleet_org_id_fk, vehicle_code
            from fleet_master
            where fleet_vehicle_id_pk = :fleet_vehicle_id
            and fleet_org_id_fk = :fleet_org_id_fk
        """)

        params_get = {
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_org_id_fk": fleet_org_id_fk
        }
        if conn is not None:
            df_fleet_vehicle_id = pd.read_sql(sql=get_fleet_vehicle_id, con=conn, params=params_get)
        else:
            fleet_engine = db_engine()
            with fleet_engine.begin() as fleet_conn:
                df_fleet_vehicle_id = pd.read_sql(sql=get_fleet_vehicle_id, con=fleet_conn, params=params_get)

        if df_fleet_vehicle_id.empty:
            return {"error": "Fleet vehicle ID not found."}

        existing_vehicle = df_fleet_vehicle_id.iloc[0].to_dict()
        workflow_payload = {
            **payload,
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_vehicle_id_pk": fleet_vehicle_id,
            "fleet_org_id_fk": existing_vehicle.get("fleet_org_id_fk"),
            "vehicle_code": existing_vehicle.get("vehicle_code"),
        }
        workflow_approved, workflow_response = is_fleet_management_workflow_approved(
            payload=workflow_payload,
            workflow_action="DELETE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Fleet vehicle deletion submitted for approval.",
                    domain_reference_id=fleet_vehicle_id,
                )
            return {"error": "Fleet vehicle deletion blocked by workflow.", "workflow": workflow_response}

        delete_fleet_master = text("""
            delete from fleet_master
            where fleet_vehicle_id_pk = :fleet_vehicle_id
            and fleet_org_id_fk = :fleet_org_id_fk
        """)

        params_delete = {
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_org_id_fk": fleet_org_id_fk
        }

        if conn is not None:
            result = conn.execute(delete_fleet_master, params_delete)
        else:
            fleet_engine = db_engine()
            with fleet_engine.begin() as fleet_conn:
                result = fleet_conn.execute(delete_fleet_master, params_delete)
        if result.rowcount == 0:
            return {"error": "Fleet vehicle ID not found at execution time."}

        return {
            "message": f"Successfully deleted fleet vehicle ID: {fleet_vehicle_id}",
            "fleet_vehicle_id_pk": fleet_vehicle_id,
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response,
        }

    except Exception as e:
        return {"error": f"Failed to delete fleet vehicle. Error Message: {str(e)}"}


def delete_fleet(payload: dict, conn=None):
    return delete_fleet_vehicle(payload, conn=conn)
