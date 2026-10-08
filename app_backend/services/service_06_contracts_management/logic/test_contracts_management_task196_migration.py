"""Exercise the shipped migration, including refusal and transactional recovery."""
import unittest

from sqlalchemy import text

from .task196_postgres_fixture import DisposableContractDatabase, MIGRATIONS, apply_task196_migration
from .test_contracts_management_task196 import priced_payload
from .test_contracts_management_task196_postgres import insert_row


TARGETS = {"ck_contracts_management_revenue_basis", "ck_contracts_management_pricing_completeness", "ck_contracts_management_day_km_nonnegative"}
MIGRATION_EVIDENCE = []


def snapshot(engine):
    with engine.connect() as conn:
        return {
            "constraints": [dict(r) for r in conn.exec_driver_sql("SELECT oid,conname,contype,convalidated,connoinherit,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='public.contracts_management'::regclass ORDER BY conname").mappings()],
            "columns": [dict(r) for r in conn.exec_driver_sql("SELECT attname,format_type(atttypid,atttypmod) AS datatype,attnotnull,attgenerated,pg_get_expr(adbin,adrelid) AS expression FROM pg_attribute LEFT JOIN pg_attrdef ON adrelid=attrelid AND adnum=attnum WHERE attrelid='public.contracts_management'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum").mappings()],
            "indexes": [tuple(r) for r in conn.exec_driver_sql("SELECT indexrelid,pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid='public.contracts_management'::regclass ORDER BY indexrelid")],
            "rows": [dict(r) for r in conn.exec_driver_sql("SELECT * FROM public.contracts_management ORDER BY cont_id_pk").mappings()],
        }


class ContractTask196MigrationTest(unittest.TestCase):
    def setUp(self):
        self.database = DisposableContractDatabase(legacy=True)
        self.addCleanup(self.database.close)
        self.engine = self.database.engine

    def test_migration_preserves_history_and_unrelated_schema_and_is_idempotent(self):
        with self.engine.begin() as conn:
            for basis in ("PER_BUS", "PER_PASSENGER", "PER_PASSENGER_AND_PER_BUS"):
                insert_row(conn, priced_payload(basis, cont_contract_number=basis, total_contract_value=125000))
        before = snapshot(self.engine)
        apply_task196_migration(self.engine)
        after = snapshot(self.engine)
        for name in ("columns", "indexes", "rows"):
            self.assertEqual(before[name], after[name], name)
        self.assertEqual([r for r in before["constraints"] if r["conname"] not in TARGETS],
                         [r for r in after["constraints"] if r["conname"] not in TARGETS])
        self.assertEqual(len(after["constraints"]), len(before["constraints"]) + 1)
        apply_task196_migration(self.engine)
        self.assertEqual(after, snapshot(self.engine), "Repeated migration must preserve even constraint OIDs")
        MIGRATION_EVIDENCE.append({"test": self.id(), "before": before["constraints"], "after": after["constraints"], "historical_rows_unchanged": True, "repeat_changed_objects": False})

    def test_migrated_and_fresh_installation_constraints_match(self):
        canonical = DisposableContractDatabase()
        self.addCleanup(canonical.close)
        apply_task196_migration(self.engine)
        migrated = snapshot(self.engine)
        fresh = snapshot(canonical.engine)
        for rows in (migrated["constraints"], fresh["constraints"]):
            for row in rows:
                row.pop("oid")
        self.assertEqual(migrated["constraints"], fresh["constraints"])
        self.assertEqual(migrated["columns"], fresh["columns"])
        self.assertEqual([r[1] for r in migrated["indexes"]], [r[1] for r in fresh["indexes"]])

    def test_negative_historical_values_stop_without_changing_any_row_or_constraint(self):
        with self.engine.begin() as conn:
            contract_id = insert_row(conn, priced_payload("PER_BUS", cont_no_of_days=-1))
        before = snapshot(self.engine)
        with self.assertRaisesRegex(Exception, f"TASK196_NEGATIVE_VALUES.*{contract_id}"):
            apply_task196_migration(self.engine)
        self.assertEqual(before, snapshot(self.engine))

    def test_unknown_existing_constraint_is_not_overwritten(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE public.contracts_management DROP CONSTRAINT ck_contracts_management_revenue_basis")
            conn.exec_driver_sql("ALTER TABLE public.contracts_management ADD CONSTRAINT ck_contracts_management_revenue_basis CHECK (cont_revenue_basis IS NOT NULL)")
        before = snapshot(self.engine)
        with self.assertRaisesRegex(Exception, "TASK196_CONSTRAINT_MISMATCH"):
            apply_task196_migration(self.engine)
        self.assertEqual(before, snapshot(self.engine))

    def test_missing_existing_constraint_is_not_silently_recreated(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE public.contracts_management DROP CONSTRAINT ck_contracts_management_pricing_completeness")
        before = snapshot(self.engine)
        with self.assertRaisesRegex(Exception, "TASK196_CONSTRAINT_MISMATCH"):
            apply_task196_migration(self.engine)
        self.assertEqual(before, snapshot(self.engine))

    def test_unknown_nonnegative_constraint_is_not_overwritten(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE public.contracts_management ADD CONSTRAINT ck_contracts_management_day_km_nonnegative CHECK (cont_no_of_days IS NULL)")
        before = snapshot(self.engine)
        with self.assertRaisesRegex(Exception, "TASK196_CONSTRAINT_MISMATCH"):
            apply_task196_migration(self.engine)
        self.assertEqual(before, snapshot(self.engine))

    def test_column_type_nullability_or_default_drift_blocks_migration(self):
        for change, undo in (("TYPE numeric(12,2)", "TYPE numeric(14,2)"), ("SET NOT NULL", "DROP NOT NULL"), ("SET DEFAULT 0", "DROP DEFAULT")):
            with self.subTest(change=change):
                with self.engine.begin() as conn:
                    conn.exec_driver_sql("ALTER TABLE public.contracts_management ALTER COLUMN cont_no_of_days " + change)
                before = snapshot(self.engine)
                with self.assertRaisesRegex(Exception, "TASK196_COLUMN_MISMATCH"):
                    apply_task196_migration(self.engine)
                self.assertEqual(before, snapshot(self.engine))
                with self.engine.begin() as conn:
                    conn.exec_driver_sql("ALTER TABLE public.contracts_management ALTER COLUMN cont_no_of_days " + undo)

    def test_missing_approval_blocks_migration(self):
        before = snapshot(self.engine)
        with self.assertRaisesRegex(Exception, "TASK196_TARGET_NOT_APPROVED"):
            apply_task196_migration(self.engine, approved=False)
        self.assertEqual(before, snapshot(self.engine))

    def run_script(self, *, oid=None, database=None, inject_failure=False):
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            actual_oid = conn.exec_driver_sql("SELECT 'public.contracts_management'::regclass::oid").scalar_one()
            conn.execute(text("SELECT set_config('task196.expected_database',:db,false),set_config('task196.expected_contract_oid',:oid,false),set_config('task196.migration_approved','20261008_task196_v1',false)"), {"db": database or self.engine.url.database, "oid": str(oid or actual_oid)})
            script = (MIGRATIONS / "TASK196_DATABASE_MIGRATION.sql").read_text()
            if inject_failure:
                # Isolated fault injection AFTER all three constraint changes.
                script = script.replace("    RAISE NOTICE 'Task196", "    RAISE EXCEPTION 'TASK196_TEST_FAULT_AFTER_DDL';\n    RAISE NOTICE 'Task196")
            try:
                with conn.connection.cursor() as cursor:
                    cursor.execute(script)
            except Exception:
                conn.exec_driver_sql("ROLLBACK")
                raise

    def test_wrong_database_or_table_identity_blocks_migration(self):
        before = snapshot(self.engine)
        for values in ({"oid": 1}, {"database": "wrong_database_tests"}):
            with self.assertRaisesRegex(Exception, "TASK196_TARGET_NOT_APPROVED"):
                self.run_script(**values)
            self.assertEqual(before, snapshot(self.engine))

    def test_failure_after_ddl_rolls_back_all_constraint_changes(self):
        before = snapshot(self.engine)
        with self.assertRaisesRegex(Exception, "TASK196_TEST_FAULT_AFTER_DDL"):
            self.run_script(inject_failure=True)
        self.assertEqual(before, snapshot(self.engine))

    def test_lock_timeout_leaves_database_unchanged(self):
        before = snapshot(self.engine)
        with self.engine.begin() as blocker:
            blocker.exec_driver_sql("LOCK TABLE public.contracts_management IN ACCESS SHARE MODE")
            with self.assertRaisesRegex(Exception, "lock timeout"):
                apply_task196_migration(self.engine)
        self.assertEqual(before, snapshot(self.engine))

    def test_known_not_valid_constraint_is_validated(self):
        apply_task196_migration(self.engine)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE public.contracts_management DROP CONSTRAINT ck_contracts_management_day_km_nonnegative")
            conn.exec_driver_sql("ALTER TABLE public.contracts_management ADD CONSTRAINT ck_contracts_management_day_km_nonnegative CHECK ((cont_no_of_days IS NULL OR cont_no_of_days>=0) AND (cont_per_day_rate IS NULL OR cont_per_day_rate>=0) AND (cont_no_of_kms IS NULL OR cont_no_of_kms>=0) AND (cont_per_km_rate IS NULL OR cont_per_km_rate>=0)) NOT VALID")
        apply_task196_migration(self.engine)
        self.assertTrue(all(r["convalidated"] for r in snapshot(self.engine)["constraints"]))

    def run_read_only_artifact(self, filename):
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            try:
                with conn.connection.cursor() as cursor:
                    cursor.execute((MIGRATIONS / filename).read_text())
            except Exception:
                conn.exec_driver_sql("ROLLBACK")
                raise

    def test_preflight_is_read_only_and_verification_rejects_old_accepts_new(self):
        before = snapshot(self.engine)
        self.run_read_only_artifact("TASK196_DATABASE_PREFLIGHT.sql")
        self.assertEqual(before, snapshot(self.engine))
        with self.assertRaisesRegex(Exception, "TASK196_VERIFICATION_FAILED"):
            self.run_read_only_artifact("TASK196_DATABASE_VERIFICATION.sql")
        self.assertEqual(before, snapshot(self.engine))
        apply_task196_migration(self.engine)
        after = snapshot(self.engine)
        self.run_read_only_artifact("TASK196_DATABASE_VERIFICATION.sql")
        self.assertEqual(after, snapshot(self.engine))


if __name__ == "__main__":
    unittest.main()
