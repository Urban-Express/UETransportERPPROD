-- Optional controlled seed, SEPARATE from schema migration. Not executed in production.
-- Caller must set: SET ar_rollout.urban_express_org_id = '<verified organization ID>';
-- Approved initial values from the user's 7 October 2026 clarification.
BEGIN;
DO $$
DECLARE org_id INTEGER := current_setting('ar_rollout.urban_express_org_id')::integer;
        organization public.organization_master%ROWTYPE;
BEGIN
    SELECT * INTO STRICT organization FROM public.organization_master WHERE org_id_pk=org_id FOR UPDATE;
    IF organization.org_name NOT ILIKE '%Urban Express%' THEN
        RAISE EXCEPTION 'Selected organization is not Urban Express; verify the seed target';
    END IF;
    IF nullif(btrim(organization.company_registration_number),'') IS NULL THEN
        UPDATE public.organization_master SET company_registration_number='1349832' WHERE org_id_pk=org_id;
    ELSIF btrim(organization.company_registration_number) <> '1349832' THEN
        RAISE EXCEPTION 'Existing company registration conflicts with approved Trade License 1349832; resolve explicitly';
    END IF;
    INSERT INTO public.organization_tax_configuration
        (tax_org_id_fk,tax_code,tax_name,tax_treatment,tax_rate,is_default,is_active,created_by)
    VALUES (org_id,'STANDARD_VAT','Standard VAT','STANDARD',5.00,TRUE,TRUE,'AR approved configuration seed')
    ON CONFLICT (tax_org_id_fk,tax_code) DO NOTHING;
    INSERT INTO public.organization_invoice_configuration
        (invoice_org_id_fk,organization_trn,bank_name,bank_account_name,bank_account_number,iban,signatory_name,signatory_designation,created_by)
    SELECT org_id,'104382694800003','Emirates Islamic Bank','Urban Express Transport L.L.C',
        '3708506470001','AE810340003708506470001','Dr. Mohamad Al Hashimi','Founder & CEO','AR approved configuration seed'
    WHERE NOT EXISTS (SELECT 1 FROM public.organization_invoice_configuration WHERE invoice_org_id_fk=org_id AND is_active);
END $$;
COMMIT;

ROLLBACK;

SELECT now();

SELECT
    to_regclass('public.accounts_receivables')              AS ar_header,
    to_regclass('public.accounts_receivable_lines')         AS ar_lines,
    to_regclass('public.organization_tax_configuration')    AS tax_config,
    to_regclass('public.organization_invoice_configuration') AS invoice_config;

SELECT
    org_id_pk,
    org_name,
    company_registration_number
FROM public.organization_master
ORDER BY org_id_pk;

ROLLBACK;

SET ar_rollout.urban_express_org_id = '1';

SELECT
    current_setting('ar_rollout.urban_express_org_id') AS seed_org_id;

SELECT
    org_id_pk,
    org_name,
    company_registration_number
FROM public.organization_master
WHERE org_id_pk =
      current_setting('ar_rollout.urban_express_org_id')::integer;

SELECT
    tax_config_id_pk,
    tax_org_id_fk,
    tax_code,
    tax_name,
    tax_treatment,
    tax_rate,
    is_default,
    is_active
FROM public.organization_tax_configuration
ORDER BY tax_org_id_fk, tax_code;

SELECT
    invoice_config_id_pk,
    invoice_org_id_fk,
    organization_trn,
    bank_name,
    bank_account_name,
    bank_account_number,
    iban,
    signatory_name,
    signatory_designation,
    is_active
FROM public.organization_invoice_configuration
ORDER BY invoice_org_id_fk;

-- AFTER migration: compare historical IDs/money to baseline_verification.sql output.
BEGIN READ ONLY;
SELECT ar_id_pk,ar_invoice_number,ar_invoice_amount,ar_tax_amount,ar_received_amount,ar_balance_amount
FROM public.accounts_receivables ORDER BY ar_id_pk;
SELECT count(*) AS header_count, sum(ar_invoice_amount) AS gross, sum(ar_tax_amount) AS included_tax,
       sum(ar_received_amount) AS received, sum(ar_balance_amount) AS balance FROM public.accounts_receivables;
-- Must return zero rows after migration/new writes.
SELECT h.ar_id_pk FROM public.accounts_receivables h
LEFT JOIN LATERAL (SELECT count(*) AS n,sum(ar_line_net_amount) AS net,sum(ar_line_tax_amount) AS tax,
    sum(ar_line_total_amount) AS total FROM public.accounts_receivable_lines l WHERE l.ar_line_ar_id_fk=h.ar_id_pk) s ON TRUE
WHERE (h.ar_subtotal_amount IS NULL AND s.n <> 0)
   OR (h.ar_subtotal_amount IS NOT NULL AND (s.n=0 OR h.ar_subtotal_amount<>s.net
       OR h.ar_tax_amount<>s.tax OR h.ar_invoice_amount<>s.total OR h.ar_invoice_identity_snapshot IS NULL));
SELECT count(*) AS legacy_header_only FROM public.accounts_receivables WHERE ar_subtotal_amount IS NULL;
SELECT conrelid::regclass AS table_name,conname,pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid IN ('public.accounts_receivable_lines'::regclass,'public.organization_tax_configuration'::regclass,
    'public.organization_invoice_configuration'::regclass,'public.accounts_receivables'::regclass) ORDER BY 1,2;
SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename IN
    ('accounts_receivable_lines','organization_tax_configuration','organization_invoice_configuration','accounts_receivables');
SELECT tax_org_id_fk,count(*) FILTER (WHERE is_active AND is_default) AS active_defaults
FROM public.organization_tax_configuration GROUP BY tax_org_id_fk;
COMMIT;