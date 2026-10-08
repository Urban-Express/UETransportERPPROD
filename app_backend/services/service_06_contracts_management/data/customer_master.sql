BEGIN;

CREATE TABLE customer_master
(
    cust_id_pk                      SERIAL,
    cust_org_id_fk                  INTEGER NOT NULL,

    cust_code                       VARCHAR(30) NOT NULL,
    cust_name                       VARCHAR(200) NOT NULL,
    cust_category                   VARCHAR(100) NOT NULL,

    cust_status                     VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',

    cust_contact_person_name        VARCHAR(150),
    cust_contact_person_designation VARCHAR(100),

    cust_phone_primary              VARCHAR(30),
    cust_phone_secondary            VARCHAR(30),

    cust_email_primary              VARCHAR(254),
    cust_email_secondary            VARCHAR(254),

    cust_billing_address            TEXT,
    cust_service_address            TEXT,

    cust_tax_registration_number    VARCHAR(50),
    cust_credit_period_days         SMALLINT NOT NULL DEFAULT 0,

    cust_notes                      TEXT,

    created_by                      VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    created_at                      TIMESTAMP WITHOUT TIME ZONE
                                    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by                      VARCHAR(100),
    updated_at                      TIMESTAMP WITHOUT TIME ZONE,

    CONSTRAINT pk_customer_master
        PRIMARY KEY (cust_id_pk),

    CONSTRAINT fk_customer_master_organization
        FOREIGN KEY (cust_org_id_fk)
        REFERENCES public.organization_master (org_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT uq_customer_master_code_per_org
        UNIQUE (cust_org_id_fk, cust_code),

    /*
       This additional unique constraint supports the composite
       organization-consistency foreign key in contracts_management.
    */
    CONSTRAINT uq_customer_master_id_org
        UNIQUE (cust_id_pk, cust_org_id_fk),

    CONSTRAINT ck_customer_master_status
        CHECK
        (
            cust_status IN
            (
                'ACTIVE',
                'INACTIVE',
                'SUSPENDED',
                'BLACKLISTED'
            )
        ),

    CONSTRAINT ck_customer_master_credit_period
        CHECK (cust_credit_period_days >= 0)
);

CREATE INDEX idx_customer_master_org
    ON public.customer_master (cust_org_id_fk);

CREATE INDEX idx_customer_master_name
    ON public.customer_master (cust_org_id_fk, cust_name);

CREATE INDEX idx_customer_master_category
    ON public.customer_master (cust_org_id_fk, cust_category);

COMMIT;

-- NEW TABLE STRUCTURE SCRIPT
BEGIN;

-- ============================================================
-- STEP 1: Rename existing Customer contact fields
-- Existing data in these columns is preserved automatically.
-- ============================================================

ALTER TABLE customer_master
RENAME COLUMN cust_contact_person_name
TO procurement_head_name;

ALTER TABLE customer_master
RENAME COLUMN cust_phone_primary
TO procurement_head_phone_primary;

ALTER TABLE customer_master
RENAME COLUMN cust_phone_secondary
TO procurement_head_phone_secondary;

ALTER TABLE customer_master
RENAME COLUMN cust_email_primary
TO procurement_head_email_primary;

ALTER TABLE customer_master
RENAME COLUMN cust_email_secondary
TO procurement_head_email_secondary;


-- ============================================================
-- STEP 2: Drop obsolete field and add new fields
-- ============================================================

ALTER TABLE customer_master

DROP COLUMN cust_contact_person_designation,

ADD COLUMN portal_system varchar(100),

ADD COLUMN operation_incharge_name varchar(150),
ADD COLUMN operation_incharge_phone_primary varchar(30),
ADD COLUMN operation_incharge_phone_secondary varchar(30),
ADD COLUMN operation_incharge_email_primary varchar(254),
ADD COLUMN operation_incharge_email_secondary varchar(254),

ADD COLUMN operation_head_name varchar(150),
ADD COLUMN operation_head_phone_primary varchar(30),
ADD COLUMN operation_head_phone_secondary varchar(30),
ADD COLUMN operation_head_email_primary varchar(254),
ADD COLUMN operation_head_email_secondary varchar(254),

ADD COLUMN finance_incharge_name varchar(150),
ADD COLUMN finance_incharge_phone_primary varchar(30),
ADD COLUMN finance_incharge_phone_secondary varchar(30),
ADD COLUMN finance_incharge_email_primary varchar(254),
ADD COLUMN finance_incharge_email_secondary varchar(254),

ADD COLUMN finance_head_name varchar(150),
ADD COLUMN finance_head_phone_primary varchar(30),
ADD COLUMN finance_head_phone_secondary varchar(30),
ADD COLUMN finance_head_email_primary varchar(254),
ADD COLUMN finance_head_email_secondary varchar(254),

ADD COLUMN wcr_incharge_name varchar(150),
ADD COLUMN wcr_incharge_phone_primary varchar(30),
ADD COLUMN wcr_incharge_phone_secondary varchar(30),
ADD COLUMN wcr_incharge_email_primary varchar(254),
ADD COLUMN wcr_incharge_email_secondary varchar(254),

ADD COLUMN grn_incharge_name varchar(150),
ADD COLUMN grn_incharge_phone_primary varchar(30),
ADD COLUMN grn_incharge_phone_secondary varchar(30),
ADD COLUMN grn_incharge_email_primary varchar(254),
ADD COLUMN grn_incharge_email_secondary varchar(254);


-- ============================================================
-- STEP 3: Explicitly enforce the intended datatypes/lengths
-- on the five renamed columns.
--
-- This is technically unnecessary if their existing datatypes
-- already match these definitions, but guarantees the final
-- structure matches your intended schema.
-- ============================================================

ALTER TABLE customer_master
ALTER COLUMN procurement_head_name TYPE varchar(150),
ALTER COLUMN procurement_head_phone_primary TYPE varchar(30),
ALTER COLUMN procurement_head_phone_secondary TYPE varchar(30),
ALTER COLUMN procurement_head_email_primary TYPE varchar(254),
ALTER COLUMN procurement_head_email_secondary TYPE varchar(254);

COMMIT;