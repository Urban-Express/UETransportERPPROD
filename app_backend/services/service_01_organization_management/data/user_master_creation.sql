-- =====================================================
-- USER MASTER TABLE (HYBRID AUTH: ENTRA + LOCAL)
-- =====================================================

create table if not exists user_master (

    -- Primary Key
    user_id_pk                  bigserial primary key,

    -- Authentication Provider
    auth_provider               varchar(50) not null default 'LOCAL',
    -- Values: 'LOCAL', 'ENTRA'

    -- Entra Integration
    entra_object_id             varchar(100) unique,
    user_principal_name         varchar(255),

    -- Local Authentication (SECURE)
    password_hash               varchar(255),

    -- Basic Identity
    first_name                  varchar(100),
    last_name                   varchar(100),
    display_name                varchar(255),

    -- Contact Details
    email                       varchar(255) unique not null,
    phone_number                varchar(50),

    -- Organization Mapping
    user_org_id_fk              bigint not null,
    user_department_id_fk       bigint,

    -- System Control Flags
    is_active                   boolean default true,
    is_deleted                  boolean default false,

    -- Audit Fields
    created_at                  timestamp default current_timestamp,
    created_by                  varchar(100),
    updated_at                  timestamp,
    updated_by                  varchar(100),

    -- =====================================================
    -- CONSTRAINTS
    -- =====================================================

    -- Enforce auth logic consistency
    constraint chk_auth_provider
    check (
        (auth_provider = 'LOCAL' AND password_hash is not null)
        OR
        (auth_provider = 'ENTRA' AND entra_object_id is not null)
    ),

    -- Foreign Keys
    constraint fk_user_org
        foreign key (user_org_id_fk)
        references organization_master(org_id_pk)
        on delete restrict,

    constraint fk_user_department
        foreign key (user_department_id_fk)
        references department_master(dep_id_pk)
        on delete set null
);

-- =====================================================
-- INDEXES
-- =====================================================

create index if not exists idx_user_entra_object_id 
on user_master(entra_object_id);

create index if not exists idx_user_email 
on user_master(email);

create index if not exists idx_user_org 
on user_master(user_org_id_fk);

create index if not exists idx_user_auth_provider
on user_master(auth_provider);

-- =====================================================
-- INSERT SEED RECORD (SYSTEM ADMIN - LOCAL AUTH)
-- =====================================================

-- NOTE:
-- Replace the password hash below with a real bcrypt/argon2 hash

insert into user_master (
    auth_provider,
    entra_object_id,
    user_principal_name,
    password_hash,
    first_name,
    last_name,
    display_name,
    email,
    phone_number,
    user_org_id_fk,
    user_department_id_fk,
    is_active,
    is_deleted,
    created_by
)
values (
    'LOCAL',
    null,
    'system_admin',
    '$2b$12$REPLACE_WITH_BCRYPT_HASH',  -- ⚠️ REQUIRED
    'System',
    'Administrator',
    'System Administrator',
    'system.admin@ue.local',
    null,
    1,          -- ⚠️ REPLACE with valid org_id_pk
    null,
    true,
    false,
    'SYSTEM'
)
on conflict (email) do nothing;