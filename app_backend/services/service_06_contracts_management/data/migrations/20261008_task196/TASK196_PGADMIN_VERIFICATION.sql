-- Task #196: independent READ ONLY verification for PostgreSQL 18.
-- Exact constraint definitions verified on PostgreSQL 18.6.
-- Run the ENTIRE file after the migration commits, against the SAME confirmed
-- Railway database. A fresh Query Tool is permitted; no session variables needed.
-- Enable Auto commit and Auto rollback on error. No table/data changes are made.
-- Require TASK196_PGADMIN_VERIFICATION_PASS and no errors.
-- The final ROLLBACK is intentional: it closes this READ ONLY transaction.

BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '20s';
SET LOCAL lock_timeout = '5s';
SET LOCAL search_path = pg_catalog, public;

SELECT current_database() AS database_name, current_schema() AS current_schema,
       current_setting('search_path') AS verification_search_path,
       inet_server_addr()::text AS server_address, inet_server_port() AS server_port,
       'public.contracts_management'::regclass::oid AS contract_table_oid;

DO $verify$
DECLARE
    -- Fixed values from the confirmed production preflight. Do not auto-derive or bypass.
    v_expected_database CONSTANT text := 'railway';
    v_expected_database_oid CONSTANT oid := 16384;
    v_expected_contract_oid CONSTANT oid := 17544;
    v_expected_system_identifier CONSTANT text := '7626676962491977764';
    v_name text; v_expected text; v_field text;
BEGIN
    IF current_setting('server_version_num')::integer / 10000 <> 18 THEN
        RAISE EXCEPTION 'TASK196_VERSION_MISMATCH: this reviewed script requires PostgreSQL 18';
    END IF;
    IF current_database() IS DISTINCT FROM v_expected_database
       OR (SELECT oid FROM pg_database WHERE datname = current_database()) IS DISTINCT FROM v_expected_database_oid
       OR to_regclass('public.contracts_management')::oid IS DISTINCT FROM v_expected_contract_oid THEN
        RAISE EXCEPTION 'TASK196_TARGET_MISMATCH: database name/OID or Contract table OID differs from the confirmed production target; stop for review';
    END IF;
    IF NOT has_function_privilege('pg_catalog.pg_control_system()', 'EXECUTE') THEN
        RAISE EXCEPTION 'TASK196_IDENTITY_PERMISSION_REQUIRED: use an authorized role able to read the PostgreSQL cluster identity; do not bypass this check';
    END IF;
    IF (SELECT system_identifier::text FROM pg_control_system()) IS DISTINCT FROM v_expected_system_identifier THEN
        RAISE EXCEPTION 'TASK196_CLUSTER_MISMATCH: connected to a different PostgreSQL cluster; stop for review';
    END IF;
    FOR v_name, v_expected IN SELECT * FROM (VALUES
        ('ck_contracts_management_day_km_nonnegative', $expected$CHECK ((((cont_no_of_days IS NULL) OR (cont_no_of_days >= (0)::numeric)) AND ((cont_per_day_rate IS NULL) OR (cont_per_day_rate >= (0)::numeric)) AND ((cont_no_of_kms IS NULL) OR (cont_no_of_kms >= (0)::numeric)) AND ((cont_per_km_rate IS NULL) OR (cont_per_km_rate >= (0)::numeric))))$expected$),
        ('ck_contracts_management_pricing_completeness', $expected$CHECK ((((cont_status)::text = 'DRAFT'::text) OR (((cont_revenue_basis)::text = 'PER_PASSENGER'::text) AND (cont_no_of_passengers > 0) AND (cont_per_passenger_rate_pm IS NOT NULL)) OR (((cont_revenue_basis)::text = 'PER_BUS'::text) AND (cont_no_of_buses > 0) AND ((cont_big_bus_count_gt_34 = 0) OR (cont_big_bus_rate_pm IS NOT NULL)) AND ((cont_medium_bus_count_17_34 = 0) OR (cont_medium_bus_rate_pm IS NOT NULL)) AND ((cont_small_bus_count_lt_17 = 0) OR (cont_small_bus_rate_pm IS NOT NULL))) OR (((cont_revenue_basis)::text = 'PER_PASSENGER_AND_PER_BUS'::text) AND (cont_no_of_passengers > 0) AND (cont_per_passenger_rate_pm IS NOT NULL) AND (cont_no_of_buses > 0) AND ((cont_big_bus_count_gt_34 = 0) OR (cont_big_bus_rate_pm IS NOT NULL)) AND ((cont_medium_bus_count_17_34 = 0) OR (cont_medium_bus_rate_pm IS NOT NULL)) AND ((cont_small_bus_count_lt_17 = 0) OR (cont_small_bus_rate_pm IS NOT NULL))) OR (((cont_revenue_basis)::text = 'PER_DAY'::text) AND (cont_no_of_days IS NOT NULL) AND (cont_no_of_days > (0)::numeric) AND (cont_per_day_rate IS NOT NULL)) OR (((cont_revenue_basis)::text = 'PER_KILOMETER'::text) AND (cont_no_of_kms IS NOT NULL) AND (cont_no_of_kms > (0)::numeric) AND (cont_per_km_rate IS NOT NULL))))$expected$),
        ('ck_contracts_management_revenue_basis', $expected$CHECK (((cont_revenue_basis)::text = ANY ((ARRAY['PER_BUS'::character varying, 'PER_PASSENGER'::character varying, 'PER_PASSENGER_AND_PER_BUS'::character varying, 'PER_DAY'::character varying, 'PER_KILOMETER'::character varying])::text[])))$expected$)
    ) AS expected(name, definition) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.contracts_management'::regclass AND conname=v_name
              AND contype='c' AND conenforced AND convalidated AND NOT connoinherit
              AND pg_get_constraintdef(oid)=v_expected) THEN
            RAISE EXCEPTION 'TASK196_VERIFICATION_FAILED: unexpected or unvalidated %', v_name;
        END IF;
    END LOOP;
    FOREACH v_field IN ARRAY ARRAY['cont_no_of_days','cont_per_day_rate','cont_no_of_kms','cont_per_km_rate'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.contracts_management'::regclass
            AND attname=v_field AND NOT attisdropped AND format_type(atttypid,atttypmod)='numeric(14,2)'
            AND NOT attnotnull AND NOT atthasdef AND attgenerated='') THEN
            RAISE EXCEPTION 'TASK196_COLUMN_VERIFICATION_FAILED: %', v_field;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.contracts_management'::regclass
        AND attname='cont_revenue_basis' AND attnotnull AND NOT attisdropped) THEN
        RAISE EXCEPTION 'TASK196_BASIS_NULLABILITY_VERIFICATION_FAILED';
    END IF;
    IF EXISTS (SELECT 1 FROM public.contracts_management
        WHERE cont_no_of_days<0 OR cont_per_day_rate<0 OR cont_no_of_kms<0 OR cont_per_km_rate<0) THEN
        RAISE EXCEPTION 'TASK196_NEGATIVE_VALUES_PRESENT';
    END IF;
    RAISE NOTICE 'TASK196_PGADMIN_VERIFICATION_PASS';
END
$verify$;

SELECT conname,conenforced,convalidated,pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE conrelid='public.contracts_management'::regclass ORDER BY conname;
SELECT column_name,data_type,numeric_precision,numeric_scale,is_nullable,column_default,is_generated,generation_expression
FROM information_schema.columns WHERE table_schema='public' AND table_name='contracts_management' ORDER BY ordinal_position;
SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='contracts_management' ORDER BY indexname;
-- Compare unrelated constraints, columns and indexes with preflight evidence.
-- This schema check does not replace the authorized live workflow/GET tests.
ROLLBACK;
