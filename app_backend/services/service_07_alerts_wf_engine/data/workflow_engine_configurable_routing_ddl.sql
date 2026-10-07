-- UE Transport ERP configurable workflow engine additive migration.
-- This migration keeps all existing module workflow request tables and adds
-- central definition/version/graph/runtime tables used by new workflow requests.

CREATE TABLE IF NOT EXISTS workflow_definitions (
    workflow_definition_id_pk BIGSERIAL PRIMARY KEY,
    workflow_code VARCHAR(100) NOT NULL,
    workflow_name VARCHAR(255) NOT NULL,
    service_name VARCHAR(100) NOT NULL,
    entity_name VARCHAR(100) NOT NULL,
    organization_id_fk BIGINT NOT NULL REFERENCES organization_master(org_id_pk),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(255),
    updated_at TIMESTAMPTZ,
    updated_by VARCHAR(255),
    CONSTRAINT uq_workflow_definition_org_code UNIQUE (organization_id_fk, workflow_code)
);

CREATE TABLE IF NOT EXISTS workflow_definition_versions (
    workflow_version_id_pk BIGSERIAL PRIMARY KEY,
    workflow_definition_id_fk BIGINT NOT NULL
        REFERENCES workflow_definitions(workflow_definition_id_pk) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    version_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
    applies_to_action VARCHAR(30) NOT NULL DEFAULT 'ALL',
    allow_self_approval BOOLEAN NOT NULL DEFAULT FALSE,
    canvas_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    published_at TIMESTAMPTZ,
    published_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(255),
    CONSTRAINT ck_workflow_version_status
        CHECK (version_status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
    CONSTRAINT uq_workflow_definition_version_number
        UNIQUE (workflow_definition_id_fk, version_number)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_published_action
    ON workflow_definition_versions (workflow_definition_id_fk, applies_to_action)
    WHERE version_status = 'PUBLISHED';

CREATE TABLE IF NOT EXISTS workflow_nodes (
    workflow_node_id_pk BIGSERIAL PRIMARY KEY,
    workflow_version_id_fk BIGINT NOT NULL
        REFERENCES workflow_definition_versions(workflow_version_id_pk) ON DELETE CASCADE,
    node_key VARCHAR(100) NOT NULL,
    node_type VARCHAR(30) NOT NULL,
    user_principal_name VARCHAR(255),
    sequence_hint INTEGER,
    canvas_x NUMERIC,
    canvas_y NUMERIC,
    node_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_workflow_node_type
        CHECK (node_type IN ('REQUESTER', 'APPROVER', 'END')),
    CONSTRAINT uq_workflow_node_key UNIQUE (workflow_version_id_fk, node_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_node_version_node_id
    ON workflow_nodes(workflow_version_id_fk, workflow_node_id_pk);

CREATE TABLE IF NOT EXISTS workflow_edges (
    workflow_edge_id_pk BIGSERIAL PRIMARY KEY,
    workflow_version_id_fk BIGINT NOT NULL
        REFERENCES workflow_definition_versions(workflow_version_id_pk) ON DELETE CASCADE,
    source_node_id_fk BIGINT NOT NULL REFERENCES workflow_nodes(workflow_node_id_pk) ON DELETE CASCADE,
    target_node_id_fk BIGINT NOT NULL REFERENCES workflow_nodes(workflow_node_id_pk) ON DELETE CASCADE,
    edge_sequence INTEGER,
    edge_type VARCHAR(30) NOT NULL DEFAULT 'SEQUENTIAL',
    condition_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_workflow_edge_type
        CHECK (edge_type IN ('SEQUENTIAL'))
);

CREATE TABLE IF NOT EXISTS workflow_instances (
    workflow_instance_id_pk BIGSERIAL PRIMARY KEY,
    workflow_definition_id_fk BIGINT NOT NULL REFERENCES workflow_definitions(workflow_definition_id_pk),
    workflow_version_id_fk BIGINT NOT NULL REFERENCES workflow_definition_versions(workflow_version_id_pk),
    workflow_code VARCHAR(100) NOT NULL,
    organization_id_fk BIGINT NOT NULL REFERENCES organization_master(org_id_pk),
    workflow_action VARCHAR(30) NOT NULL,
    requester_user_principal_name VARCHAR(255) NOT NULL,
    current_node_id_fk BIGINT REFERENCES workflow_nodes(workflow_node_id_pk),
    workflow_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_APPROVAL',
    request_payload JSONB NOT NULL,
    execution_result JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    route_snapshot JSONB NOT NULL,
    legacy_request_table VARCHAR(100),
    legacy_workflow_request_id BIGINT,
    routing_source VARCHAR(50) NOT NULL DEFAULT 'CONFIGURED_WORKFLOW',
    execution_key VARCHAR(100),
    execution_started_at TIMESTAMPTZ,
    execution_completed_at TIMESTAMPTZ,
    CONSTRAINT ck_workflow_instance_status
        CHECK (workflow_status IN ('PENDING_APPROVAL', 'EXECUTED', 'REJECTED', 'EXECUTION_FAILED', 'ERROR')),
    CONSTRAINT ck_workflow_instance_routing_source
        CHECK (routing_source IN ('CONFIGURED_WORKFLOW', 'LEGACY_ASSIGNED_APPROVER'))
);

CREATE TABLE IF NOT EXISTS workflow_instance_steps (
    workflow_instance_step_id_pk BIGSERIAL PRIMARY KEY,
    workflow_instance_id_fk BIGINT NOT NULL
        REFERENCES workflow_instances(workflow_instance_id_pk) ON DELETE CASCADE,
    workflow_node_id_fk BIGINT NOT NULL REFERENCES workflow_nodes(workflow_node_id_pk),
    step_sequence INTEGER NOT NULL,
    assigned_approver_user_principal_name VARCHAR(255) NOT NULL,
    step_status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    acted_by_user_principal_name VARCHAR(255),
    approval_comments TEXT,
    rejection_comments TEXT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    acted_at TIMESTAMPTZ,
    CONSTRAINT ck_workflow_step_status
        CHECK (step_status IN ('PENDING', 'APPROVED', 'REJECTED', 'SKIPPED')),
    CONSTRAINT uq_workflow_instance_step_sequence
        UNIQUE (workflow_instance_id_fk, step_sequence)
);

CREATE INDEX IF NOT EXISTS idx_workflow_instances_status
    ON workflow_instances(workflow_status);

CREATE INDEX IF NOT EXISTS idx_workflow_steps_pending
    ON workflow_instance_steps(assigned_approver_user_principal_name, step_status);

ALTER TABLE IF EXISTS workflow_instances
    ADD COLUMN IF NOT EXISTS routing_source VARCHAR(50) NOT NULL DEFAULT 'CONFIGURED_WORKFLOW',
    ADD COLUMN IF NOT EXISTS execution_key VARCHAR(100),
    ADD COLUMN IF NOT EXISTS execution_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS execution_completed_at TIMESTAMPTZ;

ALTER TABLE IF EXISTS workflow_nodes
    DROP CONSTRAINT IF EXISTS ck_workflow_node_type,
    ADD CONSTRAINT ck_workflow_node_type
        CHECK (node_type IN ('REQUESTER', 'APPROVER', 'END')) NOT VALID;

ALTER TABLE IF EXISTS workflow_edges
    DROP CONSTRAINT IF EXISTS ck_workflow_edge_type,
    ADD CONSTRAINT ck_workflow_edge_type
        CHECK (edge_type IN ('SEQUENTIAL')) NOT VALID;

ALTER TABLE IF EXISTS workflow_instances
    DROP CONSTRAINT IF EXISTS ck_workflow_instance_routing_source,
    ADD CONSTRAINT ck_workflow_instance_routing_source
        CHECK (routing_source IN ('CONFIGURED_WORKFLOW', 'LEGACY_ASSIGNED_APPROVER')) NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_execution_key
    ON workflow_instances(execution_key)
    WHERE execution_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_instance_pending_step
    ON workflow_instance_steps(workflow_instance_id_fk)
    WHERE step_status = 'PENDING';

DO $$
BEGIN
    IF to_regclass('public.payroll_workflow_requests') IS NOT NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_pending_workflow
            ON payroll_workflow_requests(payroll_run_id_fk)
            WHERE workflow_status = 'PENDING_APPROVAL';
    END IF;
END $$;

ALTER TABLE IF EXISTS asset_master_workflow_requests
    ADD COLUMN IF NOT EXISTS workflow_instance_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS workflow_version_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS routing_source VARCHAR(50) NOT NULL DEFAULT 'LEGACY_ASSIGNED_APPROVER';

ALTER TABLE IF EXISTS contracts_management_workflow_requests
    ADD COLUMN IF NOT EXISTS workflow_instance_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS workflow_version_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS routing_source VARCHAR(50) NOT NULL DEFAULT 'LEGACY_ASSIGNED_APPROVER';

ALTER TABLE IF EXISTS fleet_management_workflow_requests
    ADD COLUMN IF NOT EXISTS workflow_instance_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS workflow_version_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS routing_source VARCHAR(50) NOT NULL DEFAULT 'LEGACY_ASSIGNED_APPROVER';

ALTER TABLE IF EXISTS payroll_workflow_requests
    ADD COLUMN IF NOT EXISTS workflow_instance_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS workflow_version_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS routing_source VARCHAR(50) NOT NULL DEFAULT 'LEGACY_ASSIGNED_APPROVER';

ALTER TABLE IF EXISTS accounts_payables_workflow_requests
    ADD COLUMN IF NOT EXISTS workflow_instance_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS workflow_version_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS routing_source VARCHAR(50) NOT NULL DEFAULT 'LEGACY_ASSIGNED_APPROVER';

ALTER TABLE IF EXISTS accounts_receivables_workflow_requests
    ADD COLUMN IF NOT EXISTS workflow_instance_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS workflow_version_id_fk BIGINT,
    ADD COLUMN IF NOT EXISTS routing_source VARCHAR(50) NOT NULL DEFAULT 'LEGACY_ASSIGNED_APPROVER';

DO $$
DECLARE
    legacy_table_name TEXT;
BEGIN
    FOREACH legacy_table_name IN ARRAY ARRAY[
        'asset_master_workflow_requests',
        'contracts_management_workflow_requests',
        'fleet_management_workflow_requests',
        'payroll_workflow_requests',
        'accounts_payables_workflow_requests',
        'accounts_receivables_workflow_requests'
    ]
    LOOP
        IF to_regclass('public.' || legacy_table_name) IS NOT NULL THEN
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS idx_%I_instance ON %I(workflow_instance_id_fk)',
                legacy_table_name,
                legacy_table_name
            );
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS idx_%I_version ON %I(workflow_version_id_fk)',
                legacy_table_name,
                legacy_table_name
            );
        END IF;
    END LOOP;
END $$;

INSERT INTO workflow_definitions (
    workflow_code,
    workflow_name,
    service_name,
    entity_name,
    organization_id_fk,
    is_active,
    created_by
)
SELECT workflow_code, workflow_name, service_name, entity_name, org.org_id_pk, TRUE, 'SYSTEM'
FROM organization_master org
CROSS JOIN (
    VALUES
        ('ASSET_MASTER', 'Asset Master', 'service_08_financial_management', 'asset_master'),
        ('CONTRACTS_MANAGEMENT', 'Contracts Management', 'service_06_contracts_management', 'contracts_management'),
        ('FLEET_MANAGEMENT', 'Fleet Management', 'service_03_fleet_management', 'fleet_master'),
        ('PAYROLL', 'Payroll', 'service_02_hr_payroll', 'payroll_run'),
        ('ACCOUNTS_PAYABLE', 'Accounts Payable', 'service_08_financial_management', 'accounts_payables'),
        ('ACCOUNTS_RECEIVABLE', 'Accounts Receivable', 'service_08_financial_management', 'accounts_receivables')
) catalog(workflow_code, workflow_name, service_name, entity_name)
ON CONFLICT (organization_id_fk, workflow_code) DO NOTHING;

INSERT INTO permission_master (module_name, action_name, permission_code)
VALUES ('WORKFLOW', 'ADMIN', 'WF_ADMIN')
ON CONFLICT (module_name, action_name) DO NOTHING;
