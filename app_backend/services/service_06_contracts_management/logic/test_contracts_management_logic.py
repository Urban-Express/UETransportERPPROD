import os
import tempfile
import time

from app_backend.services.service_06_contracts_management.integrations.firebase_contract_document_download import download_contract_document
from app_backend.services.service_06_contracts_management.logic.contracts_management_create_data import create_contract
from app_backend.services.service_06_contracts_management.logic.contracts_management_delete_data import delete_contract
from app_backend.services.service_06_contracts_management.logic.contracts_management_get_data import get_contract
from app_backend.services.service_06_contracts_management.logic.contracts_management_update_data import update_contract
from app_backend.services.service_06_contracts_management.logic.customer_master_create_data import create_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_delete_data import delete_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_get_data import get_customer_master


TEST_ORG_ID = 1
TEST_DEPT_ID = 1
TEST_USER = "SYSTEM_TEST"
TEST_USER_PRINCIPAL_NAME = "system_admin"
TEST_RUN_ID = int(time.time())


def print_runtime(program_name, start_time):
    end_time = time.perf_counter()
    print(f"Runtime of {program_name}: {(end_time - start_time):.2f} seconds.")


def create_test_contract_file(file_name):
    test_directory = os.path.join(
        tempfile.gettempdir(),
        "ue_transport_erp_contract_document_tests"
    )
    os.makedirs(test_directory, exist_ok=True)
    test_file_path = os.path.join(test_directory, file_name)

    with open(test_file_path, "wb") as test_file:
        test_file.write(
            b"%PDF-1.4\n"
            b"1 0 obj\n"
            b"<< /Type /Catalog /Pages 2 0 R >>\n"
            b"endobj\n"
            b"2 0 obj\n"
            b"<< /Type /Pages /Kids [] /Count 0 >>\n"
            b"endobj\n"
            b"trailer\n"
            b"<< /Root 1 0 R >>\n"
            b"%%EOF\n"
        )

    return test_file_path


def get_created_customer_id(cust_code):
    df_customer_master, json_customer_master = get_customer_master()
    print(f"customer_master data in dataframe:\n{df_customer_master}")
    print(f"customer_master data in json:\n{json_customer_master}")

    if "cust_code" not in df_customer_master.columns:
        return None

    df_created_customer = df_customer_master[
        df_customer_master["cust_code"].eq(cust_code)
    ]
    if df_created_customer.empty:
        return None

    return int(df_created_customer.iloc[-1]["cust_id_pk"])


def get_created_contract_id(cont_contract_number):
    df_contracts_management, json_contracts_management = get_contract()
    print(f"contracts_management data in dataframe:\n{df_contracts_management}")
    print(f"contracts_management data in json:\n{json_contracts_management}")

    if "cont_contract_number" not in df_contracts_management.columns:
        return None

    df_created_contract = df_contracts_management[
        df_contracts_management["cont_contract_number"].eq(cont_contract_number)
    ]
    if df_created_contract.empty:
        return None

    return int(df_created_contract.iloc[-1]["cont_id_pk"])


# ============================================================
# contracts_management table: create, get, update and delete tests
# ============================================================

cust_code = f"TEST-CONTRACT-CUST-{TEST_RUN_ID}"
cont_contract_number = f"TEST-CONTRACT-{TEST_RUN_ID}"
cust_id = None
cont_id = None

create_customer_payload = {
    "cust_org_id_fk": TEST_ORG_ID,
    "cust_code": cust_code,
    "cust_name": "Test Contract Customer LLC",
    "cust_category": "Contract Test Customer",
    "cust_status": "ACTIVE",
    "created_by": TEST_USER,
    "updated_by": TEST_USER
}

start_time = time.perf_counter()
print(create_customer_master(payload=create_customer_payload))
print_runtime("create customer_master dependency program", start_time)

start_time = time.perf_counter()
cust_id = get_created_customer_id(cust_code)
print(f"Created dependency cust_id_pk: {cust_id}")
print_runtime("get customer_master dependency program", start_time)

if cust_id:
    create_file_path = create_test_contract_file(
        f"{cont_contract_number}_create.pdf"
    )
    create_contract_payload = {
        "user_principal_name": TEST_USER_PRINCIPAL_NAME,
        "cont_org_id_fk": TEST_ORG_ID,
        "cont_cust_id_fk": cust_id,
        "cont_dep_id_fk": TEST_DEPT_ID,
        "cont_contract_number": cont_contract_number,
        "cont_contract_name": "Test Contract",
        "cont_start_date": "2026-01-01",
        "cont_end_date": "2026-12-31",
        "cont_status": "DRAFT",
        "cont_revenue_basis": "PER_BUS",
        "cont_currency_code": "AED",
        "cont_no_of_passengers": 0,
        "cont_big_bus_count_gt_34": 1,
        "cont_medium_bus_count_17_34": 0,
        "cont_small_bus_count_lt_17": 0,
        "cont_per_passenger_rate_pm": None,
        "cont_big_bus_rate_pm": 12000.00,
        "cont_medium_bus_rate_pm": None,
        "cont_small_bus_rate_pm": None,
        "cont_no_of_billing_months": 12,
        "cont_no_of_work_days_per_week": 5,
        "cont_no_of_round_trips_per_day": 2,
        "cont_driver_responsibility_party": "ORGANIZATION",
        "cont_driver_accommodation_resp_party": "ORGANIZATION",
        "cont_fuel_responsibility_party": "ORGANIZATION",
        "cont_salik_responsibility_party": "CUSTOMER",
        "cont_permit_responsibility_party": "ORGANIZATION",
        "cont_no_of_free_trips_per_month": 0,
        "cont_extra_trip_charge": 250.00,
        "cont_km_cap_pm_per_bus": 5000.00,
        "cont_extra_km_charge_per_km": 5.00,
        "cont_notes": "contracts_management_create_test",
        "file_path": create_file_path,
        "created_by": TEST_USER,
        "updated_by": TEST_USER
    }

    start_time = time.perf_counter()
    print(create_contract(payload=create_contract_payload))
    print_runtime("create contracts_management program", start_time)

    start_time = time.perf_counter()
    cont_id = get_created_contract_id(cont_contract_number)
    print(f"Created cont_id_pk: {cont_id}")
    print_runtime("get contracts_management program", start_time)

if cont_id:
    update_file_path = create_test_contract_file(
        f"{cont_contract_number}_update.pdf"
    )
    update_contract_payload = {
        **create_contract_payload,
        "cont_id": cont_id,
        "cont_contract_name": "Updated Test Contract",
        "cont_big_bus_rate_pm": 12500.00,
        "cont_extra_trip_charge": 300.00,
        "cont_notes": "contracts_management_update_test",
        "file_path": update_file_path
    }

    start_time = time.perf_counter()
    print(update_contract(payload=update_contract_payload))
    print_runtime("update contracts_management program", start_time)

    download_directory = os.path.join(
        tempfile.gettempdir(),
        "ue_transport_erp_contract_document_download_tests"
    )
    start_time = time.perf_counter()
    print(download_contract_document({
        "cont_id": cont_id,
        "download_directory": download_directory
    }))
    print_runtime("download contract document program", start_time)


# ============================================================
# Delete test records
# ============================================================

if cont_id:
    start_time = time.perf_counter()
    print(delete_contract(payload={"cont_id": cont_id}))
    print_runtime("delete contracts_management program", start_time)

if cust_id:
    start_time = time.perf_counter()
    print(delete_customer_master(payload={"cust_id": cust_id}))
    print_runtime("delete customer_master dependency program", start_time)
