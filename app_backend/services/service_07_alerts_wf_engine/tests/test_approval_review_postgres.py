"""Task 164 integration tests. Only use an explicit disposable loopback *_tests DB.

Creates and drops its OWN random database; never uses the application's DB URL.
GCS is versioned in-memory storage with real offline SDK URL signing.
"""
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import MultipleResultsFound

from app_backend.services.main import app
from app_backend.services.auth_context import get_authenticated_context
from app_backend.services import auth_context as auth_module
from app_backend.services.service_03_fleet_management.api import main as fleet_api
from app_backend.services.service_06_contracts_management.api import main as contract_api
from app_backend.services.service_08_financial_management.api import main as financial_api
from app_backend.services.service_03_fleet_management.logic import fleet_master_get_data as fleet_read
from app_backend.services.service_06_contracts_management.logic import contracts_management_get_data as contract_read
from app_backend.services.service_08_financial_management.logic import accounts_payables_get_data as ap_read
from app_backend.services.service_08_financial_management.logic import accounts_receivables_get_data as ar_read
from app_backend.services.service_02_hr_payroll.logic.payroll_module import OffCyclePayrollRun, MonthlyPayrollRun
from app_backend.services.service_07_alerts_wf_engine.api import workflow_runtime_api as runtime_api, workflow_admin_api as admin_api
from app_backend.services.service_07_alerts_wf_engine.api.main import app as workflow_app
from app_backend.services.service_07_alerts_wf_engine.logic import workflow_runtime_logic
from app_backend.services.service_07_alerts_wf_engine import workflow_runtime_engine as runtime_engine
from app_backend.services.service_07_alerts_wf_engine import workflow_document_download as download
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import apply_workflow_engine_migration
from app_backend.services.service_07_alerts_wf_engine.tests.test_workflow_attachment_download import (
    VersionedBucket, stage_case, CASES, context, NEW_BYTES, OLD_BYTES,
)

URL = os.getenv('TASK164_TEST_DATABASE_URL')
SERVICES = Path(__file__).resolve().parents[2]
READS = {
    'fleet': ('fleet-master','fleet_master','fleet_vehicle_id_pk','fleet_org_id_fk',fleet_read),
    'contract': ('contracts','contracts_management','cont_id_pk','cont_org_id_fk',contract_read),
    'ap': ('accounts-payables','accounts_payables','ap_id_pk','ap_org_id_fk',ap_read),
    'ar': ('accounts-receivables','accounts_receivables','ar_id_pk','ar_org_id_fk',ar_read),
}
LEGACY = {'contract':'contracts_management_workflow_requests','ap':'accounts_payables_workflow_requests',
          'ar':'accounts_receivables_workflow_requests'}


@unittest.skipUnless(URL, 'TASK164_TEST_DATABASE_URL is required (disposable loopback *_tests database).')
class ApprovalReviewPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=make_url(URL)
        if url.get_backend_name()!='postgresql' or url.host not in ('127.0.0.1','localhost') or not url.database.endswith('_tests'):
            raise RuntimeError('Only explicit disposable loopback PostgreSQL *_tests databases are allowed.')
        cls.db_name=f'task164_{uuid4().hex}_tests'
        cls.admin=create_engine(url,isolation_level='AUTOCOMMIT')
        with cls.admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{cls.db_name}"')
        cls.addClassCleanup(cls.cleanup_database)
        cls.engine=create_engine(url.set(database=cls.db_name))
        cls.evidence=[]
        with cls.engine.begin() as c:
            c.exec_driver_sql('CREATE TABLE organization_master (org_id_pk INTEGER PRIMARY KEY, org_name TEXT)')
            c.exec_driver_sql("INSERT INTO organization_master VALUES (77,'Test organization'),(88,'Other organization')")
            c.exec_driver_sql('CREATE TABLE department_master (dep_id_pk INTEGER PRIMARY KEY, dep_org_id_fk INTEGER)')
            c.exec_driver_sql('CREATE TABLE customer_master (cust_id_pk INTEGER PRIMARY KEY, cust_org_id_fk INTEGER, UNIQUE(cust_id_pk,cust_org_id_fk))')
            c.exec_driver_sql('CREATE TABLE supplier_master (supp_id_pk INTEGER PRIMARY KEY, supp_org_id_fk INTEGER, UNIQUE(supp_id_pk,supp_org_id_fk))')
            c.exec_driver_sql('INSERT INTO department_master VALUES(20,77),(21,88)')
            c.exec_driver_sql('INSERT INTO customer_master VALUES(10,77),(11,88)')
            c.exec_driver_sql('INSERT INTO supplier_master VALUES(30,77),(31,88)')
            c.exec_driver_sql('''CREATE TABLE employee_master (empl_id_pk INTEGER PRIMARY KEY, empl_org_id_fk INTEGER,
                employee_id TEXT, employee_name TEXT, employee_designation TEXT, monthly_basic_salary NUMERIC,
                monthly_allowance NUMERIC, monthly_accomodation NUMERIC, bank_name TEXT, bank_account_number TEXT)''')
            c.exec_driver_sql("INSERT INTO employee_master VALUES(1,77,'E1','Employee 1','Driver',3000,400,500,'Bank','123'),(2,77,'E2','Employee 2','Driver',3000,400,500,'Bank','456')")
            fleet_ddl=(SERVICES/'service_03_fleet_management/data/fleet_management_tables_ddl.sql').read_text()
            # Bootstrap calls this table fleet_vehicle; deployed logic uses fleet_master.
            fleet_ddl=re.search(r'CREATE TABLE fleet_vehicle \(.*?\n\);',fleet_ddl,re.S).group(0)
            c.exec_driver_sql(fleet_ddl.replace('CREATE TABLE fleet_vehicle (','CREATE TABLE fleet_master ('))
            for name in ('service_06_contracts_management/data/contracts_management.sql',
                         'service_08_financial_management/data/accounts_payables.sql',
                         'service_08_financial_management/data/accounts_receivable.sql',
                         'service_02_hr_payroll/data/simplified_payroll_module_ddl.sql'):
                script=(SERVICES/name).read_text()
                script=re.sub(r'^\s*(BEGIN|COMMIT);\s*$', '', script, flags=re.M)
                c.exec_driver_sql(script)
            # Existing fields used by contract CRUD are absent from the older bootstrap.
            c.exec_driver_sql('ALTER TABLE contracts_management ADD COLUMN cont_link_path TEXT, ADD COLUMN cont_approval_status TEXT, ADD COLUMN total_contract_value NUMERIC(14,2)')
            for name in ('service_06_contracts_management/data/contracts_management_workflow_requests.sql',
                         'service_08_financial_management/data/accounts_payables_workflow_requests.sql',
                         'service_08_financial_management/data/accounts_receivables_workflow_requests.sql'):
                c.exec_driver_sql((SERVICES/name).read_text())
            c.exec_driver_sql('CREATE TABLE permission_master (module_name TEXT, action_name TEXT, permission_code TEXT)')
            c.exec_driver_sql('''CREATE TABLE user_master (user_id_pk INTEGER PRIMARY KEY,user_principal_name TEXT,
                user_org_id_fk INTEGER,display_name TEXT,email TEXT,is_active BOOLEAN DEFAULT TRUE,is_deleted BOOLEAN DEFAULT FALSE)''')
            c.exec_driver_sql("""INSERT INTO user_master(user_id_pk,user_principal_name,user_org_id_fk) VALUES
                (1,'requester@example.invalid',77),(2,'reviewer@example.invalid',77),(3,'other@example.invalid',77),
                (4,'admin@example.invalid',77),(5,'foreign@example.invalid',88),(6,'historical@example.invalid',77)""")
            c.exec_driver_sql("""CREATE VIEW v_user_access_rights AS SELECT user_principal_name,'WORKFLOW'::text AS module_name,
                action_name,'test'::text AS role_name FROM user_master CROSS JOIN (VALUES('SUBMIT'),('APPROVE')) a(action_name)
                UNION ALL SELECT 'admin@example.invalid','WORKFLOW','ADMIN','admin'""")
            apply_workflow_engine_migration(c)
            for code in ('CONTRACTS_MANAGEMENT','ACCOUNTS_PAYABLE','ACCOUNTS_RECEIVABLE'):
                definition=c.execute(text('SELECT workflow_definition_id_pk FROM workflow_definitions WHERE workflow_code=:code AND organization_id_fk=77'),{'code':code}).scalar_one()
                version=c.execute(text("INSERT INTO workflow_definition_versions(workflow_definition_id_fk,version_number,version_status,applies_to_action) VALUES(:id,1,'PUBLISHED','ALL') RETURNING workflow_version_id_pk"),{'id':definition}).scalar_one()
                nodes=[]
                for key,kind,principal in [('requester','REQUESTER','requester@example.invalid'),('approver','APPROVER','reviewer@example.invalid'),('end','END',None)]:
                    nodes.append(c.execute(text('''INSERT INTO workflow_nodes(workflow_version_id_fk,node_key,node_type,user_principal_name)
                        VALUES(:version,:key,:kind,:principal) RETURNING workflow_node_id_pk'''),{'version':version,'key':key,'kind':kind,'principal':principal}).scalar_one())
                for seq,(a,b) in enumerate(zip(nodes,nodes[1:]),1):
                    c.execute(text('INSERT INTO workflow_edges(workflow_version_id_fk,source_node_id_fk,target_node_id_fk,edge_sequence) VALUES(:v,:a,:b,:s)'),{'v':version,'a':a,'b':b,'s':seq})
        cls.bucket=VersionedBucket()

    @classmethod
    def cleanup_database(cls):
        if hasattr(cls,'evidence') and os.getenv('TASK164_EVIDENCE_PATH'):
            Path(os.environ['TASK164_EVIDENCE_PATH']).write_text(json.dumps(cls.evidence,indent=2,default=str)+'\n')
        if hasattr(cls,'engine'):
            cls.engine.dispose()
        with cls.admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{cls.db_name}" WITH (FORCE)')
        cls.admin.dispose()

    def setUp(self):
        self.auth=context()
        for module in (fleet_read,contract_read,ap_read,ar_read,runtime_engine,workflow_runtime_logic,admin_api):
            p=patch.object(module,'db_engine',return_value=self.engine)
            p.start(); self.addCleanup(p.stop)
        for application in (app,fleet_api.app,contract_api.app,financial_api.app,workflow_app):
            previous=dict(application.dependency_overrides)
            self.addCleanup(self.restore_overrides,application,previous)
            application.dependency_overrides[get_authenticated_context]=lambda:self.auth
            application.dependency_overrides[runtime_api.get_authenticated_workflow_context]=lambda:self.auth
        for key,value in [('FIREBASE_STORAGE_BUCKET','task164-test-bucket'),('initialize_firebase_app',lambda:None)]:
            p=patch.object(download,key,value); p.start(); self.addCleanup(p.stop)
        p=patch.object(download.storage,'bucket',return_value=self.bucket); p.start(); self.addCleanup(p.stop)
        for module in (auth_module,admin_api):
            p=patch.object(module,'get_authenticated_user',side_effect=lambda token:self.auth)
            p.start(); self.addCleanup(p.stop)
        self.client=TestClient(app,headers={'Authorization':'Bearer task164-test'}); self.addCleanup(self.client.close)
        with self.engine.begin() as c:
            c.exec_driver_sql('TRUNCATE payroll_correction_recovery,payroll_adjustment,payroll_employee_detail,payroll_run,workflow_instances,contracts_management_workflow_requests,accounts_payables_workflow_requests,accounts_receivables_workflow_requests RESTART IDENTITY CASCADE')
            c.exec_driver_sql('TRUNCATE fleet_master,contracts_management,accounts_payables,accounts_receivables RESTART IDENTITY CASCADE')
            for org,record,cust,dep,supp in [(77,9,10,20,30),(88,10,11,21,31)]:
                c.execute(text("""INSERT INTO fleet_master(fleet_vehicle_id_pk,fleet_org_id_fk,vehicle_code,fleet_type,fleet_category,vehicle_brand_name,
                    vehicle_total_seats_including_driver,vehicle_plate_number,vehicle_chassis_number,mulkiya_number,mulkiya_expiry_date)
                    VALUES(:id,:org,:label,'bus','corporate','Test',40,:label,:label,:label,'2027-01-01')"""),{'id':record,'org':org,'label':str(record)})
                c.execute(text("""INSERT INTO contracts_management(cont_id_pk,cont_org_id_fk,cont_cust_id_fk,cont_dep_id_fk,cont_contract_number,
                    cont_start_date,cont_revenue_basis,cont_no_of_billing_months,cont_no_of_work_days_per_week,cont_no_of_round_trips_per_day,cont_link_path)
                    VALUES(:id,:org,:cust,:dep,:label,'2026-01-01','PER_BUS',12,5,2,'committed/OLD_FILE.pdf')"""),{'id':record,'org':org,'cust':cust,'dep':dep,'label':str(record)})
                c.execute(text("""INSERT INTO accounts_payables(ap_id_pk,ap_org_id_fk,ap_supp_id_fk,ap_invoice_number,ap_invoice_date,ap_currency_code,
                    ap_invoice_amount,ap_paid_amount,ap_invoice_file_path) VALUES(:id,:org,:supp,:label,'2026-01-01','AED',100,10,'gs://task164-test-bucket/committed/OLD_FILE.pdf')"""),{'id':record,'org':org,'supp':supp,'label':str(record)})
                c.execute(text("""INSERT INTO accounts_receivables(ar_id_pk,ar_org_id_fk,ar_cust_id_fk,ar_invoice_number,ar_invoice_date,ar_currency_code,
                    ar_invoice_amount,ar_received_amount,ar_invoice_file_path) VALUES(:id,:org,:cust,:label,'2026-01-01','AED',100,10,'gs://task164-test-bucket/committed/OLD_FILE.pdf')"""),{'id':record,'org':org,'cust':cust,'label':str(record)})

    @staticmethod
    def restore_overrides(application,previous):
        application.dependency_overrides.clear(); application.dependency_overrides.update(previous)

    def read(self,domain,record=9,extra=None):
        route,_,pk,_,_=READS[domain]
        if domain in ('ap','ar'):
            return self.client.post('/api/v1/'+route,json={pk:record,**(extra or {})})
        return self.client.get(f'/api/v1/{route}/{record}')

    def create_workflow(self,domain,action='CREATE',filename='NEW_FILE.pdf'):
        staged=stage_case(self,self.bucket,domain,action,filename)
        payload=staged['request_payload']
        payload['user_principal_name']='requester@example.invalid'
        payload['created_by']='requester@example.invalid'
        payload['updated_by']='requester@example.invalid'
        if domain=='contract':
            payload.update(cont_cust_id_fk=10,cont_dep_id_fk=20,cont_start_date='2026-01-01',cont_status='DRAFT',
                cont_revenue_basis='PER_BUS',cont_currency_code='AED',cont_big_bus_count_gt_34=1,cont_big_bus_rate_pm=1000,
                cont_no_of_billing_months=12,cont_no_of_work_days_per_week=5,cont_no_of_round_trips_per_day=2,total_contract_value=12000)
        elif domain=='ap':
            payload.update(ap_supp_id_fk=30,ap_invoice_date='2026-01-01',ap_currency_code='AED',ap_invoice_amount=250)
        else:
            payload.update(ar_cust_id_fk=10,ar_invoice_date='2026-01-01',ar_currency_code='AED',ar_invoice_amount=250)
        result=runtime_engine.start_workflow(workflow_code=staged['workflow_code'],workflow_action=action,
            organization_id=77,requester_user_principal_name='requester@example.invalid',request_payload=payload,
            legacy_table_name=LEGACY[domain])
        self.assertEqual(result.get('workflow_status'),'PENDING_APPROVAL',result)
        return result,payload

    def workflow_download(self,result,payload):
        meta=payload['workflow_document_attachment']
        return self.client.post(f"/api/v1/workflow-engine/instances/{result['workflow_instance_id']}/attachments/{meta['attachment_id']}/download",
                                json={'version_id':meta['version_id']})

    def test_workflow_visibility_real_sql(self):
        result,payload=self.create_workflow('ap')
        for principal,org,expected in [('reviewer@example.invalid',77,200),('requester@example.invalid',77,200),
                ('admin@example.invalid',77,200),('other@example.invalid',77,404),('reviewer@example.invalid',88,404)]:
            with self.subTest(principal=principal,org=org):
                self.auth=context(org,principal)
                self.assertEqual(self.workflow_download(result,payload).status_code,expected)
        with self.engine.begin() as c:
            c.execute(text("UPDATE workflow_instance_steps SET acted_by_user_principal_name='historical@example.invalid' WHERE workflow_instance_id_fk=:id"),{'id':result['workflow_instance_id']})
        self.auth=context(77,'historical@example.invalid')
        self.assertEqual(self.workflow_download(result,payload).status_code,200)

    def test_rejected_workflow_remains_unavailable_after_cleanup(self):
        from app_backend.services.service_07_alerts_wf_engine import workflow_document_cleanup as cleanup
        result,payload=self.create_workflow('ap')
        with patch.object(cleanup,'initialize_firebase_app'), patch.object(cleanup.storage,'bucket',return_value=self.bucket):
            rejected=runtime_engine.reject_workflow_step(workflow_code='ACCOUNTS_PAYABLE',
                payload={'workflow_instance_id':result['workflow_instance_id'],'workflow_instance_step_id':result['workflow_instance_step_id'],
                         'acting_user_principal_name':'reviewer@example.invalid'},legacy_table_name=LEGACY['ap'],
                acting_user_principal_name='reviewer@example.invalid',organization_id=77)
        self.assertEqual(rejected['workflow_status'],'REJECTED',rejected)
        self.assertEqual(self.workflow_download(result,payload).status_code,409)
        path=payload['workflow_document_staged_blob_path']
        self.assertFalse(any(p==path for p,_ in self.bucket.records))

    def test_idempotent_replay_keeps_original_attachment(self):
        result,payload=self.create_workflow('ap')
        repeated,other=self.create_workflow('ap')
        self.assertEqual(result['workflow_instance_id'],repeated['workflow_instance_id'])
        self.assertNotEqual(payload['workflow_document_attachment']['attachment_id'],other['workflow_document_attachment']['attachment_id'])
        self.assertEqual(self.workflow_download(result,payload).status_code,200)
        self.assertEqual(self.workflow_download(repeated,other).status_code,404)

    def payroll_run(self,kind):
        run=OffCyclePayrollRun.__new__(OffCyclePayrollRun) if kind!='MONTHLY' else MonthlyPayrollRun.__new__(MonthlyPayrollRun)
        run.payroll_engine=self.engine
        with self.engine.connect() as c:
            sequence=c.execute(text('SELECT COALESCE(MAX(payroll_run_sequence),0)+1 FROM payroll_run WHERE payroll_run_type=:kind'),{'kind':kind}).scalar_one()
        result=run.create_payroll_run_header({'payroll_org_id_fk':77,'payroll_run_code':f'{kind}-{sequence}',
            'payroll_run_type':kind,'payroll_year':2026,'payroll_month':9,'payroll_run_sequence':sequence,
            'payroll_period_start_date':'2026-09-01','payroll_period_end_date':'2026-09-30','user_principal_name':'requester@example.invalid'})
        self.assertNotIn('error',result,result)
        return run,result['payroll_run_id_pk']

    def adjustment(self,run,employee,kind,amount):
        response=run.create_payroll_adjustment({'payroll_adjustment_org_id_fk':77,'payroll_adjustment_empl_id_fk':employee,
            'payroll_year':2026,'payroll_month':9,'adjustment_date':'2026-09-01','adjustment_type':kind,
            'adjustment_amount':amount,'adjustment_status':'APPROVED'})
        self.assertNotIn('error',response,response)
        return response['payroll_adjustment_id_pk']

    def process_payroll(self,kind,values):
        run,run_id=self.payroll_run(kind)
        ids={}
        for employee,adjustment_type,amount in values:
            ids.setdefault(str(employee),[]).append(self.adjustment(run,employee,adjustment_type,amount))
        result=run.process_selected_employees({'payroll_run_id':run_id,'payroll_org_id_fk':77,
            'employee_ids':sorted({v[0] for v in values}) or [1],'adjustment_ids_by_employee':ids})
        self.assertNotIn('error',result,result)
        return run,run_id,result

    def assert_payroll_reconciles(self,run,run_id,result,expected_payable,expected_recoverable,expected_correction):
        with self.engine.connect() as c:
            stored=dict(c.execute(text('SELECT * FROM payroll_run WHERE payroll_run_id_pk=:id'),{'id':run_id}).mappings().one())
            rows=[dict(row) for row in c.execute(text('SELECT * FROM payroll_employee_detail WHERE payroll_run_id_fk=:id'),{'id':run_id}).mappings()]
            ledger=c.execute(text("SELECT COALESCE(SUM(recovered_amount),0) FROM payroll_correction_recovery WHERE correction_payroll_run_id_fk=:id AND recovery_status='APPLIED'"),{'id':run_id}).scalar_one()
        self.assertEqual(stored['total_net_salary'],sum(r['net_salary'] for r in rows))
        self.assertEqual(stored['total_gross_salary'],sum(r['gross_salary'] for r in rows))
        self.assertEqual(stored['total_deductions'],sum(r['total_deduction'] for r in rows))
        self.assertEqual(stored['total_correction_net_amount'],sum(r['correction_net_amount'] for r in rows))
        self.assertEqual(stored['total_correction_recovery'],sum(r['correction_recovery_amount'] for r in rows))
        self.assertEqual(ledger,expected_recoverable)
        self.assertEqual(result['total_payable_amount'],expected_payable)
        self.assertEqual(result['total_recoverable_amount'],expected_recoverable)
        self.assertEqual(result['net_correction_amount'],expected_correction)
        for row in rows:
            self.assertEqual(row['basic_salary']+row['monthly_allowance']+row['accommodation_allowance'],0)
        api=run.get_payroll_run({'payroll_run_id':run_id,'payroll_org_id_fk':77})
        self.assertEqual(api['total_payable_amount'],expected_payable)
        expected_display=expected_correction if stored['payroll_run_type']=='CORRECTION' else expected_payable
        self.assertEqual(api['total_net_salary'],expected_display)
        self.assertEqual(api['display_total_net_salary'],expected_display)
        listed=run.list_payroll_runs({'payroll_org_id_fk':77})[0]
        selected=listed[listed['payroll_run_id_pk']==run_id].iloc[0]
        self.assertEqual(Decimal(str(selected['total_payable_amount'])),expected_payable)
        self.assertEqual(Decimal(str(selected['total_net_salary'])),expected_display)
        details=run.retrieve_payroll_details_for_run({'payroll_run_id':run_id,'payroll_org_id_fk':77})[0]
        self.assertEqual(Decimal(str(details['payable_amount'].sum())),expected_payable)
        self.assertEqual(Decimal(str(details['recoverable_amount'].sum())),expected_recoverable)
        self.evidence.append({'case':stored['payroll_run_type'],'stored_total_net_salary':stored['total_net_salary'],
            'api_total_net_salary':api['total_net_salary'],'total_payable_amount':expected_payable,
            'total_recoverable_amount':expected_recoverable,'net_correction_amount':expected_correction})

    def test_payroll_one_time_adjustments_included_once(self):
        run,id,result=self.process_payroll('ONE_TIME',[(1,'BONUS','500'),(1,'OTHER_DEDUCTION','100')])
        self.assert_payroll_reconciles(run,id,result,Decimal('400'),Decimal('0'),Decimal('0'))

    def test_payroll_final_settlement_entered_adjustments_only(self):
        run,id,result=self.process_payroll('FINAL_SETTLEMENT',[(1,'OTHER_EARNING','1200'),(1,'OTHER_DEDUCTION','250')])
        self.assert_payroll_reconciles(run,id,result,Decimal('950'),Decimal('0'),Decimal('0'))
        with self.engine.connect() as c:
            columns=set(c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='payroll_employee_detail'")).scalars())
        self.assertFalse(columns & {'gratuity','leave_settlement','arrears','final_net_settlement'})

    def test_payroll_correction_mixed_payable_and_recovery(self):
        monthly,source=self.payroll_run('MONTHLY')
        result=monthly.process_payroll_run({'payroll_run_id':source,'payroll_org_id_fk':77})
        self.assertNotIn('error',result,result)
        run,id,result=self.process_payroll('CORRECTION',[(1,'BONUS','200'),(2,'OTHER_DEDUCTION','350')])
        self.assert_payroll_reconciles(run,id,result,Decimal('200'),Decimal('350'),Decimal('-150'))
        self.assertEqual(result['net_correction_amount'],result['total_payable_amount']-result['total_recoverable_amount'])

    def test_payroll_zero_values_all_off_cycle_types(self):
        for kind in ('ONE_TIME','CORRECTION','FINAL_SETTLEMENT'):
            with self.subTest(kind=kind):
                run,id,result=self.process_payroll(kind,[])
                self.assert_payroll_reconciles(run,id,result,Decimal('0'),Decimal('0'),Decimal('0'))

    def test_payroll_correction_positive_and_zero_adjustments(self):
        for earning,deduction in [('500','100'),('100','100')]:
            run,id,result=self.process_payroll('CORRECTION',[(1,'BONUS',earning),(1,'OTHER_DEDUCTION',deduction)])
            delta=Decimal(earning)-Decimal(deduction)
            self.assert_payroll_reconciles(run,id,result,delta,Decimal('0'),delta)

    def test_payroll_noncorrection_deduction_shortfall_rejected(self):
        for kind in ('ONE_TIME','FINAL_SETTLEMENT'):
            run,id=self.payroll_run(kind)
            adjustment=self.adjustment(run,1,'OTHER_DEDUCTION','100')
            result=run.process_selected_employees({'payroll_run_id':id,'payroll_org_id_fk':77,'employee_ids':[1],
                'adjustment_ids_by_employee':{'1':[adjustment]}})
            self.assertIn('deductions exceed payable earnings',result['error'])
            with self.engine.connect() as c:
                self.assertFalse(c.execute(text('SELECT processed_flag FROM payroll_adjustment WHERE payroll_adjustment_id_pk=:id'),{'id':adjustment}).scalar_one())

    def test_payroll_adjustment_flags_and_positive_magnitudes(self):
        run,id=self.payroll_run('ONE_TIME')
        ids=[self.adjustment(run,1,'BONUS','50'),self.adjustment(run,1,'OTHER_DEDUCTION','10')]
        with self.engine.connect() as c:
            rows=c.execute(text('SELECT earning_deduction_flag,adjustment_amount FROM payroll_adjustment ORDER BY payroll_adjustment_id_pk')).all()
        self.assertEqual(rows,[('EARNING',Decimal('50')),('DEDUCTION',Decimal('10'))])
        for amount in ('0','-1'):
            result=run.create_payroll_adjustment({'payroll_adjustment_org_id_fk':77,'adjustment_type':'BONUS','adjustment_amount':amount})
            self.assertIn('greater than zero',result['error'])


def exact_read_case(domain,case):
    def test(self):
        route,table,pk,org,module=READS[domain]
        if case=='unauthenticated':
            self.client.headers.pop('Authorization',None)
            for application in (app,fleet_api.app,contract_api.app,financial_api.app):
                application.dependency_overrides.pop(get_authenticated_context,None)
            self.assertEqual(self.read(domain).status_code,401)
        elif case=='foreign_record':
            response=self.read(domain,10)
            if domain in ('ap','ar'):
                self.assertEqual(response.status_code,200); self.assertEqual(response.json()['data'],[])
            else:
                self.assertEqual(response.status_code,404)
        elif case=='absent':
            response=self.read(domain,999)
            self.assertEqual(response.status_code,200 if domain in ('ap','ar') else 404)
            if domain in ('ap','ar'): self.assertEqual(response.json()['data'],[])
        elif case=='organization_context':
            self.auth=context(88)
            response=self.read(domain,10)
            self.assertEqual(response.status_code,200,response.text)
            data=response.json()['data']; data=data[0] if isinstance(data,list) else data
            self.assertEqual(data[org],88)
        elif case=='complete_dto':
            response=self.read(domain)
            self.assertEqual(response.status_code,200,response.text)
            data=response.json()['data']
            if isinstance(data,list): self.assertEqual(len(data),1); data=data[0]
            with self.engine.connect() as c:
                stored=dict(c.execute(text(f'SELECT * FROM {table} WHERE {pk}=9')).mappings().one())
            self.assertEqual(set(data),set(stored))
            self.assertEqual(data[pk],9); self.assertEqual(data[org],77)
            if domain in ('ap','ar'): self.assertEqual(data[domain+'_balance_amount'],90)
        elif case=='list_unchanged':
            response=self.client.get('/api/v1/'+route)
            self.assertEqual(response.status_code,200,response.text)
            self.assertIsInstance(response.json()['data'],list)
            self.assertEqual(len(response.json()['data']),1)
        elif case=='positive_id':
            for id in (0,-1,'abc'):
                self.assertEqual(self.read(domain,id).status_code,422)
        elif case=='cardinality':
            bad_engine=MagicMock()
            bad_engine.connect.return_value.__enter__.return_value.execute.return_value.mappings.return_value.one_or_none.side_effect=MultipleResultsFound()
            with patch.object(module,'db_engine',return_value=bad_engine):
                response=self.read(domain)
            self.assertEqual(response.status_code,500)
            self.assertEqual(response.json()['error'],'EXACT_RECORD_INTEGRITY_ERROR')
        elif case=='org_assertion':
            self.assertEqual(self.read(domain,extra={org:88}).status_code,403)
        elif case=='alias':
            response=self.client.post('/api/v1/'+route,json={domain+'_id':9})
            self.assertEqual(response.status_code,200)
            self.assertEqual([row[pk] for row in response.json()['data']],[9])
    return test

for domain in READS:
    cases=['complete_dto','organization_context','foreign_record','absent','unauthenticated','list_unchanged']
    cases+=['positive_id','cardinality'] if domain in ('fleet','contract') else ['org_assertion','alias']
    for case in cases:
        setattr(ApprovalReviewPostgresTests,f'test_{domain}_{case}',exact_read_case(domain,case))


def persisted_document_case(domain,action,same_name=False):
    def test(self):
        filename='SAME_FILE.pdf' if same_name else 'NEW_FILE.pdf'
        old_name='SAME_FILE.pdf' if same_name else 'OLD_FILE.pdf'
        old_path=f'committed/{old_name}'
        from io import BytesIO
        old_blob=self.bucket.blob(old_path)
        old_blob.upload_from_file(BytesIO(OLD_BYTES),content_type='application/pdf')
        if same_name:
            _,table,pk,_,_=READS[domain]
            pointer=CASES[domain][3]
            reference=old_path if domain=='contract' else f'gs://task164-test-bucket/{old_path}'
            with self.engine.begin() as c:
                c.execute(text(f'UPDATE {table} SET {pointer}=:reference WHERE {pk}=9'),{'reference':reference})
        result,payload=self.create_workflow(domain,action,filename)
        id=result['workflow_instance_id']
        with self.engine.connect() as c:
            current=c.execute(text('SELECT request_payload FROM workflow_instances WHERE workflow_instance_id_pk=:id'),{'id':id}).scalar_one()
            legacy=c.execute(text(f'SELECT request_payload FROM {LEGACY[domain]} WHERE workflow_instance_id_fk=:id'),{'id':id}).scalar_one()
        self.assertEqual(current['workflow_document_attachment'],legacy['workflow_document_attachment'])
        self.assertEqual(current['workflow_document_attachment'],payload['workflow_document_attachment'])
        discovery=self.client.get(f'/api/v1/workflow-engine/instances/{id}')
        self.assertEqual(discovery.status_code,200,discovery.text)
        self.assertNotIn('firebase_upload_files',discovery.text)
        response=self.workflow_download(result,payload)
        self.assertEqual(response.status_code,200,response.text)
        output=response.json()['data']
        bytes=self.bucket.fetch(output['download_url'])
        proposed_hash=hashlib.sha256(bytes).hexdigest()
        self.assertEqual(proposed_hash,hashlib.sha256(NEW_BYTES).hexdigest())
        self.assertNotEqual(proposed_hash,hashlib.sha256(OLD_BYTES).hexdigest())
        self.evidence.append({'case':f'{domain.upper()} {action}' + (' SAME_FILENAME' if same_name else ''),'workflow_instance_id':id,
            'committed_filename':old_name,'proposed_filename':filename,
            'old_sha256':hashlib.sha256(OLD_BYTES).hexdigest(),'new_sha256':hashlib.sha256(NEW_BYTES).hexdigest(),
            'retrieved_sha256':proposed_hash,'storage_generation':current['workflow_document_attachment']['storage_generation'],
            'version_id':output['version_id'],'response':{**output,'download_url':'<redacted signed URL>'}})
    return test

for domain in CASES:
    setattr(ApprovalReviewPostgresTests,f'test_{domain}_same_filename_persisted',persisted_document_case(domain,'UPDATE',True))
    for action in ('CREATE','UPDATE'):
        setattr(ApprovalReviewPostgresTests,f'test_{domain}_{action.lower()}_persisted_attachment',persisted_document_case(domain,action))

def execution_case(domain,action):
    def test(self):
        from app_backend.services.service_07_alerts_wf_engine.contracts_management_wf import execute_approved_contracts_action
        from app_backend.services.service_07_alerts_wf_engine.accounts_payables_wf import execute_approved_accounts_payables_action
        from app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf import execute_approved_accounts_receivables_action
        adapter={'contract':execute_approved_contracts_action,'ap':execute_approved_accounts_payables_action,
                 'ar':execute_approved_accounts_receivables_action}[domain]
        result,payload=self.create_workflow(domain,action)
        response=runtime_engine.approve_workflow_step(workflow_code=CASES[domain][2],
            payload={'workflow_instance_id':result['workflow_instance_id'],
                     'workflow_instance_step_id':result['workflow_instance_step_id'],
                     'acting_user_principal_name':'reviewer@example.invalid'},
            execution_adapter=adapter,legacy_table_name=LEGACY[domain],
            acting_user_principal_name='reviewer@example.invalid',organization_id=77)
        self.assertEqual(response['workflow_status'],'EXECUTED',response)
        _,table,pk,_,_=READS[domain]
        pointer=CASES[domain][3]
        record_id=response['domain_reference_id']
        with self.engine.connect() as c:
            committed=c.execute(text(f'SELECT {pointer} FROM {table} WHERE {pk}=:id'),{'id':record_id}).scalar_one()
        self.assertEqual(committed,payload[pointer])
        generation=int(payload['workflow_document_attachment']['storage_generation'])
        self.assertEqual(self.bucket.records[(payload['workflow_document_staged_blob_path'],generation)]['bytes'],NEW_BYTES)
        self.assertEqual(self.workflow_download(result,payload).status_code,409)
    return test

for domain in CASES:
    for action in ('CREATE','UPDATE'):
        setattr(ApprovalReviewPostgresTests,f'test_{domain}_{action.lower()}_execution_retains_pointer',execution_case(domain,action))

if __name__=='__main__':
    unittest.main()
