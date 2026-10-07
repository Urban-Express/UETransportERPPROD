from app_backend.services.service_01_organization_management.logic.role_master_get_data import get_role
from app_backend.services.service_01_organization_management.logic.permissions_master_get_data import get_permissions
from app_backend.services.service_01_organization_management.logic.user_role_assignment_master import assign_user_role
import time

# Testing the get role data program logic
t0 = time.perf_counter()
df_role, json_role = get_role()
print(f"Role data in dataframe:\n{df_role}")
print(f"Role data in json:\n{json_role}")
t1 = time.perf_counter()
print(f"Runtime of get role data program: {(t1 - t0):.2f} seconds.")

# Testing the get permissions data program logic
t2 = time.perf_counter()
df_permissions, json_permissions = get_permissions()
print(f"Permissions data in dataframe:\n{df_permissions}")
print(f"Permissions data in json:\n{json_permissions}")
t3 = time.perf_counter()
print(f"Runtime of get permissions data program: {(t3 - t2):.2f} seconds.")

# Testing the assign user role program logic
assign_user_role_payload = {
    'user_id': 5,
    'role_id': 1
}
t4 = time.perf_counter()
print(assign_user_role(payload=assign_user_role_payload))
t5 = time.perf_counter()
print(f"Runtime of assign user role program: {(t5 - t4):.2f} seconds.")