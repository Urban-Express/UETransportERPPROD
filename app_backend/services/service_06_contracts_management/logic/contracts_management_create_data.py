from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    has_trusted_workflow_document_path,
)
from app_backend.services.service_06_contracts_management.integrations.firebase_contract_document_upload import (
    DEFAULT_CONTRACT_STORAGE_FOLDER,
    upload_contract_document_to_firebase,
)
from app_backend.services.service_07_alerts_wf_engine.contracts_management_wf import (
    is_contracts_management_workflow_approved,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    workflow_document_staging_storage_folder,
    is_pending_workflow_submission,
    pending_workflow_submission_response,
    strip_ephemeral_document_fields,
    workflow_document_stream_required_error,
    workflow_staged_document_payload,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    cleanup_staged_workflow_document,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    is_trusted_workflow_execution,
)


CONTRACT_EPHEMERAL_DOCUMENT_FIELDS = ("file_path", "contract_file_path")


def get_contract_insert_params(payload: dict):
    return {
        "cont_org_id_fk": payload.get("cont_org_id_fk"),
        "cont_cust_id_fk": payload.get("cont_cust_id_fk"),
        "cont_dep_id_fk": payload.get("cont_dep_id_fk"),
        "cont_contract_number": payload.get("cont_contract_number"),
        "cont_contract_name": payload.get("cont_contract_name"),
        "cont_start_date": payload.get("cont_start_date"),
        "cont_end_date": payload.get("cont_end_date"),
        "cont_status": payload.get("cont_status", "DRAFT"),
        "cont_revenue_basis": payload.get("cont_revenue_basis"),
        "cont_currency_code": payload.get("cont_currency_code", "AED"),
        "cont_no_of_passengers": payload.get("cont_no_of_passengers", 0),
        "cont_big_bus_count_gt_34": payload.get("cont_big_bus_count_gt_34", 0),
        "cont_medium_bus_count_17_34": payload.get("cont_medium_bus_count_17_34", 0),
        "cont_small_bus_count_lt_17": payload.get("cont_small_bus_count_lt_17", 0),
        "cont_per_passenger_rate_pm": payload.get("cont_per_passenger_rate_pm"),
        "cont_big_bus_rate_pm": payload.get("cont_big_bus_rate_pm"),
        "cont_medium_bus_rate_pm": payload.get("cont_medium_bus_rate_pm"),
        "cont_small_bus_rate_pm": payload.get("cont_small_bus_rate_pm"),
        "cont_no_of_billing_months": payload.get("cont_no_of_billing_months"),
        "cont_no_of_work_days_per_week": payload.get("cont_no_of_work_days_per_week"),
        "cont_no_of_round_trips_per_day": payload.get("cont_no_of_round_trips_per_day"),
        "cont_driver_responsibility_party": payload.get(
            "cont_driver_responsibility_party",
            "NOT_SPECIFIED"
        ),
        "cont_driver_accommodation_resp_party": payload.get(
            "cont_driver_accommodation_resp_party",
            "NOT_SPECIFIED"
        ),
        "cont_fuel_responsibility_party": payload.get(
            "cont_fuel_responsibility_party",
            "NOT_SPECIFIED"
        ),
        "cont_salik_responsibility_party": payload.get(
            "cont_salik_responsibility_party",
            "NOT_SPECIFIED"
        ),
        "cont_permit_responsibility_party": payload.get(
            "cont_permit_responsibility_party",
            "NOT_SPECIFIED"
        ),
        "cont_no_of_free_trips_per_month": payload.get(
            "cont_no_of_free_trips_per_month",
            0
        ),
        "cont_extra_trip_charge": payload.get("cont_extra_trip_charge", 0),
        "cont_km_cap_pm_per_bus": payload.get("cont_km_cap_pm_per_bus"),
        "cont_extra_km_charge_per_km": payload.get("cont_extra_km_charge_per_km"),
        "total_contract_value": payload.get("total_contract_value"),
        "cont_notes": payload.get("cont_notes"),
        "cont_link_path": payload.get("cont_link_path"),
        "cont_approval_status": payload.get("cont_approval_status"),
        "created_by": payload.get("created_by"),
        "updated_by": payload.get("updated_by")
    }


def validate_contract_references_for_organization(
    payload: dict,
    cont_org_id_fk,
    contract_engine=None,
    conn=None,
):
    if not cont_org_id_fk:
        return {"error": "cont_org_id_fk is required."}

    if conn is not None:
        return _validate_contract_references_in_conn(conn, payload, cont_org_id_fk)

    owns_engine = contract_engine is None
    contract_engine = contract_engine or db_engine()
    try:
        with contract_engine.begin() as contract_conn:
            return _validate_contract_references_in_conn(contract_conn, payload, cont_org_id_fk)
    finally:
        if owns_engine:
            contract_engine.dispose()

    return None


def _validate_contract_references_in_conn(conn, payload: dict, cont_org_id_fk):
    cont_cust_id_fk = payload.get("cont_cust_id_fk")
    if cont_cust_id_fk:
        customer_row = conn.execute(
            text("""
                select cust_id_pk
                from customer_master
                where cust_id_pk = :cont_cust_id_fk
                and cust_org_id_fk = :cont_org_id_fk
                limit 1
            """),
            {
                "cont_cust_id_fk": cont_cust_id_fk,
                "cont_org_id_fk": cont_org_id_fk,
            },
        ).first()
        if not customer_row:
            return {"error": "Customer ID not found."}

    cont_dep_id_fk = payload.get("cont_dep_id_fk")
    if cont_dep_id_fk:
        department_row = conn.execute(
            text("""
                select dep_id_pk
                from department_master
                where dep_id_pk = :cont_dep_id_fk
                and dep_org_id_fk = :cont_org_id_fk
                limit 1
            """),
            {
                "cont_dep_id_fk": cont_dep_id_fk,
                "cont_org_id_fk": cont_org_id_fk,
            },
        ).first()
        if not department_row:
            return {"error": "Department ID not found."}
    return None


def contract_number_exists(cont_org_id_fk, cont_contract_number, exclude_cont_id=None, conn=None):
    if not cont_contract_number or not cont_org_id_fk:
        return False
    get_contract_number = text("""
        select cont_id_pk
        from contracts_management
        where cont_org_id_fk = :cont_org_id_fk
        and lower(cont_contract_number) = lower(:cont_contract_number)
        and (:exclude_cont_id is null or cont_id_pk <> :exclude_cont_id)
    """)
    params = {
        "cont_org_id_fk": cont_org_id_fk,
        "cont_contract_number": cont_contract_number,
        "exclude_cont_id": exclude_cont_id,
    }
    if conn is not None:
        df_contract_number = pd.read_sql(sql=get_contract_number, con=conn, params=params)
    else:
        contract_engine = db_engine()
        try:
            with contract_engine.begin() as contract_conn:
                df_contract_number = pd.read_sql(sql=get_contract_number, con=contract_conn, params=params)
        finally:
            contract_engine.dispose()
    return not df_contract_number.empty


def stage_contract_document_for_workflow(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    require_contract: bool = False,
    workflow_action: str = "CREATE",
) -> tuple[dict | None, dict | None]:
    if file_stream:
        cont_org_id_fk = payload.get("cont_org_id_fk") or payload.get("authenticated_org_id")
        staging_payload = {
            **payload,
            "storage_folder": workflow_document_staging_storage_folder(
                DEFAULT_CONTRACT_STORAGE_FOLDER,
                "CONTRACTS_MANAGEMENT",
                workflow_action,
                cont_org_id_fk,
                payload,
            ),
        }
        upload_response = upload_contract_document_to_firebase(
            payload=staging_payload,
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            update_contract_link=False,
            require_contract=require_contract,
        )
        if upload_response.get("error"):
            return None, upload_response
        return workflow_staged_document_payload(
            payload,
            "cont_link_path",
            upload_response.get("cont_link_path"),
            upload_response,
            CONTRACT_EPHEMERAL_DOCUMENT_FIELDS,
        ), None

    stream_error = workflow_document_stream_required_error(
        payload,
        "cont_link_path",
        CONTRACT_EPHEMERAL_DOCUMENT_FIELDS,
        "Contract document",
    )
    if stream_error:
        return None, stream_error
    return strip_ephemeral_document_fields(payload, CONTRACT_EPHEMERAL_DOCUMENT_FIELDS), None


def create_contracts_management(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    contract_engine = None
    try:
        workflow_pending_created = False
        contract_engine = None if conn is not None else db_engine()
        cont_contract_number = payload.get("cont_contract_number")
        cont_org_id_fk = payload.get("cont_org_id_fk") or payload.get("authenticated_org_id")

        reference_error = validate_contract_references_for_organization(
            payload,
            cont_org_id_fk,
            contract_engine,
            conn=conn,
        )
        if reference_error:
            return reference_error

        if contract_number_exists(cont_org_id_fk, cont_contract_number, conn=conn):
            return {"error": f"Contract already exists: {cont_contract_number}"}

        payload, staging_error = stage_contract_document_for_workflow(
            payload,
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            require_contract=False,
            workflow_action="CREATE",
        )
        if staging_error:
            return staging_error

        staged_document_uploaded = bool(
            file_stream
            and payload.get("workflow_document_staging_status") == "STAGED"
        )
        if not has_trusted_workflow_document_path(
            payload,
            "cont_link_path",
            staged_in_current_request=staged_document_uploaded,
            trusted_workflow_execution=is_trusted_workflow_execution(),
        ):
            payload = {
                **payload,
                "cont_link_path": None,
            }

        workflow_approved, workflow_response = is_contracts_management_workflow_approved(
            payload=payload,
            workflow_action="CREATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                workflow_pending_created = True
                return pending_workflow_submission_response(
                    workflow_response,
                    "Contract creation submitted for approval.",
                )
            cleanup_result = cleanup_staged_workflow_document(payload, force=True)
            return {
                "error": "Contract creation blocked by workflow.",
                "workflow": workflow_response,
                "document_cleanup": cleanup_result,
            }

        payload = {
            **payload,
            "cont_approval_status": workflow_response.get("workflow_status")
        }

        insert_into_contracts_management = text("""
            insert into contracts_management(
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
                cont_link_path,
                cont_approval_status,
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
                :cont_link_path,
                :cont_approval_status,
                :created_by,
                :updated_by
            )
            returning cont_id_pk
        """)

        params_insert = {
            **get_contract_insert_params(payload),
            "cont_org_id_fk": cont_org_id_fk
        }

        if conn is not None:
            cont_id = conn.execute(
                insert_into_contracts_management,
                params_insert
            ).scalar_one()
        else:
            with contract_engine.begin() as contract_conn:
                cont_id = contract_conn.execute(
                    insert_into_contracts_management,
                    params_insert
                ).scalar_one()

        return {
            "message": f"Successfully created contract: {cont_contract_number}",
            "cont_id_pk": cont_id,
            "cont_contract_number": cont_contract_number,
            "cont_link_path": params_insert.get("cont_link_path"),
            "cont_approval_status": params_insert.get("cont_approval_status"),
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        response = {"error": f"Failed to create contract. Error Message: {str(e)}"}
        if not locals().get("workflow_pending_created"):
            cleanup_result = cleanup_staged_workflow_document(locals().get("payload"), force=True)
            if cleanup_result.get("cleanup_attempted"):
                response["document_cleanup"] = cleanup_result
        return response
    finally:
        if contract_engine is not None:
            contract_engine.dispose()


def create_contract(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    return create_contracts_management(payload, file_stream, file_name, content_type, conn)
