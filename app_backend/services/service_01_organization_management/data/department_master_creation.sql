create table department_master(
dep_id_pk serial primary key,
dep_org_id_fk INT not null,
department_name varchar(1000),
cost_center_flag varchar(5),
profit_center_flag varchar(5),
created_by varchar(100),
created_at timestamp default current_timestamp,

constraint fk_org_dep
foreign key (dep_org_id_fk)
references organization_master(org_id_pk)
);

insert into department_master(
dep_org_id_fk,
department_name,
cost_center_flag,
created_by
)values(
1,
'HR Department',
'Y',
'SYSTEM'
);

select * from department_master;