-- Run BEFORE additive DDL; uses only existing columns. Save output for comparison.
BEGIN READ ONLY;
SELECT ar_id_pk,ar_invoice_number,ar_invoice_amount,ar_tax_amount,ar_received_amount,ar_balance_amount
FROM public.accounts_receivables ORDER BY ar_id_pk;
SELECT count(*) AS header_count,sum(ar_invoice_amount) AS gross,sum(ar_tax_amount) AS included_tax,
       sum(ar_received_amount) AS received,sum(ar_balance_amount) AS balance
FROM public.accounts_receivables;
SELECT org_id_pk,org_name,company_registration_number FROM public.organization_master ORDER BY org_id_pk;
COMMIT;
