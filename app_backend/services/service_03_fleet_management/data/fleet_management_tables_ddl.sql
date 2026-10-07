-- ============================================================
-- FLEET MANAGEMENT DDL
-- Dependencies:
--   organization_master(org_id_pk)
--   employee_master(empl_id_pk, empl_org_id_fk)
-- ============================================================


-- ============================================================
-- EMPLOYEE MASTER SUPPORTING UNIQUE CONSTRAINT
-- Required for organization-safe driver relationships
-- ============================================================

ALTER TABLE employee_master
ADD CONSTRAINT uq_employee_master_org_employee
UNIQUE (
    empl_org_id_fk,
    empl_id_pk
);


-- ============================================================
-- 1. FLEET VEHICLE
-- Grain: One row per vehicle
-- Purpose: Vehicle master and current fleet status
-- ============================================================

CREATE TABLE fleet_vehicle (
    fleet_vehicle_id_pk BIGSERIAL PRIMARY KEY,

    -- Organization relationship
    fleet_org_id_fk BIGINT NOT NULL,

    -- Internal vehicle identification
    vehicle_code VARCHAR(50) NOT NULL,

    -- Vehicle classification
    fleet_type VARCHAR(30) NOT NULL,
    fleet_category VARCHAR(30) NOT NULL,

    -- Vehicle specifications
    vehicle_brand_name VARCHAR(100) NOT NULL,
    vehicle_model_name VARCHAR(100),
    vehicle_model_year SMALLINT,
    vehicle_color VARCHAR(50),

    vehicle_total_seats_including_driver INTEGER NOT NULL,

    -- Registration and physical identification
    vehicle_plate_number VARCHAR(50) NOT NULL,
    vehicle_plate_emirate VARCHAR(50),

    vehicle_chassis_number VARCHAR(100) NOT NULL,
    vehicle_engine_number VARCHAR(100),

    -- Mulkiya details
    mulkiya_number VARCHAR(100) NOT NULL,
    mulkiya_expiry_date DATE NOT NULL,

    -- Salik details
    salik_tag_number VARCHAR(100),

    -- Insurance details
    vehicle_insurance_provider VARCHAR(150),
    vehicle_insurance_number VARCHAR(100),
    vehicle_insurance_type VARCHAR(50),

    vehicle_insurance_start_date DATE,
    vehicle_insurance_expiry_date DATE,

    -- Odometer details
    current_odometer_km NUMERIC(12,1)
        NOT NULL DEFAULT 0,

    odometer_last_updated_at TIMESTAMPTZ,

    -- Current operational state
    vehicle_operational_status VARCHAR(30)
        NOT NULL DEFAULT 'available',

    vehicle_deployment_status VARCHAR(30)
        NOT NULL DEFAULT 'unassigned',

    -- Ownership details
    vehicle_ownership_type VARCHAR(30)
        NOT NULL DEFAULT 'owned',

    vehicle_owner_legal_entity VARCHAR(200),

    -- Acquisition and depreciation details
    vehicle_acquisition_date DATE,

    vehicle_acquisition_cost_aed NUMERIC(18,2),

    depreciation_start_date DATE,

    depreciation_method VARCHAR(30)
        NOT NULL DEFAULT 'straight_line',

    useful_life_months INTEGER,

    residual_value_aed NUMERIC(18,2)
        NOT NULL DEFAULT 0,

    vehicle_monthly_depreciation_expense_aed NUMERIC(18,2)
        GENERATED ALWAYS AS (
            CASE
                WHEN depreciation_method = 'straight_line'
                     AND vehicle_acquisition_cost_aed IS NOT NULL
                     AND useful_life_months IS NOT NULL
                     AND useful_life_months > 0
                THEN ROUND(
                    (
                        vehicle_acquisition_cost_aed
                        - COALESCE(residual_value_aed, 0)
                    ) / useful_life_months,
                    2
                )
                ELSE NULL
            END
        ) STORED,

    -- Flexible fields
    field_flex_field_1 TEXT,
    field_flex_field_2 TEXT,
    field_flex_field_3 TEXT,
    field_flex_field_4 TEXT,

    -- Audit fields
    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by BIGINT,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by BIGINT,

    -- Organization relationship
    CONSTRAINT fk_fleet_vehicle_organization
        FOREIGN KEY (
            fleet_org_id_fk
        )
        REFERENCES organization_master (
            org_id_pk
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    -- Organization-level uniqueness
    CONSTRAINT uq_fleet_vehicle_org_vehicle_code
        UNIQUE (
            fleet_org_id_fk,
            vehicle_code
        ),

    CONSTRAINT uq_fleet_vehicle_org_plate
        UNIQUE (
            fleet_org_id_fk,
            vehicle_plate_emirate,
            vehicle_plate_number
        ),

    CONSTRAINT uq_fleet_vehicle_chassis_number
        UNIQUE (
            vehicle_chassis_number
        ),

    CONSTRAINT uq_fleet_vehicle_org_mulkiya
        UNIQUE (
            fleet_org_id_fk,
            mulkiya_number
        ),

    CONSTRAINT uq_fleet_vehicle_org_salik_tag
        UNIQUE (
            fleet_org_id_fk,
            salik_tag_number
        ),

    -- Required for organization-safe relationships
    CONSTRAINT uq_fleet_vehicle_org_vehicle
        UNIQUE (
            fleet_org_id_fk,
            fleet_vehicle_id_pk
        ),

    -- Validation constraints
    CONSTRAINT chk_fleet_vehicle_type
        CHECK (
            fleet_type IN (
                'car',
                'suv',
                'van',
                'minibus',
                'bus',
                'truck',
                'special_purpose'
            )
        ),

    CONSTRAINT chk_fleet_vehicle_category
        CHECK (
            fleet_category IN (
                'corporate',
                'school',
                'mixed_use'
            )
        ),

    CONSTRAINT chk_fleet_vehicle_model_year
        CHECK (
            vehicle_model_year IS NULL
            OR vehicle_model_year BETWEEN 1980 AND 2100
        ),

    CONSTRAINT chk_fleet_vehicle_seats
        CHECK (
            vehicle_total_seats_including_driver > 0
        ),

    CONSTRAINT chk_fleet_vehicle_odometer
        CHECK (
            current_odometer_km >= 0
        ),

    CONSTRAINT chk_fleet_vehicle_operational_status
        CHECK (
            vehicle_operational_status IN (
                'available',
                'allocated',
                'in_service',
                'under_maintenance',
                'out_of_service',
                'inactive',
                'retired',
                'sold'
            )
        ),

    CONSTRAINT chk_fleet_vehicle_deployment_status
        CHECK (
            vehicle_deployment_status IN (
                'client_assigned',
                'school_route',
                'corporate_pool',
                'standby',
                'spare',
                'workshop',
                'unassigned'
            )
        ),

    CONSTRAINT chk_fleet_vehicle_ownership_type
        CHECK (
            vehicle_ownership_type IN (
                'owned',
                'leased',
                'financed',
                'rented',
                'client_provided'
            )
        ),

    CONSTRAINT chk_fleet_vehicle_insurance_dates
        CHECK (
            vehicle_insurance_start_date IS NULL
            OR vehicle_insurance_expiry_date IS NULL
            OR vehicle_insurance_expiry_date
                >= vehicle_insurance_start_date
        ),

    CONSTRAINT chk_fleet_vehicle_acquisition_cost
        CHECK (
            vehicle_acquisition_cost_aed IS NULL
            OR vehicle_acquisition_cost_aed >= 0
        ),

    CONSTRAINT chk_fleet_vehicle_residual_value
        CHECK (
            residual_value_aed >= 0
        ),

    CONSTRAINT chk_fleet_vehicle_residual_vs_cost
        CHECK (
            vehicle_acquisition_cost_aed IS NULL
            OR residual_value_aed
                <= vehicle_acquisition_cost_aed
        ),

    CONSTRAINT chk_fleet_vehicle_useful_life
        CHECK (
            useful_life_months IS NULL
            OR useful_life_months > 0
        ),

    CONSTRAINT chk_fleet_vehicle_depreciation_method
        CHECK (
            depreciation_method IN (
                'straight_line',
                'declining_balance',
                'units_of_production',
                'not_applicable'
            )
        )
);


-- ============================================================
-- 2. FLEET DRIVER ALLOCATION
-- Grain: One row per vehicle-driver allocation period
-- Purpose: Current and historical driver allocations
-- ============================================================

CREATE TABLE fleet_driver_allocation (
    fleet_driver_allocation_id_pk BIGSERIAL PRIMARY KEY,

    -- Organization relationship
    fleet_org_id_fk BIGINT NOT NULL,

    -- Vehicle relationship
    fleet_vehicle_id_fk BIGINT NOT NULL,

    -- Employee/driver relationship
    fleet_driver_empl_id_fk BIGINT NOT NULL,

    -- Allocation period
    allocation_start_datetime TIMESTAMPTZ NOT NULL,
    allocation_end_datetime TIMESTAMPTZ,

    -- Allocation state
    allocation_status VARCHAR(30)
        NOT NULL DEFAULT 'scheduled',

    -- Optional operational identifiers
    route_id_fk BIGINT,
    shift_id_fk BIGINT,

    -- Allocation details
    allocation_reason TEXT,

    allocated_by BIGINT,

    allocated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Deallocation details
    deallocation_reason TEXT,

    deallocated_by BIGINT,
    deallocated_at TIMESTAMPTZ,

    -- Audit fields
    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by BIGINT,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by BIGINT,

    -- Organization relationship
    CONSTRAINT fk_driver_allocation_organization
        FOREIGN KEY (
            fleet_org_id_fk
        )
        REFERENCES organization_master (
            org_id_pk
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    -- Organization-safe vehicle relationship
    CONSTRAINT fk_driver_allocation_vehicle
        FOREIGN KEY (
            fleet_org_id_fk,
            fleet_vehicle_id_fk
        )
        REFERENCES fleet_master (
            fleet_org_id_fk,
            fleet_vehicle_id_pk
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    -- Organization-safe employee relationship
    CONSTRAINT fk_driver_allocation_employee
        FOREIGN KEY (
            fleet_org_id_fk,
            fleet_driver_empl_id_fk
        )
        REFERENCES employee_master (
            empl_org_id_fk,
            empl_id_pk
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    -- Validation constraints
    CONSTRAINT chk_driver_allocation_status
        CHECK (
            allocation_status IN (
                'scheduled',
                'active',
                'completed',
                'cancelled'
            )
        ),

    CONSTRAINT chk_driver_allocation_dates
        CHECK (
            allocation_end_datetime IS NULL
            OR allocation_end_datetime
                >= allocation_start_datetime
        ),

    CONSTRAINT chk_driver_deallocation_dates
        CHECK (
            deallocated_at IS NULL
            OR deallocated_at >= allocated_at
        ),

    CONSTRAINT chk_completed_allocation_end_date
        CHECK (
            allocation_status <> 'completed'
            OR allocation_end_datetime IS NOT NULL
        ),

    CONSTRAINT chk_active_allocation_open_end
        CHECK (
            allocation_status <> 'active'
            OR allocation_end_datetime IS NULL
        )
);

-- ============================================================
-- 3. FLEET STATUS HISTORY
-- Grain: One row per vehicle status period
-- Purpose: Historical fleet-status tracking
-- ============================================================

-- Dropping this table for UE Implementation
drop table fleet_status_history;

CREATE TABLE fleet_status_history (
    fleet_status_history_id_pk BIGSERIAL PRIMARY KEY,

    -- Organization relationship
    fleet_org_id_fk BIGINT NOT NULL,

    -- Vehicle relationship
    fleet_vehicle_id_fk BIGINT NOT NULL,

    -- Status transition
    previous_vehicle_status VARCHAR(30),
    new_vehicle_status VARCHAR(30) NOT NULL,

    -- Effective status period
    status_effective_from TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    status_effective_to TIMESTAMPTZ,

    -- Status-change details
    status_change_source VARCHAR(30)
        NOT NULL DEFAULT 'manual',

    status_change_reason TEXT,
    status_change_remarks TEXT,

    -- Optional future maintenance relationship
    related_maintenance_order_id_fk BIGINT,

    changed_by BIGINT,

    changed_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Flexible fields
    field_flex_field_1 TEXT,
    field_flex_field_2 TEXT,
    field_flex_field_3 TEXT,
    field_flex_field_4 TEXT,

    -- Audit fields
    created_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by BIGINT,

    updated_at TIMESTAMPTZ
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by BIGINT,

    -- Organization relationship
    CONSTRAINT fk_status_history_organization
        FOREIGN KEY (
            fleet_org_id_fk
        )
        REFERENCES organization_master (
            org_id_pk
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    -- Organization-safe vehicle relationship
    CONSTRAINT fk_status_history_vehicle
        FOREIGN KEY (
            fleet_org_id_fk,
            fleet_vehicle_id_fk
        )
        REFERENCES fleet_vehicle (
            fleet_org_id_fk,
            fleet_vehicle_id_pk
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    -- Validation constraints
    CONSTRAINT chk_status_history_previous_status
        CHECK (
            previous_vehicle_status IS NULL
            OR previous_vehicle_status IN (
                'available',
                'allocated',
                'in_service',
                'under_maintenance',
                'out_of_service',
                'inactive',
                'retired',
                'sold'
            )
        ),

    CONSTRAINT chk_status_history_new_status
        CHECK (
            new_vehicle_status IN (
                'available',
                'allocated',
                'in_service',
                'under_maintenance',
                'out_of_service',
                'inactive',
                'retired',
                'sold'
            )
        ),

    CONSTRAINT chk_status_history_source
        CHECK (
            status_change_source IN (
                'manual',
                'driver_allocation',
                'driver_deallocation',
                'maintenance',
                'inspection',
                'system'
            )
        ),

    CONSTRAINT chk_status_history_dates
        CHECK (
            status_effective_to IS NULL
            OR status_effective_to
                >= status_effective_from
        ),

    CONSTRAINT chk_status_history_transition
        CHECK (
            previous_vehicle_status IS NULL
            OR previous_vehicle_status
                <> new_vehicle_status
        )
);


-- Only one current/open status record per vehicle.

CREATE UNIQUE INDEX uq_current_status_record_per_vehicle
ON fleet_status_history (
    fleet_org_id_fk,
    fleet_vehicle_id_fk
)
WHERE status_effective_to IS NULL;


-- ============================================================
-- SUPPORTING INDEXES: FLEET VEHICLE
-- ============================================================

CREATE INDEX idx_fleet_vehicle_org
ON fleet_vehicle (
    fleet_org_id_fk
);


CREATE INDEX idx_fleet_vehicle_operational_status
ON fleet_vehicle (
    fleet_org_id_fk,
    vehicle_operational_status
);


CREATE INDEX idx_fleet_vehicle_deployment_status
ON fleet_vehicle (
    fleet_org_id_fk,
    vehicle_deployment_status
);


CREATE INDEX idx_fleet_vehicle_type_category
ON fleet_vehicle (
    fleet_org_id_fk,
    fleet_type,
    fleet_category
);


CREATE INDEX idx_fleet_vehicle_mulkiya_expiry
ON fleet_vehicle (
    mulkiya_expiry_date
);


CREATE INDEX idx_fleet_vehicle_insurance_expiry
ON fleet_vehicle (
    vehicle_insurance_expiry_date
);


-- ============================================================
-- SUPPORTING INDEXES: DRIVER ALLOCATION
-- ============================================================

CREATE INDEX idx_driver_allocation_vehicle
ON fleet_driver_allocation (
    fleet_org_id_fk,
    fleet_vehicle_id_fk,
    allocation_start_datetime DESC
);


CREATE INDEX idx_driver_allocation_employee
ON fleet_driver_allocation (
    fleet_org_id_fk,
    fleet_driver_empl_id_fk,
    allocation_start_datetime DESC
);


CREATE INDEX idx_driver_allocation_status
ON fleet_driver_allocation (
    fleet_org_id_fk,
    allocation_status
);


CREATE INDEX idx_driver_allocation_period
ON fleet_driver_allocation (
    allocation_start_datetime,
    allocation_end_datetime
);


-- ============================================================
-- SUPPORTING INDEXES: STATUS HISTORY
-- ============================================================

CREATE INDEX idx_status_history_vehicle_date
ON fleet_status_history (
    fleet_org_id_fk,
    fleet_vehicle_id_fk,
    status_effective_from DESC
);


CREATE INDEX idx_status_history_new_status
ON fleet_status_history (
    fleet_org_id_fk,
    new_vehicle_status
);


CREATE INDEX idx_status_history_effective_period
ON fleet_status_history (
    status_effective_from,
    status_effective_to
);


-- ============================================================
-- AUTOMATIC UPDATED_AT FUNCTION
-- ============================================================

CREATE OR REPLACE FUNCTION fleet_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


-- ============================================================
-- UPDATED_AT TRIGGER: FLEET VEHICLE
-- ============================================================

CREATE TRIGGER trg_fleet_vehicle_set_updated_at
BEFORE UPDATE ON fleet_vehicle
FOR EACH ROW
EXECUTE FUNCTION fleet_set_updated_at();


-- ============================================================
-- UPDATED_AT TRIGGER: DRIVER ALLOCATION
-- ============================================================

CREATE TRIGGER trg_driver_allocation_set_updated_at
BEFORE UPDATE ON fleet_driver_allocation
FOR EACH ROW
EXECUTE FUNCTION fleet_set_updated_at();


-- ============================================================
-- UPDATED_AT TRIGGER: STATUS HISTORY
-- ============================================================

CREATE TRIGGER trg_status_history_set_updated_at
BEFORE UPDATE ON fleet_status_history
FOR EACH ROW
EXECUTE FUNCTION fleet_set_updated_at();
