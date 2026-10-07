from app_backend.services.service_01_organization_management.logic.department_master_create_data import create_department
from app_backend.services.service_01_organization_management.logic.department_master_get_data import get_department
from app_backend.services.service_01_organization_management.logic.department_master_update_data import update_department
from app_backend.services.service_01_organization_management.logic.department_master_delete_data import delete_department
import time

# Testing the create department program logic
create_dep_payload = {
    'dep_org_id_fk': 1,
    'department_name': 'Operations Department',
    'cost_center_flag': 'Y',
    'profit_center_flag': 'Y',
    'created_by': 'SYSTEM_UPDATE'
}
t0 = time.perf_counter()
print(create_department(payload=create_dep_payload))
t1 = time.perf_counter()
print(f"Runtime of create organization program: {(t1 - t0):.2f} seconds.")

# Testing the update department data program logic
update_dep_payload = {
    'dep_id': 4,
    'dep_org_id_fk': 1,
    'department_name': 'Strategy Department',
    'cost_center_flag': 'Y',
    'profit_center_flag': '',
    'created_by': 'SYSTEM_UPDATE'
}
t2 = time.perf_counter()
print(update_department(payload=update_dep_payload))
t3 = time.perf_counter()
print(f"Runtime of update organization program: {(t3 - t2):.2f} seconds.")

# Testing the delete department data program logic
delete_dep_payload = {
    'dep_id': 3
}
t4 = time.perf_counter()
print(delete_department(payload=delete_dep_payload))
t5 = time.perf_counter()
print(f"Runtime of delete organization program: {(t5 - t4):.2f} seconds.")

# Testing the get organization data program logic
t6 = time.perf_counter()
df_org, json_org = get_department()
print(f"Org data in dataframe:\n{df_org}")
print(f"Org data in json:\n{json_org}")
t7 = time.perf_counter()
print(f"Runtime of get organization data program: {(t7 - t6):.2f} seconds.")