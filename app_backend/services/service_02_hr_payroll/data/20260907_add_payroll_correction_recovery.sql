-- ============================================================
-- Payroll correction recovery migration
--
-- Run this on an existing payroll database. Do not rerun the full
-- simplified_payroll_module_ddl.sql bootstrap script on a database that
-- already has payroll_run/payroll_adjustment/payroll_employee_detail.
-- ============================================================

BEGIN;

ALTER TABLE public.payroll_run
    ADD COLUMN IF NOT EXISTS total_correction_recovery NUMERIC(18,2) NOT NULL DEFAULT 0;

ALTER TABLE public.payroll_run
    ADD COLUMN IF NOT EXISTS total_correction_net_amount NUMERIC(18,2) NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_payroll_run_correction_recovery'
        AND conrelid = 'public.payroll_run'::regclass
    ) THEN
        ALTER TABLE public.payroll_run
            ADD CONSTRAINT chk_payroll_run_correction_recovery
            CHECK (total_correction_recovery >= 0);
    END IF;
END $$;

ALTER TABLE public.payroll_employee_detail
    ADD COLUMN IF NOT EXISTS correction_recovery_amount NUMERIC(18,2) NOT NULL DEFAULT 0;

ALTER TABLE public.payroll_employee_detail
    ADD COLUMN IF NOT EXISTS correction_net_amount NUMERIC(18,2) NOT NULL DEFAULT 0;

UPDATE public.payroll_employee_detail ped
SET
    correction_net_amount = ped.gross_salary - ped.total_deduction,
    correction_recovery_amount = GREATEST(ped.total_deduction - ped.gross_salary, 0)
FROM public.payroll_run pr
WHERE pr.payroll_run_id_pk = ped.payroll_run_id_fk
AND pr.payroll_run_type = 'CORRECTION';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_payroll_detail_correction_recovery'
        AND conrelid = 'public.payroll_employee_detail'::regclass
    ) THEN
        ALTER TABLE public.payroll_employee_detail
            ADD CONSTRAINT chk_payroll_detail_correction_recovery
            CHECK (correction_recovery_amount >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_payroll_detail_correction_recovery_net'
        AND conrelid = 'public.payroll_employee_detail'::regclass
    ) THEN
        ALTER TABLE public.payroll_employee_detail
            ADD CONSTRAINT chk_payroll_detail_correction_recovery_net
            CHECK (
                correction_recovery_amount <=
                GREATEST(-correction_net_amount, 0)
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_payroll_detail_correction_recovery_limit'
        AND conrelid = 'public.payroll_employee_detail'::regclass
    ) THEN
        ALTER TABLE public.payroll_employee_detail
            ADD CONSTRAINT chk_payroll_detail_correction_recovery_limit
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
                )
            );
    END IF;
END $$;

ALTER TABLE public.payroll_employee_detail
    DROP CONSTRAINT IF EXISTS chk_payroll_detail_net_salary;

ALTER TABLE public.payroll_employee_detail
    DROP COLUMN IF EXISTS net_salary;

ALTER TABLE public.payroll_employee_detail
    ADD COLUMN net_salary NUMERIC(18,2)
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
                    )
            END
        ) STORED;

ALTER TABLE public.payroll_employee_detail
    ADD CONSTRAINT chk_payroll_detail_net_salary
    CHECK (net_salary >= 0);

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

UPDATE public.payroll_run pr
SET
    total_employee_count = totals.total_employee_count,
    total_gross_salary = totals.total_gross_salary,
    total_deductions = totals.total_deductions,
    total_correction_net_amount = totals.total_correction_net_amount,
    total_correction_recovery = totals.total_correction_recovery,
    total_net_salary = totals.total_payable_amount,
    updated_at = CURRENT_TIMESTAMP
FROM (
    SELECT
        payroll_run_id_fk,
        COUNT(*)::INTEGER AS total_employee_count,
        COALESCE(SUM(gross_salary), 0)::NUMERIC(18,2) AS total_gross_salary,
        COALESCE(SUM(total_deduction), 0)::NUMERIC(18,2) AS total_deductions,
        COALESCE(SUM(correction_net_amount), 0)::NUMERIC(18,2) AS total_correction_net_amount,
        COALESCE(SUM(correction_recovery_amount), 0)::NUMERIC(18,2) AS total_correction_recovery,
        COALESCE(SUM(net_salary), 0)::NUMERIC(18,2) AS total_payable_amount
    FROM public.payroll_employee_detail
    GROUP BY payroll_run_id_fk
) totals
WHERE pr.payroll_run_id_pk = totals.payroll_run_id_fk;

COMMIT;
