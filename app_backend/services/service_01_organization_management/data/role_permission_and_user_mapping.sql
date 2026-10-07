-- =====================================================
-- ROLE MASTER
-- =====================================================

create table if not exists role_master (

    role_id_pk             bigserial primary key,
    role_name              varchar(255) not null unique,
    role_description       text,

    is_active              boolean default true,

    created_at             timestamp default current_timestamp,
    created_by             varchar(100)
);

-- =====================================================
-- PERMISSION MASTER
-- =====================================================

create table if not exists permission_master (

    permission_id_pk       bigserial primary key,

    module_name            varchar(100) not null,
    action_name            varchar(100) not null,

    permission_code        varchar(200) unique,

    created_at             timestamp default current_timestamp,

    constraint uq_permission unique (module_name, action_name)
);

-- =====================================================
-- ROLE ↔ PERMISSION MAPPING
-- =====================================================

create table if not exists role_permission_mapping (

    role_permission_id_pk  bigserial primary key,

    role_id_fk             bigint not null,
    permission_id_fk       bigint not null,

    created_at             timestamp default current_timestamp,

    constraint uq_role_permission unique (role_id_fk, permission_id_fk),

    constraint fk_rp_role
        foreign key (role_id_fk)
        references role_master(role_id_pk)
        on delete cascade,

    constraint fk_rp_permission
        foreign key (permission_id_fk)
        references permission_master(permission_id_pk)
        on delete restrict
);

-- =====================================================
-- USER ↔ ROLE MAPPING
-- =====================================================

create table if not exists user_role_mapping (

    user_role_id_pk        bigserial primary key,

    user_id_fk             bigint not null,
    role_id_fk             bigint not null,

    created_at             timestamp default current_timestamp,

    constraint uq_user_role unique (user_id_fk, role_id_fk),

    constraint fk_user_role_user
        foreign key (user_id_fk)
        references user_master(user_id_pk)
        on delete cascade,

    constraint fk_user_role_role
        foreign key (role_id_fk)
        references role_master(role_id_pk)
        on delete restrict
);

-- =====================================================
-- INDEXES
-- =====================================================

create index if not exists idx_rp_role on role_permission_mapping(role_id_fk);
create index if not exists idx_rp_permission on role_permission_mapping(permission_id_fk);
create index if not exists idx_ur_user on user_role_mapping(user_id_fk);

-- =====================================================
-- SEED: ROLES
-- =====================================================

insert into role_master (role_name, role_description, created_by) values

('System Administrator', 'Full system access', 'SYSTEM'),

('Organization Management - Admin', 'Full access to org module', 'SYSTEM'),
('Organization Management - User', 'Read-only org module', 'SYSTEM'),

('HR and Payroll Management - Admin', 'Full HR access', 'SYSTEM'),
('HR and Payroll Management - User', 'Read-only HR', 'SYSTEM'),

('Fleet Management - Admin', 'Full fleet access', 'SYSTEM'),
('Fleet Management - User', 'Read-only fleet', 'SYSTEM'),

('Maintenance Management - Admin', 'Full maintenance access', 'SYSTEM'),
('Maintenance Management - User', 'Read-only maintenance', 'SYSTEM'),

('Contracts Management - Admin', 'Full contracts access', 'SYSTEM'),
('Contracts Management - User', 'Read-only contracts', 'SYSTEM'),

('Finance Management - Admin', 'Full finance access', 'SYSTEM'),
('Finance Management - User', 'Read-only finance', 'SYSTEM')

on conflict do nothing;

-- =====================================================
-- SEED: PERMISSIONS (CRUD + WORKFLOW)
-- =====================================================

insert into permission_master (module_name, action_name, permission_code) values

-- GENERIC CRUD (per module)
('ORG', 'CREATE', 'ORG_CREATE'),
('ORG', 'READ', 'ORG_READ'),
('ORG', 'UPDATE', 'ORG_UPDATE'),
('ORG', 'DELETE', 'ORG_DELETE'),

('HR', 'CREATE', 'HR_CREATE'),
('HR', 'READ', 'HR_READ'),
('HR', 'UPDATE', 'HR_UPDATE'),
('HR', 'DELETE', 'HR_DELETE'),

('FLEET', 'CREATE', 'FLEET_CREATE'),
('FLEET', 'READ', 'FLEET_READ'),
('FLEET', 'UPDATE', 'FLEET_UPDATE'),
('FLEET', 'DELETE', 'FLEET_DELETE'),

('MAINTENANCE', 'CREATE', 'MAINT_CREATE'),
('MAINTENANCE', 'READ', 'MAINT_READ'),
('MAINTENANCE', 'UPDATE', 'MAINT_UPDATE'),
('MAINTENANCE', 'DELETE', 'MAINT_DELETE'),

('CONTRACTS', 'CREATE', 'CONTRACT_CREATE'),
('CONTRACTS', 'READ', 'CONTRACT_READ'),
('CONTRACTS', 'UPDATE', 'CONTRACT_UPDATE'),
('CONTRACTS', 'DELETE', 'CONTRACT_DELETE'),

('FINANCE', 'CREATE', 'FIN_CREATE'),
('FINANCE', 'READ', 'FIN_READ'),
('FINANCE', 'UPDATE', 'FIN_UPDATE'),
('FINANCE', 'DELETE', 'FIN_DELETE'),

-- WORKFLOW PERMISSIONS
('WORKFLOW', 'SUBMIT', 'WF_SUBMIT'),
('WORKFLOW', 'APPROVE', 'WF_APPROVE'),
('WORKFLOW', 'REJECT', 'WF_REJECT'),
('WORKFLOW', 'ESCALATE', 'WF_ESCALATE')

on conflict do nothing;

-- =====================================================
-- SEED: USER ROLE MAPPING
-- =====================================================

insert into user_role_mapping (
    user_id_fk,
    role_id_fk,
    created_at
)
select 
    u.user_id_pk,
    r.role_id_pk,
    current_timestamp
from user_master u
join role_master r 
    on r.role_name = 'System Administrator'
where u.email = 'system.admin@ue.local'
on conflict do nothing;

-- =====================================================
-- ROLE PERMISSION MAPPING SEED SCRIPT - CORRECTED
-- =====================================================
-- Logic:
-- 1. System Administrator gets all permissions.
-- 2. Module Admin gets READ + workflow permissions only.
-- 3. Module User gets CREATE, READ, UPDATE, DELETE + WF_SUBMIT.
-- 4. Workflow allocation is kept as-is.
-- =====================================================


-- =====================================================
-- OPTIONAL: ADD UNIQUE CONSTRAINT SAFELY
-- =====================================================

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'uq_role_permission_mapping_role_permission'
    ) then
        alter table role_permission_mapping
        add constraint uq_role_permission_mapping_role_permission
        unique (role_id_fk, permission_id_fk);
    end if;
end $$;


-- =====================================================
-- CLEAR EXISTING ROLE-PERMISSION MAPPINGS
-- =====================================================
-- Required because old Admin mappings currently include CREATE/UPDATE/DELETE.

delete from role_permission_mapping;


-- =====================================================
-- INSERT CORRECTED ROLE-PERMISSION MAPPINGS
-- =====================================================

with role_permission_seed as (

    -- =====================================================
    -- SYSTEM ADMINISTRATOR: ALL PERMISSIONS
    -- =====================================================

    select
        'System Administrator' as role_name,
        permission_code
    from permission_master


    union all

    -- =====================================================
    -- ORGANIZATION MANAGEMENT - ADMIN
    -- Admin gets READ only + workflow permissions
    -- =====================================================

    select 'Organization Management - Admin', 'ORG_READ'
    union all select 'Organization Management - Admin', 'WF_SUBMIT'
    union all select 'Organization Management - Admin', 'WF_APPROVE'
    union all select 'Organization Management - Admin', 'WF_REJECT'


    union all

    -- =====================================================
    -- ORGANIZATION MANAGEMENT - USER
    -- User gets CREATE, READ, UPDATE, DELETE + WF_SUBMIT
    -- =====================================================

    select 'Organization Management - User', 'ORG_CREATE'
    union all select 'Organization Management - User', 'ORG_READ'
    union all select 'Organization Management - User', 'ORG_UPDATE'
    union all select 'Organization Management - User', 'ORG_DELETE'
    union all select 'Organization Management - User', 'WF_SUBMIT'


    union all

    -- =====================================================
    -- HR AND PAYROLL MANAGEMENT - ADMIN
    -- =====================================================

    select 'HR and Payroll Management - Admin', 'HR_READ'
    union all select 'HR and Payroll Management - Admin', 'WF_SUBMIT'
    union all select 'HR and Payroll Management - Admin', 'WF_APPROVE'
    union all select 'HR and Payroll Management - Admin', 'WF_REJECT'


    union all

    -- =====================================================
    -- HR AND PAYROLL MANAGEMENT - USER
    -- =====================================================

    select 'HR and Payroll Management - User', 'HR_CREATE'
    union all select 'HR and Payroll Management - User', 'HR_READ'
    union all select 'HR and Payroll Management - User', 'HR_UPDATE'
    union all select 'HR and Payroll Management - User', 'HR_DELETE'
    union all select 'HR and Payroll Management - User', 'WF_SUBMIT'


    union all

    -- =====================================================
    -- FLEET MANAGEMENT - ADMIN
    -- =====================================================

    select 'Fleet Management - Admin', 'FLEET_READ'
    union all select 'Fleet Management - Admin', 'WF_SUBMIT'
    union all select 'Fleet Management - Admin', 'WF_APPROVE'
    union all select 'Fleet Management - Admin', 'WF_REJECT'


    union all

    -- =====================================================
    -- FLEET MANAGEMENT - USER
    -- =====================================================

    select 'Fleet Management - User', 'FLEET_CREATE'
    union all select 'Fleet Management - User', 'FLEET_READ'
    union all select 'Fleet Management - User', 'FLEET_UPDATE'
    union all select 'Fleet Management - User', 'FLEET_DELETE'
    union all select 'Fleet Management - User', 'WF_SUBMIT'


    union all

    -- =====================================================
    -- MAINTENANCE MANAGEMENT - ADMIN
    -- =====================================================

    select 'Maintenance Management - Admin', 'MAINT_READ'
    union all select 'Maintenance Management - Admin', 'WF_SUBMIT'
    union all select 'Maintenance Management - Admin', 'WF_APPROVE'
    union all select 'Maintenance Management - Admin', 'WF_REJECT'


    union all

    -- =====================================================
    -- MAINTENANCE MANAGEMENT - USER
    -- =====================================================

    select 'Maintenance Management - User', 'MAINT_CREATE'
    union all select 'Maintenance Management - User', 'MAINT_READ'
    union all select 'Maintenance Management - User', 'MAINT_UPDATE'
    union all select 'Maintenance Management - User', 'MAINT_DELETE'
    union all select 'Maintenance Management - User', 'WF_SUBMIT'


    union all

    -- =====================================================
    -- CONTRACTS MANAGEMENT - ADMIN
    -- =====================================================

    select 'Contracts Management - Admin', 'CONTRACT_READ'
    union all select 'Contracts Management - Admin', 'WF_SUBMIT'
    union all select 'Contracts Management - Admin', 'WF_APPROVE'
    union all select 'Contracts Management - Admin', 'WF_REJECT'


    union all

    -- =====================================================
    -- CONTRACTS MANAGEMENT - USER
    -- =====================================================

    select 'Contracts Management - User', 'CONTRACT_CREATE'
    union all select 'Contracts Management - User', 'CONTRACT_READ'
    union all select 'Contracts Management - User', 'CONTRACT_UPDATE'
    union all select 'Contracts Management - User', 'CONTRACT_DELETE'
    union all select 'Contracts Management - User', 'WF_SUBMIT'


    union all

    -- =====================================================
    -- FINANCE MANAGEMENT - ADMIN
    -- Finance Admin keeps workflow allocation as-is, including WF_ESCALATE
    -- =====================================================

    select 'Finance Management - Admin', 'FIN_READ'
    union all select 'Finance Management - Admin', 'WF_SUBMIT'
    union all select 'Finance Management - Admin', 'WF_APPROVE'
    union all select 'Finance Management - Admin', 'WF_REJECT'
    union all select 'Finance Management - Admin', 'WF_ESCALATE'


    union all

    -- =====================================================
    -- FINANCE MANAGEMENT - USER
    -- =====================================================

    select 'Finance Management - User', 'FIN_CREATE'
    union all select 'Finance Management - User', 'FIN_READ'
    union all select 'Finance Management - User', 'FIN_UPDATE'
    union all select 'Finance Management - User', 'FIN_DELETE'
    union all select 'Finance Management - User', 'WF_SUBMIT'

)

insert into role_permission_mapping (
    role_id_fk,
    permission_id_fk,
    created_at
)
select
    r.role_id_pk,
    p.permission_id_pk,
    now()
from role_permission_seed rps
join role_master r
    on r.role_name = rps.role_name
join permission_master p
    on p.permission_code = rps.permission_code
where r.is_active = true
on conflict (role_id_fk, permission_id_fk)
do nothing;