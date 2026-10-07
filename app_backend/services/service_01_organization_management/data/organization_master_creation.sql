create table organization_master(
org_id_pk serial primary key,
org_name varchar(500) NOT NULL,
org_address varchar(1000),
org_phone_primary varchar(20) NOT NULL,
org_phone_secondary varchar(20),
org_email_primary varchar(50),
org_email_secondary varchar(50),
company_registration_number varchar(1000),
created_by varchar(25),
created_at timestamp default current_timestamp
);

insert into organization_master(
org_name,
org_address,
org_phone_primary,
org_phone_secondary,
org_email_primary,
created_by
)
values(
'Urban Express',
'Office no 105, First Floor, B Block, Bel Rasheed Twin Towers, Al Qusais, Dubai, UAE',
'+971 52 1124 424',
'+971 52 1134 434',
'info@urbanexpress.ae',
'System'
);

select * from organization_master;