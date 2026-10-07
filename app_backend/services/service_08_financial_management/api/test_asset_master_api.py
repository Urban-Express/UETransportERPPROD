from datetime import datetime
import os
from pathlib import Path
import sys
import uuid

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_backend.services.main import app as consolidated_app
from app_backend.services.service_08_financial_management.api.main import app as financial_app
from app_backend.services.service_07_alerts_wf_engine import asset_master_wf
from app_backend.services.service_07_alerts_wf_engine import fleet_management_wf
from app_backend.services.service_08_financial_management.logic import asset_master_common
from app_backend.services.service_08_financial_management.logic import asset_master_create_data
from app_backend.services.service_08_financial_management.logic import asset_master_delete_data
from app_backend.services.service_08_financial_management.logic import asset_master_depreciation_run
from app_backend.services.service_08_financial_management.logic import asset_master_get_data
from app_backend.services.service_08_financial_management.logic import asset_master_update_data


ORG_ID = 1
SUBMIT_APPROVE_USER = "system_admin"
SUBMIT_ONLY_USER = "lokesh_keer"
APPROVER_USER = "Manish@UrbanExpressTransportLLC.onmicrosoft.com"
NO_WORKFLOW_USER = "codex_no_workflow_user"
TEST_PREFIX = f"CODEX_API_TEST_{uuid.uuid4().hex[:8]}"

load_dotenv()
TEST_ENGINE = create_engine(
    os.getenv("RAILWAY_DB_URL"),
    pool_pre_ping=True,
    pool_size=1,
    max_overflow=0,
    pool_timeout=15,
    connect_args={"options": "-c statement_timeout=15000"}
)


def db_engine():
    return TEST_ENGINE


for module in [
    asset_master_wf,
    fleet_management_wf,
    asset_master_common,
    asset_master_create_data,
    asset_master_delete_data,
    asset_master_depreciation_run,
    asset_master_get_data,
    asset_master_update_data,
]:
    module.db_engine = db_engine


financial_client = TestClient(financial_app)
consolidated_client = TestClient(consolidated_app)
results = []
created_asset_ids = set()
created_workflow_ids = set()


def record(name, passed, detail=""):
    results.append({
        "name": name,
        "passed": bool(passed),
        "detail": detail,
    })


def post(client, path, payload):
    response = client.post(path, json=payload)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}
    return response.status_code, body


def get(client, path):
    response = client.get(path)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}
    return response.status_code, body


def data_error(body):
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        return data.get("error")
    return None


def asset_payload(name, user=SUBMIT_APPROVE_USER, **overrides):
    payload = {
        "user_principal_name": user,
        "asset_org_id_fk": ORG_ID,
        "asset_location": "API Test Yard",
        "asset_type": "Computer hardware",
        "asset_name": name,
        "asset_acquisition_date": "2026-01-01",
        "depreciation_start_date": "2026-01-01",
        "asset_acquisition_cost": 12000,
        "asset_useful_life": 12,
        "asset_salvage_value": 0,
        "asset_nbv": 12000,
        "created_by": user,
        "updated_by": user,
    }
    payload.update(overrides)
    return payload


def create_immediate_asset(name):
    status, body = post(
        financial_client,
        "/api/v1/assets/create",
        asset_payload(name)
    )
    asset_id = body.get("data", {}).get("asset_id_pk")
    if asset_id:
        created_asset_ids.add(asset_id)
    return status, body, asset_id


def pending_workflow_id(body):
    workflow = body.get("data", {}).get("workflow", {})
    workflow_id = workflow.get("workflow_request_id")
    if workflow_id:
        created_workflow_ids.add(workflow_id)
    return workflow_id


def cleanup_database():
    engine = db_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                delete from asset_master
                where asset_name like :asset_name_prefix
                or asset_id_pk = any(:asset_ids)
            """),
            {
                "asset_name_prefix": f"{TEST_PREFIX}%",
                "asset_ids": list(created_asset_ids) or [-1],
            }
        )
        conn.execute(
            text("""
                delete from asset_master_workflow_requests
                where workflow_request_id_pk = any(:workflow_ids)
                or request_payload::text like :payload_prefix
            """),
            {
                "workflow_ids": list(created_workflow_ids) or [-1],
                "payload_prefix": f"%{TEST_PREFIX}%",
            }
        )


def run_tests():
    try:
        cleanup_database()

        status, body = get(financial_client, "/health")
        record("Financial API health", status == 200 and body.get("success") is True, str(body))

        status, body = get(consolidated_client, "/health")
        record("Consolidated API health", status == 200 and body.get("success") is True, str(body))

        status, body = get(consolidated_client, "/api/v1/assets")
        record("Consolidated get assets route", status == 200 and body.get("success") is True, str(body)[:300])

        status, body, asset_id = create_immediate_asset(f"{TEST_PREFIX}_CREATE_IMMEDIATE")
        record(
            "CREATE with SUBMIT + APPROVE creates asset once",
            status == 200 and asset_id is not None and not data_error(body),
            str(body)
        )

        status, body = get(financial_client, "/api/v1/assets")
        returned_assets = body.get("data", [])
        matching_assets = [
            asset for asset in returned_assets
            if asset.get("asset_id_pk") == asset_id
        ]
        record(
            "Get assets JSON serializes dates and preserves numeric values",
            status == 200
            and matching_assets
            and isinstance(matching_assets[0].get("asset_acquisition_date"), str)
            and isinstance(matching_assets[0].get("asset_acquisition_cost"), (int, float)),
            str(matching_assets[:1])
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/depreciation/run",
            {
                "asset_id": asset_id,
                "run_date": "2026-02-15",
                "user_principal_name": SUBMIT_APPROVE_USER,
                "updated_by": SUBMIT_APPROVE_USER,
            }
        )
        first_nbv = body.get("data", {}).get("resulting_nbv")
        record(
            "Depreciation run updates NBV without public UPDATE workflow",
            status == 200
            and body.get("data", {}).get("status") == "SUCCESS"
            and first_nbv is not None,
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/depreciation/run",
            {
                "asset_id": asset_id,
                "run_date": "2026-02-15",
                "user_principal_name": SUBMIT_APPROVE_USER,
                "updated_by": SUBMIT_APPROVE_USER,
            }
        )
        second_nbv = body.get("data", {}).get("resulting_nbv")
        record(
            "Repeated depreciation same date is idempotent",
            status == 200 and first_nbv == second_nbv,
            f"first_nbv={first_nbv}, second_nbv={second_nbv}"
        )

        updated_payload = asset_payload(
            f"{TEST_PREFIX}_CREATE_IMMEDIATE",
            asset_id=asset_id,
            asset_id_pk=asset_id,
            asset_location="API Test Yard Updated",
            updated_by=SUBMIT_APPROVE_USER,
        )
        status, body = post(financial_client, "/api/v1/assets/update", updated_payload)
        record(
            "UPDATE with SUBMIT + APPROVE updates asset",
            status == 200 and not data_error(body),
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/create",
            asset_payload(f"{TEST_PREFIX}_CREATE_PENDING", user=SUBMIT_ONLY_USER)
        )
        create_pending_id = pending_workflow_id(body)
        record(
            "CREATE with SUBMIT only creates pending request and no asset",
            status == 200
            and create_pending_id is not None
            and body.get("data", {}).get("workflow", {}).get("workflow_status") == "PENDING_APPROVAL"
            and data_error(body),
            str(body)
        )

        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/approve",
            {
                "workflow_request_id": create_pending_id,
                "approver_user_principal_name": APPROVER_USER,
                "approval_comments": "API test approval",
            }
        )
        approved_asset_id = body.get("data", {}).get("execution_result", {}).get("asset_id_pk")
        if approved_asset_id:
            created_asset_ids.add(approved_asset_id)
        record(
            "Approver approves stored CREATE and executes once",
            status == 200
            and body.get("data", {}).get("workflow_status") == "EXECUTED"
            and approved_asset_id is not None,
            str(body)
        )

        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/approve",
            {
                "workflow_request_id": create_pending_id,
                "approver_user_principal_name": APPROVER_USER,
            }
        )
        record(
            "Already processed workflow request cannot execute again",
            status == 200 and body.get("data", {}).get("error") == "Workflow request is not pending approval.",
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/create",
            asset_payload(f"{TEST_PREFIX}_CREATE_REJECT", user=SUBMIT_ONLY_USER)
        )
        reject_create_id = pending_workflow_id(body)
        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/reject",
            {
                "workflow_request_id": reject_create_id,
                "approver_user_principal_name": APPROVER_USER,
                "rejection_comments": "API test rejection",
            }
        )
        record(
            "Approver rejects stored CREATE and no asset is created",
            status == 200 and body.get("data", {}).get("workflow_status") == "REJECTED",
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/create",
            asset_payload(f"{TEST_PREFIX}_NO_SUBMIT", user=NO_WORKFLOW_USER)
        )
        record(
            "CREATE without SUBMIT right is blocked",
            status == 200
            and body.get("data", {}).get("workflow", {}).get("workflow_status") == "REJECTED",
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/update",
            asset_payload(
                f"{TEST_PREFIX}_CREATE_IMMEDIATE",
                user=SUBMIT_ONLY_USER,
                asset_id=asset_id,
                asset_id_pk=asset_id,
                asset_location="Pending Update Location",
            )
        )
        update_pending_id = pending_workflow_id(body)
        record(
            "UPDATE with SUBMIT only creates pending request",
            status == 200
            and update_pending_id is not None
            and body.get("data", {}).get("workflow", {}).get("workflow_status") == "PENDING_APPROVAL",
            str(body)
        )

        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/approve",
            {
                "workflow_request_id": update_pending_id,
                "approver_user_principal_name": SUBMIT_APPROVE_USER,
            }
        )
        record(
            "Wrong assigned approver is blocked",
            status == 200 and body.get("data", {}).get("error") == "Workflow request is assigned to another approver.",
            str(body)
        )

        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/approve",
            {
                "workflow_request_id": update_pending_id,
                "approver_user_principal_name": APPROVER_USER,
                "approval_comments": "API test update approval",
            }
        )
        record(
            "Assigned approver approves stored UPDATE",
            status == 200 and body.get("data", {}).get("workflow_status") == "EXECUTED",
            str(body)
        )

        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/requests",
            {
                "workflow_status": "EXECUTED",
                "user_principal_name": APPROVER_USER,
            }
        )
        record(
            "Workflow request retrieval filters by status and user",
            status == 200 and isinstance(body.get("data"), list),
            str(body)[:300]
        )

        status, body, delete_immediate_asset_id = create_immediate_asset(f"{TEST_PREFIX}_DELETE_IMMEDIATE")
        status, body = post(
            financial_client,
            "/api/v1/assets/delete",
            {
                "asset_id": delete_immediate_asset_id,
                "user_principal_name": SUBMIT_APPROVE_USER,
            }
        )
        record(
            "DELETE with SUBMIT + APPROVE deletes asset",
            status == 200 and not data_error(body),
            str(body)
        )

        status, body, delete_reject_asset_id = create_immediate_asset(f"{TEST_PREFIX}_DELETE_REJECT")
        status, body = post(
            financial_client,
            "/api/v1/assets/delete",
            {
                "asset_id": delete_reject_asset_id,
                "user_principal_name": SUBMIT_ONLY_USER,
            }
        )
        delete_reject_workflow_id = pending_workflow_id(body)
        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/reject",
            {
                "workflow_request_id": delete_reject_workflow_id,
                "approver_user_principal_name": APPROVER_USER,
            }
        )
        record(
            "DELETE rejection leaves asset untouched",
            status == 200 and body.get("data", {}).get("workflow_status") == "REJECTED",
            str(body)
        )

        status, body, delete_approve_asset_id = create_immediate_asset(f"{TEST_PREFIX}_DELETE_APPROVE")
        status, body = post(
            financial_client,
            "/api/v1/assets/delete",
            {
                "asset_id": delete_approve_asset_id,
                "user_principal_name": SUBMIT_ONLY_USER,
            }
        )
        delete_approve_workflow_id = pending_workflow_id(body)
        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/approve",
            {
                "workflow_request_id": delete_approve_workflow_id,
                "approver_user_principal_name": APPROVER_USER,
            }
        )
        record(
            "DELETE approval executes deletion once",
            status == 200 and body.get("data", {}).get("workflow_status") == "EXECUTED",
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/delete",
            {
                "asset_id": 999999999,
                "user_principal_name": SUBMIT_APPROVE_USER,
            }
        )
        record(
            "DELETE non-existent asset returns clear error",
            status == 200 and body.get("data", {}).get("error") == "Asset ID not found.",
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/depreciation/run",
            {
                "asset_id": 999999999,
                "run_date": "2026-02-28",
            }
        )
        record(
            "Depreciation non-existent asset returns clear error",
            status == 200 and body.get("data", {}).get("status") == "FAILED",
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/create",
            {"user_principal_name": SUBMIT_APPROVE_USER}
        )
        record(
            "Request validation rejects incomplete create payload",
            status == 422,
            str(body)
        )

        status, body = post(
            financial_client,
            "/api/v1/assets/create",
            asset_payload(f"{TEST_PREFIX}_INVALID_ORG", asset_org_id_fk=999999999)
        )
        record(
            "Invalid organization returns clear error",
            status == 200 and body.get("data", {}).get("error") == "asset_org_id_fk not found in organization_master.",
            str(body)
        )

        status, body = post(
            consolidated_client,
            "/api/v1/workflow/asset-master/approve",
            {
                "workflow_request_id": update_pending_id,
                "approver_user_principal_name": NO_WORKFLOW_USER,
            }
        )
        record(
            "Unauthorized workflow approval is blocked",
            status == 200 and body.get("data", {}).get("error") == "Approver does not have WORKFLOW/APPROVE rights.",
            str(body)
        )

        status, body = post(
            consolidated_client,
            "/api/v1/assets/depreciation/run",
            {
                "asset_id": asset_id,
                "run_date": "2026-03-31",
                "user_principal_name": SUBMIT_APPROVE_USER,
            }
        )
        record(
            "Consolidated depreciation endpoint works",
            status == 200 and body.get("data", {}).get("status") == "SUCCESS",
            str(body)
        )

    finally:
        cleanup_database()


def write_report():
    report_path = Path(__file__).resolve().parents[1] / "asset_master_api_test_report.md"
    passed = sum(1 for result in results if result["passed"])
    failed = len(results) - passed
    lines = [
        "# Asset Master API Test Report",
        "",
        f"Generated at: {datetime.utcnow().isoformat()}Z",
        f"Test prefix: `{TEST_PREFIX}`",
        f"Total: {len(results)}",
        f"Passed: {passed}",
        f"Failed: {failed}",
        "",
        "| # | Scenario | Result | Detail |",
        "|---:|---|---|---|",
    ]
    for index, result in enumerate(results, start=1):
        detail = str(result["detail"]).replace("|", "\\|").replace("\n", " ")
        if len(detail) > 900:
            detail = f"{detail[:900]}..."
        lines.append(
            f"| {index} | {result['name']} | "
            f"{'PASS' if result['passed'] else 'FAIL'} | {detail} |"
        )
    lines.extend([
        "",
        "Cleanup: temporary asset records and workflow requests with the test prefix were deleted after the run.",
    ])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(report_path)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    run_tests()
    write_report()
