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
