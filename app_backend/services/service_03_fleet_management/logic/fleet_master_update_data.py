from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.fleet_management_wf import is_fleet_management_workflow_approved
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from app_backend.services.service_03_fleet_management.logic.fleet_master_create_data import (
    fleet_vehicle_code_exists,
)
from sqlalchemy import text
import pandas as pd


# Update existing fleet vehicle
def update_fleet_vehicle(payload: dict, conn=None):
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
        if fleet_vehicle_code_exists(
            payload.get("vehicle_code"),
            fleet_org_id_fk,
            exclude_fleet_vehicle_id=fleet_vehicle_id,
            conn=conn,
        ):
            return {"error": f"Fleet vehicle already exists: {payload.get('vehicle_code')}"}

        workflow_payload = {
            **payload,
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_vehicle_id_pk": fleet_vehicle_id,
            "fleet_org_id_fk": existing_vehicle.get("fleet_org_id_fk"),
        }
        workflow_approved, workflow_response = is_fleet_management_workflow_approved(
            payload=workflow_payload,
            workflow_action="UPDATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Fleet vehicle update submitted for approval.",
                    domain_reference_id=fleet_vehicle_id,
                )
            return {"error": "Fleet vehicle update blocked by workflow.", "workflow": workflow_response}

        update_fleet_master = text("""
            update fleet_master
            set
                fleet_org_id_fk = :fleet_org_id_fk,
                vehicle_code = :vehicle_code,
                fleet_type = :fleet_type,
                fleet_category = :fleet_category,
                vehicle_brand_name = :vehicle_brand_name,
                vehicle_model_name = :vehicle_model_name,
                vehicle_model_year = :vehicle_model_year,
                vehicle_color = :vehicle_color,
                vehicle_total_seats_including_driver = :vehicle_total_seats_including_driver,
                vehicle_plate_number = :vehicle_plate_number,
                vehicle_plate_emirate = :vehicle_plate_emirate,
                vehicle_chassis_number = :vehicle_chassis_number,
                vehicle_engine_number = :vehicle_engine_number,
                mulkiya_number = :mulkiya_number,
                mulkiya_expiry_date = :mulkiya_expiry_date,
                salik_tag_number = :salik_tag_number,
                vehicle_insurance_provider = :vehicle_insurance_provider,
                vehicle_insurance_number = :vehicle_insurance_number,
                vehicle_insurance_type = :vehicle_insurance_type,
                vehicle_insurance_start_date = :vehicle_insurance_start_date,
                vehicle_insurance_expiry_date = :vehicle_insurance_expiry_date,
                current_odometer_km = :current_odometer_km,
                odometer_last_updated_at = :odometer_last_updated_at,
                vehicle_operational_status = :vehicle_operational_status,
                vehicle_deployment_status = :vehicle_deployment_status,
                vehicle_ownership_type = :vehicle_ownership_type,
                vehicle_owner_legal_entity = :vehicle_owner_legal_entity,
                vehicle_acquisition_date = :vehicle_acquisition_date,
                vehicle_acquisition_cost_aed = :vehicle_acquisition_cost_aed,
                depreciation_start_date = :depreciation_start_date,
                depreciation_method = :depreciation_method,
                useful_life_months = :useful_life_months,
                residual_value_aed = :residual_value_aed,
                field_flex_field_1 = :field_flex_field_1,
                field_flex_field_2 = :field_flex_field_2,
                field_flex_field_3 = :field_flex_field_3,
                field_flex_field_4 = :field_flex_field_4,
                updated_by = :updated_by
            where fleet_vehicle_id_pk = :fleet_vehicle_id
            and fleet_org_id_fk = :fleet_org_id_fk
        """)

        params_update = {
            "fleet_vehicle_id": fleet_vehicle_id,
            "fleet_org_id_fk": fleet_org_id_fk,
            "vehicle_code": payload.get("vehicle_code"),
            "fleet_type": payload.get("fleet_type"),
            "fleet_category": payload.get("fleet_category"),
            "vehicle_brand_name": payload.get("vehicle_brand_name"),
            "vehicle_model_name": payload.get("vehicle_model_name"),
            "vehicle_model_year": payload.get("vehicle_model_year"),
            "vehicle_color": payload.get("vehicle_color"),
            "vehicle_total_seats_including_driver": payload.get(
                "vehicle_total_seats_including_driver"
            ),
            "vehicle_plate_number": payload.get("vehicle_plate_number"),
            "vehicle_plate_emirate": payload.get("vehicle_plate_emirate"),
            "vehicle_chassis_number": payload.get("vehicle_chassis_number"),
            "vehicle_engine_number": payload.get("vehicle_engine_number"),
            "mulkiya_number": payload.get("mulkiya_number"),
            "mulkiya_expiry_date": payload.get("mulkiya_expiry_date"),
            "salik_tag_number": payload.get("salik_tag_number"),
            "vehicle_insurance_provider": payload.get("vehicle_insurance_provider"),
            "vehicle_insurance_number": payload.get("vehicle_insurance_number"),
            "vehicle_insurance_type": payload.get("vehicle_insurance_type"),
            "vehicle_insurance_start_date": payload.get("vehicle_insurance_start_date"),
            "vehicle_insurance_expiry_date": payload.get(
                "vehicle_insurance_expiry_date"
            ),
            "current_odometer_km": payload.get("current_odometer_km"),
            "odometer_last_updated_at": payload.get("odometer_last_updated_at"),
            "vehicle_operational_status": payload.get("vehicle_operational_status"),
            "vehicle_deployment_status": payload.get("vehicle_deployment_status"),
            "vehicle_ownership_type": payload.get("vehicle_ownership_type"),
            "vehicle_owner_legal_entity": payload.get("vehicle_owner_legal_entity"),
            "vehicle_acquisition_date": payload.get("vehicle_acquisition_date"),
            "vehicle_acquisition_cost_aed": payload.get(
                "vehicle_acquisition_cost_aed"
            ),
            "depreciation_start_date": payload.get("depreciation_start_date"),
            "depreciation_method": payload.get("depreciation_method"),
            "useful_life_months": payload.get("useful_life_months"),
            "residual_value_aed": payload.get("residual_value_aed"),
            "field_flex_field_1": payload.get("field_flex_field_1"),
            "field_flex_field_2": payload.get("field_flex_field_2"),
            "field_flex_field_3": payload.get("field_flex_field_3"),
            "field_flex_field_4": payload.get("field_flex_field_4"),
            "updated_by": payload.get("updated_by")
        }

        if conn is not None:
            result = conn.execute(update_fleet_master, params_update)
        else:
            fleet_engine = db_engine()
            with fleet_engine.begin() as fleet_conn:
                result = fleet_conn.execute(update_fleet_master, params_update)
        if result.rowcount == 0:
            return {"error": "Fleet vehicle ID not found at execution time."}

        return {
            "message": f"Successfully updated fleet vehicle: {payload.get('vehicle_code')}",
            "fleet_vehicle_id_pk": fleet_vehicle_id,
            "vehicle_code": payload.get("vehicle_code"),
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response,
        }

    except Exception as e:
        return {"error": f"Failed to update fleet vehicle. Error Message: {str(e)}"}


def update_fleet(payload: dict, conn=None):
    return update_fleet_vehicle(payload, conn=conn)
