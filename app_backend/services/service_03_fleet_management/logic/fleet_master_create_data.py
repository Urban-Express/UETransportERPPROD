from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.fleet_management_wf import is_fleet_management_workflow_approved
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from sqlalchemy import text
import pandas as pd


# Create new fleet vehicle
def fleet_vehicle_code_exists(vehicle_code, fleet_org_id_fk, exclude_fleet_vehicle_id=None, conn=None):
    if not vehicle_code or not fleet_org_id_fk:
        return False
    get_vehicle_code = text("""
        select fleet_vehicle_id_pk
        from fleet_master
        where lower(vehicle_code) = lower(:vehicle_code)
        and fleet_org_id_fk = :fleet_org_id_fk
        and (:exclude_fleet_vehicle_id is null or fleet_vehicle_id_pk <> :exclude_fleet_vehicle_id)
    """)
    params = {
        "vehicle_code": vehicle_code,
        "fleet_org_id_fk": fleet_org_id_fk,
        "exclude_fleet_vehicle_id": exclude_fleet_vehicle_id,
    }
    if conn is not None:
        df_vehicle_code = pd.read_sql(sql=get_vehicle_code, con=conn, params=params)
    else:
        fleet_engine = db_engine()
        with fleet_engine.begin() as fleet_conn:
            df_vehicle_code = pd.read_sql(sql=get_vehicle_code, con=fleet_conn, params=params)
    return not df_vehicle_code.empty


def create_fleet_vehicle(payload: dict, conn=None):
    try:
        vehicle_code = payload.get("vehicle_code")
        fleet_org_id_fk = payload.get("fleet_org_id_fk") or payload.get("authenticated_org_id")

        if fleet_vehicle_code_exists(vehicle_code, fleet_org_id_fk, conn=conn):
            return {"error": f"Fleet vehicle already exists: {vehicle_code}"}

        workflow_approved, workflow_response = is_fleet_management_workflow_approved(
            payload=payload,
            workflow_action="CREATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Fleet vehicle creation submitted for approval.",
            )
            return {"error": "Fleet vehicle creation blocked by workflow.", "workflow": workflow_response}

        insert_into_fleet_master = text("""
            insert into fleet_master(
                fleet_org_id_fk,
                vehicle_code,
                fleet_type,
                fleet_category,
                vehicle_brand_name,
                vehicle_model_name,
                vehicle_model_year,
                vehicle_color,
                vehicle_total_seats_including_driver,
                vehicle_plate_number,
                vehicle_plate_emirate,
                vehicle_chassis_number,
                vehicle_engine_number,
                mulkiya_number,
                mulkiya_expiry_date,
                salik_tag_number,
                vehicle_insurance_provider,
                vehicle_insurance_number,
                vehicle_insurance_type,
                vehicle_insurance_start_date,
                vehicle_insurance_expiry_date,
                current_odometer_km,
                odometer_last_updated_at,
                vehicle_operational_status,
                vehicle_deployment_status,
                vehicle_ownership_type,
                vehicle_owner_legal_entity,
                vehicle_acquisition_date,
                vehicle_acquisition_cost_aed,
                depreciation_start_date,
                depreciation_method,
                useful_life_months,
                residual_value_aed,
                field_flex_field_1,
                field_flex_field_2,
                field_flex_field_3,
                field_flex_field_4,
                created_by,
                updated_by
            ) values (
                :fleet_org_id_fk,
                :vehicle_code,
                :fleet_type,
                :fleet_category,
                :vehicle_brand_name,
                :vehicle_model_name,
                :vehicle_model_year,
                :vehicle_color,
                :vehicle_total_seats_including_driver,
                :vehicle_plate_number,
                :vehicle_plate_emirate,
                :vehicle_chassis_number,
                :vehicle_engine_number,
                :mulkiya_number,
                :mulkiya_expiry_date,
                :salik_tag_number,
                :vehicle_insurance_provider,
                :vehicle_insurance_number,
                :vehicle_insurance_type,
                :vehicle_insurance_start_date,
                :vehicle_insurance_expiry_date,
                :current_odometer_km,
                :odometer_last_updated_at,
                :vehicle_operational_status,
                :vehicle_deployment_status,
                :vehicle_ownership_type,
                :vehicle_owner_legal_entity,
                :vehicle_acquisition_date,
                :vehicle_acquisition_cost_aed,
                :depreciation_start_date,
                :depreciation_method,
                :useful_life_months,
                :residual_value_aed,
                :field_flex_field_1,
                :field_flex_field_2,
                :field_flex_field_3,
                :field_flex_field_4,
                :created_by,
                :updated_by
            )
            returning fleet_vehicle_id_pk
        """)

        params_insert = {
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
            "created_by": payload.get("created_by"),
            "updated_by": payload.get("updated_by")
        }

        if conn is not None:
            fleet_vehicle_id = conn.execute(insert_into_fleet_master, params_insert).scalar_one()
        else:
            fleet_engine = db_engine()
            with fleet_engine.begin() as fleet_conn:
                fleet_vehicle_id = fleet_conn.execute(insert_into_fleet_master, params_insert).scalar_one()

        return {
            "message": f"Successfully created fleet vehicle: {vehicle_code}",
            "fleet_vehicle_id_pk": fleet_vehicle_id,
            "vehicle_code": vehicle_code,
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response,
        }

    except Exception as e:
        return {"error": f"Failed to create fleet vehicle. Error Message: {str(e)}"}


def create_fleet(payload: dict, conn=None):
    return create_fleet_vehicle(payload, conn=conn)
