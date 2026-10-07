"""Payroll component tests; never provision or read Penalties records.

Integration tests require PAYROLL_COMPONENT_TEST_DATABASE_URL, an explicit
loopback PostgreSQL 17+ *_tests database. Each class creates/drops its own random
DB. Application DB URLs are never used for test connections.
"""
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app_backend.services.main import app
from app_backend.services.auth_context import get_authenticated_context
from app_backend.services.service_02_hr_payroll.api import main as api
from app_backend.services.service_02_hr_payroll.logic import payroll_module as payroll
from app_backend.services.service_07_alerts_wf_engine import payroll_wf

SERVICE = Path(__file__).resolve().parents[1]
MIGRATION = SERVICE / 'data/20261001_include_penalty_deduction_in_payroll.sql'
URL = os.getenv('PAYROLL_COMPONENT_TEST_DATABASE_URL')
D = Decimal


def script_body(script):
    return re.sub(r'^\s*(BEGIN|COMMIT);\s*$', '', script, flags=re.M)


class PayrollPenaltyUnitTests(unittest.TestCase):
    def setUp(self):
        self.run = payroll.MonthlyPayrollRun.__new__(payroll.MonthlyPayrollRun)

    def test_python_deduction_and_net_include_penalty_once(self):
        values = dict(basic_salary=D('10000'), other_deduction_amount=D('1000'), penalty_deduction=D('500'))
        self.assertEqual(self.run._calculate_detail_total_deduction(values), D('1500'))
        self.assertEqual(self.run._calculate_detail_net_salary(values), D('8500'))

    def test_null_omitted_and_decimal_arithmetic(self):
        for value in (None, D('0')):
            self.assertEqual(self.run._calculate_detail_total_deduction({'penalty_deduction': value}), 0)
        self.assertEqual(self.run._calculate_detail_total_deduction({}), 0)
        self.assertEqual(self.run._calculate_detail_total_deduction(
            {'fine_deduction': D('0.10'), 'penalty_deduction': D('0.20')}), D('0.30'))

    def test_adjustment_mapping_remains_independent(self):
        totals, ids = self.run.map_adjustments_to_payroll_detail([
            {'adjustment_type': 'OTHER_DEDUCTION', 'adjustment_amount': '300', 'payroll_adjustment_id_pk': 1}])
        self.assertNotIn('penalty_deduction', totals)
        self.assertNotIn('penalty_deduction', payroll.ADJUSTMENT_COLUMNS.values())
        self.assertEqual(totals['other_deduction_amount'], D('300'))
        self.assertEqual(ids, [1])

    def test_decimal_request_and_no_process_input_expansion(self):
        request = api.PayrollDetailUpdatePayload(penalty_deduction='250.15')
        self.assertEqual(request.penalty_deduction, D('250.15'))
        for cls in (api.MonthlyPayrollProcessPayload, api.EmployeePayrollProcessPayload,
                    api.OffCyclePayrollProcessPayload, api.PayrollAdjustmentCreatePayload):
            self.assertNotIn('penalty_deduction', cls.model_fields)

    def test_openapi_request_and_open_detail_response_schemas(self):
        schema = app.openapi()
        field = schema['components']['schemas']['PayrollDetailUpdatePayload']['properties']['penalty_deduction']
        self.assertIn({'type': 'null'}, field['anyOf'])
        self.assertNotIn('penalty_deduction', schema['components']['schemas']['PayrollDetailUpdatePayload'].get('required', []))
        for kind in ('monthly', 'off-cycle'):
            for suffix in ('', '/employee'):
                route = schema['paths'][f'/api/v1/payroll/{kind}/details{suffix}']['post']
                dto = route['responses']['200']['content']['application/json']['schema']['properties']['data']
                if not suffix:
                    dto = dto['items']
                self.assertTrue(dto['additionalProperties'])
                self.assertIn('penalty_deduction', dto['properties'])
                self.assertTrue(route['security'])


@unittest.skipUnless(URL, 'PAYROLL_COMPONENT_TEST_DATABASE_URL is required (disposable loopback *_tests DB).')
class PayrollPenaltyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(URL)
        if (url.get_backend_name() != 'postgresql' or url.host not in ('127.0.0.1', 'localhost')
                or not (url.database or '').endswith('_tests')):
            raise RuntimeError('Only explicit disposable loopback PostgreSQL *_tests databases are allowed.')
        cls.name = f'payroll_component_{uuid4().hex}_tests'
        cls.admin = create_engine(url, isolation_level='AUTOCOMMIT')
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{cls.name}"')
        cls.addClassCleanup(cls.cleanup_database)
        cls.engine = create_engine(url.set(database=cls.name))
        with cls.engine.begin() as c:
            c.exec_driver_sql('CREATE TABLE organization_master (org_id_pk INTEGER PRIMARY KEY)')
            c.exec_driver_sql('INSERT INTO organization_master VALUES (77), (88)')
            c.exec_driver_sql('''CREATE TABLE employee_master (
                empl_id_pk INTEGER PRIMARY KEY, empl_org_id_fk INTEGER, employee_id TEXT,
                employee_name TEXT, employee_designation TEXT, monthly_basic_salary NUMERIC,
                monthly_allowance NUMERIC, monthly_accomodation NUMERIC, bank_name TEXT, bank_account_number TEXT)''')
            c.exec_driver_sql("""INSERT INTO employee_master VALUES
                (1,77,'E1','Test employee','Driver',10000,0,0,'Test bank','1'),
                (2,77,'E2','Second employee','Driver',10000,0,0,'Test bank','2')""")
            c.exec_driver_sql(script_body((SERVICE / 'data/simplified_payroll_module_ddl.sql').read_text()))

    @classmethod
    def cleanup_database(cls):
        if hasattr(cls, 'engine'):
            cls.engine.dispose()
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{cls.name}" WITH (FORCE)')
        cls.admin.dispose()

    def setUp(self):
        with self.engine.begin() as c:
            c.exec_driver_sql('TRUNCATE payroll_correction_recovery,payroll_adjustment,payroll_employee_detail,payroll_run RESTART IDENTITY CASCADE')
        self.sequence = 0
        self.monthly = payroll.MonthlyPayrollRun.__new__(payroll.MonthlyPayrollRun)
        self.offcycle = payroll.OffCyclePayrollRun.__new__(payroll.OffCyclePayrollRun)
        for run in (self.monthly, self.offcycle):
            run.payroll_engine = self.engine
        for name, value in [('monthly_payroll_run', self.monthly), ('off_cycle_payroll_run', self.offcycle)]:
            p = patch.object(payroll, name, value); p.start(); self.addCleanup(p.stop)
        self.context = {'authenticated': True, 'user': {'user_id': 7, 'user_principal_name': 'payroll.test@example.invalid'},
                        'organization': {'org_id': 77}}
        for application in (app, api.app):
            previous = dict(application.dependency_overrides)
            self.addCleanup(self.restore_overrides, application, previous)
            application.dependency_overrides[get_authenticated_context] = lambda: self.context
        self.client = TestClient(app); self.addCleanup(self.client.close)

    @staticmethod
    def restore_overrides(application, previous):
        application.dependency_overrides.clear(); application.dependency_overrides.update(previous)

    def post(self, path, payload):
        response = self.client.post('/api/v1/payroll/' + path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def create(self, kind='MONTHLY'):
        self.sequence += 1
        result = self.post(('monthly' if kind == 'MONTHLY' else 'off-cycle') + '/create', {
            'payroll_org_id_fk': 77, 'payroll_run_code': f'{kind}-{self.sequence}', 'payroll_run_type': kind,
            'payroll_run_sequence': self.sequence, 'payroll_year': 2026, 'payroll_month': 10,
            'payroll_period_start_date': '2026-10-01', 'payroll_period_end_date': '2026-10-31'})
        return result['payroll_run_id_pk']

    def process(self, run_id, kind='MONTHLY', employees=None, **extra):
        return self.post(('monthly' if kind == 'MONTHLY' else 'off-cycle') + '/process', {
            'payroll_run_id': run_id, 'employee_ids': employees or [1], **extra})

    def update(self, run_id, **values):
        return self.post('monthly/details/update', {'payroll_run_id': run_id, 'payroll_employee_id_fk': 1, **values})

    def detail(self, run_id, kind='MONTHLY'):
        return self.post(('monthly' if kind == 'MONTHLY' else 'off-cycle') + '/details/employee', {
            'payroll_run_id': run_id, 'payroll_employee_id_fk': 1})

    def rows(self, sql, **values):
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(text(sql), values).mappings()]

    def adjustment(self, kind, amount, employee=1):
        response = self.monthly.create_payroll_adjustment({
            'payroll_adjustment_org_id_fk': 77, 'payroll_adjustment_empl_id_fk': employee,
            'payroll_year': 2026, 'payroll_month': 10, 'adjustment_date': '2026-10-01',
            'adjustment_type': kind, 'adjustment_amount': amount, 'adjustment_status': 'APPROVED'})
        self.assertNotIn('error', response, response)
        return response['payroll_adjustment_id_pk']

    def test_public_monthly_crud_omission_null_and_zero(self):
        run = self.create(); self.process(run)
        self.assertEqual(self.detail(run)['penalty_deduction'], 0)
        self.update(run, penalty_deduction='250.00')
        self.assertEqual(self.detail(run)['penalty_deduction'], 250)
        self.update(run, penalty_deduction='300.00')
        self.update(run, other_deduction_amount=25)
        self.assertEqual(self.detail(run)['penalty_deduction'], 300)
        self.update(run, penalty_deduction=None, fine_deduction=10)
        self.assertEqual(self.detail(run)['penalty_deduction'], 300)
        self.update(run, penalty_deduction=0)
        self.assertEqual(self.detail(run)['penalty_deduction'], 0)

    def test_create_helper_persists_supplied_component(self):
        run = self.create()
        with self.engine.begin() as c:
            header = self.monthly._get_payroll_run_for_update(c, run, 77)
            employee = self.monthly._get_employee_for_update(c, 1, 77)
            payload, _ = self.monthly._build_detail_payload(header, employee, [], True, {})
            payload['penalty_deduction'] = D('250.00')
            self.monthly._create_payroll_employee_detail(c, payload)
            self.monthly._recalculate_payroll_run_totals(c, run, 77)
        self.assertEqual(self.detail(run)['penalty_deduction'], 250)
        self.assertEqual(self.detail(run)['net_salary'], 9750)

    def test_10000_minus_1000_minus_500_and_run_reconciliation(self):
        run = self.create(); self.process(run, employees=[1, 2])
        totals = self.update(run, other_deduction_amount=1000, penalty_deduction=500)
        detail = self.detail(run)
        self.assertEqual((detail['gross_salary'], detail['total_deduction'], detail['net_salary']), (10000, 1500, 8500))
        self.assertEqual((totals['total_deductions'], totals['total_net_salary']), (1500, 18500))
        details = self.post('monthly/details', {'payroll_run_id': run})
        self.assertEqual(sum(r['total_deduction'] for r in details), totals['total_deductions'])
        self.assertEqual(sum(r['net_salary'] for r in details), totals['total_net_salary'])
        self.assertEqual(details[0]['penalty_deduction'], 500)
        recalculated = self.post('monthly/recalculate', {'payroll_run_id': run})
        self.assertEqual(recalculated['total_net_salary'], 18500)

    def test_existing_deductions_and_adjustments_included_once(self):
        for kind in payroll.DEDUCTION_ADJUSTMENT_COLUMNS:
            self.adjustment(kind, '10.10')
        run = self.create(); self.process(run)
        totals = self.update(run, penalty_deduction='0.20')
        detail = self.detail(run)
        self.assertEqual(detail['total_deduction'], 91.1)
        self.assertEqual(totals['total_net_salary'], 9908.9)
        for column in payroll.DEDUCTION_ADJUSTMENT_COLUMNS.values():
            self.assertEqual(detail[column], 10.1)
        rows = self.rows('SELECT * FROM payroll_adjustment')
        self.assertEqual(len(rows), 9)
        self.assertTrue(all(r['processed_flag'] for r in rows))

    def test_decimal_rounding_and_preserved_null_read(self):
        run = self.create(); self.process(run)
        self.update(run, penalty_deduction='250.155')
        row = self.rows('SELECT penalty_deduction FROM payroll_employee_detail')[0]
        self.assertEqual(row['penalty_deduction'], D('250.16'))
        with self.engine.begin() as c:
            c.exec_driver_sql('UPDATE payroll_employee_detail SET penalty_deduction=NULL')
        self.assertIsNone(self.detail(run)['penalty_deduction'])
        listed = self.post('monthly/details', {'payroll_run_id': run})
        self.assertIsNone(listed[0]['penalty_deduction'])
        self.assertEqual(listed[0]['net_salary'], 10000)

    def test_negative_value_rejected_without_mutation(self):
        run = self.create(); self.process(run); self.update(run, penalty_deduction=250)
        response = self.client.post('/api/v1/payroll/monthly/details/update', json={
            'payroll_run_id': run, 'payroll_employee_id_fk': 1, 'penalty_deduction': -1})
        self.assertNotEqual(response.status_code, 200)
        self.assertIn('cannot be negative', response.text)
        self.assertEqual(self.detail(run)['penalty_deduction'], 250)

    def test_org_and_auth_enforcement_unchanged(self):
        run = self.create(); self.process(run)
        response = self.client.post('/api/v1/payroll/monthly/details/update', json={
            'payroll_run_id': run, 'payroll_employee_id_fk': 1, 'payroll_org_id_fk': 88, 'penalty_deduction': 250})
        self.assertEqual(response.status_code, 403)
        app.dependency_overrides.clear(); api.app.dependency_overrides.clear()
        self.assertEqual(self.client.post('/api/v1/payroll/monthly/details/update', json={
            'payroll_run_id': run, 'payroll_employee_id_fk': 1, 'penalty_deduction': 250}).status_code, 401)
        self.assertEqual(self.rows('SELECT penalty_deduction FROM payroll_employee_detail')[0]['penalty_deduction'], 0)

    def test_approval_adapter_preserves_component_and_totals(self):
        run = self.create(); self.process(run); self.update(run, penalty_deduction=250)
        with self.engine.begin() as c:
            result = payroll_wf.execute_approved_payroll_action('APPROVAL', {
                'payroll_run_id': run, 'payroll_org_id_fk': 77, 'acting_user_principal_name': 'qa@example.invalid'}, conn=c)
            self.assertNotIn('error', result, result)
        detail = self.detail(run)
        self.assertEqual(detail['penalty_deduction'], 250)
        self.assertEqual(detail['net_salary'], 9750)
        self.assertEqual(detail['payroll_detail_status'], 'APPROVED')

    def test_cancel_preserves_component_and_existing_status_semantics(self):
        run = self.create(); self.process(run); self.update(run, penalty_deduction=250)
        self.post('monthly/cancel', {'payroll_run_id': run})
        detail = self.detail(run)
        self.assertEqual(detail['penalty_deduction'], 250)
        self.assertEqual(detail['payroll_detail_status'], 'CANCELLED')

    def test_duplicate_process_does_not_overwrite_component(self):
        run = self.create(); self.process(run); self.update(run, penalty_deduction=250)
        response = self.client.post('/api/v1/payroll/monthly/process-employee', json={
            'payroll_run_id': run, 'payroll_employee_id_fk': 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.detail(run)['penalty_deduction'], 250)

    def test_one_time_and_final_settlement_compatible_reads_and_totals(self):
        for kind in ('ONE_TIME', 'FINAL_SETTLEMENT'):
            with self.subTest(kind=kind):
                run = self.create(kind); self.adjustment('OTHER_EARNING', '1000')
                self.process(run, kind)
                self.assertEqual(self.detail(run, kind)['penalty_deduction'], 0)
                self.update(run, penalty_deduction=250)
                detail = self.detail(run, kind)
                self.assertEqual((detail['gross_salary'], detail['total_deduction'], detail['net_salary']), (1000, 250, 750))
                self.assertEqual(detail['basic_salary'], 0)
                header = self.post('off-cycle/get', {'payroll_run_id': run})
                self.assertEqual(header['total_payable_amount'], 750)
                listed = self.post('off-cycle/details', {'payroll_run_id': run})
                self.assertEqual(listed[0]['penalty_deduction'], 250)

    def correction_fixture(self):
        source = self.create(); self.process(source)
        run = self.create('CORRECTION')
        self.adjustment('BONUS', '100'); self.adjustment('OTHER_DEDUCTION', '500')
        self.process(run, 'CORRECTION', source_payroll_run_id=source)
        # Represent an already-stored consistent row/ledger. No application
        # Penalties integration or new adjustment mapping is involved.
        with self.engine.begin() as c:
            c.execute(text('''UPDATE payroll_employee_detail SET penalty_deduction=50,
                correction_net_amount=-450, correction_recovery_amount=450 WHERE payroll_run_id_fk=:id'''), {'id': run})
            c.execute(text('UPDATE payroll_correction_recovery SET recovered_amount=450 WHERE correction_payroll_run_id_fk=:id'), {'id': run})
            self.offcycle._recalculate_payroll_run_totals(c, run, 77)
        return run

    def test_correction_existing_value_reads_and_serialization(self):
        run = self.correction_fixture()
        detail = self.detail(run, 'CORRECTION')
        self.assertEqual((detail['penalty_deduction'], detail['total_deduction'], detail['net_salary']), (50, 550, 0))
        self.assertEqual(detail['display_net_salary'], -450)
        listed = self.post('off-cycle/details', {'payroll_run_id': run})
        self.assertEqual(listed[0]['penalty_deduction'], 50)
        header = self.post('off-cycle/get', {'payroll_run_id': run})
        self.assertEqual(header['total_recoverable_amount'], 450)
        self.assertEqual(header['net_correction_amount'], -450)

    def test_correction_direct_edit_rejected_detail_and_ledger_unchanged(self):
        run = self.correction_fixture()
        snapshots = {table: self.rows(f'SELECT * FROM {table} ORDER BY 1') for table in (
            'payroll_employee_detail', 'payroll_correction_recovery', 'payroll_adjustment', 'payroll_run')}
        for value in (0, 50, 300):
            response = self.client.post('/api/v1/payroll/monthly/details/update', json={
                'payroll_run_id': run, 'payroll_employee_id_fk': 1, 'penalty_deduction': value, 'bonus_amount': 200})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn('current correction mechanism does not recalculate', response.text)
            for table, before in snapshots.items():
                self.assertEqual(self.rows(f'SELECT * FROM {table} ORDER BY 1'), before)

    def test_correction_restriction_is_clear_for_terminal_runs_too(self):
        run = self.correction_fixture()
        for status in ('APPROVED', 'PAID', 'CANCELLED'):
            with self.engine.begin() as c:
                c.execute(text('UPDATE payroll_run SET payroll_status=:status WHERE payroll_run_id_pk=:id'),
                          {'status': status, 'id': run})
            response = self.client.post('/api/v1/payroll/monthly/details/update', json={
                'payroll_run_id': run, 'payroll_employee_id_fk': 1, 'penalty_deduction': 0})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn('current correction mechanism does not recalculate', response.text)
            self.assertEqual(self.detail(run, 'CORRECTION')['penalty_deduction'], 50)

    def test_correction_omitted_or_null_penalty_does_not_block_unrelated_update(self):
        run = self.correction_fixture()
        for values in ({'bonus_amount': 100}, {'bonus_amount': 100, 'penalty_deduction': None}):
            self.update(run, **values)
            self.assertEqual(self.detail(run, 'CORRECTION')['penalty_deduction'], 50)

    def test_existing_correction_no_copy_or_automatic_penalty_delta(self):
        source = self.create(); self.process(source); self.update(source, penalty_deduction=500)
        correction = self.create('CORRECTION'); self.adjustment('BONUS', '200')
        self.process(correction, 'CORRECTION', source_payroll_run_id=source)
        self.assertEqual(self.detail(source)['penalty_deduction'], 500)
        corrected = self.detail(correction, 'CORRECTION')
        self.assertEqual(corrected['penalty_deduction'], 0)
        self.assertEqual(corrected['display_net_salary'], 200)

    def test_no_penalties_tables_are_required(self):
        tables = self.rows("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        self.assertNotIn('penalty_master', {r['tablename'] for r in tables})
        self.assertNotIn('penalty_recovery_schedule', {r['tablename'] for r in tables})
        run = self.create(); self.process(run); self.update(run, penalty_deduction=250)
        self.assertEqual(self.detail(run)['penalty_deduction'], 250)

    def old_expressions(self):
        # Emulate the inspected live schema; keep the existing column untouched.
        ddl = (SERVICE / 'data/simplified_payroll_module_ddl.sql').read_text()
        with self.engine.begin() as c:
            for name in ('total_deduction', 'net_salary'):
                expression = re.search(rf'{name} NUMERIC\(18,2\)\s+GENERATED ALWAYS AS \((.*?)\) STORED', ddl, re.S).group(1)
                expression = expression.replace('+ COALESCE(penalty_deduction, 0)', '')
                c.exec_driver_sql(f'ALTER TABLE payroll_employee_detail ALTER COLUMN {name} SET EXPRESSION AS ({expression})')
            constraint = re.search(
                r'CONSTRAINT chk_payroll_detail_correction_recovery_limit\s+CHECK \((.*?)\n        \),', ddl, re.S
            ).group(1).replace('+ COALESCE(penalty_deduction, 0)', '')
            c.exec_driver_sql('ALTER TABLE payroll_employee_detail DROP CONSTRAINT chk_payroll_detail_correction_recovery_limit')
            c.exec_driver_sql('ALTER TABLE payroll_employee_detail ADD CONSTRAINT chk_payroll_detail_correction_recovery_limit CHECK (' + constraint + ')')
        self.addCleanup(self.restore_expressions)

    def restore_expressions(self):
        with self.engine.begin() as c:
            c.exec_driver_sql('TRUNCATE payroll_correction_recovery,payroll_adjustment,payroll_employee_detail,payroll_run RESTART IDENTITY CASCADE')
            c.exec_driver_sql(script_body(MIGRATION.read_text()))

    def apply_migration(self):
        with self.engine.begin() as c:
            c.exec_driver_sql(script_body(MIGRATION.read_text()))

    def test_migration_updates_formulas_and_run_totals_without_recreating_column(self):
        self.old_expressions()
        run = self.create(); self.process(run); self.update(run, other_deduction_amount=1000, penalty_deduction=500)
        before = self.rows("SELECT attnum,atttypid,atttypmod,attnotnull FROM pg_attribute WHERE attrelid='payroll_employee_detail'::regclass AND attname='penalty_deduction'")
        self.assertEqual(self.detail(run)['net_salary'], 9000)
        self.apply_migration(); self.apply_migration()
        self.assertEqual(self.detail(run)['net_salary'], 8500)
        self.assertEqual(self.detail(run)['total_deduction'], 1500)
        header = self.post('monthly/get', {'payroll_run_id': run})
        self.assertEqual(header['total_deductions'], 1500)
        self.assertEqual(header['total_net_salary'], 8500)
        after = self.rows("SELECT attnum,atttypid,atttypmod,attnotnull FROM pg_attribute WHERE attrelid='payroll_employee_detail'::regclass AND attname='penalty_deduction'")
        self.assertEqual(before, after)
        definition = self.rows("SELECT is_nullable,column_default FROM information_schema.columns WHERE table_name='payroll_employee_detail' AND column_name='penalty_deduction'")[0]
        self.assertEqual(definition, {'is_nullable': 'YES', 'column_default': None})

    def test_migration_stops_for_nonzero_finalized_or_correction_values(self):
        self.old_expressions()
        run = self.create(); self.process(run); self.update(run, penalty_deduction=500)
        for kind, status in [('MONTHLY', 'APPROVED'), ('CORRECTION', 'PROCESSED')]:
            with self.subTest(kind=kind), self.engine.begin() as c:
                c.execute(text('UPDATE payroll_run SET payroll_run_type=:kind,payroll_status=:status WHERE payroll_run_id_pk=:id'), {'kind': kind, 'status': status, 'id': run})
            with self.assertRaisesRegex(DBAPIError, 'approved reconciliation'):
                self.apply_migration()
            self.assertEqual(self.detail(run)['net_salary'], 10000)
            self.assertEqual(self.detail(run)['penalty_deduction'], 500)

    def test_migration_null_zero_rows_and_recovery_limit(self):
        run = self.create(); self.process(run, employees=[1, 2])
        with self.engine.begin() as c:
            c.exec_driver_sql('UPDATE payroll_employee_detail SET penalty_deduction=NULL WHERE payroll_employee_id_fk=1')
        self.old_expressions(); self.apply_migration()
        self.assertIsNone(self.detail(run)['penalty_deduction'])
        self.assertEqual(self.detail(run)['net_salary'], 10000)
        # The constraint uses the new component, without editing ledger logic.
        with self.engine.begin() as c:
            c.exec_driver_sql('''UPDATE payroll_employee_detail SET basic_salary=0,
                penalty_deduction=50,correction_net_amount=-50,correction_recovery_amount=50
                WHERE payroll_employee_id_fk=2''')
        row = self.rows('SELECT total_deduction,net_salary FROM payroll_employee_detail WHERE payroll_employee_id_fk=2')[0]
        self.assertEqual(row, {'total_deduction': D('50'), 'net_salary': D('0')})


if __name__ == '__main__':
    unittest.main()
