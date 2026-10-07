BEGIN;

CREATE TABLE public.maintenance_master (
    maint_id_pk BIGSERIAL PRIMARY KEY,

    maint_fleet_vehicle_id_fk BIGINT NOT NULL,

    maint_image_path TEXT,

    -- Preventative maintenance details
    preventive_maintenance_date DATE,
    preventive_maintenance_job_work TEXT,
    preventive_maintenance_workshop VARCHAR(200),
    preventive_maintenance_amount NUMERIC(14,2),

    -- Breakdown details
    breakdown_date DATE,
    breakdown_job_work TEXT,
    breakdown_workshop VARCHAR(200),
    breakdown_amount NUMERIC(14,2),

    -- Accident details
    accident_date DATE,
    accident_job_work TEXT,
    accident_workshop VARCHAR(200),
    accident_amount NUMERIC(14,2),

    -- Deployment details
    deployment_type VARCHAR(20),
    deployment_client_name VARCHAR(200),
    deployment_from_date DATE,

    -- Audit fields
    created_by VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by VARCHAR(100),
    updated_at TIMESTAMP WITHOUT TIME ZONE,

    CONSTRAINT fk_maintenance_master_fleet_vehicle
        FOREIGN KEY (maint_fleet_vehicle_id_fk)
        REFERENCES public.fleet_master (fleet_vehicle_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT ck_maintenance_preventive_amount
        CHECK (
            preventive_maintenance_amount IS NULL
            OR preventive_maintenance_amount >= 0
        ),

    CONSTRAINT ck_maintenance_breakdown_amount
        CHECK (
            breakdown_amount IS NULL
            OR breakdown_amount >= 0
        ),

    CONSTRAINT ck_maintenance_accident_amount
        CHECK (
            accident_amount IS NULL
            OR accident_amount >= 0
        ),

    CONSTRAINT ck_maintenance_deployment_type
        CHECK (
            deployment_type IS NULL
            OR deployment_type IN ('CLIENT', 'SPARE')
        )
);

CREATE INDEX idx_maintenance_master_fleet_vehicle
    ON public.maintenance_master (maint_fleet_vehicle_id_fk);

CREATE INDEX idx_maintenance_master_preventive_date
    ON public.maintenance_master (preventive_maintenance_date);

CREATE INDEX idx_maintenance_master_breakdown_date
    ON public.maintenance_master (breakdown_date);

CREATE INDEX idx_maintenance_master_accident_date
    ON public.maintenance_master (accident_date);

CREATE INDEX idx_maintenance_master_deployment_from_date
    ON public.maintenance_master (deployment_from_date);

COMMIT;