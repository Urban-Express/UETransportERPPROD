CREATE TABLE asset_master_workflow_requests (
    workflow_request_id_pk BIGSERIAL PRIMARY KEY,

    service_name VARCHAR(100) NOT NULL DEFAULT 'service_08_financial_management',
    entity_name VARCHAR(100) NOT NULL DEFAULT 'asset_master',

    workflow_action VARCHAR(30) NOT NULL,
    workflow_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_APPROVAL',
    workflow_confirmation VARCHAR(1) NOT NULL DEFAULT 'N',

    requester_user_principal_name VARCHAR(255) NOT NULL,
    approver_user_principal_name VARCHAR(255),

    request_payload JSONB NOT NULL,
    execution_result JSONB,

    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,

    approval_comments TEXT,
    rejection_comments TEXT,

    CONSTRAINT ck_asset_master_wf_action
        CHECK (
            workflow_action IN (
                'CREATE',
                'UPDATE',
                'DELETE'
            )
        ),

    CONSTRAINT ck_asset_master_wf_status
        CHECK (
            workflow_status IN (
                'PENDING_APPROVAL',
                'APPROVED',
                'REJECTED',
                'EXECUTED',
                'EXECUTION_FAILED',
                'ERROR'
            )
        ),

    CONSTRAINT ck_asset_master_wf_confirmation
        CHECK (
            workflow_confirmation IN (
                'Y',
                'N'
            )
        )
);

CREATE INDEX idx_asset_master_wf_status
    ON public.asset_master_workflow_requests (
        workflow_status
    );

CREATE INDEX idx_asset_master_wf_requester
    ON public.asset_master_workflow_requests (
        requester_user_principal_name
    );

CREATE INDEX idx_asset_master_wf_approver
    ON public.asset_master_workflow_requests (
        approver_user_principal_name
    );

CREATE INDEX idx_asset_master_wf_requested_at
    ON public.asset_master_workflow_requests (
        requested_at
    );
