import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from firebase_admin import storage
from sqlalchemy import event, text


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_03_fleet_management.logic.fleet_master_create_data import create_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_master_delete_data import delete_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_master_update_data import update_fleet_vehicle
from app_backend.services.service_06_contracts_management.logic.contracts_management_create_data import create_contract
from app_backend.services.service_06_contracts_management.logic.contracts_management_update_data import update_contract
from app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf import (
    approve_accounts_payables_workflow,
    reject_accounts_payables_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf import (
    approve_accounts_receivables_workflow,
    reject_accounts_receivables_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.asset_master_wf import approve_asset_master_workflow
from app_backend.services.service_07_alerts_wf_engine.contracts_management_wf import (
    approve_contracts_management_workflow,
    reject_contracts_management_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.fleet_management_wf import (
    approve_fleet_management_workflow,
)
from app_backend.services.service_07_alerts_wf_engine.payroll_wf import (
    approve_payroll_workflow,
    submit_payroll_run_for_approval,
)
from app_backend.services.service_07_alerts_wf_engine.tests.live_postgres_workflow_engine_tests import (
    APPROVER_X,
    AUDIT_USER,
    REQUESTER,
    setup_reference_data,
    save_and_publish,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    initialize_firebase_app,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_create_data import (
    create_accounts_payable,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_delete_data import (
    delete_accounts_payable,
)
from app_backend.services.service_08_financial_management.logic.accounts_payables_update_data import (
    update_accounts_payable,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_create_data import (
    create_accounts_receivable,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_delete_data import (
    delete_accounts_receivable,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_update_data import (
    update_accounts_receivable,
)
from app_backend.services.service_08_financial_management.logic.asset_master_create_data import create_asset
from app_backend.services.service_08_financial_management.logic.asset_master_delete_data import delete_asset
from app_backend.services.service_08_financial_management.logic.asset_master_update_data import update_asset
import app_backend.services.service_07_alerts_wf_engine.payroll_wf as payroll_wf
import app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers as workflow_adapter_helpers
import app_backend.services.service_07_alerts_wf_engine.workflow_definition_service as definition_service
import app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine as runtime_engine


REAL_RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
REAL_MARKER = f"WF_REAL_ADAPTER_{REAL_RUN_ID}"
RESULTS: list[dict] = []


def record(test_id, name, passed, details):
    result = {
        "test_id": test_id,
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "details": details,
    }
    RESULTS.append(result)
    print(f"{test_id} {result['status']} - {name}: {details}", flush=True)


def bounded_db_engine():
    engine = db_engine()

    @event.listens_for(engine, "connect")
    def set_timeouts(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("set lock_timeout = '5s'")
        cursor.execute("set statement_timeout = '30s'")
        cursor.close()

    return engine


def patch_engines():
    definition_service.db_engine = bounded_db_engine
    runtime_engine.db_engine = bounded_db_engine
    workflow_adapter_helpers.db_engine = bounded_db_engine
    payroll_wf.db_engine = bounded_db_engine
    payroll_wf._PAYROLL_WORKFLOW_ENGINE = None


def one_step(workflow_code, action, org_id):
    return save_and_publish(
        org_id,
        REQUESTER,
        [APPROVER_X],
        action,
        workflow_code=workflow_code,
    )


def workflow_response(result):
    if isinstance(result, dict) and isinstance(result.get("workflow"), dict):
        return result["workflow"]
    return result


def approval_payload(pending_result, org_field=None, org_id=None, **extra):
    workflow = workflow_response(pending_result)
    payload = {
        "workflow_request_id": workflow["workflow_request_id"],
        "workflow_instance_id": workflow["workflow_instance_id"],
        "workflow_instance_step_id": workflow["workflow_instance_step_id"],
        "approver_user_principal_name": APPROVER_X,
        "approval_comments": "Real adapter Railway approval",
        **extra,
    }
    if org_field:
        payload[org_field] = org_id
    return payload


def rejection_payload(pending_result, org_field=None, org_id=None):
    payload = approval_payload(
        pending_result,
        org_field=org_field,
        org_id=org_id,
    )
    payload.pop("approval_comments", None)
    payload["rejection_comments"] = "Real adapter Railway document rejection"
    return payload


def fetch_one(conn, sql, params):
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def scalar(conn, sql, params):
    return conn.execute(text(sql), params).scalar_one()


def setup_business_reference_data(org_id):
    engine = bounded_db_engine()
    try:
        with engine.begin() as conn:
            department = fetch_one(
                conn,
                """
                    select dep_id_pk
                    from department_master
                    where dep_org_id_fk = :org_id
                    and department_name = :department_name
                    limit 1
                """,
                {"org_id": org_id, "department_name": REAL_MARKER},
            )
            if department:
                dep_id = department["dep_id_pk"]
            else:
                dep_id = conn.execute(
                    text("""
                        insert into department_master (
                            dep_org_id_fk,
                            department_name,
                            cost_center_flag,
                            profit_center_flag,
                            created_by
                        ) values (
                            :org_id,
                            :department_name,
                            'Y',
                            'N',
                            :audit_user
                        )
                        returning dep_id_pk
                    """),
                    {
                        "org_id": org_id,
                        "department_name": REAL_MARKER,
                        "audit_user": AUDIT_USER,
                    },
                ).scalar_one()

            cust_id = conn.execute(
                text("""
                    insert into customer_master (
                        cust_org_id_fk,
                        cust_code,
                        cust_name,
                        cust_category,
                        cust_status,
                        created_by,
                        updated_by
                    ) values (
                        :org_id,
                        :cust_code,
                        :cust_name,
                        'Workflow Test',
                        'ACTIVE',
                        :audit_user,
                        :audit_user
                    )
                    on conflict (cust_org_id_fk, cust_code) do update
                    set cust_name = excluded.cust_name,
                        updated_by = excluded.updated_by,
                        updated_at = current_timestamp
                    returning cust_id_pk
                """),
                {
                    "org_id": org_id,
                    "cust_code": REAL_MARKER[:30],
                    "cust_name": REAL_MARKER,
                    "audit_user": AUDIT_USER,
                },
            ).scalar_one()

            supp_id = conn.execute(
                text("""
                    insert into supplier_master (
                        supp_org_id_fk,
                        supp_code,
                        supp_name,
                        supp_category,
                        supp_status,
                        created_by,
                        updated_by
                    ) values (
                        :org_id,
                        :supp_code,
                        :supp_name,
                        'Workflow Test',
                        'ACTIVE',
                        :audit_user,
                        :audit_user
                    )
                    on conflict (supp_org_id_fk, supp_code) do update
                    set supp_name = excluded.supp_name,
                        updated_by = excluded.updated_by,
                        updated_at = current_timestamp
                    returning supp_id_pk
                """),
                {
                    "org_id": org_id,
                    "supp_code": REAL_MARKER[:30],
                    "supp_name": REAL_MARKER,
                    "audit_user": AUDIT_USER,
                },
            ).scalar_one()
            return {"dep_id": dep_id, "cust_id": cust_id, "supp_id": supp_id}
    finally:
        engine.dispose()


def service_request(domain, action, suffix):
    return f"{REAL_MARKER}:{domain}:{action}:{suffix}"


def asset_payload(org_id, suffix, **overrides):
    payload = {
        "user_principal_name": REQUESTER,
        "asset_org_id_fk": org_id,
        "asset_location": f"{REAL_MARKER} location {suffix}",
        "asset_type": "Computer hardware",
        "asset_name": f"{REAL_MARKER}_ASSET_{suffix}",
        "asset_acquisition_date": "2026-09-01",
        "depreciation_start_date": "2026-09-01",
        "asset_acquisition_cost": 1000,
        "asset_useful_life": 12,
        "asset_salvage_value": 0,
        "asset_nbv": 1000,
        "created_by": AUDIT_USER,
        "updated_by": AUDIT_USER,
        "service_request_id": service_request("ASSET", "BASE", suffix),
    }
    payload.update(overrides)
    return payload


def insert_asset_seed(conn, org_id, suffix):
    payload = asset_payload(org_id, suffix)
    return conn.execute(
        text("""
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
        """),
        payload,
    ).scalar_one()


def contract_payload(org_id, refs, suffix, **overrides):
    payload = {
        "user_principal_name": REQUESTER,
        "cont_org_id_fk": org_id,
        "cont_cust_id_fk": refs["cust_id"],
        "cont_dep_id_fk": refs["dep_id"],
        "cont_contract_number": f"{REAL_MARKER}_CT_{suffix}"[:50],
        "cont_contract_name": f"{REAL_MARKER} Contract {suffix}",
        "cont_start_date": "2026-09-01",
        "cont_end_date": "2027-08-31",
        "cont_status": "DRAFT",
        "cont_revenue_basis": "PER_BUS",
        "cont_currency_code": "AED",
        "cont_no_of_passengers": 0,
        "cont_big_bus_count_gt_34": 1,
        "cont_medium_bus_count_17_34": 0,
        "cont_small_bus_count_lt_17": 0,
        "cont_per_passenger_rate_pm": None,
        "cont_big_bus_rate_pm": 1000,
        "cont_medium_bus_rate_pm": None,
        "cont_small_bus_rate_pm": None,
        "cont_no_of_billing_months": 12,
        "cont_no_of_work_days_per_week": 5,
        "cont_no_of_round_trips_per_day": 2,
        "cont_driver_responsibility_party": "NOT_SPECIFIED",
        "cont_driver_accommodation_resp_party": "NOT_SPECIFIED",
        "cont_fuel_responsibility_party": "NOT_SPECIFIED",
        "cont_salik_responsibility_party": "NOT_SPECIFIED",
        "cont_permit_responsibility_party": "NOT_SPECIFIED",
        "cont_no_of_free_trips_per_month": 0,
        "cont_extra_trip_charge": 0,
        "cont_km_cap_pm_per_bus": None,
        "cont_extra_km_charge_per_km": None,
        "total_contract_value": 12000,
        "cont_notes": f"{REAL_MARKER} notes {suffix}",
        "created_by": AUDIT_USER,
        "updated_by": AUDIT_USER,
        "service_request_id": service_request("CONTRACT", "BASE", suffix),
    }
    payload.update(overrides)
    return payload


def insert_contract_seed(conn, org_id, refs, suffix):
    payload = contract_payload(org_id, refs, suffix)
    return conn.execute(
        text("""
            insert into contracts_management (
                cont_org_id_fk,
                cont_cust_id_fk,
                cont_dep_id_fk,
                cont_contract_number,
                cont_contract_name,
                cont_start_date,
                cont_end_date,
                cont_status,
                cont_revenue_basis,
                cont_currency_code,
                cont_no_of_passengers,
                cont_big_bus_count_gt_34,
                cont_medium_bus_count_17_34,
                cont_small_bus_count_lt_17,
                cont_per_passenger_rate_pm,
                cont_big_bus_rate_pm,
                cont_medium_bus_rate_pm,
                cont_small_bus_rate_pm,
                cont_no_of_billing_months,
                cont_no_of_work_days_per_week,
                cont_no_of_round_trips_per_day,
                cont_driver_responsibility_party,
                cont_driver_accommodation_resp_party,
                cont_fuel_responsibility_party,
                cont_salik_responsibility_party,
                cont_permit_responsibility_party,
                cont_no_of_free_trips_per_month,
                cont_extra_trip_charge,
                cont_km_cap_pm_per_bus,
                cont_extra_km_charge_per_km,
                total_contract_value,
                cont_notes,
                created_by,
                updated_by
            ) values (
                :cont_org_id_fk,
                :cont_cust_id_fk,
                :cont_dep_id_fk,
                :cont_contract_number,
                :cont_contract_name,
                :cont_start_date,
                :cont_end_date,
                :cont_status,
                :cont_revenue_basis,
                :cont_currency_code,
                :cont_no_of_passengers,
                :cont_big_bus_count_gt_34,
                :cont_medium_bus_count_17_34,
                :cont_small_bus_count_lt_17,
                :cont_per_passenger_rate_pm,
                :cont_big_bus_rate_pm,
                :cont_medium_bus_rate_pm,
                :cont_small_bus_rate_pm,
                :cont_no_of_billing_months,
                :cont_no_of_work_days_per_week,
                :cont_no_of_round_trips_per_day,
                :cont_driver_responsibility_party,
                :cont_driver_accommodation_resp_party,
                :cont_fuel_responsibility_party,
                :cont_salik_responsibility_party,
                :cont_permit_responsibility_party,
                :cont_no_of_free_trips_per_month,
                :cont_extra_trip_charge,
                :cont_km_cap_pm_per_bus,
                :cont_extra_km_charge_per_km,
                :total_contract_value,
                :cont_notes,
                :created_by,
                :updated_by
            )
            returning cont_id_pk
        """),
        payload,
    ).scalar_one()


def fleet_payload(org_id, suffix, **overrides):
    token = suffix.replace("_", "")[-18:]
    payload = {
        "user_principal_name": REQUESTER,
        "fleet_org_id_fk": org_id,
        "vehicle_code": f"{REAL_MARKER}_FLT_{suffix}"[:50],
        "fleet_type": "bus",
        "fleet_category": "corporate",
        "vehicle_brand_name": "WorkflowTest",
        "vehicle_model_name": "Adapter",
        "vehicle_model_year": 2026,
        "vehicle_color": "White",
        "vehicle_total_seats_including_driver": 40,
        "vehicle_plate_number": f"PLT{token}"[:50],
        "vehicle_plate_emirate": "Dubai",
        "vehicle_chassis_number": f"CHS{REAL_RUN_ID}{token}"[:100],
        "vehicle_engine_number": f"ENG{REAL_RUN_ID}{token}"[:100],
        "mulkiya_number": f"MUL{REAL_RUN_ID}{token}"[:100],
        "mulkiya_expiry_date": "2027-12-31",
        "salik_tag_number": f"SAL{REAL_RUN_ID}{token}"[:100],
        "vehicle_insurance_provider": "Workflow Insurance",
        "vehicle_insurance_number": f"INS{REAL_RUN_ID}{token}"[:100],
        "vehicle_insurance_type": "comprehensive",
        "vehicle_insurance_start_date": "2026-09-01",
        "vehicle_insurance_expiry_date": "2027-08-31",
        "current_odometer_km": 100,
        "odometer_last_updated_at": "2026-09-01T00:00:00+00:00",
        "vehicle_operational_status": "available",
        "vehicle_deployment_status": "unassigned",
        "vehicle_ownership_type": "owned",
        "vehicle_owner_legal_entity": "Urban Express",
        "vehicle_acquisition_date": "2026-09-01",
        "vehicle_acquisition_cost_aed": 100000,
        "depreciation_start_date": "2026-09-01",
        "depreciation_method": "straight_line",
        "useful_life_months": 60,
        "residual_value_aed": 10000,
        "field_flex_field_1": REAL_MARKER,
        "field_flex_field_2": suffix,
        "field_flex_field_3": None,
        "field_flex_field_4": None,
        "created_by": 1,
        "updated_by": 1,
        "service_request_id": service_request("FLEET", "BASE", suffix),
    }
    payload.update(overrides)
    return payload


def insert_fleet_seed(conn, org_id, suffix):
    payload = fleet_payload(org_id, suffix)
    return conn.execute(
        text("""
            insert into fleet_master (
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
        """),
        payload,
    ).scalar_one()


def ap_payload(org_id, refs, suffix, **overrides):
    payload = {
        "user_principal_name": REQUESTER,
        "ap_org_id_fk": org_id,
        "ap_supp_id_fk": refs["supp_id"],
        "ap_invoice_number": f"{REAL_MARKER}_AP_{suffix}"[:100],
        "ap_invoice_date": "2026-09-01",
        "ap_due_date": "2026-09-30",
        "ap_description": f"{REAL_MARKER} AP {suffix}",
        "ap_currency_code": "AED",
        "ap_invoice_amount": 1000,
        "ap_tax_amount": 50,
        "ap_paid_amount": 0,
        "ap_approval_status": "DRAFT",
        "ap_payment_status": "UNPAID",
        "ap_notes": REAL_MARKER,
        "created_by": AUDIT_USER,
        "updated_by": AUDIT_USER,
        "service_request_id": service_request("AP", "BASE", suffix),
    }
    payload.update(overrides)
    return payload


def insert_ap_seed(conn, org_id, refs, suffix):
    payload = ap_payload(org_id, refs, suffix)
    row = conn.execute(
        text("""
            insert into accounts_payables (
                ap_org_id_fk,
                ap_supp_id_fk,
                ap_invoice_number,
                ap_invoice_date,
                ap_due_date,
                ap_description,
                ap_currency_code,
                ap_invoice_amount,
                ap_tax_amount,
                ap_paid_amount,
                ap_approval_status,
                ap_payment_status,
                ap_notes,
                created_by,
                updated_by
            ) values (
                :ap_org_id_fk,
                :ap_supp_id_fk,
                :ap_invoice_number,
                :ap_invoice_date,
                :ap_due_date,
                :ap_description,
                :ap_currency_code,
                :ap_invoice_amount,
                :ap_tax_amount,
                :ap_paid_amount,
                :ap_approval_status,
                :ap_payment_status,
                :ap_notes,
                :created_by,
                :updated_by
            )
            returning ap_id_pk
        """),
        payload,
    )
    return row.scalar_one()


def ar_payload(org_id, refs, suffix, **overrides):
    payload = {
        "user_principal_name": REQUESTER,
        "ar_org_id_fk": org_id,
        "ar_cust_id_fk": refs["cust_id"],
        "ar_invoice_number": f"{REAL_MARKER}_AR_{suffix}"[:100],
        "ar_invoice_date": "2026-09-01",
        "ar_due_date": "2026-09-30",
        "ar_contract": "Workflow test",
        "ar_description": f"{REAL_MARKER} AR {suffix}",
        "ar_currency_code": "AED",
        "ar_invoice_amount": 1000,
        "ar_tax_amount": 50,
        "ar_received_amount": 0,
        "ar_approval_status": "DRAFT",
        "ar_collection_status": "OUTSTANDING",
        "ar_notes": REAL_MARKER,
        "created_by": AUDIT_USER,
        "updated_by": AUDIT_USER,
        "service_request_id": service_request("AR", "BASE", suffix),
    }
    payload.update(overrides)
    return payload


def insert_ar_seed(conn, org_id, refs, suffix):
    payload = ar_payload(org_id, refs, suffix)
    return conn.execute(
        text("""
            insert into accounts_receivables (
                ar_org_id_fk,
                ar_cust_id_fk,
                ar_invoice_number,
                ar_invoice_date,
                ar_due_date,
                ar_contract,
                ar_description,
                ar_currency_code,
                ar_invoice_amount,
                ar_tax_amount,
                ar_received_amount,
                ar_approval_status,
                ar_collection_status,
                ar_notes,
                created_by,
                updated_by
            ) values (
                :ar_org_id_fk,
                :ar_cust_id_fk,
                :ar_invoice_number,
                :ar_invoice_date,
                :ar_due_date,
                :ar_contract,
                :ar_description,
                :ar_currency_code,
                :ar_invoice_amount,
                :ar_tax_amount,
                :ar_received_amount,
                :ar_approval_status,
                :ar_collection_status,
                :ar_notes,
                :created_by,
                :updated_by
            )
            returning ar_id_pk
        """),
        payload,
    ).scalar_one()


def insert_payroll_run(conn, org_id, suffix):
    return conn.execute(
        text("""
            insert into payroll_run (
                payroll_org_id_fk,
                payroll_run_code,
                payroll_run_description,
                payroll_year,
                payroll_month,
                payroll_run_sequence,
                payroll_run_type,
                payroll_period_start_date,
                payroll_period_end_date,
                payroll_payment_date,
                payroll_status,
                created_by,
                processed_by,
                updated_by
            ) values (
                :org_id,
                :run_code,
                :description,
                2026,
                9,
                1,
                'MONTHLY',
                date '2026-09-01',
                date '2026-09-30',
                date '2026-10-05',
                'PROCESSED',
                :audit_user,
                :audit_user,
                :audit_user
            )
            returning payroll_run_id_pk
        """),
        {
            "org_id": org_id,
            "run_code": f"{REAL_MARKER}_PAY_{suffix}"[:100],
            "description": f"{REAL_MARKER} payroll {suffix}",
            "audit_user": AUDIT_USER,
        },
    ).scalar_one()


def blob_exists(blob_path, bucket_name):
    initialize_firebase_app()
    bucket = storage.bucket(bucket_name) if bucket_name else storage.bucket()
    return bucket.blob(blob_path).exists()


def staged_document_payload(conn, pending_result):
    workflow = workflow_response(pending_result)
    row = fetch_one(
        conn,
        """
            select request_payload
            from workflow_instances
            where workflow_instance_id_pk = :workflow_instance_id
        """,
        {"workflow_instance_id": workflow["workflow_instance_id"]},
    )
    payload = row["request_payload"]
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def test_create(
    test_id,
    label,
    workflow_code,
    action,
    org_id,
    table_name,
    count_sql,
    count_params,
    create_callable,
    create_payload,
    approve_callable,
    org_field,
):
    one_step(workflow_code, action, org_id)
    engine = bounded_db_engine()
    try:
        with engine.begin() as conn:
            before_count = scalar(conn, count_sql, count_params)
        pending = create_callable(create_payload)
        workflow = workflow_response(pending)
        with engine.begin() as conn:
            pending_count = scalar(conn, count_sql, count_params)
        approved = approve_callable(approval_payload(pending, org_field, org_id))
        with engine.begin() as conn:
            after_count = scalar(conn, count_sql, count_params)
        record(
            test_id,
            label,
            before_count == 0
            and pending_count == 0
            and after_count == 1
            and workflow.get("workflow_status") == "PENDING_APPROVAL"
            and approved.get("workflow_status") == "EXECUTED"
            and approved.get("business_operation_executed") is True,
            {
                "table": table_name,
                "before_count": before_count,
                "pending_count": pending_count,
                "after_count": after_count,
                "workflow_request_id": workflow.get("workflow_request_id"),
                "workflow_instance_id": workflow.get("workflow_instance_id"),
                "approve_status": approved.get("workflow_status"),
                "execution_result": approved.get("execution_result"),
            },
        )
    finally:
        engine.dispose()


def test_update(
    test_id,
    label,
    workflow_code,
    org_id,
    seed_callable,
    select_sql,
    payload_builder,
    update_callable,
    approve_callable,
    org_field,
    unchanged_field,
    changed_value,
):
    one_step(workflow_code, "UPDATE", org_id)
    engine = bounded_db_engine()
    try:
        with engine.begin() as conn:
            record_id = seed_callable(conn)
            before = fetch_one(conn, select_sql, {"record_id": record_id, "org_id": org_id})
        payload = payload_builder(record_id)
        pending = update_callable(payload)
        workflow = workflow_response(pending)
        with engine.begin() as conn:
            during = fetch_one(conn, select_sql, {"record_id": record_id, "org_id": org_id})
        approved = approve_callable(approval_payload(pending, org_field, org_id))
        with engine.begin() as conn:
            after = fetch_one(conn, select_sql, {"record_id": record_id, "org_id": org_id})
        record(
            test_id,
            label,
            workflow.get("workflow_status") == "PENDING_APPROVAL"
            and before[unchanged_field] == during[unchanged_field]
            and after[unchanged_field] == changed_value
            and approved.get("workflow_status") == "EXECUTED"
            and approved.get("business_operation_executed") is True,
            {
                "record_id": record_id,
                "before": before,
                "during": during,
                "after": after,
                "workflow_request_id": workflow.get("workflow_request_id"),
                "approve_status": approved.get("workflow_status"),
                "execution_result": approved.get("execution_result"),
            },
        )
    finally:
        engine.dispose()


def test_delete(
    test_id,
    label,
    workflow_code,
    org_id,
    seed_callable,
    count_sql,
    delete_payload_builder,
    delete_callable,
    approve_callable,
    org_field,
):
    one_step(workflow_code, "DELETE", org_id)
    engine = bounded_db_engine()
    try:
        with engine.begin() as conn:
            record_id = seed_callable(conn)
            before_count = scalar(conn, count_sql, {"record_id": record_id, "org_id": org_id})
        pending = delete_callable(delete_payload_builder(record_id))
        workflow = workflow_response(pending)
        with engine.begin() as conn:
            pending_count = scalar(conn, count_sql, {"record_id": record_id, "org_id": org_id})
        approved = approve_callable(approval_payload(pending, org_field, org_id))
        with engine.begin() as conn:
            after_count = scalar(conn, count_sql, {"record_id": record_id, "org_id": org_id})
        record(
            test_id,
            label,
            before_count == 1
            and pending_count == 1
            and after_count == 0
            and workflow.get("workflow_status") == "PENDING_APPROVAL"
            and approved.get("workflow_status") == "EXECUTED"
            and approved.get("business_operation_executed") is True,
            {
                "record_id": record_id,
                "before_count": before_count,
                "pending_count": pending_count,
                "after_count": after_count,
                "workflow_request_id": workflow.get("workflow_request_id"),
                "approve_status": approved.get("workflow_status"),
                "execution_result": approved.get("execution_result"),
            },
        )
    finally:
        engine.dispose()


def test_documents(org_id, refs):
    document_cases = [
        (
            "RBE-13",
            "Contract staged document rejection removes Firebase object",
            "CONTRACTS_MANAGEMENT",
            create_contract,
            reject_contracts_management_workflow,
            contract_payload(
                org_id,
                refs,
                "DOC_REJECT",
                service_request_id=service_request("CONTRACT", "DOC_REJECT", "CREATE"),
            ),
            "cont_org_id_fk",
        ),
        (
            "RBE-14",
            "AP staged document rejection removes Firebase object",
            "ACCOUNTS_PAYABLE",
            create_accounts_payable,
            reject_accounts_payables_workflow,
            ap_payload(
                org_id,
                refs,
                "DOC_REJECT",
                service_request_id=service_request("AP", "DOC_REJECT", "CREATE"),
            ),
            "ap_org_id_fk",
        ),
        (
            "RBE-15",
            "AR staged document rejection removes Firebase object",
            "ACCOUNTS_RECEIVABLE",
            create_accounts_receivable,
            reject_accounts_receivables_workflow,
            ar_payload(
                org_id,
                refs,
                "DOC_REJECT",
                service_request_id=service_request("AR", "DOC_REJECT", "CREATE"),
            ),
            "ar_org_id_fk",
        ),
    ]
    engine = bounded_db_engine()
    try:
        for test_id, label, workflow_code, create_callable, reject_callable, payload, org_field in document_cases:
            one_step(workflow_code, "CREATE", org_id)
            pending = create_callable(
                payload,
                file_stream=BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"),
                file_name=f"{REAL_MARKER}_{test_id}.pdf",
                content_type="application/pdf",
            )
            workflow = workflow_response(pending)
            with engine.begin() as conn:
                staged_payload = staged_document_payload(conn, pending)
            blob_path = staged_payload.get("workflow_document_staged_blob_path")
            bucket_name = staged_payload.get("workflow_document_staged_bucket")
            exists_before = blob_exists(blob_path, bucket_name)
            rejected = reject_callable(rejection_payload(pending, org_field, org_id))
            exists_after = blob_exists(blob_path, bucket_name)
            record(
                test_id,
                label,
                workflow.get("workflow_status") == "PENDING_APPROVAL"
                and exists_before is True
                and rejected.get("workflow_status") == "REJECTED"
                and rejected.get("document_cleanup", {}).get("cleanup_completed") is True
                and exists_after is False,
                {
                    "workflow_request_id": workflow.get("workflow_request_id"),
                    "workflow_instance_id": workflow.get("workflow_instance_id"),
                    "blob_path": blob_path,
                    "bucket": bucket_name,
                    "exists_before_reject": exists_before,
                    "exists_after_reject": exists_after,
                    "reject_status": rejected.get("workflow_status"),
                    "document_cleanup": rejected.get("document_cleanup"),
                },
            )
    finally:
        engine.dispose()


def run_real_adapter_tests(org_id, refs):
    test_create(
        "RBE-01",
        "Asset CREATE actual adapter creates row only after approval",
        "ASSET_MASTER",
        "CREATE",
        org_id,
        "asset_master",
        """
            select count(*)
            from asset_master
            where asset_org_id_fk = :org_id
            and asset_name = :asset_name
        """,
        {"org_id": org_id, "asset_name": f"{REAL_MARKER}_ASSET_CREATE"},
        create_asset,
        asset_payload(
            org_id,
            "CREATE",
            service_request_id=service_request("ASSET", "CREATE", "CREATE"),
        ),
        approve_asset_master_workflow,
        "asset_org_id_fk",
    )

    test_update(
        "RBE-02",
        "Asset UPDATE actual adapter changes row only after approval",
        "ASSET_MASTER",
        org_id,
        lambda conn: insert_asset_seed(conn, org_id, "UPDATE_SEED"),
        """
            select asset_id_pk, asset_location
            from asset_master
            where asset_id_pk = :record_id
            and asset_org_id_fk = :org_id
        """,
        lambda record_id: asset_payload(
            org_id,
            "UPDATE_SEED",
            asset_id=record_id,
            asset_id_pk=record_id,
            asset_location=f"{REAL_MARKER} updated asset location",
            service_request_id=service_request("ASSET", "UPDATE", "UPDATE_SEED"),
        ),
        update_asset,
        approve_asset_master_workflow,
        "asset_org_id_fk",
        "asset_location",
        f"{REAL_MARKER} updated asset location",
    )

    test_delete(
        "RBE-03",
        "Asset DELETE actual adapter deletes row only after approval",
        "ASSET_MASTER",
        org_id,
        lambda conn: insert_asset_seed(conn, org_id, "DELETE_SEED"),
        """
            select count(*)
            from asset_master
            where asset_id_pk = :record_id
            and asset_org_id_fk = :org_id
        """,
        lambda record_id: {
            "user_principal_name": REQUESTER,
            "asset_org_id_fk": org_id,
            "asset_id": record_id,
            "asset_id_pk": record_id,
            "service_request_id": service_request("ASSET", "DELETE", "DELETE_SEED"),
        },
        delete_asset,
        approve_asset_master_workflow,
        "asset_org_id_fk",
    )

    test_create(
        "RBE-04",
        "Contract CREATE actual adapter creates row only after approval",
        "CONTRACTS_MANAGEMENT",
        "CREATE",
        org_id,
        "contracts_management",
        """
            select count(*)
            from contracts_management
            where cont_org_id_fk = :org_id
            and cont_contract_number = :cont_contract_number
        """,
        {"org_id": org_id, "cont_contract_number": f"{REAL_MARKER}_CT_CREATE"[:50]},
        create_contract,
        contract_payload(
            org_id,
            refs,
            "CREATE",
            service_request_id=service_request("CONTRACT", "CREATE", "CREATE"),
        ),
        approve_contracts_management_workflow,
        "cont_org_id_fk",
    )

    test_update(
        "RBE-05",
        "Contract UPDATE actual adapter changes row only after approval",
        "CONTRACTS_MANAGEMENT",
        org_id,
        lambda conn: insert_contract_seed(conn, org_id, refs, "UPDATE_SEED"),
        """
            select cont_id_pk, cont_contract_name
            from contracts_management
            where cont_id_pk = :record_id
            and cont_org_id_fk = :org_id
        """,
        lambda record_id: contract_payload(
            org_id,
            refs,
            "UPDATE_SEED",
            cont_id=record_id,
            cont_id_pk=record_id,
            cont_contract_name=f"{REAL_MARKER} updated contract",
            service_request_id=service_request("CONTRACT", "UPDATE", "UPDATE_SEED"),
        ),
        update_contract,
        approve_contracts_management_workflow,
        "cont_org_id_fk",
        "cont_contract_name",
        f"{REAL_MARKER} updated contract",
    )

    test_create(
        "RBE-06",
        "Fleet CREATE actual adapter creates row only after approval",
        "FLEET_MANAGEMENT",
        "CREATE",
        org_id,
        "fleet_master",
        """
            select count(*)
            from fleet_master
            where fleet_org_id_fk = :org_id
            and vehicle_code = :vehicle_code
        """,
        {"org_id": org_id, "vehicle_code": f"{REAL_MARKER}_FLT_CREATE"[:50]},
        create_fleet_vehicle,
        fleet_payload(
            org_id,
            "CREATE",
            service_request_id=service_request("FLEET", "CREATE", "CREATE"),
        ),
        approve_fleet_management_workflow,
        "fleet_org_id_fk",
    )

    test_update(
        "RBE-07",
        "Fleet UPDATE actual adapter changes row only after approval",
        "FLEET_MANAGEMENT",
        org_id,
        lambda conn: insert_fleet_seed(conn, org_id, "UPDATE_SEED"),
        """
            select fleet_vehicle_id_pk, vehicle_color
            from fleet_master
            where fleet_vehicle_id_pk = :record_id
            and fleet_org_id_fk = :org_id
        """,
        lambda record_id: fleet_payload(
            org_id,
            "UPDATE_SEED",
            fleet_vehicle_id=record_id,
            fleet_vehicle_id_pk=record_id,
            vehicle_color="Silver",
            service_request_id=service_request("FLEET", "UPDATE", "UPDATE_SEED"),
        ),
        update_fleet_vehicle,
        approve_fleet_management_workflow,
        "fleet_org_id_fk",
        "vehicle_color",
        "Silver",
    )

    test_delete(
        "RBE-08",
        "Fleet DELETE actual adapter deletes row only after approval",
        "FLEET_MANAGEMENT",
        org_id,
        lambda conn: insert_fleet_seed(conn, org_id, "DELETE_SEED"),
        """
            select count(*)
            from fleet_master
            where fleet_vehicle_id_pk = :record_id
            and fleet_org_id_fk = :org_id
        """,
        lambda record_id: {
            "user_principal_name": REQUESTER,
            "fleet_org_id_fk": org_id,
            "fleet_vehicle_id": record_id,
            "fleet_vehicle_id_pk": record_id,
            "service_request_id": service_request("FLEET", "DELETE", "DELETE_SEED"),
        },
        delete_fleet_vehicle,
        approve_fleet_management_workflow,
        "fleet_org_id_fk",
    )

    one_step("PAYROLL", "APPROVAL", org_id)
    engine = bounded_db_engine()
    try:
        with engine.begin() as conn:
            payroll_run_id = insert_payroll_run(conn, org_id, "APPROVAL")
            before_status = scalar(
                conn,
                "select payroll_status from payroll_run where payroll_run_id_pk = :run_id",
                {"run_id": payroll_run_id},
            )
        payroll_pending = submit_payroll_run_for_approval(
            {
                "user_principal_name": REQUESTER,
                "payroll_org_id_fk": org_id,
                "payroll_run_id": payroll_run_id,
                "payroll_run_id_pk": payroll_run_id,
                "service_request_id": service_request("PAYROLL", "APPROVAL", "APPROVAL"),
            }
        )
        with engine.begin() as conn:
            pending_status = scalar(
                conn,
                "select payroll_status from payroll_run where payroll_run_id_pk = :run_id",
                {"run_id": payroll_run_id},
            )
        payroll_approved = approve_payroll_workflow(
            approval_payload(
                payroll_pending,
                "payroll_org_id_fk",
                org_id,
                payroll_run_id=payroll_run_id,
                payroll_run_id_pk=payroll_run_id,
            )
        )
        with engine.begin() as conn:
            after_status = scalar(
                conn,
                "select payroll_status from payroll_run where payroll_run_id_pk = :run_id",
                {"run_id": payroll_run_id},
            )
        workflow = workflow_response(payroll_pending)
        record(
            "RBE-09",
            "Payroll APPROVAL actual adapter changes PROCESSED to APPROVED only after approval",
            before_status == "PROCESSED"
            and pending_status == "PROCESSED"
            and after_status == "APPROVED"
            and workflow.get("workflow_status") == "PENDING_APPROVAL"
            and payroll_approved.get("workflow_status") == "EXECUTED"
            and payroll_approved.get("payroll_status") == "APPROVED",
            {
                "payroll_run_id": payroll_run_id,
                "before_status": before_status,
                "pending_status": pending_status,
                "after_status": after_status,
                "workflow_request_id": workflow.get("workflow_request_id"),
                "approve_status": payroll_approved.get("workflow_status"),
                "execution_result": payroll_approved.get("execution_result"),
            },
        )
    finally:
        engine.dispose()

    test_create(
        "RBE-10",
        "AP CREATE actual adapter creates row only after approval",
        "ACCOUNTS_PAYABLE",
        "CREATE",
        org_id,
        "accounts_payables",
        """
            select count(*)
            from accounts_payables
            where ap_org_id_fk = :org_id
            and ap_invoice_number = :ap_invoice_number
        """,
        {"org_id": org_id, "ap_invoice_number": f"{REAL_MARKER}_AP_CREATE"[:100]},
        create_accounts_payable,
        ap_payload(
            org_id,
            refs,
            "CREATE",
            service_request_id=service_request("AP", "CREATE", "CREATE"),
        ),
        approve_accounts_payables_workflow,
        "ap_org_id_fk",
    )

    test_update(
        "RBE-11",
        "AP UPDATE actual adapter changes row only after approval",
        "ACCOUNTS_PAYABLE",
        org_id,
        lambda conn: insert_ap_seed(conn, org_id, refs, "UPDATE_SEED"),
        """
            select ap_id_pk, ap_invoice_amount
            from accounts_payables
            where ap_id_pk = :record_id
            and ap_org_id_fk = :org_id
        """,
        lambda record_id: ap_payload(
            org_id,
            refs,
            "UPDATE_SEED",
            ap_id=record_id,
            ap_id_pk=record_id,
            ap_invoice_amount=1500,
            service_request_id=service_request("AP", "UPDATE", "UPDATE_SEED"),
        ),
        update_accounts_payable,
        approve_accounts_payables_workflow,
        "ap_org_id_fk",
        "ap_invoice_amount",
        1500,
    )

    test_delete(
        "RBE-12",
        "AP DELETE actual adapter deletes row only after approval",
        "ACCOUNTS_PAYABLE",
        org_id,
        lambda conn: insert_ap_seed(conn, org_id, refs, "DELETE_SEED"),
        """
            select count(*)
            from accounts_payables
            where ap_id_pk = :record_id
            and ap_org_id_fk = :org_id
        """,
        lambda record_id: {
            "user_principal_name": REQUESTER,
            "ap_org_id_fk": org_id,
            "ap_id": record_id,
            "ap_id_pk": record_id,
            "service_request_id": service_request("AP", "DELETE", "DELETE_SEED"),
        },
        delete_accounts_payable,
        approve_accounts_payables_workflow,
        "ap_org_id_fk",
    )

    test_create(
        "RBE-16",
        "AR CREATE actual adapter creates row only after approval",
        "ACCOUNTS_RECEIVABLE",
        "CREATE",
        org_id,
        "accounts_receivables",
        """
            select count(*)
            from accounts_receivables
            where ar_org_id_fk = :org_id
            and ar_invoice_number = :ar_invoice_number
        """,
        {"org_id": org_id, "ar_invoice_number": f"{REAL_MARKER}_AR_CREATE"[:100]},
        create_accounts_receivable,
        ar_payload(
            org_id,
            refs,
            "CREATE",
            service_request_id=service_request("AR", "CREATE", "CREATE"),
        ),
        approve_accounts_receivables_workflow,
        "ar_org_id_fk",
    )

    test_update(
        "RBE-17",
        "AR UPDATE actual adapter changes row only after approval",
        "ACCOUNTS_RECEIVABLE",
        org_id,
        lambda conn: insert_ar_seed(conn, org_id, refs, "UPDATE_SEED"),
        """
            select ar_id_pk, ar_invoice_amount
            from accounts_receivables
            where ar_id_pk = :record_id
            and ar_org_id_fk = :org_id
        """,
        lambda record_id: ar_payload(
            org_id,
            refs,
            "UPDATE_SEED",
            ar_id=record_id,
            ar_id_pk=record_id,
            ar_invoice_amount=1750,
            service_request_id=service_request("AR", "UPDATE", "UPDATE_SEED"),
        ),
        update_accounts_receivable,
        approve_accounts_receivables_workflow,
        "ar_org_id_fk",
        "ar_invoice_amount",
        1750,
    )

    test_delete(
        "RBE-18",
        "AR DELETE actual adapter deletes row only after approval",
        "ACCOUNTS_RECEIVABLE",
        org_id,
        lambda conn: insert_ar_seed(conn, org_id, refs, "DELETE_SEED"),
        """
            select count(*)
            from accounts_receivables
            where ar_id_pk = :record_id
            and ar_org_id_fk = :org_id
        """,
        lambda record_id: {
            "user_principal_name": REQUESTER,
            "ar_org_id_fk": org_id,
            "ar_id": record_id,
            "ar_id_pk": record_id,
            "service_request_id": service_request("AR", "DELETE", "DELETE_SEED"),
        },
        delete_accounts_receivable,
        approve_accounts_receivables_workflow,
        "ar_org_id_fk",
    )

    test_documents(org_id, refs)


def cleanup_business_rows(org_id):
    engine = bounded_db_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("delete from accounts_receivables where ar_org_id_fk = :org_id and ar_invoice_number like :marker"),
                {"org_id": org_id, "marker": f"{REAL_MARKER}%"},
            )
            conn.execute(
                text("delete from accounts_payables where ap_org_id_fk = :org_id and ap_invoice_number like :marker"),
                {"org_id": org_id, "marker": f"{REAL_MARKER}%"},
            )
            conn.execute(
                text("delete from contracts_management where cont_org_id_fk = :org_id and cont_contract_number like :marker"),
                {"org_id": org_id, "marker": f"{REAL_MARKER}%"},
            )
            conn.execute(
                text("delete from fleet_master where fleet_org_id_fk = :org_id and field_flex_field_1 = :marker"),
                {"org_id": org_id, "marker": REAL_MARKER},
            )
            conn.execute(
                text("delete from asset_master where asset_org_id_fk = :org_id and asset_name like :marker"),
                {"org_id": org_id, "marker": f"{REAL_MARKER}%"},
            )
            conn.execute(
                text("delete from payroll_run where payroll_org_id_fk = :org_id and payroll_run_code like :marker"),
                {"org_id": org_id, "marker": f"{REAL_MARKER}%"},
            )
            conn.execute(
                text("delete from supplier_master where supp_org_id_fk = :org_id and supp_code = :code"),
                {"org_id": org_id, "code": REAL_MARKER[:30]},
            )
            conn.execute(
                text("delete from customer_master where cust_org_id_fk = :org_id and cust_code = :code"),
                {"org_id": org_id, "code": REAL_MARKER[:30]},
            )
            conn.execute(
                text("delete from department_master where dep_org_id_fk = :org_id and department_name = :name"),
                {"org_id": org_id, "name": REAL_MARKER},
            )
    finally:
        engine.dispose()


def main():
    print(f"Starting real-adapter Railway workflow E2E: {REAL_MARKER}", flush=True)
    patch_engines()
    engine = bounded_db_engine()
    try:
        with engine.begin() as conn:
            org_id = setup_reference_data(conn)
    finally:
        engine.dispose()

    refs = setup_business_reference_data(org_id)
    try:
        run_real_adapter_tests(org_id, refs)
    finally:
        cleanup_business_rows(org_id)

    passed = sum(1 for result in RESULTS if result["status"] == "PASS")
    failed = len(RESULTS) - passed
    print(json.dumps(
        {
            "run_marker": REAL_MARKER,
            "organization_id": org_id,
            "reference_ids": refs,
            "passed": passed,
            "failed": failed,
            "results": RESULTS,
            "business_row_cleanup": "attempted",
        },
        indent=2,
        default=str,
    ))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
