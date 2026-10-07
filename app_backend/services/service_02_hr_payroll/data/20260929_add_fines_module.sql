-- ID_003: employee-linked Fine data entry and attachments only.
-- Creation DDL; existing tables are not altered by CREATE TABLE IF NOT EXISTS.
BEGIN;

-- Explicit sequence/default keeps automatic IDs without BIGSERIAL's implicit
-- NOT NULL constraint. No primary key, foreign key, CHECK, UNIQUE, or NOT NULL
-- constraints are declared for fine_master.
CREATE TABLE IF NOT EXISTS public.fine_master (
    fine_id_pk BIGSERIAL PRIMARY KEY,
    fine_org_id_fk BIGINT,
    fine_empl_id_fk BIGINT,
    fine_date DATE,
    fine_on VARCHAR(20),
    fine_accountability VARCHAR(20),
    payment_authority VARCHAR(20),
    amount_paid NUMERIC(18,2),
    recovery_split NUMERIC(5,2),
    fine_attachment_path TEXT,
    created_by VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(100),
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
	-- Organization relationship
	CONSTRAINT fk_fine_master_organization
		FOREIGN KEY (fine_org_id_fk)
		REFERENCES organization_master(org_id_pk)
		ON UPDATE RESTRICT
		ON DELETE RESTRICT,
	-- Employee relationship
	CONSTRAINT fk_fine_master_employee
		FOREIGN KEY (fine_empl_id_fk)
		REFERENCES employee_master(empl_id_pk)
		ON UPDATE RESTRICT
		ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_fine_org_id ON public.fine_master(fine_org_id_fk, fine_id_pk);
CREATE INDEX IF NOT EXISTS idx_fine_org_date ON public.fine_master(fine_org_id_fk, fine_date);
CREATE INDEX IF NOT EXISTS idx_fine_org_employee ON public.fine_master(fine_org_id_fk, fine_empl_id_fk);
COMMIT;
