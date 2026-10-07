import os
import tempfile
import time

from app_backend.services.service_04_maintenance_management.integrations.firebase_maintenance_image_download import download_maintenance_image
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_create_data import create_maintenance
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_delete_data import delete_maintenance
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_get_data import get_maintenance
from app_backend.services.service_04_maintenance_management.logic.maintenance_master_update_data import update_maintenance


TEST_FLEET_VEHICLE_ID = 5
TEST_USER = "SYSTEM_TEST"
TEST_RUN_ID = int(time.time())


def print_runtime(program_name, start_time):
    end_time = time.perf_counter()
    print(f"Runtime of {program_name}: {(end_time - start_time):.2f} seconds.")


def create_test_image_file(file_name):
    test_directory = os.path.join(
        tempfile.gettempdir(),
        "ue_transport_erp_maintenance_image_tests"
    )
    os.makedirs(test_directory, exist_ok=True)
    test_file_path = os.path.join(test_directory, file_name)

    with open(test_file_path, "wb") as test_file:
        test_file.write(
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07"
            b"\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01\"\x00\x02\x11\x01\x03\x11\x01"
            b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xd2\xcf \xff\xd9"
        )

    return test_file_path


def get_created_maint_id(maint_fleet_vehicle_id_fk):
    df_maintenance_master, json_maintenance_master = get_maintenance()
    print(f"maintenance_master data in dataframe:\n{df_maintenance_master}")
    print(f"maintenance_master data in json:\n{json_maintenance_master}")

    if "maint_fleet_vehicle_id_fk" not in df_maintenance_master.columns:
        return None

    df_created_maintenance = df_maintenance_master[
        df_maintenance_master["maint_fleet_vehicle_id_fk"].eq(maint_fleet_vehicle_id_fk)
        & df_maintenance_master["preventive_maintenance_job_work"].eq(
            "maintenance_master_create_test"
        )
    ]
    if df_created_maintenance.empty:
        return None

    return int(df_created_maintenance.iloc[-1]["maint_id_pk"])


# ============================================================
# maintenance_master table: create, get, update and delete tests
# ============================================================

create_file_path = create_test_image_file(f"maintenance_create_{TEST_RUN_ID}.jpg")

create_maintenance_payload = {
    "maint_fleet_vehicle_id_fk": TEST_FLEET_VEHICLE_ID,
    "preventive_maintenance_date": "2026-08-01",
    "preventive_maintenance_job_work": "maintenance_master_create_test",
    "preventive_maintenance_workshop": "Test Workshop",
    "preventive_maintenance_amount": 500.00,
    "breakdown_date": None,
    "breakdown_job_work": None,
    "breakdown_workshop": None,
    "breakdown_amount": None,
    "accident_date": None,
    "accident_job_work": None,
    "accident_workshop": None,
    "accident_amount": None,
    "deployment_type": "SPARE",
    "deployment_client_name": None,
    "deployment_from_date": "2026-08-01",
    "file_path": create_file_path,
    "created_by": TEST_USER,
    "updated_by": TEST_USER
}

start_time = time.perf_counter()
print(create_maintenance(payload=create_maintenance_payload))
print_runtime("create maintenance_master program", start_time)

start_time = time.perf_counter()
maint_id = get_created_maint_id(TEST_FLEET_VEHICLE_ID)
print(f"Created maint_id_pk: {maint_id}")
print_runtime("get maintenance_master program", start_time)

if maint_id:
    update_file_path = create_test_image_file(f"maintenance_update_{TEST_RUN_ID}.jpg")
    update_maintenance_payload = {
        **create_maintenance_payload,
        "maint_id": maint_id,
        "preventive_maintenance_job_work": "maintenance_master_update_test",
        "preventive_maintenance_amount": 650.00,
        "breakdown_date": "2026-08-02",
        "breakdown_job_work": "Test breakdown inspection",
        "breakdown_workshop": "Updated Test Workshop",
        "breakdown_amount": 250.00,
        "file_path": update_file_path
    }

    start_time = time.perf_counter()
    print(update_maintenance(payload=update_maintenance_payload))
    print_runtime("update maintenance_master program", start_time)

    download_directory = os.path.join(
        tempfile.gettempdir(),
        "ue_transport_erp_maintenance_image_download_tests"
    )

    start_time = time.perf_counter()
    print(download_maintenance_image({
        "maint_id": maint_id,
        "download_directory": download_directory
    }))
    print_runtime("download maintenance image program", start_time)

if maint_id:
    start_time = time.perf_counter()
    print(delete_maintenance(payload={"maint_id": maint_id}))
    print_runtime("delete maintenance_master program", start_time)
