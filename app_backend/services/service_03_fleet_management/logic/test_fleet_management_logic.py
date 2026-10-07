from datetime import datetime, timedelta, timezone
import time

from app_backend.services.service_03_fleet_management.logic.fleet_master_create_data import create_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_master_get_data import get_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_master_update_data import update_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_master_delete_data import delete_fleet_vehicle
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_create_data import create_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_get_data import get_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_update_data import update_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_delete_data import delete_fleet_driver_allocation
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_create_data import create_fleet_status_history
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_get_data import get_fleet_status_history
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_update_data import update_fleet_status_history
from app_backend.services.service_03_fleet_management.logic.fleet_status_history_delete_data import delete_fleet_status_history
from app_backend.services.service_02_hr_payroll.logic.employee_master_create_data import create_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_get_data import get_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_delete_data import delete_employee_master


TEST_ORG_ID = 1
TEST_USER_ID = 1
TEST_USER_PRINCIPAL_NAME = "system_admin"
TEST_RUN_ID = int(time.time())


def print_runtime(program_name, start_time):
    end_time = time.perf_counter()
    print(f"Runtime of {program_name}: {(end_time - start_time):.2f} seconds.")


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_datetime(value):
    return value.isoformat()


def get_created_fleet_vehicle_id(vehicle_code):
    df_fleet_master, json_fleet_master = get_fleet_vehicle(
        {"fleet_org_id_fk": TEST_ORG_ID}
    )
    print(f"fleet_master data in dataframe:\n{df_fleet_master}")
    print(f"fleet_master data in json:\n{json_fleet_master}")

    df_created_vehicle = df_fleet_master[
        df_fleet_master["vehicle_code"].eq(vehicle_code)
    ]
    if df_created_vehicle.empty:
        return None

    return int(df_created_vehicle.iloc[-1]["fleet_vehicle_id_pk"])


def get_created_driver_allocation_id(fleet_vehicle_id):
    df_driver_allocation, json_driver_allocation = get_fleet_driver_allocation(
        {"fleet_org_id_fk": TEST_ORG_ID}
    )
    print(f"fleet_driver_allocation data in dataframe:\n{df_driver_allocation}")
    print(f"fleet_driver_allocation data in json:\n{json_driver_allocation}")

    df_created_allocation = df_driver_allocation[
        df_driver_allocation["fleet_vehicle_id_fk"].eq(fleet_vehicle_id)
    ]
    if df_created_allocation.empty:
        return None

    return int(df_created_allocation.iloc[-1]["fleet_driver_allocation_id_pk"])


def get_created_status_history_id(fleet_vehicle_id):
    df_status_history, json_status_history = get_fleet_status_history(
        {"fleet_org_id_fk": TEST_ORG_ID}
    )
    print(f"fleet_status_history data in dataframe:\n{df_status_history}")
    print(f"fleet_status_history data in json:\n{json_status_history}")

    df_created_status_history = df_status_history[
        df_status_history["fleet_vehicle_id_fk"].eq(fleet_vehicle_id)
    ]
    if df_created_status_history.empty:
        return None

    return int(df_created_status_history.iloc[-1]["fleet_status_history_id_pk"])


def get_created_empl_id(employee_id):
    df_employee_master, json_employee_master = get_employee_master(
        {"empl_org_id_fk": TEST_ORG_ID}
    )
    print(f"employee_master data in dataframe:\n{df_employee_master}")
    print(f"employee_master data in json:\n{json_employee_master}")

    df_created_employee = df_employee_master[
        df_employee_master["employee_id"].eq(employee_id)
    ]
    if df_created_employee.empty:
        return None

    return int(df_created_employee.iloc[-1]["empl_id_pk"])


# ============================================================
# employee_master test driver dependency
# ============================================================

driver_employee_id = f"TEST-FLEET-DRIVER-{TEST_RUN_ID}"

create_driver_employee_payload = {
    "empl_org_id_fk": TEST_ORG_ID,
    "secondary_key_dept": 1,
    "employee_id": driver_employee_id,
    "employee_name": "Test Fleet Driver",
    "employee_designation": "Driver",
    "joining_date": "2026-01-01",
    "nationality": "United Arab Emirates",
    "passport_number": f"FLEETPASS{TEST_RUN_ID}",
    "passport_expiry_date": "2031-12-31",
    "passport_issuing_country": "United Arab Emirates",
    "passport_with": "employee",
    "visa_number": f"FLEETVISA{TEST_RUN_ID}",
    "visa_expiry_date": "2028-12-31",
    "emirates_id_number": f"FLEETEID{TEST_RUN_ID}",
    "emirates_id_expiry_date": "2029-12-31",
    "phone_number_company": f"+971520{TEST_RUN_ID}",
    "phone_number_personal": f"+971521{TEST_RUN_ID}",
    "phone_number_home": None,
    "email_id_company": f"test.fleet.driver.{TEST_RUN_ID}@urbanexpress.ae",
    "email_id_personal": f"test.fleet.driver.{TEST_RUN_ID}@example.com",
    "address_in_base_location": "Dubai test base address",
    "address_in_home_location": "Dubai test home address",
    "driver_licence_number": f"FLEETDL{TEST_RUN_ID}",
    "driver_licence_expiry_date": "2030-12-31",
    "driver_licence_type": "light vehicle",
    "permit_number": f"FLEETPERMIT{TEST_RUN_ID}",
    "permit_expiry_date": "2028-12-31",
    "permit_type": "driver permit",
    "insurance_number": f"FLEETINS{TEST_RUN_ID}",
    "insurance_expiry_date": "2027-12-31",
    "bank_name": "Test Bank",
    "bank_address": "Dubai test bank address",
    "bank_account_number": f"FLEETACC{TEST_RUN_ID}",
    "monthly_basic_salary": 5000.00,
    "monthly_allowance": 1000.00,
    "monthly_accomodation": 1500.00,
    "field_flex_field_1": "fleet_management_driver_dependency_test",
    "field_flex_field_2": None,
    "field_flex_field_3": None,
    "field_flex_field_4": None,
    "created_by": TEST_USER_ID,
    "updated_by": TEST_USER_ID,
    "employee_image_path": None,
    "reporting_to_employee_id": TEST_USER_ID
}

start_time = time.perf_counter()
print(create_employee_master(payload=create_driver_employee_payload))
print_runtime("create employee_master driver dependency program", start_time)

start_time = time.perf_counter()
test_driver_empl_id = get_created_empl_id(driver_employee_id)
print(f"Created driver empl_id_pk: {test_driver_empl_id}")
print_runtime("get employee_master driver dependency program", start_time)


# ============================================================
# fleet_master table: create, get, update and delete tests
# ============================================================

vehicle_code = f"TEST-FLEET-{TEST_RUN_ID}"

create_fleet_master_payload = {
    "user_principal_name": TEST_USER_PRINCIPAL_NAME,
    "fleet_org_id_fk": TEST_ORG_ID,
    "vehicle_code": vehicle_code,
    "fleet_type": "van",
    "fleet_category": "corporate",
    "vehicle_brand_name": "Toyota",
    "vehicle_model_name": "Hiace",
    "vehicle_model_year": 2024,
    "vehicle_color": "White",
    "vehicle_total_seats_including_driver": 13,
    "vehicle_plate_number": f"TST{TEST_RUN_ID}",
    "vehicle_plate_emirate": "Dubai",
    "vehicle_chassis_number": f"CHASSIS{TEST_RUN_ID}",
    "vehicle_engine_number": f"ENGINE{TEST_RUN_ID}",
    "mulkiya_number": f"MULKIYA{TEST_RUN_ID}",
    "mulkiya_expiry_date": "2027-12-31",
    "salik_tag_number": f"SALIK{TEST_RUN_ID}",
    "vehicle_insurance_provider": "Test Insurance Provider",
    "vehicle_insurance_number": f"INS{TEST_RUN_ID}",
    "vehicle_insurance_type": "comprehensive",
    "vehicle_insurance_start_date": "2026-01-01",
    "vehicle_insurance_expiry_date": "2027-01-01",
    "current_odometer_km": 100.0,
    "odometer_last_updated_at": iso_datetime(utc_now()),
    "vehicle_operational_status": "available",
    "vehicle_deployment_status": "unassigned",
    "vehicle_ownership_type": "owned",
    "vehicle_owner_legal_entity": "UE Transport Test Entity",
    "vehicle_acquisition_date": "2026-01-01",
    "vehicle_acquisition_cost_aed": 125000.00,
    "depreciation_start_date": "2026-01-01",
    "depreciation_method": "straight_line",
    "useful_life_months": 60,
    "residual_value_aed": 10000.00,
    "field_flex_field_1": "fleet_master_create_test",
    "field_flex_field_2": None,
    "field_flex_field_3": None,
    "field_flex_field_4": None
}

start_time = time.perf_counter()
print(create_fleet_vehicle(payload=create_fleet_master_payload))
print_runtime("create fleet_master program", start_time)

start_time = time.perf_counter()
fleet_vehicle_id = get_created_fleet_vehicle_id(vehicle_code)
print(f"Created fleet_vehicle_id_pk: {fleet_vehicle_id}")
print_runtime("get fleet_master program", start_time)

if fleet_vehicle_id:
    update_fleet_master_payload = {
        **create_fleet_master_payload,
        "fleet_vehicle_id": fleet_vehicle_id,
        "vehicle_color": "Silver",
        "current_odometer_km": 250.0,
        "vehicle_operational_status": "allocated",
        "vehicle_deployment_status": "client_assigned",
        "field_flex_field_1": "fleet_master_update_test"
    }

    start_time = time.perf_counter()
    print(update_fleet_vehicle(payload=update_fleet_master_payload))
    print_runtime("update fleet_master program", start_time)


# ============================================================
# fleet_driver_allocation table: create, get, update and delete tests
# ============================================================

allocation_start = utc_now()
allocation_end = allocation_start + timedelta(hours=8)

if fleet_vehicle_id and test_driver_empl_id:
    create_driver_allocation_payload = {
        "fleet_org_id_fk": TEST_ORG_ID,
        "fleet_vehicle_id_fk": fleet_vehicle_id,
        "fleet_driver_empl_id_fk": test_driver_empl_id,
        "allocation_start_datetime": iso_datetime(allocation_start),
        "allocation_end_datetime": iso_datetime(allocation_end),
        "allocation_status": "scheduled",
        "route_id_fk": None,
        "shift_id_fk": None,
        "allocation_reason": "Fleet logic test allocation",
        "deallocation_reason": None,
        "created_by": TEST_USER_ID,
        "updated_by": TEST_USER_ID
    }

    start_time = time.perf_counter()
    print(create_fleet_driver_allocation(payload=create_driver_allocation_payload))
    print_runtime("create fleet_driver_allocation program", start_time)

    start_time = time.perf_counter()
    fleet_driver_allocation_id = get_created_driver_allocation_id(fleet_vehicle_id)
    print(f"Created fleet_driver_allocation_id_pk: {fleet_driver_allocation_id}")
    print_runtime("get fleet_driver_allocation program", start_time)

    if fleet_driver_allocation_id:
        update_driver_allocation_payload = {
            "fleet_org_id_fk": TEST_ORG_ID,
            "fleet_driver_allocation_id": fleet_driver_allocation_id,
            "allocation_status": "completed",
            "allocation_end_datetime": iso_datetime(allocation_end),
            "deallocation_reason": "Fleet logic test completed",
            "updated_by": TEST_USER_ID
        }

        start_time = time.perf_counter()
        print(update_fleet_driver_allocation(payload=update_driver_allocation_payload))
        print_runtime("update fleet_driver_allocation program", start_time)


# ============================================================
# fleet_status_history table: create, get, update and delete tests
# ============================================================

status_effective_from = utc_now()

if fleet_vehicle_id:
    create_status_history_payload = {
        "fleet_org_id_fk": TEST_ORG_ID,
        "fleet_vehicle_id_fk": fleet_vehicle_id,
        "previous_vehicle_status": None,
        "new_vehicle_status": "available",
        "status_effective_from": iso_datetime(status_effective_from),
        "status_effective_to": None,
        "status_change_source": "manual",
        "status_change_reason": "Fleet logic test status create",
        "status_change_remarks": "Created by test_fleet_management_logic.py",
        "related_maintenance_order_id_fk": None,
        "changed_by": TEST_USER_ID,
        "changed_at": iso_datetime(status_effective_from),
        "field_flex_field_1": "fleet_status_history_create_test",
        "field_flex_field_2": None,
        "field_flex_field_3": None,
        "field_flex_field_4": None,
        "created_by": TEST_USER_ID,
        "updated_by": TEST_USER_ID
    }

    start_time = time.perf_counter()
    print(create_fleet_status_history(payload=create_status_history_payload))
    print_runtime("create fleet_status_history program", start_time)

    start_time = time.perf_counter()
    fleet_status_history_id = get_created_status_history_id(fleet_vehicle_id)
    print(f"Created fleet_status_history_id_pk: {fleet_status_history_id}")
    print_runtime("get fleet_status_history program", start_time)

    if fleet_status_history_id:
        update_status_history_payload = {
            **create_status_history_payload,
            "fleet_status_history_id": fleet_status_history_id,
            "previous_vehicle_status": "available",
            "new_vehicle_status": "allocated",
            "status_change_reason": "Fleet logic test status update",
            "status_change_remarks": "Updated by test_fleet_management_logic.py",
            "field_flex_field_1": "fleet_status_history_update_test"
        }

        start_time = time.perf_counter()
        print(update_fleet_status_history(payload=update_status_history_payload))
        print_runtime("update fleet_status_history program", start_time)


# ============================================================
# Delete test records in child-to-parent order
# ============================================================

if "fleet_driver_allocation_id" in locals() and fleet_driver_allocation_id:
    delete_driver_allocation_payload = {
        "fleet_org_id_fk": TEST_ORG_ID,
        "fleet_driver_allocation_id": fleet_driver_allocation_id
    }

    start_time = time.perf_counter()
    print(delete_fleet_driver_allocation(payload=delete_driver_allocation_payload))
    print_runtime("delete fleet_driver_allocation program", start_time)

if "fleet_status_history_id" in locals() and fleet_status_history_id:
    delete_status_history_payload = {
        "fleet_status_history_id": fleet_status_history_id
    }

    start_time = time.perf_counter()
    print(delete_fleet_status_history(payload=delete_status_history_payload))
    print_runtime("delete fleet_status_history program", start_time)

if fleet_vehicle_id:
    delete_fleet_master_payload = {
        "user_principal_name": TEST_USER_PRINCIPAL_NAME,
        "fleet_vehicle_id": fleet_vehicle_id
    }

    start_time = time.perf_counter()
    print(delete_fleet_vehicle(payload=delete_fleet_master_payload))
    print_runtime("delete fleet_master program", start_time)

if "test_driver_empl_id" in locals() and test_driver_empl_id:
    delete_driver_employee_payload = {
        "empl_id": test_driver_empl_id,
        "empl_org_id_fk": TEST_ORG_ID
    }

    start_time = time.perf_counter()
    print(delete_employee_master(payload=delete_driver_employee_payload))
    print_runtime("delete employee_master driver dependency program", start_time)
