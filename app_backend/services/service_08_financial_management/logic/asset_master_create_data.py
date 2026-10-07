from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.asset_master_wf import (
    is_asset_master_workflow_approved,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from app_backend.services.service_08_financial_management.logic.asset_master_common import (
    get_asset_params,
    validate_asset_payload,
    verify_organization_exists,
)


def create_asset_master(payload: dict, conn=None):
    try:
        asset_org_id_fk = payload.get("asset_org_id_fk") or payload.get("authenticated_org_id")
        validation_error = validate_asset_payload(payload)
        if validation_error:
            return validation_error

        if not verify_organization_exists(asset_org_id_fk, conn=conn):
            return {"error": "asset_org_id_fk not found in organization_master."}

        workflow_approved, workflow_response = is_asset_master_workflow_approved(
            payload=payload,
            workflow_action="CREATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Asset creation submitted for approval.",
                )
            return {
                "error": "Asset creation blocked by workflow.",
                "workflow": workflow_response
            }

        insert_asset_master = text("""
            insert into asset_master (
                asset_org_id_fk,
                asset_location,
                asset_type,
                asset_name,
                asset_acquisition_date,
                depreciation_start_date,
                asset_acquisition_cost,
                asset_useful_life,
                asset_salvage_value,
                asset_nbv,
                created_by,
                updated_by
            ) values (
                :asset_org_id_fk,
                :asset_location,
                :asset_type,
                :asset_name,
                :asset_acquisition_date,
                :depreciation_start_date,
                :asset_acquisition_cost,
                :asset_useful_life,
                :asset_salvage_value,
                :asset_nbv,
                :created_by,
                :updated_by
            )
            returning asset_id_pk
        """)

        params_insert = {
            **get_asset_params(payload),
            "asset_org_id_fk": asset_org_id_fk
        }
        if conn is not None:
            asset_id = conn.execute(insert_asset_master, params_insert).scalar_one()
        else:
            asset_engine = db_engine()
            with asset_engine.begin() as asset_conn:
                asset_id = asset_conn.execute(insert_asset_master, params_insert).scalar_one()

        return {
            "message": f"Successfully created asset: {payload.get('asset_name')}",
            "asset_id_pk": asset_id,
            "asset_name": payload.get("asset_name"),
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        return {"error": f"Failed to create asset. Error Message: {str(e)}"}


def create_asset(payload: dict, conn=None):
    return create_asset_master(payload, conn=conn)
