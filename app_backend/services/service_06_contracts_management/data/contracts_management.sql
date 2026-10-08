BEGIN;

/*
===============================================================================
1. Add a composite unique constraint to department_master.

This is required so that contracts_management can validate that:
    cont_dep_id_fk and cont_org_id_fk belong to the same organization.
===============================================================================
*/

DO
$$
BEGIN
    IF NOT EXISTS
    (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_department_master_id_org'
          AND conrelid = 'public.department_master'::regclass
    )
    THEN
        ALTER TABLE public.department_master
        ADD CONSTRAINT uq_department_master_id_org
        UNIQUE (dep_id_pk, dep_org_id_fk);
    END IF;
END
$$;


/*
===============================================================================
2. Create contracts_management
===============================================================================
*/

CREATE TABLE public.contracts_management
(
    cont_id_pk                              SERIAL,

    /*
    ---------------------------------------------------------------------------
    Master-table relationships
    ---------------------------------------------------------------------------
    */

    cont_org_id_fk                          INTEGER NOT NULL,
    cont_cust_id_fk                         INTEGER NOT NULL,
    cont_dep_id_fk                          INTEGER NOT NULL,

    /*
    ---------------------------------------------------------------------------
    Contract identification
    ---------------------------------------------------------------------------
    */

    cont_contract_number                    VARCHAR(50) NOT NULL,
    cont_contract_name                      VARCHAR(200),

    cont_start_date                         DATE NOT NULL,
    cont_end_date                           DATE,

    cont_status                             VARCHAR(20)
                                            NOT NULL DEFAULT 'DRAFT',

    /*
    ---------------------------------------------------------------------------
    Revenue terms
    ---------------------------------------------------------------------------
    */

    cont_revenue_basis                      VARCHAR(40) NOT NULL,

    cont_currency_code                      CHAR(3)
                                            NOT NULL DEFAULT 'AED',

    /*
    ---------------------------------------------------------------------------
    Passenger and bus quantities
    ---------------------------------------------------------------------------
    */

    cont_no_of_passengers                   INTEGER NOT NULL DEFAULT 0,

    cont_big_bus_count_gt_34                INTEGER NOT NULL DEFAULT 0,

    cont_medium_bus_count_17_34             INTEGER NOT NULL DEFAULT 0,

    cont_small_bus_count_lt_17              INTEGER NOT NULL DEFAULT 0,

    cont_no_of_days                         NUMERIC(14,2),
    cont_no_of_kms                          NUMERIC(14,2),

    /*
       No. of buses is calculated automatically and must not be entered
       separately by the front end.
    */
    cont_no_of_buses                        INTEGER
        GENERATED ALWAYS AS
        (
            cont_big_bus_count_gt_34
            + cont_medium_bus_count_17_34
            + cont_small_bus_count_lt_17
        ) STORED,

    /*
    ---------------------------------------------------------------------------
    Monthly pricing
    ---------------------------------------------------------------------------
    */

    cont_per_passenger_rate_pm              NUMERIC(14,2),

    cont_big_bus_rate_pm                    NUMERIC(14,2),

    cont_medium_bus_rate_pm                 NUMERIC(14,2),

    cont_small_bus_rate_pm                  NUMERIC(14,2),

    cont_per_day_rate                       NUMERIC(14,2),
    cont_per_km_rate                        NUMERIC(14,2),

    /* Later-established value field; no automatic calculation. */
    total_contract_value                    DOUBLE PRECISION,

    /*
    ---------------------------------------------------------------------------
    Billing and operational terms
    ---------------------------------------------------------------------------
    */

    cont_no_of_billing_months               SMALLINT NOT NULL,

    cont_no_of_work_days_per_week           SMALLINT NOT NULL,

    cont_no_of_round_trips_per_day          SMALLINT NOT NULL,

    /*
    ---------------------------------------------------------------------------
    Responsibility allocation

    Permitted values:
        ORGANIZATION
        CUSTOMER
        SHARED
        NOT_APPLICABLE
        NOT_SPECIFIED

    The spreadsheet value "UE" should be saved as ORGANIZATION.
    ---------------------------------------------------------------------------
    */

    cont_driver_responsibility_party
        VARCHAR(30) NOT NULL DEFAULT 'NOT_SPECIFIED',

    cont_driver_accommodation_resp_party
        VARCHAR(30) NOT NULL DEFAULT 'NOT_SPECIFIED',

    cont_fuel_responsibility_party
        VARCHAR(30) NOT NULL DEFAULT 'NOT_SPECIFIED',

    cont_salik_responsibility_party
        VARCHAR(30) NOT NULL DEFAULT 'NOT_SPECIFIED',

    cont_permit_responsibility_party
        VARCHAR(30) NOT NULL DEFAULT 'NOT_SPECIFIED',

    /*
    ---------------------------------------------------------------------------
    Additional charges and allowances
    ---------------------------------------------------------------------------
    */

    cont_no_of_free_trips_per_month         INTEGER NOT NULL DEFAULT 0,

    cont_extra_trip_charge                  NUMERIC(14,2)
                                            NOT NULL DEFAULT 0,

    cont_km_cap_pm_per_bus                  NUMERIC(12,2),

    cont_extra_km_charge_per_km             NUMERIC(12,2),

    cont_notes                              TEXT,
    cont_link_path                          TEXT,
    cont_approval_status                    VARCHAR(50),

    /*
    ---------------------------------------------------------------------------
    Audit fields
    ---------------------------------------------------------------------------
    */

    created_by                              VARCHAR(100)
                                            NOT NULL DEFAULT CURRENT_USER,

    created_at                              TIMESTAMP WITHOUT TIME ZONE
                                            NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by                              VARCHAR(100),

    updated_at                              TIMESTAMP WITHOUT TIME ZONE,

    /*
    ---------------------------------------------------------------------------
    Primary key
    ---------------------------------------------------------------------------
    */

    CONSTRAINT pk_contracts_management
        PRIMARY KEY (cont_id_pk),

    /*
    ---------------------------------------------------------------------------
    Standard foreign keys
    ---------------------------------------------------------------------------
    */

    CONSTRAINT fk_contracts_management_organization
        FOREIGN KEY (cont_org_id_fk)
        REFERENCES public.organization_master (org_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_contracts_management_customer
        FOREIGN KEY (cont_cust_id_fk)
        REFERENCES public.customer_master (cust_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_contracts_management_department
        FOREIGN KEY (cont_dep_id_fk)
        REFERENCES public.department_master (dep_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    /*
    ---------------------------------------------------------------------------
    Cross-organization protection

    These constraints prevent, for example, an Urban Express contract from
    being linked to a customer or department belonging to another organization.
    ---------------------------------------------------------------------------
    */

    CONSTRAINT fk_contracts_management_customer_org
        FOREIGN KEY
        (
            cont_cust_id_fk,
            cont_org_id_fk
        )
        REFERENCES public.customer_master
        (
            cust_id_pk,
            cust_org_id_fk
        )
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_contracts_management_department_org
        FOREIGN KEY
        (
            cont_dep_id_fk,
            cont_org_id_fk
        )
        REFERENCES public.department_master
        (
            dep_id_pk,
            dep_org_id_fk
        )
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    /*
    ---------------------------------------------------------------------------
    Uniqueness
    ---------------------------------------------------------------------------
    */

    CONSTRAINT uq_contracts_management_number_per_org
        UNIQUE
        (
            cont_org_id_fk,
            cont_contract_number
        ),

    /*
    ---------------------------------------------------------------------------
    Date and quantity validation
    ---------------------------------------------------------------------------
    */

    CONSTRAINT ck_contracts_management_dates
        CHECK
        (
            cont_end_date IS NULL
            OR cont_end_date >= cont_start_date
        ),

    CONSTRAINT ck_contracts_management_passenger_count
        CHECK (cont_no_of_passengers >= 0),

    CONSTRAINT ck_contracts_management_bus_counts
        CHECK
        (
            cont_big_bus_count_gt_34 >= 0
            AND cont_medium_bus_count_17_34 >= 0
            AND cont_small_bus_count_lt_17 >= 0
        ),

    CONSTRAINT ck_contracts_management_billing_months
        CHECK (cont_no_of_billing_months > 0),

    CONSTRAINT ck_contracts_management_work_days
        CHECK
        (
            cont_no_of_work_days_per_week
            BETWEEN 1 AND 7
        ),

    CONSTRAINT ck_contracts_management_round_trips
        CHECK
        (
            cont_no_of_round_trips_per_day >= 0
        ),

    /*
    ---------------------------------------------------------------------------
    Revenue-basis validation
    ---------------------------------------------------------------------------
    */

    CONSTRAINT ck_contracts_management_revenue_basis
        CHECK
        (
            cont_revenue_basis IN
            (
                'PER_BUS',
                'PER_PASSENGER',
                'PER_PASSENGER_AND_PER_BUS',
                'PER_DAY',
                'PER_KILOMETER'
            )
        ),

    /*
       Draft contracts may be incomplete.

       Non-draft contracts must contain the applicable passenger and/or
       vehicle rates.
    */
    CONSTRAINT ck_contracts_management_pricing_completeness
        CHECK
        (
            cont_status = 'DRAFT'

            OR

            (
                cont_revenue_basis = 'PER_PASSENGER'

                AND cont_no_of_passengers > 0
                AND cont_per_passenger_rate_pm IS NOT NULL
            )

            OR

            (
                cont_revenue_basis = 'PER_BUS'

                AND cont_no_of_buses > 0

                AND
                (
                    cont_big_bus_count_gt_34 = 0
                    OR cont_big_bus_rate_pm IS NOT NULL
                )

                AND
                (
                    cont_medium_bus_count_17_34 = 0
                    OR cont_medium_bus_rate_pm IS NOT NULL
                )

                AND
                (
                    cont_small_bus_count_lt_17 = 0
                    OR cont_small_bus_rate_pm IS NOT NULL
                )
            )

            OR

            (
                cont_revenue_basis = 'PER_PASSENGER_AND_PER_BUS'

                AND cont_no_of_passengers > 0
                AND cont_per_passenger_rate_pm IS NOT NULL

                AND cont_no_of_buses > 0

                AND
                (
                    cont_big_bus_count_gt_34 = 0
                    OR cont_big_bus_rate_pm IS NOT NULL
                )

                AND
                (
                    cont_medium_bus_count_17_34 = 0
                    OR cont_medium_bus_rate_pm IS NOT NULL
                )

                AND
                (
                    cont_small_bus_count_lt_17 = 0
                    OR cont_small_bus_rate_pm IS NOT NULL
                )
            )

            OR
            (
                cont_revenue_basis = 'PER_DAY'
                AND cont_no_of_days IS NOT NULL
                AND cont_no_of_days > 0
                AND cont_per_day_rate IS NOT NULL
            )
            OR
            (
                cont_revenue_basis = 'PER_KILOMETER'
                AND cont_no_of_kms IS NOT NULL
                AND cont_no_of_kms > 0
                AND cont_per_km_rate IS NOT NULL
            )
        ),

    /*
    ---------------------------------------------------------------------------
    Monetary validation
    ---------------------------------------------------------------------------
    */

    CONSTRAINT ck_contracts_management_day_km_nonnegative
        CHECK
        (
            (cont_no_of_days IS NULL OR cont_no_of_days >= 0)
            AND (cont_per_day_rate IS NULL OR cont_per_day_rate >= 0)
            AND (cont_no_of_kms IS NULL OR cont_no_of_kms >= 0)
            AND (cont_per_km_rate IS NULL OR cont_per_km_rate >= 0)
        ),

    CONSTRAINT ck_contracts_management_rates
        CHECK
        (
            (
                cont_per_passenger_rate_pm IS NULL
                OR cont_per_passenger_rate_pm >= 0
            )
            AND
            (
                cont_big_bus_rate_pm IS NULL
                OR cont_big_bus_rate_pm >= 0
            )
            AND
            (
                cont_medium_bus_rate_pm IS NULL
                OR cont_medium_bus_rate_pm >= 0
            )
            AND
            (
                cont_small_bus_rate_pm IS NULL
                OR cont_small_bus_rate_pm >= 0
            )
            AND cont_extra_trip_charge >= 0
            AND
            (
                cont_km_cap_pm_per_bus IS NULL
                OR cont_km_cap_pm_per_bus >= 0
            )
            AND
            (
                cont_extra_km_charge_per_km IS NULL
                OR cont_extra_km_charge_per_km >= 0
            )
        ),

    CONSTRAINT ck_contracts_management_free_trips
        CHECK
        (
            cont_no_of_free_trips_per_month >= 0
        ),

    /*
    ---------------------------------------------------------------------------
    Responsibility-party validation
    ---------------------------------------------------------------------------
    */

    CONSTRAINT ck_contracts_management_responsibility_parties
        CHECK
        (
            cont_driver_responsibility_party IN
            (
                'ORGANIZATION',
                'CUSTOMER',
                'SHARED',
                'NOT_APPLICABLE',
                'NOT_SPECIFIED'
            )

            AND cont_driver_accommodation_resp_party IN
            (
                'ORGANIZATION',
                'CUSTOMER',
                'SHARED',
                'NOT_APPLICABLE',
                'NOT_SPECIFIED'
            )

            AND cont_fuel_responsibility_party IN
            (
                'ORGANIZATION',
                'CUSTOMER',
                'SHARED',
                'NOT_APPLICABLE',
                'NOT_SPECIFIED'
            )

            AND cont_salik_responsibility_party IN
            (
                'ORGANIZATION',
                'CUSTOMER',
                'SHARED',
                'NOT_APPLICABLE',
                'NOT_SPECIFIED'
            )

            AND cont_permit_responsibility_party IN
            (
                'ORGANIZATION',
                'CUSTOMER',
                'SHARED',
                'NOT_APPLICABLE',
                'NOT_SPECIFIED'
            )
        ),

    /*
    ---------------------------------------------------------------------------
    Contract status validation
    ---------------------------------------------------------------------------
    */

    CONSTRAINT ck_contracts_management_status
        CHECK
        (
            cont_status IN
            (
                'DRAFT',
                'ACTIVE',
                'SUSPENDED',
                'EXPIRED',
                'TERMINATED',
                'CANCELLED'
            )
        )
);


/*
===============================================================================
3. Indexes
===============================================================================
*/

CREATE INDEX idx_contracts_management_org
    ON public.contracts_management (cont_org_id_fk);

CREATE INDEX idx_contracts_management_customer
    ON public.contracts_management
    (
        cont_org_id_fk,
        cont_cust_id_fk
    );

CREATE INDEX idx_contracts_management_department
    ON public.contracts_management
    (
        cont_org_id_fk,
        cont_dep_id_fk
    );

CREATE INDEX idx_contracts_management_status
    ON public.contracts_management
    (
        cont_org_id_fk,
        cont_status
    );

CREATE INDEX idx_contracts_management_dates
    ON public.contracts_management
    (
        cont_start_date,
        cont_end_date
    );

COMMIT;
