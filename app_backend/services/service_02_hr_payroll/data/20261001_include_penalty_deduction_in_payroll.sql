-- Payroll component compatibility; PostgreSQL 17+ (live inspection: 18.6).
-- Prerequisite: penalty_deduction already exists as NUMERIC(18,2).
-- Does NOT add/recreate the column or modify its nullability/default.
-- Apply after the correction-recovery migration, before the updated application.
-- No Payroll Adjustments, correction ledgers, or Penalties records are changed.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

-- Match application lock order, and keep preflight and expression changes atomic.
LOCK TABLE public.payroll_run IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.payroll_employee_detail IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF current_setting('server_version_num')::integer < 170000 THEN
        RAISE EXCEPTION 'This migration requires PostgreSQL 17 or later.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'payroll_employee_detail'
          AND column_name = 'penalty_deduction'
          AND data_type = 'numeric' AND numeric_precision = 18 AND numeric_scale = 2
          AND is_generated = 'NEVER'
    ) THEN
        RAISE EXCEPTION 'Expected existing payroll_employee_detail.penalty_deduction NUMERIC(18,2); inspect schema before proceeding.';
    END IF;
    IF EXISTS (SELECT 1 FROM public.payroll_employee_detail WHERE penalty_deduction < 0) THEN
        RAISE EXCEPTION 'Negative existing penalty_deduction requires review; no data was changed.';
    END IF;
    -- Never silently rewrite finalized salaries or correction-ledger economics.
    IF EXISTS (
        SELECT 1 FROM public.payroll_employee_detail ped
        JOIN public.payroll_run pr ON pr.payroll_run_id_pk = ped.payroll_run_id_fk
        WHERE COALESCE(ped.penalty_deduction, 0) <> 0
          AND (pr.payroll_run_type = 'CORRECTION'
               OR pr.payroll_status NOT IN ('DRAFT', 'PROCESSED')
               OR ped.payroll_detail_status NOT IN ('DRAFT', 'PROCESSED'))
    ) THEN
        RAISE EXCEPTION 'Existing nonzero penalty_deduction on correction or non-editable payroll requires an approved reconciliation; migration stopped without changes.';
    END IF;
END $$;

ALTER TABLE public.payroll_employee_detail
    ALTER COLUMN total_deduction SET EXPRESSION AS (
        unpaid_leave_deduction + loan_deduction + advance_deduction
        + fine_deduction + fuel_deduction + salik_deduction + darb_deduction
        + charging_cost_deduction + other_deduction_amount
        + COALESCE(penalty_deduction, 0)
    ),
    ALTER COLUMN net_salary SET EXPRESSION AS (
        CASE
            WHEN correction_net_amount <> 0 OR correction_recovery_amount <> 0
            THEN GREATEST(correction_net_amount, 0)
            ELSE (
                basic_salary + monthly_allowance + accommodation_allowance
                + overtime_amount + bonus_amount + incentive_amount
                + reimbursement_amount + other_earning_amount
            ) - (
                unpaid_leave_deduction + loan_deduction + advance_deduction
                + fine_deduction + fuel_deduction + salik_deduction + darb_deduction
                + charging_cost_deduction + other_deduction_amount
                + COALESCE(penalty_deduction, 0)
            )
        END
    ),
    DROP CONSTRAINT chk_payroll_detail_correction_recovery_limit,
    ADD CONSTRAINT chk_payroll_detail_correction_recovery_limit CHECK (
        correction_recovery_amount <= (
            unpaid_leave_deduction + loan_deduction + advance_deduction
            + fine_deduction + fuel_deduction + salik_deduction + darb_deduction
            + charging_cost_deduction + other_deduction_amount
            + COALESCE(penalty_deduction, 0)
        )
    );

-- Refresh only affected editable run totals from generated detail values.
-- Do not add the penalty again to SUM(total_deduction).
UPDATE public.payroll_run pr
SET total_deductions = totals.deductions,
    total_net_salary = totals.net_salary
FROM (
    SELECT payroll_run_id_fk,
           COALESCE(SUM(total_deduction), 0)::NUMERIC(18,2) AS deductions,
           COALESCE(SUM(net_salary), 0)::NUMERIC(18,2) AS net_salary
    FROM public.payroll_employee_detail
    GROUP BY payroll_run_id_fk
    HAVING BOOL_OR(COALESCE(penalty_deduction, 0) <> 0)
) totals
WHERE pr.payroll_run_id_pk = totals.payroll_run_id_fk
  AND pr.payroll_status IN ('DRAFT', 'PROCESSED')
  AND pr.payroll_run_type <> 'CORRECTION'
  AND (pr.total_deductions IS DISTINCT FROM totals.deductions
       OR pr.total_net_salary IS DISTINCT FROM totals.net_salary);

COMMIT;
