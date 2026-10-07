create or replace view v_user_access_rights as
select
um.user_principal_name,
rm.role_name,
rm.role_description,
pm.module_name,
pm.action_name,
pm.permission_code
from
user_master um,
user_role_mapping urm,
role_master rm,
role_permission_mapping rpm,
permission_master pm
where
um.user_id_pk = urm.user_id_fk
and urm.role_id_fk = rm.role_id_pk
and rm.role_id_pk = rpm.role_id_fk
and rpm.permission_id_fk = pm.permission_id_pk
order by
um.user_principal_name,
rm.role_name,
rm.role_description,
pm.module_name,
pm.action_name,
pm.permission_code;

select * from v_user_access_rights;