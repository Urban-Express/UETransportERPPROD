import time

from app_backend.services.service_06_contracts_management.logic.customer_master_create_data import create_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_get_data import get_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_update_data import update_customer_master
from app_backend.services.service_06_contracts_management.logic.customer_master_delete_data import delete_customer_master


TEST_ORG_ID = 1
TEST_USER = "SYSTEM_TEST"
TEST_RUN_ID = int(time.time())


def print_runtime(program_name, start_time):
    end_time = time.perf_counter()
    print(f"Runtime of {program_name}: {(end_time - start_time):.2f} seconds.")


def get_created_cust_id(cust_code):
    df_customer_master, json_customer_master = get_customer_master()
    print(f"customer_master data in dataframe:\n{df_customer_master}")
    print(f"customer_master data in json:\n{json_customer_master}")

    if "cust_code" not in df_customer_master.columns:
        print("cust_code column not found in customer_master dataframe.")
        return None

    df_created_customer = df_customer_master[
        df_customer_master["cust_code"].eq(cust_code)
    ]
    if df_created_customer.empty:
        return None

    return int(df_created_customer.iloc[-1]["cust_id_pk"])


# ============================================================
# customer_master table: create, get, update and delete tests
# ============================================================

cust_code = f"TEST-CUST-{TEST_RUN_ID}"

create_customer_master_payload = {
    "cust_org_id_fk": TEST_ORG_ID,
    "cust_code": cust_code,
    "cust_name": "Test Customer LLC",
    "cust_category": "Corporate",
    "cust_status": "ACTIVE",
    "cust_contact_person_name": "Test Contact",
    "cust_contact_person_designation": "Operations Manager",
    "cust_phone_primary": f"+97150{TEST_RUN_ID}",
    "cust_phone_secondary": f"+97151{TEST_RUN_ID}",
    "cust_email_primary": f"test.customer.{TEST_RUN_ID}@example.com",
    "cust_email_secondary": f"test.customer.secondary.{TEST_RUN_ID}@example.com",
    "cust_billing_address": "Dubai test billing address",
    "cust_service_address": "Dubai test service address",
    "cust_tax_registration_number": f"TRN{TEST_RUN_ID}",
    "cust_credit_period_days": 30,
    "cust_notes": "customer_master_create_test",
    "created_by": TEST_USER,
    "updated_by": TEST_USER
}

start_time = time.perf_counter()
print(create_customer_master(payload=create_customer_master_payload))
print_runtime("create customer_master program", start_time)

start_time = time.perf_counter()
cust_id = get_created_cust_id(cust_code)
print(f"Created cust_id_pk: {cust_id}")
print_runtime("get customer_master program", start_time)

if cust_id:
    update_customer_master_payload = {
        **create_customer_master_payload,
        "cust_id": cust_id,
        "cust_name": "Updated Test Customer LLC",
        "cust_category": "Key Account",
        "cust_contact_person_name": "Updated Test Contact",
        "cust_phone_primary": f"+97152{TEST_RUN_ID}",
        "cust_credit_period_days": 45,
        "cust_notes": "customer_master_update_test"
    }

    start_time = time.perf_counter()
    print(update_customer_master(payload=update_customer_master_payload))
    print_runtime("update customer_master program", start_time)


# ============================================================
# Delete test records
# ============================================================

if cust_id:
    delete_customer_master_payload = {
        "cust_id": cust_id
    }

    start_time = time.perf_counter()
    print(delete_customer_master(payload=delete_customer_master_payload))
    print_runtime("delete customer_master program", start_time)
