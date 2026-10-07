-- ============================================================
-- SIMPLIFIED PAYROLL MODULE
-- PostgreSQL DDL
--
-- Existing parent tables assumed:
--   public.organization_master (org_id_pk)
--   public.employee_master     (empl_id_pk)
--
-- This is a bootstrap DDL for a new payroll schema. For an existing payroll
-- database, apply the required incremental migrations in date order instead.
-- 20261001_include_penalty_deduction_in_payroll.sql requires the manually added
-- penalty_deduction column and must follow the correction-recovery migration.
--
-- Tables created:
--   1. public.payroll_run
--   2. public.payroll_adjustment
--   3. public.payroll_employee_detail
--   4. public.payroll_correction_recovery
-- ============================================================

BEGIN;


-- ============================================================
-- DIVISION 1 OF 4: PAYROLL RUN
-- One record represents one monthly, one-time, correction,
-- or final-settlement payroll batch.
-- ============================================================

CREATE TABLE public.payroll_run (
    payroll_run_id_pk               BIGSERIAL PRIMARY KEY,

    payroll_org_id_fk               INTEGER NOT NULL,

    payroll_run_code                VARCHAR(50) NOT NULL,
    payroll_run_description         VARCHAR(255),

    payroll_year                    INTEGER NOT NULL,
    payroll_month                   INTEGER NOT NULL,
    payroll_run_sequence            INTEGER NOT NULL DEFAULT 1,

    payroll_run_type                VARCHAR(30) NOT NULL DEFAULT 'MONTHLY',

    payroll_period_start_date       DATE NOT NULL,
    payroll_period_end_date         DATE NOT NULL,
    payroll_payment_date            DATE,

    payroll_status                  VARCHAR(30) NOT NULL DEFAULT 'DRAFT',

    total_employee_count            INTEGER NOT NULL DEFAULT 0,
    total_gross_salary              NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_deductions                NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_correction_net_amount     NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_correction_recovery       NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_net_salary                NUMERIC(18,2) NOT NULL DEFAULT 0,

    created_by                      VARCHAR(100),
    created_at                      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    processed_by                    VARCHAR(100),
    processed_at                    TIMESTAMP,

    approved_by                     VARCHAR(100),
    approved_at                     TIMESTAMP,

    paid_by                         VARCHAR(100),
    paid_at                         TIMESTAMP,

    cancellation_reason             TEXT,

    field_flex_field_1              VARCHAR(255),
    field_flex_field_2              VARCHAR(255),
    field_flex_field_3              VARCHAR(255),
    field_flex_field_4              VARCHAR(255),

    updated_by                      VARCHAR(100),
    updated_at                      TIMESTAMP,

    CONSTRAINT fk_payroll_run_organization
        FOREIGN KEY (payroll_org_id_fk)
        REFERENCES public.organization_master (org_id_pk),

    CONSTRAINT uq_payroll_run_code
        UNIQUE (payroll_org_id_fk, payroll_run_code),

    CONSTRAINT uq_payroll_run_sequence
        UNIQUE (
            payroll_org_id_fk,
            payroll_year,
            payroll_month,
            payroll_run_type,
            payroll_run_sequence
        ),

    CONSTRAINT chk_payroll_run_year
        CHECK (payroll_year BETWEEN 2000 AND 2200),

    CONSTRAINT chk_payroll_run_month
        CHECK (payroll_month BETWEEN 1 AND 12),

    CONSTRAINT chk_payroll_run_sequence
        CHECK (payroll_run_sequence > 0),

    CONSTRAINT chk_payroll_run_type
        CHECK (
            payroll_run_type IN (
                'MONTHLY',
                'ONE_TIME',
                'FINAL_SETTLEMENT',
                'CORRECTION'
            )
        ),

    CONSTRAINT chk_payroll_run_status
        CHECK (
            payroll_status IN (
                'DRAFT',
                'PROCESSED',
                'APPROVED',
                'PAID',
                'CANCELLED'
            )
        ),

    CONSTRAINT chk_payroll_run_period
        CHECK (
            payroll_period_end_date >= payroll_period_start_date
        ),

    CONSTRAINT chk_payroll_run_employee_count
        CHECK (total_employee_count >= 0),

    CONSTRAINT chk_payroll_run_gross
        CHECK (total_gross_salary >= 0),

    CONSTRAINT chk_payroll_run_deductions
        CHECK (total_deductions >= 0),

    CONSTRAINT chk_payroll_run_correction_recovery
        CHECK (total_correction_recovery >= 0),

    CONSTRAINT chk_payroll_run_net
        CHECK (total_net_salary >= 0)
);

CREATE INDEX idx_payroll_run_org_period
    ON public.payroll_run (
        payroll_org_id_fk,
        payroll_year,
        payroll_month
    );

CREATE INDEX idx_payroll_run_status
    ON public.payroll_run (
        payroll_org_id_fk,
        payroll_status
    );


-- ============================================================
-- DIVISION 2 OF 4: PAYROLL ADJUSTMENT
-- Stores one-time earnings and deductions before they are
-- included in a payroll run.
-- ============================================================

CREATE TABLE public.payroll_adjustment (
    payroll_adjustment_id_pk          BIGSERIAL PRIMARY KEY,

    payroll_adjustment_org_id_fk      INTEGER NOT NULL,
    payroll_adjustment_empl_id_fk     INTEGER NOT NULL,

    payroll_year                      INTEGER NOT NULL,
    payroll_month                     INTEGER NOT NULL,

    adjustment_date                   DATE NOT NULL DEFAULT CURRENT_DATE,

    adjustment_type                   VARCHAR(50) NOT NULL,
    earning_deduction_flag            VARCHAR(20) NOT NULL,

    adjustment_description            VARCHAR(500),
    adjustment_amount                 NUMERIC(18,2) NOT NULL,

    adjustment_status                 VARCHAR(20) NOT NULL DEFAULT 'DRAFT',

    external_reference                VARCHAR(100),

    approved_by                       VARCHAR(100),
    approved_at                       TIMESTAMP,

    rejected_by                       VARCHAR(100),
    rejected_at                       TIMESTAMP,
    rejection_reason                  VARCHAR(500),

    payroll_run_id_fk                 BIGINT,

    processed_flag                    BOOLEAN NOT NULL DEFAULT FALSE,
    processed_at                      TIMESTAMP,

    created_by                        VARCHAR(100),
    created_at                        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by                        VARCHAR(100),
    updated_at                        TIMESTAMP,

    field_flex_field_1                VARCHAR(255),
    field_flex_field_2                VARCHAR(255),
    field_flex_field_3                VARCHAR(255),
    field_flex_field_4                VARCHAR(255),

    CONSTRAINT fk_payroll_adjustment_organization
        FOREIGN KEY (payroll_adjustment_org_id_fk)
        REFERENCES public.organization_master (org_id_pk),

    CONSTRAINT fk_payroll_adjustment_employee
        FOREIGN KEY (payroll_adjustment_empl_id_fk)
        REFERENCES public.employee_master (empl_id_pk),

    CONSTRAINT fk_payroll_adjustment_run
        FOREIGN KEY (payroll_run_id_fk)
        REFERENCES public.payroll_run (payroll_run_id_pk),

    CONSTRAINT chk_payroll_adjustment_year
        CHECK (payroll_year BETWEEN 2000 AND 2200),

    CONSTRAINT chk_payroll_adjustment_month
        CHECK (payroll_month BETWEEN 1 AND 12),

    CONSTRAINT chk_payroll_adjustment_type
        CHECK (
            adjustment_type IN (
                'OVERTIME',
                'BONUS',
                'INCENTIVE',
                'REIMBURSEMENT',
                'OTHER_EARNING',
                'UNPAID_LEAVE',
                'LOAN_DEDUCTION',
                'ADVANCE_DEDUCTION',
                'FINE_DEDUCTION',
                'FUEL_DEDUCTION',
                'SALIK_DEDUCTION',
                'DARB_DEDUCTION',
                'CHARGING_COST_DEDUCTION',
                'OTHER_DEDUCTION'
            )
        ),

    CONSTRAINT chk_payroll_adjustment_flag
        CHECK (
            earning_deduction_flag IN (
                'EARNING',
                'DEDUCTION'
            )
        ),

    CONSTRAINT chk_payroll_adjustment_amount
        CHECK (adjustment_amount > 0),

    CONSTRAINT chk_payroll_adjustment_status
        CHECK (
            adjustment_status IN (
                'DRAFT',
                'APPROVED',
                'REJECTED',
                'PROCESSED',
                'CANCELLED'
            )
        ),

    CONSTRAINT chk_payroll_adjustment_processing
        CHECK (
            processed_flag = FALSE
            OR payroll_run_id_fk IS NOT NULL
        )
);

CREATE INDEX idx_payroll_adjustment_employee_period
    ON public.payroll_adjustment (
        payroll_adjustment_empl_id_fk,
        payroll_year,
        payroll_month
    );

CREATE INDEX idx_payroll_adjustment_pending
    ON public.payroll_adjustment (
        payroll_adjustment_org_id_fk,
        payroll_year,
        payroll_month,
        adjustment_status,
        processed_flag
    );

CREATE INDEX idx_payroll_adjustment_run
    ON public.payroll_adjustment (payroll_run_id_fk);


-- ============================================================
-- DIVISION 3 OF 4: PAYROLL EMPLOYEE DETAIL
-- Stores the final payroll calculation for each employee
-- included in a payroll run.
-- ============================================================

CREATE TABLE public.payroll_employee_detail (
    payroll_employee_detail_id_pk      BIGSERIAL PRIMARY KEY,

    payroll_run_id_fk                  BIGINT NOT NULL,
    payroll_employee_id_fk             INTEGER NOT NULL,

    -- Employee information snapshots
    employee_id_snapshot               VARCHAR(100),
    employee_name_snapshot             VARCHAR(255) NOT NULL,
    employee_designation_snapshot      VARCHAR(255),

    -- Fixed salary values copied from employee_master
    basic_salary                       NUMERIC(18,2) NOT NULL DEFAULT 0,
    monthly_allowance                  NUMERIC(18,2) NOT NULL DEFAULT 0,
    accommodation_allowance            NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- One-time or variable earnings
    overtime_amount                    NUMERIC(18,2) NOT NULL DEFAULT 0,
    bonus_amount                       NUMERIC(18,2) NOT NULL DEFAULT 0,
    incentive_amount                   NUMERIC(18,2) NOT NULL DEFAULT 0,
    reimbursement_amount               NUMERIC(18,2) NOT NULL DEFAULT 0,
    other_earning_amount               NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Deductions
    unpaid_leave_deduction             NUMERIC(18,2) NOT NULL DEFAULT 0,
    loan_deduction                     NUMERIC(18,2) NOT NULL DEFAULT 0,
    advance_deduction                  NUMERIC(18,2) NOT NULL DEFAULT 0,
    fine_deduction                     NUMERIC(18,2) NOT NULL DEFAULT 0,
    fuel_deduction                     NUMERIC(18,2) NOT NULL DEFAULT 0,
    salik_deduction                    NUMERIC(18,2) NOT NULL DEFAULT 0,
    darb_deduction                     NUMERIC(18,2) NOT NULL DEFAULT 0,
    charging_cost_deduction            NUMERIC(18,2) NOT NULL DEFAULT 0,
    other_deduction_amount             NUMERIC(18,2) NOT NULL DEFAULT 0,
    -- Existing live column: nullable, no default. Fresh databases match it.
    penalty_deduction                  NUMERIC(18,2),

    -- Signed correction delta and recoverable settlement against a prior
    -- monthly payroll detail. Neither field is an earning.
    correction_net_amount              NUMERIC(18,2) NOT NULL DEFAULT 0,
    correction_recovery_amount         NUMERIC(18,2) NOT NULL DEFAULT 0,

    -- Attendance and proration information
    total_period_days                  NUMERIC(8,2),
    payable_days                       NUMERIC(8,2),
    unpaid_leave_days                  NUMERIC(8,2) NOT NULL DEFAULT 0,

    -- Automatically calculated totals
    gross_salary NUMERIC(18,2)
        GENERATED ALWAYS AS (
              basic_salary
            + monthly_allowance
            + accommodation_allowance
            + overtime_amount
            + bonus_amount
            + incentive_amount
            + reimbursement_amount
            + other_earning_amount
        ) STORED,

    total_deduction NUMERIC(18,2)
        GENERATED ALWAYS AS (
              unpaid_leave_deduction
            + loan_deduction
            + advance_deduction
            + fine_deduction
            + fuel_deduction
            + salik_deduction
            + darb_deduction
            + charging_cost_deduction
            + other_deduction_amount
            + COALESCE(penalty_deduction, 0)
        ) STORED,

    net_salary NUMERIC(18,2)
        GENERATED ALWAYS AS (
            CASE
                WHEN correction_net_amount <> 0
                    OR correction_recovery_amount <> 0
                THEN GREATEST(correction_net_amount, 0)
                ELSE
                    (
                          basic_salary
                        + monthly_allowance
                        + accommodation_allowance
                        + overtime_amount
                        + bonus_amount
                        + incentive_amount
                        + reimbursement_amount
                        + other_earning_amount
                    )
                    -
                    (
                          unpaid_leave_deduction
                        + loan_deduction
                        + advance_deduction
                        + fine_deduction
                        + fuel_deduction
                        + salik_deduction
                        + darb_deduction
                        + charging_cost_deduction
                        + other_deduction_amount
                        + COALESCE(penalty_deduction, 0)
                    )
            END
        ) STORED,

    -- Bank and payment snapshots
    bank_name_snapshot                 VARCHAR(255),
    bank_account_number_snapshot       VARCHAR(100),
    iban_number_snapshot               VARCHAR(50),

    payment_method                     VARCHAR(30) NOT NULL
                                           DEFAULT 'BANK_TRANSFER',

    payment_status                     VARCHAR(30) NOT NULL
                                           DEFAULT 'NOT_PROCESSED',

    payment_reference                  VARCHAR(150),
    payment_processed_at               TIMESTAMP,

    payroll_detail_status              VARCHAR(30) NOT NULL
                                           DEFAULT 'DRAFT',

    exception_flag                     BOOLEAN NOT NULL DEFAULT FALSE,
    exception_description              VARCHAR(1000),

    remarks                            TEXT,

    created_by                         VARCHAR(100),
    created_at                         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by                         VARCHAR(100),
    updated_at                         TIMESTAMP,

    field_flex_field_1                 VARCHAR(255),
    field_flex_field_2                 VARCHAR(255),
    field_flex_field_3                 VARCHAR(255),
    field_flex_field_4                 VARCHAR(255),

    CONSTRAINT fk_payroll_employee_detail_run
        FOREIGN KEY (payroll_run_id_fk)
        REFERENCES public.payroll_run (payroll_run_id_pk),

    CONSTRAINT fk_payroll_employee_detail_employee
        FOREIGN KEY (payroll_employee_id_fk)
        REFERENCES public.employee_master (empl_id_pk),

    CONSTRAINT uq_payroll_run_employee
        UNIQUE (
            payroll_run_id_fk,
            payroll_employee_id_fk
        ),

    CONSTRAINT chk_payroll_detail_basic_salary
        CHECK (basic_salary >= 0),

    CONSTRAINT chk_payroll_detail_monthly_allowance
        CHECK (monthly_allowance >= 0),

    CONSTRAINT chk_payroll_detail_accommodation
        CHECK (accommodation_allowance >= 0),

    CONSTRAINT chk_payroll_detail_overtime
        CHECK (overtime_amount >= 0),

    CONSTRAINT chk_payroll_detail_bonus
        CHECK (bonus_amount >= 0),

    CONSTRAINT chk_payroll_detail_incentive
        CHECK (incentive_amount >= 0),

    CONSTRAINT chk_payroll_detail_reimbursement
        CHECK (reimbursement_amount >= 0),

    CONSTRAINT chk_payroll_detail_other_earning
        CHECK (other_earning_amount >= 0),

    CONSTRAINT chk_payroll_detail_unpaid_leave_deduction
        CHECK (unpaid_leave_deduction >= 0),

    CONSTRAINT chk_payroll_detail_loan_deduction
        CHECK (loan_deduction >= 0),

    CONSTRAINT chk_payroll_detail_advance_deduction
        CHECK (advance_deduction >= 0),

    CONSTRAINT chk_payroll_detail_fine_deduction
        CHECK (fine_deduction >= 0),

    CONSTRAINT chk_payroll_detail_fuel_deduction
        CHECK (fuel_deduction >= 0),

    CONSTRAINT chk_payroll_detail_salik_deduction
        CHECK (salik_deduction >= 0),

    CONSTRAINT chk_payroll_detail_darb_deduction
        CHECK (darb_deduction >= 0),

    CONSTRAINT chk_payroll_detail_charging_cost_deduction
        CHECK (charging_cost_deduction >= 0),

    CONSTRAINT chk_payroll_detail_other_deduction
        CHECK (other_deduction_amount >= 0),

    CONSTRAINT chk_payroll_detail_correction_recovery
        CHECK (correction_recovery_amount >= 0),

    CONSTRAINT chk_payroll_detail_correction_recovery_net
        CHECK (
            correction_recovery_amount <=
            GREATEST(-correction_net_amount, 0)
        ),

    CONSTRAINT chk_payroll_detail_correction_recovery_limit
        CHECK (
            correction_recovery_amount <=
            (
                  unpaid_leave_deduction
                + loan_deduction
                + advance_deduction
                + fine_deduction
                + fuel_deduction
                + salik_deduction
                + darb_deduction
                + charging_cost_deduction
                + other_deduction_amount
                + COALESCE(penalty_deduction, 0)
            )
        ),

    CONSTRAINT chk_payroll_detail_days
        CHECK (
            (total_period_days IS NULL OR total_period_days >= 0)
            AND
            (payable_days IS NULL OR payable_days >= 0)
            AND
            unpaid_leave_days >= 0
        ),

    CONSTRAINT chk_payroll_detail_payable_days
        CHECK (
            total_period_days IS NULL
            OR payable_days IS NULL
            OR payable_days <= total_period_days
        ),

    CONSTRAINT chk_payroll_detail_net_salary
        CHECK (net_salary >= 0),

    CONSTRAINT chk_payroll_payment_method
        CHECK (
            payment_method IN (
                'BANK_TRANSFER',
                'WPS',
                'CASH',
                'CHEQUE'
            )
        ),

    CONSTRAINT chk_payroll_payment_status
        CHECK (
            payment_status IN (
                'NOT_PROCESSED',
                'READY',
                'SUBMITTED',
                'PAID',
                'FAILED',
                'CANCELLED'
            )
        ),

    CONSTRAINT chk_payroll_detail_status
        CHECK (
            payroll_detail_status IN (
                'DRAFT',
                'PROCESSED',
                'APPROVED',
                'PAID',
                'CANCELLED'
            )
        )
);

CREATE INDEX idx_payroll_employee_detail_run
    ON public.payroll_employee_detail (payroll_run_id_fk);

CREATE INDEX idx_payroll_employee_detail_employee
    ON public.payroll_employee_detail (
        payroll_employee_id_fk,
        payroll_run_id_fk
    );

CREATE INDEX idx_payroll_employee_payment_status
    ON public.payroll_employee_detail (
        payroll_run_id_fk,
        payment_status
    );


-- ============================================================
-- DIVISION 4 OF 4: PAYROLL CORRECTION RECOVERY
-- Records correction deductions recovered against a prior
-- processed payroll detail without creating fictitious earnings
-- or negative payable payroll detail rows.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.payroll_correction_recovery (
    payroll_correction_recovery_id_pk        BIGSERIAL PRIMARY KEY,

    payroll_org_id_fk                        INTEGER NOT NULL,

    correction_payroll_run_id_fk             BIGINT NOT NULL,
    correction_payroll_employee_detail_id_fk BIGINT NOT NULL,

    source_payroll_run_id_fk                 BIGINT NOT NULL,
    source_payroll_employee_detail_id_fk     BIGINT NOT NULL,

    payroll_adjustment_id_fk                 BIGINT,
    adjustment_type                          VARCHAR(50) NOT NULL,
    adjustment_amount                        NUMERIC(18,2) NOT NULL,
    recovered_amount                         NUMERIC(18,2) NOT NULL,

    recovery_status                          VARCHAR(20) NOT NULL
                                                   DEFAULT 'APPLIED',
    recovery_reason                          VARCHAR(500),

    created_by                               VARCHAR(100),
    created_at                               TIMESTAMP NOT NULL
                                                   DEFAULT CURRENT_TIMESTAMP,

    updated_by                               VARCHAR(100),
    updated_at                               TIMESTAMP,

    CONSTRAINT fk_payroll_correction_recovery_org
        FOREIGN KEY (payroll_org_id_fk)
        REFERENCES public.organization_master (org_id_pk),

    CONSTRAINT fk_payroll_correction_recovery_run
        FOREIGN KEY (correction_payroll_run_id_fk)
        REFERENCES public.payroll_run (payroll_run_id_pk),

    CONSTRAINT fk_payroll_correction_recovery_detail
        FOREIGN KEY (correction_payroll_employee_detail_id_fk)
        REFERENCES public.payroll_employee_detail (payroll_employee_detail_id_pk),

    CONSTRAINT fk_payroll_correction_recovery_source_run
        FOREIGN KEY (source_payroll_run_id_fk)
        REFERENCES public.payroll_run (payroll_run_id_pk),

    CONSTRAINT fk_payroll_correction_recovery_source_detail
        FOREIGN KEY (source_payroll_employee_detail_id_fk)
        REFERENCES public.payroll_employee_detail (payroll_employee_detail_id_pk),

    CONSTRAINT fk_payroll_correction_recovery_adjustment
        FOREIGN KEY (payroll_adjustment_id_fk)
        REFERENCES public.payroll_adjustment (payroll_adjustment_id_pk),

    CONSTRAINT chk_payroll_correction_recovery_amount
        CHECK (recovered_amount > 0),

    CONSTRAINT chk_payroll_correction_recovery_adjustment_amount
        CHECK (adjustment_amount >= recovered_amount),

    CONSTRAINT chk_payroll_correction_recovery_status
        CHECK (recovery_status IN ('APPLIED', 'CANCELLED'))
);

CREATE INDEX IF NOT EXISTS idx_payroll_correction_recovery_source_detail
    ON public.payroll_correction_recovery (
        source_payroll_employee_detail_id_fk,
        recovery_status
    );

CREATE INDEX IF NOT EXISTS idx_payroll_correction_recovery_run
    ON public.payroll_correction_recovery (
        correction_payroll_run_id_fk,
        recovery_status
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_correction_recovery_adjustment_applied
    ON public.payroll_correction_recovery (payroll_adjustment_id_fk)
    WHERE payroll_adjustment_id_fk IS NOT NULL
    AND recovery_status = 'APPLIED';


COMMIT;
