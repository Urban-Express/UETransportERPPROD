create table organization_license_master(
org_license_id serial primary key,
org_id_fk bigint not null,
org_management_module boolean default false,
hr_payroll_management_module boolean default false,
fleet_management_module boolean default false,
maintenance_management_module boolean default false,
gps_tracking_module boolean default false,
contracts_management_module boolean default false,
financial_management_module boolean default false,
created_by varchar(50),
created_at timestamp default current_timestamp, 
constraint fk_org_license
	foreign key (org_id_fk)
	references organization_master(org_id_pk)
);

insert into organization_license_master(
org_id_fk,
org_management_module,
hr_payroll_management_module,
fleet_management_module,
maintenance_management_module,
gps_tracking_module,
contracts_management_module,
financial_management_module,
created_by
) values (
1,
true,
true,
true,
true,
true,
true,
true,
'SYSTEM_UPDATE'
);

select * from organization_license_master;