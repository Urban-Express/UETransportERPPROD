BEGIN;

CREATE TABLE supplier_master
(
    supp_id_pk                      SERIAL,
    supp_org_id_fk                  INTEGER NOT NULL,

    supp_code                       VARCHAR(30) NOT NULL,
    supp_name                       VARCHAR(200) NOT NULL,
    supp_category                   VARCHAR(100) NOT NULL,

    supp_status                     VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',

    supp_contact_person_name        VARCHAR(150),
    supp_contact_person_designation VARCHAR(100),

    supp_phone_primary              VARCHAR(30),
    supp_phone_secondary            VARCHAR(30),

    supp_email_primary              VARCHAR(254),
    supp_email_secondary            VARCHAR(254),

    supp_billing_address            TEXT,
    supp_service_address            TEXT,

    supp_tax_registration_number    VARCHAR(50),
    supp_credit_period_days         SMALLINT NOT NULL DEFAULT 0,

    supp_notes                      TEXT,

    created_by                      VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    created_at                      TIMESTAMP WITHOUT TIME ZONE
                                    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by                      VARCHAR(100),
    updated_at                      TIMESTAMP WITHOUT TIME ZONE,

    CONSTRAINT pk_supplier_master
        PRIMARY KEY (supp_id_pk),

    CONSTRAINT fk_supplier_master_organization
        FOREIGN KEY (supp_org_id_fk)
        REFERENCES public.organization_master (org_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT uq_supplier_master_code_per_org
        UNIQUE (supp_org_id_fk, supp_code),

    /*
       This additional unique constraint allows downstream tables
       to establish composite organization-consistency foreign keys
       against supplier_master.
    */
    CONSTRAINT uq_supplier_master_id_org
        UNIQUE (supp_id_pk, supp_org_id_fk),

    CONSTRAINT ck_supplier_master_status
        CHECK
        (
            supp_status IN
            (
                'ACTIVE',
                'INACTIVE',
                'SUSPENDED',
                'BLACKLISTED'
            )
        ),

    CONSTRAINT ck_supplier_master_credit_period
        CHECK (supp_credit_period_days >= 0)
);

CREATE INDEX idx_supplier_master_org
    ON public.supplier_master (supp_org_id_fk);

CREATE INDEX idx_supplier_master_name
    ON public.supplier_master (supp_org_id_fk, supp_name);

CREATE INDEX idx_supplier_master_category
    ON public.supplier_master (supp_org_id_fk, supp_category);

COMMIT;