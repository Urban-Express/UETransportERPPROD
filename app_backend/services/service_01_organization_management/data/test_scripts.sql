select * from organization_master;
select * from organization_license_master;
select * from department_master;
select * from user_master;
select * from role_master;
select * from permission_master;
select * from role_permission_mapping;
select * from user_role_mapping;

select
u.user_principal_name,
r.role_name,
p.action_name,
p.module_name
from
role_master r,
permission_master p,
user_master u,
user_role_mapping ur,
role_permission_mapping rp
where
r.role_id_pk = rp.role_id_fk
and p.permission_id_pk = rp.permission_id_fk
and u.user_id_pk = ur.user_id_fk
and ur.role_id_fk = r.role_id_pk
group by 
u.user_principal_name,
r.role_name,
p.action_name,
p.module_name
order by
p.module_name;


drop table role_master;
drop table permission_master;
drop table role_permission_mapping;
drop table user_role_mapping;
drop table user_master;

drop table organization_master;
drop table department_master;

delete from role_permission_mapping;

insert into department_master
(
dep_org_id_fk,
department_name,
cost_center_flag,
profit_center_flag,
created_by
)
values
(
1,
'CFO Office',
'Y',
'',
'SYSTEM'
);