-- =====================================================
-- AUTHENTICATION SESSIONS TABLE
-- =====================================================
-- Review and execute manually against Railway PostgreSQL.
-- This DDL is intentionally not executed from Python.

create table if not exists authentication_sessions (

    -- Primary Key
    auth_session_id_pk     bigserial primary key,

    -- Existing ERP Foreign Keys
    user_id_fk             bigint not null,
    org_id_fk              integer not null,

    -- Refresh Token Storage
    -- Store SHA-256(refresh_token) only. Never store the raw refresh token.
    refresh_token_hash     varchar(64) not null unique,

    -- Session Lifecycle
    created_at             timestamp default current_timestamp,
    access_expires_at      timestamp not null,
    refresh_expires_at     timestamp not null,
    last_refreshed_at      timestamp default current_timestamp,
    revoked_at             timestamp,
    revoke_reason          text,

    constraint fk_auth_session_user
        foreign key (user_id_fk)
        references user_master(user_id_pk)
        on delete cascade,

    constraint fk_auth_session_org
        foreign key (org_id_fk)
        references organization_master(org_id_pk)
        on delete restrict
);

-- =====================================================
-- INDEXES
-- =====================================================

create unique index if not exists idx_auth_sessions_refresh_token_hash
on authentication_sessions(refresh_token_hash);

create index if not exists idx_auth_sessions_user_id_fk
on authentication_sessions(user_id_fk);

create index if not exists idx_auth_sessions_org_id_fk
on authentication_sessions(org_id_fk);
