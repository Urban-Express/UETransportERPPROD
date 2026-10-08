from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    has_trusted_workflow_document_path,
    strip_untrusted_file_pointer_fields,
)
from app_backend.services.service_06_contracts_management.logic.contracts_management_create_data import (
    CONTRACT_DAY_KM_FIELDS,
    contract_number_exists,
    get_contract_insert_params,
    stage_contract_document_for_workflow,
    validate_contract_references_for_organization,
)
from app_backend.services.service_07_alerts_wf_engine.contracts_management_wf import (
    is_contracts_management_workflow_approved,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission,
    pending_workflow_submission_response,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    cleanup_staged_workflow_document,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_security import (
    is_trusted_workflow_execution,
)


def update_contracts_management(
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
        cont_id = (
            payload.get("cont_id")
            or payload.get("cont_id_pk")
        )
        cont_org_id_fk = payload.get("cont_org_id_fk") or payload.get("authenticated_org_id")

        if not cont_id:
            return {"error": "cont_id is required."}

        get_cont_id = text("""
            select cont_id_pk, cont_org_id_fk, cont_contract_number, cont_link_path
            from contracts_management
            where cont_id_pk = :cont_id
            and cont_org_id_fk = :cont_org_id_fk
        """)

        params_get = {
            "cont_id": cont_id,
            "cont_org_id_fk": cont_org_id_fk
        }
        if conn is not None:
            df_cont_id = pd.read_sql(sql=get_cont_id, con=conn, params=params_get)
        else:
            with contract_engine.begin() as contract_conn:
                df_cont_id = pd.read_sql(sql=get_cont_id, con=contract_conn, params=params_get)

        if df_cont_id.empty:
            return {"error": "Contract ID not found."}

        existing_contract = df_cont_id.iloc[0].to_dict()
        reference_error = validate_contract_references_for_organization(
            payload,
            cont_org_id_fk,
            contract_engine,
            conn=conn,
        )
        if reference_error:
            return reference_error

        if contract_number_exists(
            cont_org_id_fk,
            payload.get("cont_contract_number"),
            exclude_cont_id=cont_id,
            conn=conn,
        ):
            return {"error": f"Contract already exists: {payload.get('cont_contract_number')}"}

        payload, staging_error = stage_contract_document_for_workflow(
            {**payload, "cont_id": cont_id},
            file_stream=file_stream,
            file_name=file_name,
            content_type=content_type,
            require_contract=True,
            workflow_action="UPDATE",
        )
        if staging_error:
            return staging_error
        staged_document_uploaded = bool(
            file_stream
            and payload.get("workflow_document_staging_status") == "STAGED"
        )
        update_document_pointer = has_trusted_workflow_document_path(
            payload,
            "cont_link_path",
            staged_in_current_request=staged_document_uploaded,
            trusted_workflow_execution=is_trusted_workflow_execution(),
        )
        if not update_document_pointer:
            payload = strip_untrusted_file_pointer_fields(payload, "cont_link_path")

        workflow_payload = {
            **payload,
            "cont_id": cont_id,
            "cont_id_pk": cont_id,
            "cont_org_id_fk": existing_contract.get("cont_org_id_fk"),
        }
        workflow_approved, workflow_response = is_contracts_management_workflow_approved(
            payload=workflow_payload,
            workflow_action="UPDATE"
        )
        if not workflow_approved:
            if is_pending_workflow_submission(workflow_response):
                workflow_pending_created = True
                return pending_workflow_submission_response(
                    workflow_response,
                    "Contract update submitted for approval.",
                    domain_reference_id=cont_id,
                )
            cleanup_result = cleanup_staged_workflow_document(payload, force=True)
            return {
                "error": "Contract update blocked by workflow.",
                "workflow": workflow_response,
                "document_cleanup": cleanup_result,
            }

        payload = {
            **payload,
            "cont_approval_status": workflow_response.get("workflow_status")
        }

        document_pointer_set_clause = (
            "                cont_link_path = :cont_link_path,\n"
            if update_document_pointer
            else ""
        )
        # Omitted fields remain untouched at execution; explicit None clears them.
        day_km_set_clause = "".join(
            f"                {field} = :{field},\n"
            for field in CONTRACT_DAY_KM_FIELDS
            if field in payload
        )

        update_contracts_management_query = text(f"""
            update contracts_management
            set
                cont_org_id_fk = :cont_org_id_fk,
                cont_cust_id_fk = :cont_cust_id_fk,
                cont_dep_id_fk = :cont_dep_id_fk,
                cont_contract_number = :cont_contract_number,
                cont_contract_name = :cont_contract_name,
                cont_start_date = :cont_start_date,
                cont_end_date = :cont_end_date,
                cont_status = :cont_status,
                cont_revenue_basis = :cont_revenue_basis,
                cont_currency_code = :cont_currency_code,
                cont_no_of_passengers = :cont_no_of_passengers,
                cont_big_bus_count_gt_34 = :cont_big_bus_count_gt_34,
                cont_medium_bus_count_17_34 = :cont_medium_bus_count_17_34,
                cont_small_bus_count_lt_17 = :cont_small_bus_count_lt_17,
                cont_per_passenger_rate_pm = :cont_per_passenger_rate_pm,
                cont_big_bus_rate_pm = :cont_big_bus_rate_pm,
                cont_medium_bus_rate_pm = :cont_medium_bus_rate_pm,
                cont_small_bus_rate_pm = :cont_small_bus_rate_pm,
                cont_no_of_billing_months = :cont_no_of_billing_months,
                cont_no_of_work_days_per_week = :cont_no_of_work_days_per_week,
                cont_no_of_round_trips_per_day = :cont_no_of_round_trips_per_day,
                cont_driver_responsibility_party = :cont_driver_responsibility_party,
                cont_driver_accommodation_resp_party = :cont_driver_accommodation_resp_party,
                cont_fuel_responsibility_party = :cont_fuel_responsibility_party,
                cont_salik_responsibility_party = :cont_salik_responsibility_party,
                cont_permit_responsibility_party = :cont_permit_responsibility_party,
                cont_no_of_free_trips_per_month = :cont_no_of_free_trips_per_month,
                cont_extra_trip_charge = :cont_extra_trip_charge,
                cont_km_cap_pm_per_bus = :cont_km_cap_pm_per_bus,
                cont_extra_km_charge_per_km = :cont_extra_km_charge_per_km,
{day_km_set_clause}\
                total_contract_value = :total_contract_value,
                cont_notes = :cont_notes,
{document_pointer_set_clause}\
                cont_approval_status = :cont_approval_status,
                updated_by = :updated_by,
                updated_at = CURRENT_TIMESTAMP
            where cont_id_pk = :cont_id
            and cont_org_id_fk = :cont_org_id_fk
            returning cont_id_pk, cont_link_path
        """)

        params_update = {
            **get_contract_insert_params(payload),
            "cont_id": cont_id,
            "cont_org_id_fk": cont_org_id_fk
        }
        if not update_document_pointer:
            params_update.pop("cont_link_path", None)

        if conn is not None:
            updated_contract = conn.execute(
                update_contracts_management_query,
                params_update,
            ).mappings().one_or_none()
        else:
            with contract_engine.begin() as contract_conn:
                updated_contract = contract_conn.execute(
                    update_contracts_management_query,
                    params_update,
                ).mappings().one_or_none()
        if not updated_contract:
            return {"error": "Contract ID not found at execution time."}

        return {
            "message": f"Successfully updated contract: {payload.get('cont_contract_number')}",
            "cont_id_pk": cont_id,
            "cont_contract_number": payload.get("cont_contract_number"),
            "cont_link_path": updated_contract["cont_link_path"],
            "cont_approval_status": params_update.get("cont_approval_status"),
            "business_operation_executed": True,
            "workflow_required": True,
            "workflow": workflow_response
        }

    except Exception as e:
        response = {"error": f"Failed to update contract. Error Message: {str(e)}"}
        if not locals().get("workflow_pending_created"):
            cleanup_result = cleanup_staged_workflow_document(locals().get("payload"), force=True)
            if cleanup_result.get("cleanup_attempted"):
                response["document_cleanup"] = cleanup_result
        return response
    finally:
        if contract_engine is not None:
            contract_engine.dispose()


def update_contract(
    payload: dict,
    file_stream=None,
    file_name: str | None = None,
    content_type: str | None = None,
    conn=None,
):
    return update_contracts_management(payload, file_stream, file_name, content_type, conn)
