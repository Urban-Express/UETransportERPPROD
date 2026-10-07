from app_backend.services.service_01_organization_management.logic.user_master_create_data import create_user
from app_backend.services.service_01_organization_management.logic.user_master_get_data import get_user
from app_backend.services.service_01_organization_management.logic.user_master_delete_data import delete_user
from app_backend.services.service_01_organization_management.logic.user_master_update_data import update_user
import time

# Testing the create user program logic
create_user_payload_local = {
    'auth_provider': None,
    'entra_object_id': None,
    'user_principal_name': 'test_user',
    'password': 'PlainTextPassword123',
    'first_name': 'Test',
    'last_name': 'User',
    'display_name': 'Test User',
    'email': 'test.user@ue.local',
    'phone_number': None,
    'user_org_id_fk': 1,
    'user_department_id_fk': None,
    'is_active': True,
    'is_deleted': False,
    'created_by': 'SYSTEM'
}
create_user_payload_entra = {
    "auth_provider": "ENTRA",
    "user_principal_name": "bayesium_systems@UrbanExpressTransportLLC.onmicrosoft.com",
    "user_org_id_fk": 1,
    "user_department_id_fk": 2,
    "created_by": "SYSTEM_ADMIN"
}
t0 = time.perf_counter()
print(create_user(payload=create_user_payload_entra))
t1 = time.perf_counter()
print(f"Runtime of create user program: {(t1 - t0):.2f} seconds.")

# Testing the get user program logic
t2 = time.perf_counter()
print(get_user())
t3 = time.perf_counter()
print(f"Runtime of get user program: {(t3 - t2):.2f} seconds.")

# Testing the delete user program logic
delete_user_payload = {
    'user_id': 3
}
t4 = time.perf_counter()
print(delete_user(payload=delete_user_payload))
t5 = time.perf_counter()
print(f"Runtime of delete user program: {(t5 - t4):.2f} seconds.")

# Testing the update user program logic
update_user_payload_local = {
    'user_id': 1,
    'auth_provider': 'LOCAL',
    'entra_object_id': None,
    'user_principal_name': 'system_admin',
    'password': 'System@UE#26',
    'first_name': 'System',
    'last_name': 'Administrator',
    'display_name': 'System Administrator',
    'email': 'system.admin@ue.local',
    'phone_number': None,
    'user_org_id_fk': 1,
    'user_department_id_fk': None,
    'is_active': True,
    'is_deleted': False,
    'updated_by': 'SYSTEM'
}
update_user_payload_entra = {
    "user_id": 4,
    "auth_provider": "ENTRA",
    "user_principal_name": "Manish@UrbanExpressTransportLLC.onmicrosoft.com",
    # ERP-controlled fields
    "user_org_id_fk": 1,
    "user_department_id_fk": 1,
    "is_deleted": False,
    "updated_by": "SYSTEM"
}
t6 = time.perf_counter()
print(update_user(payload=update_user_payload_entra))
t7 = time.perf_counter()
print(f"Runtime of update user program: {(t7 - t6):.2f} seconds.")