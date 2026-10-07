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
    get_asset_by_id,
    get_asset_id_from_payload,
    get_asset_params,
    validate_asset_payload,
    verify_organization_exists,
)


def update_asset_master(payload: dict, conn=None):
    try:
        asset_id = get_asset_id_from_payload(payload)
        asset_org_id_fk = payload.get("asset_org_id_fk") or payload.get("authenticated_org_id")
        validation_error = validate_asset_payload(payload, require_id=True)
        if validation_error:
            return validation_error

        existing_asset = get_asset_by_id(asset_id, asset_org_id_fk, conn=conn)
        if not existing_asset:
            return {"error": "Asset ID not found."}

        if not verify_organization_exists(asset_org_id_fk, conn=conn):
            return {"error": "asset_org_id_fk not found in organization_master."}

        workflow_payload = {
            **payload,
            "asset_id": asset_id,
            "asset_id_pk": asset_id,
            "asset_org_id_fk": existing_asset.get("asset_org_id_fk"),
        }
        workflow_approved, workflow_response = is_asset_master_workflow_approved(
            payload=workflow_payload,
            workflow_action="UPDATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Asset update submitted for approval.",
                    domain_reference_id=asset_id,
                )
            return {
                "error": "Asset update blocked by workflow.",
                "workflow": workflow_response
            }

        update_asset_master_query = text("""
            update asset_master
            set
                asset_org_id_fk = :asset_org_id_fk,
                asset_location = :asset_location,
                asset_type = :asset_type,
                asset_name = :asset_name,
                asset_acquisition_date = :asset_acquisition_date,
                depreciation_start_date = :depreciation_start_date,
                asset_acquisition_cost = :asset_acquisition_cost,
                asset_useful_life = :asset_useful_life,
                asset_salvage_value = :asset_salvage_value,
                asset_nbv = :asset_nbv,
                updated_by = :updated_by,
                updated_at = CURRENT_TIMESTAMP
            where asset_id_pk = :asset_id
            and asset_org_id_fk = :asset_org_id_fk
        """)

        params_update = {
            **get_asset_params(payload),
            "asset_id": asset_id,
            "asset_org_id_fk": asset_org_id_fk
        }

        if conn is not None:
            result = conn.execute(update_asset_master_query, params_update)
        else:
            asset_engine = db_engine()
            with asset_engine.begin() as asset_conn:
                result = asset_conn.execute(update_asset_master_query, params_update)
        if result.rowcount == 0:
            return {"error": "Asset ID not found at execution time."}

        return {
            "message": f"Successfully updated asset: {payload.get('asset_name')}",
            "asset_id_pk": asset_id,
            "asset_name": payload.get("asset_name"),
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        return {"error": f"Failed to update asset. Error Message: {str(e)}"}


def update_asset_nbv_internal(
    asset_id,
    asset_nbv,
    updated_by=None,
    asset_org_id_fk=None,
    conn=None,
):
    update_asset_nbv_query = text("""
        update asset_master
        set
            asset_nbv = :asset_nbv,
            updated_by = :updated_by,
            updated_at = CURRENT_TIMESTAMP
        where asset_id_pk = :asset_id
        and (:asset_org_id_fk is null or asset_org_id_fk = :asset_org_id_fk)
    """)

    params_update = {
        "asset_id": asset_id,
        "asset_nbv": asset_nbv,
        "updated_by": updated_by,
        "asset_org_id_fk": asset_org_id_fk
    }

    if conn is not None:
        return conn.execute(update_asset_nbv_query, params_update)

    asset_engine = db_engine()
    with asset_engine.begin() as update_conn:
        return update_conn.execute(update_asset_nbv_query, params_update)


def update_asset(payload: dict, conn=None):
    return update_asset_master(payload, conn=conn)
