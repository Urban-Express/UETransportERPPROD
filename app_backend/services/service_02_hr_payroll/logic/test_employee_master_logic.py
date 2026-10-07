import base64
import os
import tempfile
import time

from app_backend.services.service_02_hr_payroll.logic.employee_master_create_data import create_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_get_data import get_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_update_data import update_employee_master
from app_backend.services.service_02_hr_payroll.logic.employee_master_delete_data import delete_employee_master
from app_backend.services.service_02_hr_payroll.integrations.firebase_employee_image_upload import upload_employee_image
from app_backend.services.service_02_hr_payroll.integrations.firebase_employee_image_download import download_employee_image


TEST_ORG_ID = 1
TEST_DEPT_ID = 1
TEST_USER_ID = 1
TEST_RUN_ID = int(time.time())


def print_runtime(program_name, start_time):
    end_time = time.perf_counter()
    print(f"Runtime of {program_name}: {(end_time - start_time):.2f} seconds.")


def get_created_empl_id(employee_id):
    df_employee_master, json_employee_master = get_employee_master()
    print(f"employee_master data in dataframe:\n{df_employee_master}")
    print(f"employee_master data in json:\n{json_employee_master}")

    df_created_employee = df_employee_master[
        df_employee_master["employee_id"].eq(employee_id)
    ]
    if df_created_employee.empty:
        return None

    return int(df_created_employee.iloc[-1]["empl_id_pk"])


def create_test_image_file(employee_id):
    test_image_directory = os.path.join(
        tempfile.gettempdir(),
        "ue_transport_erp_employee_image_tests"
    )
    os.makedirs(test_image_directory, exist_ok=True)

    test_image_path = os.path.join(
        test_image_directory,
        f"{employee_id}.png"
    )

    one_pixel_png_base64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )

    with open(test_image_path, "wb") as test_image:
        test_image.write(base64.b64decode(one_pixel_png_base64))

    return test_image_path


# ============================================================
# employee_master table: create, get, update and delete tests
# ============================================================

employee_id = f"TEST-EMP-{TEST_RUN_ID}"

create_employee_master_payload = {
    "empl_org_id_fk": TEST_ORG_ID,
    "secondary_key_dept": TEST_DEPT_ID,
    "employee_id": employee_id,
    "employee_name": "Test Employee",
    "employee_designation": "Test Driver",
    "joining_date": "2026-01-01",
    "nationality": "United Arab Emirates",
    "passport_number": f"PASS{TEST_RUN_ID}",
    "passport_expiry_date": "2031-12-31",
    "passport_issuing_country": "United Arab Emirates",
    "passport_with": "employee",
    "visa_number": f"VISA{TEST_RUN_ID}",
    "visa_expiry_date": "2028-12-31",
    "emirates_id_number": f"EID{TEST_RUN_ID}",
    "emirates_id_expiry_date": "2029-12-31",
    "phone_number_company": f"+971500{TEST_RUN_ID}",
    "phone_number_personal": f"+971501{TEST_RUN_ID}",
    "phone_number_home": None,
    "email_id_company": f"test.employee.{TEST_RUN_ID}@urbanexpress.ae",
    "email_id_personal": f"test.employee.{TEST_RUN_ID}@example.com",
    "address_in_base_location": "Dubai test base address",
    "address_in_home_location": "Dubai test home address",
    "driver_licence_number": f"DL{TEST_RUN_ID}",
    "driver_licence_expiry_date": "2030-12-31",
    "driver_licence_type": "light vehicle",
    "permit_number": f"PERMIT{TEST_RUN_ID}",
    "permit_expiry_date": "2028-12-31",
    "permit_type": "driver permit",
    "insurance_number": f"INS{TEST_RUN_ID}",
    "insurance_expiry_date": "2027-12-31",
    "bank_name": "Test Bank",
    "bank_address": "Dubai test bank address",
    "bank_account_number": f"ACC{TEST_RUN_ID}",
    "monthly_basic_salary": 5000.00,
    "monthly_allowance": 1000.00,
    "monthly_accomodation": 1500.00,
    "field_flex_field_1": "employee_master_create_test",
    "field_flex_field_2": None,
    "field_flex_field_3": None,
    "field_flex_field_4": None,
    "created_by": TEST_USER_ID,
    "updated_by": TEST_USER_ID,
    "employee_image_path": None,
    "reporting_to_employee_id": TEST_USER_ID
}

start_time = time.perf_counter()
print(create_employee_master(payload=create_employee_master_payload))
print_runtime("create employee_master program", start_time)

start_time = time.perf_counter()
empl_id = get_created_empl_id(employee_id)
print(f"Created empl_id_pk: {empl_id}")
print_runtime("get employee_master program", start_time)

if empl_id:
    update_employee_master_payload = {
        **create_employee_master_payload,
        "empl_id": empl_id,
        "employee_name": "Updated Test Employee",
        "employee_designation": "Updated Test Driver",
        "phone_number_company": f"+971502{TEST_RUN_ID}",
        "monthly_basic_salary": 5500.00,
        "monthly_allowance": 1200.00,
        "field_flex_field_1": "employee_master_update_test"
    }

    start_time = time.perf_counter()
    print(update_employee_master(payload=update_employee_master_payload))
    print_runtime("update employee_master program", start_time)


# ============================================================
# Firebase employee image: upload and download tests
# ============================================================

if empl_id:
    upload_file_path = create_test_image_file(employee_id)
    download_directory = os.path.join(
        tempfile.gettempdir(),
        "ue_transport_erp_employee_image_download_tests"
    )
    download_file_path = os.path.join(
        download_directory,
        f"{employee_id}_downloaded.png"
    )

    upload_employee_image_payload = {
        "employee_id": employee_id,
        "file_path": upload_file_path,
        "updated_by": TEST_USER_ID
    }

    start_time = time.perf_counter()
    print(upload_employee_image(payload=upload_employee_image_payload))
    print_runtime("upload employee image program", start_time)

    download_employee_image_payload = {
        "employee_id": employee_id,
        "download_directory": download_directory,
        "download_file_path": download_file_path
    }

    start_time = time.perf_counter()
    print(download_employee_image(payload=download_employee_image_payload))
    print_runtime("download employee image program", start_time)


# ============================================================
# Delete test records
# ============================================================

if empl_id:
    delete_employee_master_payload = {
        "empl_id": empl_id
    }

    start_time = time.perf_counter()
    print(delete_employee_master(payload=delete_employee_master_payload))
    print_runtime("delete employee_master program", start_time)
