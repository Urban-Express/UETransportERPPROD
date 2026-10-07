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
)


def delete_asset_master(payload: dict, conn=None):
    try:
        asset_id = get_asset_id_from_payload(payload)
        asset_org_id_fk = payload.get("asset_org_id_fk") or payload.get("authenticated_org_id")

        if not asset_id:
            return {"error": "asset_id is required."}
        if not asset_org_id_fk:
            return {"error": "asset_org_id_fk is required."}

        existing_asset = get_asset_by_id(asset_id, asset_org_id_fk, conn=conn)
        if not existing_asset:
            return {"error": "Asset ID not found."}

        workflow_payload = {
            **payload,
            "asset_id": asset_id,
            "asset_id_pk": asset_id,
            "asset_org_id_fk": existing_asset.get("asset_org_id_fk"),
            "asset_name": existing_asset.get("asset_name")
        }

        workflow_approved, workflow_response = is_asset_master_workflow_approved(
            payload=workflow_payload,
            workflow_action="DELETE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                return pending_workflow_submission_response(
                    workflow_response,
                    "Asset deletion submitted for approval.",
                    domain_reference_id=asset_id,
                )
            return {
                "error": "Asset deletion blocked by workflow.",
                "workflow": workflow_response
            }

        delete_asset_master_query = text("""
            delete from asset_master
            where asset_id_pk = :asset_id
            and asset_org_id_fk = :asset_org_id_fk
        """)

        if conn is not None:
            result = conn.execute(
                delete_asset_master_query,
                {
                    "asset_id": asset_id,
                    "asset_org_id_fk": asset_org_id_fk
                }
            )
        else:
            asset_engine = db_engine()
            with asset_engine.begin() as asset_conn:
                result = asset_conn.execute(
                    delete_asset_master_query,
                    {
                        "asset_id": asset_id,
                        "asset_org_id_fk": asset_org_id_fk
                    }
                )

        if result.rowcount == 0:
            return {"error": "Asset ID not found at execution time."}

        return {
            "message": f"Successfully deleted asset ID: {asset_id}",
            "asset_id_pk": asset_id,
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        return {"error": f"Failed to delete asset. Error Message: {str(e)}"}


def delete_asset(payload: dict, conn=None):
    return delete_asset_master(payload, conn=conn)
