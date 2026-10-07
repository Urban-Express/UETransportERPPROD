-- Penalty data entry and attachments only; payroll linkage belongs to the frontend.
BEGIN;

CREATE TABLE IF NOT EXISTS public.penalty_master (
    penalty_id_pk BIGSERIAL PRIMARY KEY,
    penalty_org_id_fk BIGINT NOT NULL,
    penalty_empl_id_fk BIGINT NOT NULL,
    penalty_date DATE NOT NULL,
    penalty_reason TEXT NOT NULL,

    warning_letter_issued BOOLEAN NOT NULL DEFAULT FALSE,
    warning_letter_date DATE,
    warning_letter_accepted BOOLEAN,
    penalty_attachment_path TEXT,

    financial_implication BOOLEAN NOT NULL DEFAULT FALSE,
    penalty_amount NUMERIC(18,2),
    recovery_split_percentage NUMERIC(5,2),

    created_by VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(100),
    updated_at TIMESTAMP,

    CONSTRAINT fk_penalty_master_organization
        FOREIGN KEY (penalty_org_id_fk)
        REFERENCES public.organization_master (org_id_pk)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_penalty_master_employee
        FOREIGN KEY (penalty_empl_id_fk)
        REFERENCES public.employee_master (empl_id_pk)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT ck_penalty_reason
        CHECK (penalty_reason ~ '[^[:space:]]'),
    CONSTRAINT ck_penalty_warning_letter
        CHECK (
            (warning_letter_issued = FALSE AND warning_letter_date IS NULL
                AND warning_letter_accepted IS NULL AND penalty_attachment_path IS NULL)
            OR
            (warning_letter_issued = TRUE AND warning_letter_date IS NOT NULL
                AND warning_letter_accepted IS NOT NULL)
        ),
    CONSTRAINT ck_penalty_financial_implication
        CHECK (
            (financial_implication = FALSE AND penalty_amount IS NULL
                AND recovery_split_percentage IS NULL)
            OR
            (financial_implication = TRUE AND penalty_amount IS NOT NULL
                AND penalty_amount > 0 AND penalty_amount < 'NaN'::NUMERIC
                AND recovery_split_percentage IS NOT NULL
                AND recovery_split_percentage BETWEEN 0 AND 100)
        )
);

CREATE INDEX IF NOT EXISTS idx_penalty_org_date
    ON public.penalty_master (penalty_org_id_fk, penalty_date);
CREATE INDEX IF NOT EXISTS idx_penalty_org_employee
    ON public.penalty_master (penalty_org_id_fk, penalty_empl_id_fk);
CREATE INDEX IF NOT EXISTS idx_penalty_employee_date
    ON public.penalty_master (penalty_empl_id_fk, penalty_date);

COMMIT;
