-- Employee advances only. Payroll linkage is handled by the frontend.
BEGIN;

CREATE TABLE IF NOT EXISTS public.advance_master (
    advance_id_pk BIGSERIAL PRIMARY KEY,
    advance_empl_id_fk BIGINT NOT NULL,
    advance_date DATE NOT NULL,
    advance_reason TEXT NOT NULL,
    advance_amount NUMERIC(14,2) NOT NULL,
    recovery_split_percentage NUMERIC(5,2) NOT NULL,
    created_by VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(100),
    updated_at TIMESTAMP WITHOUT TIME ZONE,

    CONSTRAINT fk_advance_master_employee
        FOREIGN KEY (advance_empl_id_fk)
        REFERENCES public.employee_master (empl_id_pk)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT ck_advance_reason
        CHECK (advance_reason ~ '[^[:space:]]'),
    CONSTRAINT ck_advance_amount
        CHECK (advance_amount > 0 AND advance_amount < 'NaN'::NUMERIC),
    CONSTRAINT ck_advance_recovery_split
        CHECK (recovery_split_percentage BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS idx_advance_employee
    ON public.advance_master (advance_empl_id_fk);
CREATE INDEX IF NOT EXISTS idx_advance_date
    ON public.advance_master (advance_date);

COMMIT;
