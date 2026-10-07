-- Additive only. Apply in development first; never run automatically on startup.
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.organization_tax_configuration (
    tax_config_id_pk BIGSERIAL PRIMARY KEY,
    tax_org_id_fk INTEGER NOT NULL REFERENCES public.organization_master(org_id_pk) ON DELETE RESTRICT,
    tax_code VARCHAR(40) NOT NULL,
    tax_name VARCHAR(200) NOT NULL,
    tax_treatment VARCHAR(30) NOT NULL CHECK (tax_treatment IN ('STANDARD','ZERO_RATED','EXEMPT','OUT_OF_SCOPE')),
    tax_rate NUMERIC(9,4) NOT NULL CHECK (tax_rate >= 0 AND tax_rate <> 'NaN'::numeric),
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from DATE,
    effective_to DATE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(100),
    CONSTRAINT uq_org_tax_code UNIQUE (tax_org_id_fk, tax_code),
    CONSTRAINT ck_org_tax_dates CHECK (effective_to >= effective_from),
    CONSTRAINT ck_org_tax_treatment_rate CHECK (tax_treatment = 'STANDARD' OR tax_rate = 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_tax_active_default
    ON public.organization_tax_configuration(tax_org_id_fk) WHERE is_default AND is_active;

CREATE TABLE IF NOT EXISTS public.organization_invoice_configuration (
    invoice_config_id_pk BIGSERIAL PRIMARY KEY,
    invoice_org_id_fk INTEGER NOT NULL REFERENCES public.organization_master(org_id_pk) ON DELETE RESTRICT,
    organization_trn VARCHAR(50) NOT NULL,
    bank_name VARCHAR(200) NOT NULL,
    bank_account_name VARCHAR(200) NOT NULL,
    bank_account_number VARCHAR(100) NOT NULL,
    iban VARCHAR(100) NOT NULL,
    signatory_name VARCHAR(200) NOT NULL,
    signatory_designation VARCHAR(200) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(100)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_invoice_active
    ON public.organization_invoice_configuration(invoice_org_id_fk) WHERE is_active;

ALTER TABLE public.accounts_receivables
    ADD COLUMN IF NOT EXISTS ar_contract_id_fk INTEGER REFERENCES public.contracts_management(cont_id_pk) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS ar_contract_number_snapshot VARCHAR(50),
    ADD COLUMN IF NOT EXISTS ar_contract_name_snapshot VARCHAR(200),
    ADD COLUMN IF NOT EXISTS ar_billing_period_start DATE,
    ADD COLUMN IF NOT EXISTS ar_billing_period_end DATE,
    ADD COLUMN IF NOT EXISTS ar_subtotal_amount NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS ar_invoice_identity_snapshot JSONB,
    ADD COLUMN IF NOT EXISTS ar_revision BIGINT NOT NULL DEFAULT 0;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.accounts_receivables'::regclass AND conname='ck_ar_line_header_totals') THEN
        ALTER TABLE public.accounts_receivables ADD CONSTRAINT ck_ar_line_header_totals
            CHECK (ar_subtotal_amount IS NULL OR (ar_subtotal_amount >= 0 AND ar_subtotal_amount <> 'NaN'::numeric
                AND ar_invoice_amount = ar_subtotal_amount + ar_tax_amount));
        ALTER TABLE public.accounts_receivables ADD CONSTRAINT ck_ar_billing_period CHECK (ar_billing_period_end >= ar_billing_period_start);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_ar_contract ON public.accounts_receivables(ar_contract_id_fk);

CREATE TABLE IF NOT EXISTS public.accounts_receivable_lines (
    ar_line_id_pk BIGSERIAL PRIMARY KEY,
    ar_line_ar_id_fk INTEGER NOT NULL REFERENCES public.accounts_receivables(ar_id_pk) ON DELETE CASCADE,
    ar_line_number INTEGER NOT NULL CHECK (ar_line_number > 0),
    ar_line_source_type VARCHAR(30) NOT NULL CHECK (ar_line_source_type IN ('CONTRACT_AUTOFILL','MANUAL','LEGACY_MIGRATION')),
    ar_line_source_contract_id_fk INTEGER REFERENCES public.contracts_management(cont_id_pk) ON DELETE RESTRICT,
    ar_line_description TEXT NOT NULL CHECK (length(btrim(ar_line_description)) > 0),
    ar_line_vehicle_description TEXT,
    ar_line_quantity NUMERIC(18,4) NOT NULL CHECK (ar_line_quantity >= 0 AND ar_line_quantity <> 'NaN'::numeric),
    ar_line_uom VARCHAR(40),
    ar_line_unit_rate NUMERIC(18,2) NOT NULL CHECK (ar_line_unit_rate >= 0 AND ar_line_unit_rate <> 'NaN'::numeric),
    ar_line_service_period_start DATE,
    ar_line_service_period_end DATE,
    ar_line_proration_method VARCHAR(30) CHECK (ar_line_proration_method = 'CALENDAR_DAYS'),
    ar_line_proration_numerator NUMERIC(18,4),
    ar_line_proration_denominator NUMERIC(18,4),
    ar_line_base_amount NUMERIC(18,2) NOT NULL CHECK (ar_line_base_amount >= 0 AND ar_line_base_amount <> 'NaN'::numeric),
    ar_line_net_amount NUMERIC(18,2) NOT NULL CHECK (ar_line_net_amount >= 0 AND ar_line_net_amount <> 'NaN'::numeric),
    ar_line_tax_config_id_fk BIGINT NOT NULL REFERENCES public.organization_tax_configuration(tax_config_id_pk) ON DELETE RESTRICT,
    ar_line_tax_code VARCHAR(40) NOT NULL,
    ar_line_tax_treatment VARCHAR(30) NOT NULL CHECK (ar_line_tax_treatment IN ('STANDARD','ZERO_RATED','EXEMPT','OUT_OF_SCOPE')),
    ar_line_tax_rate NUMERIC(9,4) NOT NULL CHECK (ar_line_tax_rate >= 0 AND ar_line_tax_rate <> 'NaN'::numeric),
    ar_line_tax_amount NUMERIC(18,2) NOT NULL CHECK (ar_line_tax_amount >= 0 AND ar_line_tax_amount <> 'NaN'::numeric),
    ar_line_total_amount NUMERIC(18,2) NOT NULL CHECK (ar_line_total_amount >= 0 AND ar_line_total_amount <> 'NaN'::numeric),
    ar_line_notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100) NOT NULL DEFAULT CURRENT_USER,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(100),
    CONSTRAINT uq_ar_line_number UNIQUE (ar_line_ar_id_fk, ar_line_number) DEFERRABLE INITIALLY IMMEDIATE,
    CONSTRAINT ck_ar_line_service_period CHECK (ar_line_service_period_end >= ar_line_service_period_start),
    CONSTRAINT ck_ar_line_total CHECK (ar_line_total_amount = ar_line_net_amount + ar_line_tax_amount),
    CONSTRAINT ck_ar_line_tax_amount CHECK (ar_line_tax_amount = round(ar_line_net_amount * ar_line_tax_rate / 100, 2)),
    CONSTRAINT ck_ar_line_tax_treatment CHECK (ar_line_tax_treatment = 'STANDARD' OR ar_line_tax_rate = 0),
    CONSTRAINT ck_ar_line_contract_source CHECK ((ar_line_source_type = 'CONTRACT_AUTOFILL') = (ar_line_source_contract_id_fk IS NOT NULL)),
    CONSTRAINT ck_ar_line_proration CHECK (
        (ar_line_proration_method IS NULL AND ar_line_proration_numerator IS NULL AND ar_line_proration_denominator IS NULL)
        OR (ar_line_proration_method IS NOT NULL AND ar_line_service_period_start IS NOT NULL
            AND ar_line_service_period_end IS NOT NULL AND ar_line_proration_numerator IS NOT NULL
            AND ar_line_proration_denominator IS NOT NULL AND ar_line_proration_numerator > 0
            AND ar_line_proration_denominator > 0 AND ar_line_proration_numerator <= ar_line_proration_denominator))
);
CREATE INDEX IF NOT EXISTS idx_ar_line_contract ON public.accounts_receivable_lines(ar_line_source_contract_id_fk);
CREATE INDEX IF NOT EXISTS idx_ar_line_tax_config ON public.accounts_receivable_lines(ar_line_tax_config_id_fk);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ar_one_contract_line ON public.accounts_receivable_lines(ar_line_ar_id_fk)
    WHERE ar_line_source_type = 'CONTRACT_AUTOFILL';

CREATE OR REPLACE FUNCTION public.fn_ar_detail_set_updated_at() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END $$;
DO $$ DECLARE t TEXT; BEGIN
    FOREACH t IN ARRAY ARRAY['accounts_receivable_lines','organization_tax_configuration','organization_invoice_configuration'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=('public.' || t)::regclass AND tgname='trg_ar_detail_updated_at') THEN
            EXECUTE format('CREATE TRIGGER trg_ar_detail_updated_at BEFORE UPDATE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.fn_ar_detail_set_updated_at()', t);
        END IF;
    END LOOP;
END $$;

COMMENT ON COLUMN public.accounts_receivables.ar_invoice_amount IS 'Total payable INCLUDING ar_tax_amount. Never add tax to this value. Historical money is unchanged.';
COMMENT ON COLUMN public.accounts_receivables.ar_subtotal_amount IS 'New line invoices: SUM(line net). NULL for historical header-only invoices; no inferred historical subtotal.';
COMMENT ON COLUMN public.accounts_receivables.ar_invoice_identity_snapshot IS 'Server-owned issuer/customer/configuration identity used for the approved document. Retained across edits unless customer changes.';
COMMENT ON TABLE public.accounts_receivable_lines IS 'Approved AR aggregate children; no public line CRUD. Header deletion cascades only through approved AR DELETE.';
COMMIT;
