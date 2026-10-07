from app_backend.services.service_01_organization_management.logic.organization_master_create_data import create_organization
from app_backend.services.service_01_organization_management.logic.organization_master_get_data import get_organization
from app_backend.services.service_01_organization_management.logic.organization_master_update_data import update_organization
from app_backend.services.service_01_organization_management.logic.organization_master_delete_data import delete_organization
from app_backend.services.service_01_organization_management.logic.organization_license_master_get_data import get_organization_license
import time

# Testing the create organization program logic
create_org_payload = {
    'org_name': 'Urban Exp',
    'org_address': 'Office no 105, First Floor, B Block, Bel Rasheed Twin Towers, Al Qusais, Dubai, UAE',
    'org_phone_primary': '+971 52 1124 424',
    'org_phone_secondary': '+971 52 1134 434',
    'org_email_primary': 'info@urbanexpress.ae',
    'org_email_secondary': '',
    'company_registration_number': '',
    'created_by': 'SYSTEM_UPDATE'
}
t0 = time.perf_counter()
print(create_organization(payload=create_org_payload))
t1 = time.perf_counter()
print(f"Runtime of create organization program: {(t1 - t0):.2f} seconds.")

# Testing the update organization program logic
update_org_payload = {
    'org_id': 1,
    'org_name': 'Urban Express',
    'org_address': 'Office no 105, First Floor, B Block, Bel Rasheed Twin Towers, Al Qusais, Dubai, UAE',
    'org_phone_primary': '+971 52 1124 424',
    'org_phone_secondary': '+971 52 1134 434',
    'org_email_primary': 'info@urbanexpress.ae',
    'org_email_secondary': '',
    'company_registration_number': '',
    'created_by': 'SYSTEM_UPDATE'
}
t2 = time.perf_counter()
print(update_organization(payload=update_org_payload))
t3 = time.perf_counter()
print(f"Runtime of update organization program: {(t3 - t2):.2f} seconds.")

# Testing the delete organization data program logic
delete_org_payload = {
    'org_id': 3
}
t4 = time.perf_counter()
print(delete_organization(payload=delete_org_payload))
t5 = time.perf_counter()
print(f"Runtime of delete organization program: {(t5 - t4):.2f} seconds.")

# Testing the get organization data program logic
t6 = time.perf_counter()
df_org, json_org = get_organization()
print(f"Org data in dataframe:\n{df_org}")
print(f"Org data in json:\n{json_org}")
t7 = time.perf_counter()
print(f"Runtime of get organization data program: {(t7 - t6):.2f} seconds.")

# Testing the get organization license data program logic
get_org_license_payload = {
    'org_id': 1
}
t8 = time.perf_counter()
df_org_licenses, org_licenses_json = get_organization_license(payload=get_org_license_payload)
print(f"Org licenses data in dataframe:\n{df_org_licenses}")
print(f"Org licenses data in json:\n{org_licenses_json}")
t9 = time.perf_counter()
print(f"Runtime of get organization licenses data program: {(t9 - t8):.2f} seconds.")