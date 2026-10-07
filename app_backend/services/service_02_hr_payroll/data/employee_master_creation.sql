-- ============================================================
-- EMPLOYEE MASTER
-- Grain: One row per employee
-- Purpose:
--   Central employee record for HR, fleet-driver allocation,
--   payroll reference, document tracking and permit tracking
-- ============================================================

CREATE TABLE IF NOT EXISTS employee_master (
    -- System-generated primary key
    empl_id_pk BIGSERIAL PRIMARY KEY,

    -- Organization relationship
    empl_org_id_fk BIGINT NOT NULL,

    -- Department identifier
    -- No foreign-key relationship until department master exists
    secondary_key_dept BIGINT,

    -- Employee identification
    employee_id VARCHAR(50) NOT NULL,
    employee_name VARCHAR(200) NOT NULL,
    employee_designation VARCHAR(150),
    joining_date DATE,
    nationality VARCHAR(100),

    -- Passport information
    passport_number VARCHAR(100),
    passport_expiry_date DATE,
    passport_issuing_country VARCHAR(100),
    passport_with VARCHAR(150),

    -- Visa information
    visa_number VARCHAR(100),
    visa_expiry_date DATE,

    -- Emirates ID information
    emirates_id_number VARCHAR(100),
    emirates_id_expiry_date DATE,

    -- Contact numbers
    phone_number_company VARCHAR(50),
    phone_number_personal VARCHAR(50),
    phone_number_home VARCHAR(50),

    -- Email addresses
    email_id_company VARCHAR(255),
    email_id_personal VARCHAR(255),

    -- Address information
    address_in_base_location TEXT,
    address_in_home_location TEXT,

    -- Driver licence information
    driver_licence_number VARCHAR(100),
    driver_licence_expiry_date DATE,
    driver_licence_type VARCHAR(100),

    -- Permit information
    permit_number VARCHAR(100),
    permit_expiry_date DATE,
    permit_type VARCHAR(100),

    -- Insurance information
    insurance_number VARCHAR(100),
    insurance_expiry_date DATE,

    -- Bank information
    bank_name VARCHAR(200),
    bank_address TEXT,
    bank_account_number VARCHAR(150),

    -- Monthly compensation
    monthly_basic_salary NUMERIC(18,2) NOT NULL DEFAULT 0,
    monthly_allowance NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Spelling retained based on the requested column name
    monthly_accomodation NUMERIC(18,2) NOT NULL DEFAULT 0,

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
    CONSTRAINT fk_employee_master_organization
        FOREIGN KEY (empl_org_id_fk)
        REFERENCES organization_master(org_id_pk)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    -- Employee ID must be unique within an organization
    CONSTRAINT uq_employee_master_org_employee_id
        UNIQUE (
            empl_org_id_fk,
            employee_id
        ),

    -- Compensation validation
    CONSTRAINT chk_employee_monthly_basic_salary
        CHECK (
            monthly_basic_salary >= 0
        ),

    CONSTRAINT chk_employee_monthly_allowance
        CHECK (
            monthly_allowance >= 0
        ),

    CONSTRAINT chk_employee_monthly_accomodation
        CHECK (
            monthly_accomodation >= 0
        )
);


-- ============================================================
-- UNIQUE IDENTIFIER INDEXES
-- Partial indexes allow multiple NULL values while preventing
-- duplicate populated identifiers.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_employee_master_emirates_id
ON employee_master (
    emirates_id_number
)
WHERE emirates_id_number IS NOT NULL;


CREATE UNIQUE INDEX IF NOT EXISTS
    uq_employee_master_passport
ON employee_master (
    passport_issuing_country,
    passport_number
)
WHERE passport_number IS NOT NULL;


CREATE UNIQUE INDEX IF NOT EXISTS
    uq_employee_master_visa_number
ON employee_master (
    visa_number
)
WHERE visa_number IS NOT NULL;


CREATE UNIQUE INDEX IF NOT EXISTS
    uq_employee_master_driver_licence
ON employee_master (
    driver_licence_number
)
WHERE driver_licence_number IS NOT NULL;


CREATE UNIQUE INDEX IF NOT EXISTS
    uq_employee_master_permit_number
ON employee_master (
    permit_number
)
WHERE permit_number IS NOT NULL;


CREATE UNIQUE INDEX IF NOT EXISTS
    uq_employee_master_company_email
ON employee_master (
    LOWER(email_id_company)
)
WHERE email_id_company IS NOT NULL;


-- ============================================================
-- SUPPORTING INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_employee_master_organization
ON employee_master (
    empl_org_id_fk
);


CREATE INDEX IF NOT EXISTS idx_employee_master_department
ON employee_master (
    empl_org_id_fk,
    secondary_key_dept
);


CREATE INDEX IF NOT EXISTS idx_employee_master_name
ON employee_master (
    employee_name
);


CREATE INDEX IF NOT EXISTS idx_employee_master_designation
ON employee_master (
    empl_org_id_fk,
    employee_designation
);


CREATE INDEX IF NOT EXISTS idx_employee_master_joining_date
ON employee_master (
    joining_date
);


CREATE INDEX IF NOT EXISTS idx_employee_master_passport_expiry
ON employee_master (
    passport_expiry_date
);


CREATE INDEX IF NOT EXISTS idx_employee_master_visa_expiry
ON employee_master (
    visa_expiry_date
);


CREATE INDEX IF NOT EXISTS idx_employee_master_emirates_id_expiry
ON employee_master (
    emirates_id_expiry_date
);


CREATE INDEX IF NOT EXISTS idx_employee_master_licence_expiry
ON employee_master (
    driver_licence_expiry_date
);


CREATE INDEX IF NOT EXISTS idx_employee_master_permit_expiry
ON employee_master (
    permit_expiry_date
);


CREATE INDEX IF NOT EXISTS idx_employee_master_insurance_expiry
ON employee_master (
    insurance_expiry_date
);


-- ============================================================
-- AUTOMATIC UPDATED_AT FUNCTION
-- ============================================================

CREATE OR REPLACE FUNCTION employee_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


-- ============================================================
-- AUTOMATIC UPDATED_AT TRIGGER
-- ============================================================

DROP TRIGGER IF EXISTS trg_employee_master_set_updated_at
ON employee_master;


CREATE TRIGGER trg_employee_master_set_updated_at
BEFORE UPDATE ON employee_master
FOR EACH ROW
EXECUTE FUNCTION employee_set_updated_at();

-- ============================================================
-- RELATIONSHIP BETWEEN EMPLOYEE MASTER AND DEPARTMENT MASTER
-- ============================================================

ALTER TABLE department_master
ADD CONSTRAINT uq_department_master_org_department
UNIQUE (dep_org_id_fk, dep_id_pk);


ALTER TABLE employee_master
ADD CONSTRAINT fk_employee_master_department
FOREIGN KEY (
    empl_org_id_fk,
    secondary_key_dept
)
REFERENCES department_master (
    dep_org_id_fk,
    dep_id_pk
)
ON UPDATE RESTRICT
ON DELETE RESTRICT;