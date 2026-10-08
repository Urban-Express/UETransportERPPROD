-- Task #196: FINAL pgAdmin migration, version 20261008_task196_pgadmin_v1.
-- Derived from the reviewed TASK196_DATABASE_MIGRATION.sql; same three changes only.
-- Target: mainline.proxy.rlwy.net:13751 / railway / public.contracts_management.
-- Dashboard-confirmed Railway Postgres service bb6e9630-5c49-4ed4-8525-851af0101163.
-- Confirm a current full backup and recovery readiness BEFORE executing.
-- Open a NEW pgAdmin Query Tool on railway. Enable Auto commit and Auto rollback
-- on error. Select the ENTIRE file and use Execute script (F5), not Execute query.
-- One explicit transaction; any validation/SQL error aborts all changes.
-- Auto rollback clears the failed transaction. If still aborted, run ROLLBACK;
-- never COMMIT a failed attempt. Stop and review errors; do not change the guards.
-- No approval GUCs, extensions, psql commands, column additions or data updates.
-- The empty TEMP reference table is dropped on commit (or rolled back on failure).

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL idle_in_transaction_session_timeout = '60s';
SET LOCAL search_path = pg_catalog, public;

DO $task196$
DECLARE
    -- Fixed values from the confirmed production preflight. Do not auto-derive or bypass.
    v_expected_database CONSTANT text := 'railway';
    v_expected_database_oid CONSTANT oid := 16384;
    v_expected_contract_oid CONSTANT oid := 17544;
    v_expected_system_identifier CONSTANT text := '7626676962491977764';
    v_old_basis CONSTANT text := $expr$cont_revenue_basis IN ('PER_PASSENGER', 'PER_BUS', 'PER_PASSENGER_AND_PER_BUS')$expr$;
    v_old_pricing CONSTANT text := $expr$(((cont_status)::text = 'DRAFT'::text) OR (((cont_revenue_basis)::text = 'PER_PASSENGER'::text) AND (cont_no_of_passengers > 0) AND (cont_per_passenger_rate_pm IS NOT NULL)) OR (((cont_revenue_basis)::text = 'PER_BUS'::text) AND (cont_no_of_buses > 0) AND ((cont_big_bus_count_gt_34 = 0) OR (cont_big_bus_rate_pm IS NOT NULL)) AND ((cont_medium_bus_count_17_34 = 0) OR (cont_medium_bus_rate_pm IS NOT NULL)) AND ((cont_small_bus_count_lt_17 = 0) OR (cont_small_bus_rate_pm IS NOT NULL))) OR (((cont_revenue_basis)::text = 'PER_PASSENGER_AND_PER_BUS'::text) AND (cont_no_of_passengers > 0) AND (cont_per_passenger_rate_pm IS NOT NULL) AND (cont_no_of_buses > 0) AND ((cont_big_bus_count_gt_34 = 0) OR (cont_big_bus_rate_pm IS NOT NULL)) AND ((cont_medium_bus_count_17_34 = 0) OR (cont_medium_bus_rate_pm IS NOT NULL)) AND ((cont_small_bus_count_lt_17 = 0) OR (cont_small_bus_rate_pm IS NOT NULL))))$expr$;
    v_new_basis CONSTANT text := $expr$cont_revenue_basis IN ('PER_BUS', 'PER_PASSENGER', 'PER_PASSENGER_AND_PER_BUS', 'PER_DAY', 'PER_KILOMETER')$expr$;
    v_new_pricing CONSTANT text := $expr$((((cont_status)::text = 'DRAFT'::text) OR (((cont_revenue_basis)::text = 'PER_PASSENGER'::text) AND (cont_no_of_passengers > 0) AND (cont_per_passenger_rate_pm IS NOT NULL)) OR (((cont_revenue_basis)::text = 'PER_BUS'::text) AND (cont_no_of_buses > 0) AND ((cont_big_bus_count_gt_34 = 0) OR (cont_big_bus_rate_pm IS NOT NULL)) AND ((cont_medium_bus_count_17_34 = 0) OR (cont_medium_bus_rate_pm IS NOT NULL)) AND ((cont_small_bus_count_lt_17 = 0) OR (cont_small_bus_rate_pm IS NOT NULL))) OR (((cont_revenue_basis)::text = 'PER_PASSENGER_AND_PER_BUS'::text) AND (cont_no_of_passengers > 0) AND (cont_per_passenger_rate_pm IS NOT NULL) AND (cont_no_of_buses > 0) AND ((cont_big_bus_count_gt_34 = 0) OR (cont_big_bus_rate_pm IS NOT NULL)) AND ((cont_medium_bus_count_17_34 = 0) OR (cont_medium_bus_rate_pm IS NOT NULL)) AND ((cont_small_bus_count_lt_17 = 0) OR (cont_small_bus_rate_pm IS NOT NULL))))
    OR (cont_revenue_basis = 'PER_DAY' AND cont_no_of_days IS NOT NULL AND cont_no_of_days > 0 AND cont_per_day_rate IS NOT NULL)
    OR (cont_revenue_basis = 'PER_KILOMETER' AND cont_no_of_kms IS NOT NULL AND cont_no_of_kms > 0 AND cont_per_km_rate IS NOT NULL))$expr$;
    v_nonnegative CONSTANT text := $expr$(cont_no_of_days IS NULL OR cont_no_of_days >= 0)
    AND (cont_per_day_rate IS NULL OR cont_per_day_rate >= 0)
    AND (cont_no_of_kms IS NULL OR cont_no_of_kms >= 0)
    AND (cont_per_km_rate IS NULL OR cont_per_km_rate >= 0)$expr$;
    v_target oid := to_regclass('public.contracts_management');
    v_field text;
    v_name text;
    v_current text;
    v_old text;
    v_new text;
    v_expected text;
    v_sql text;
    v_invalid_ids integer[];
    v_changed boolean := false;
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
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid = v_target AND relkind = 'r' AND NOT relispartition) THEN
        RAISE EXCEPTION 'TASK196_UNEXPECTED_TABLE: expected an ordinary public.contracts_management table';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = v_target OR inhparent = v_target) THEN
        RAISE EXCEPTION 'TASK196_UNEXPECTED_INHERITANCE: stop for review';
    END IF;
    -- Bound wait; prevent data/DDL races between preflight and constraint replacement.
    LOCK TABLE public.contracts_management IN ACCESS EXCLUSIVE MODE;
    -- Recheck after locking so a concurrent table rename/recreation cannot change the target.
    IF 'public.contracts_management'::regclass::oid IS DISTINCT FROM v_target THEN
        RAISE EXCEPTION 'TASK196_TARGET_CHANGED_DURING_LOCK: stop for review';
    END IF;
    FOREACH v_field IN ARRAY ARRAY['cont_no_of_days','cont_per_day_rate','cont_no_of_kms','cont_per_km_rate'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute
            WHERE attrelid = v_target AND attname = v_field AND NOT attisdropped
              AND format_type(atttypid, atttypmod) = 'numeric(14,2)'
              AND NOT attnotnull AND NOT atthasdef AND attgenerated = ''
        ) THEN
            RAISE EXCEPTION 'TASK196_COLUMN_MISMATCH: % must be nullable numeric(14,2) with no default', v_field;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = v_target
        AND attname = 'cont_revenue_basis' AND attnotnull AND NOT attisdropped) THEN
        RAISE EXCEPTION 'TASK196_BASIS_NULLABILITY_MISMATCH';
    END IF;

    -- Parse the approved old/new expressions on this PostgreSQL version and with
    -- the target column types. The empty reference table is session-local only.
    CREATE TEMP TABLE task196_constraint_reference (LIKE public.contracts_management) ON COMMIT DROP;
    EXECUTE format('ALTER TABLE pg_temp.task196_constraint_reference ADD CONSTRAINT old_basis CHECK (%s)', v_old_basis);
    EXECUTE format('ALTER TABLE pg_temp.task196_constraint_reference ADD CONSTRAINT old_pricing CHECK (%s)', v_old_pricing);
    EXECUTE format('ALTER TABLE pg_temp.task196_constraint_reference ADD CONSTRAINT new_basis CHECK (%s)', v_new_basis);
    EXECUTE format('ALTER TABLE pg_temp.task196_constraint_reference ADD CONSTRAINT new_pricing CHECK (%s)', v_new_pricing);
    EXECUTE format('ALTER TABLE pg_temp.task196_constraint_reference ADD CONSTRAINT nonnegative CHECK (%s)', v_nonnegative);

    FOR v_name, v_old, v_new IN SELECT * FROM (VALUES
        ('ck_contracts_management_revenue_basis','old_basis','new_basis'),
        ('ck_contracts_management_pricing_completeness','old_pricing','new_pricing'),
        ('ck_contracts_management_day_km_nonnegative',NULL,'nonnegative')
    ) AS definitions(constraint_name, old_reference, new_reference) LOOP
        SELECT pg_get_expr(conbin, conrelid) INTO v_current FROM pg_constraint
        WHERE conrelid = v_target AND conname = v_name AND contype = 'c' AND conenforced AND NOT connoinherit;
        IF NOT FOUND THEN
            IF v_old IS NOT NULL OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = v_target AND conname = v_name) THEN
                RAISE EXCEPTION 'TASK196_CONSTRAINT_MISMATCH: missing or unexpected %', v_name;
            END IF;
        ELSE
            IF NOT EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid = 'pg_temp.task196_constraint_reference'::regclass
                  AND conname IN (v_old, v_new) AND pg_get_expr(conbin, conrelid) = v_current) THEN
                RAISE EXCEPTION 'TASK196_CONSTRAINT_MISMATCH: unexpected definition for %; stop for review', v_name;
            END IF;
        END IF;
    END LOOP;

    SELECT array_agg(cont_id_pk ORDER BY cont_id_pk) INTO v_invalid_ids
    FROM public.contracts_management
    WHERE cont_no_of_days < 0 OR cont_per_day_rate < 0 OR cont_no_of_kms < 0 OR cont_per_km_rate < 0;
    IF v_invalid_ids IS NOT NULL THEN
        RAISE EXCEPTION 'TASK196_NEGATIVE_VALUES: affected contract IDs %; no historical values changed', v_invalid_ids;
    END IF;
    EXECUTE format('SELECT array_agg(cont_id_pk ORDER BY cont_id_pk) FROM public.contracts_management
        WHERE (%s) IS FALSE OR (%s) IS FALSE', v_new_basis, v_new_pricing) INTO v_invalid_ids;
    IF v_invalid_ids IS NOT NULL THEN
        RAISE EXCEPTION 'TASK196_INCOMPATIBLE_ROWS: affected contract IDs %; stop for review', v_invalid_ids;
    END IF;

    FOR v_name, v_new IN SELECT * FROM (VALUES
        ('ck_contracts_management_revenue_basis','new_basis'),
        ('ck_contracts_management_pricing_completeness','new_pricing'),
        ('ck_contracts_management_day_km_nonnegative','nonnegative')
    ) AS definitions(constraint_name, new_reference) LOOP
        SELECT pg_get_expr(conbin, conrelid) INTO STRICT v_expected FROM pg_constraint
        WHERE conrelid = 'pg_temp.task196_constraint_reference'::regclass AND conname = v_new;
        v_sql := CASE v_new WHEN 'new_basis' THEN v_new_basis
            WHEN 'new_pricing' THEN v_new_pricing ELSE v_nonnegative END;
        SELECT pg_get_expr(conbin, conrelid) INTO v_current FROM pg_constraint
        WHERE conrelid = v_target AND conname = v_name;
        IF NOT FOUND THEN
            EXECUTE format('ALTER TABLE public.contracts_management ADD CONSTRAINT %I CHECK (%s)', v_name, v_sql);
            v_changed := true;
        ELSIF v_current IS DISTINCT FROM v_expected THEN
            EXECUTE format('ALTER TABLE public.contracts_management DROP CONSTRAINT %I', v_name);
            EXECUTE format('ALTER TABLE public.contracts_management ADD CONSTRAINT %I CHECK (%s)', v_name, v_sql);
            v_changed := true;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = v_target AND conname = v_name AND NOT convalidated) THEN
            EXECUTE format('ALTER TABLE public.contracts_management VALIDATE CONSTRAINT %I', v_name);
            v_changed := true;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = v_target AND conname = v_name
            AND contype = 'c' AND conenforced AND convalidated AND NOT connoinherit
            AND pg_get_expr(conbin, conrelid) = v_expected) THEN
            RAISE EXCEPTION 'TASK196_VERIFICATION_FAILED: %', v_name;
        END IF;
    END LOOP;
    RAISE NOTICE 'TASK196_PGADMIN_CONSTRAINTS_VERIFIED: changed=%; COMMIT must still succeed', v_changed;
END
$task196$;

COMMIT;

SELECT 'TASK196_PGADMIN_MIGRATION_COMMITTED' AS result,
       current_database() AS database_name,
       'public.contracts_management'::regclass::oid AS contract_table_oid;
