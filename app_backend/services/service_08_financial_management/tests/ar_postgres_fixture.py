"""Disposable PostgreSQL fixtures. Never fall back to RAILWAY_DB_URL."""
from contextlib import ExitStack
from io import BytesIO
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app_backend.services import main as consolidated
from app_backend.services.auth_context import get_authenticated_context
from app_backend.services.service_08_financial_management.api import main as api
from app_backend.services.service_08_financial_management.logic import accounts_receivables_aggregate as aggregate
from app_backend.services.service_08_financial_management.integrations import firebase_ar_invoice_document_upload as upload
from app_backend.services.service_08_financial_management.integrations import firebase_ar_invoice_document_download as download
from app_backend.services.service_07_alerts_wf_engine import accounts_receivables_wf as workflow
from app_backend.services.service_07_alerts_wf_engine import workflow_runtime_engine as runtime
from app_backend.services.service_07_alerts_wf_engine import workflow_document_cleanup as cleanup
from app_backend.services.service_07_alerts_wf_engine import workflow_document_download as proposed_download
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import apply_workflow_engine_migration
from app_backend.services.service_07_alerts_wf_engine.tests.test_workflow_attachment_download import VersionedBucket, context


SERVICES = Path(__file__).resolve().parents[2]
MIGRATIONS = SERVICES / 'service_08_financial_management/data/ar_header_lines'
URL = os.getenv('AR_REDESIGN_TEST_DATABASE_URL')


def sql_script(path):
    source = Path(path).read_text()
    if Path(path).name == 'seed_urban_express.sql':
        # Only seed data inside the fixture's transaction. The file also contains
        # later manual rollback/read-only verification commands for deployment.
        source = re.split(r'^\s*COMMIT;\s*$', source, maxsplit=1, flags=re.M)[0]
    return re.sub(r'^\s*(BEGIN|COMMIT);\s*$', '', source, flags=re.M)


@unittest.skipUnless(URL, 'AR_REDESIGN_TEST_DATABASE_URL must name a disposable loopback *_tests database.')
class ARPostgresCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(URL)
        if url.get_backend_name() != 'postgresql' or url.host not in ('127.0.0.1', 'localhost') or not url.database.endswith('_tests'):
            raise RuntimeError('Refusing a non-loopback/non-test database URL.')
        cls.name = f'ar_redesign_{uuid4().hex}_tests'
        cls.admin = create_engine(url, isolation_level='AUTOCOMMIT')
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{cls.name}"')
        cls.engine = create_engine(url.set(database=cls.name))
        cls.addClassCleanup(cls.drop_database)
        with cls.engine.begin() as c:
            c.exec_driver_sql('''CREATE TABLE organization_master(org_id_pk INTEGER PRIMARY KEY,org_name TEXT,
                org_address TEXT,org_phone_primary TEXT,org_phone_secondary TEXT,org_email_primary TEXT,
                org_email_secondary TEXT,company_registration_number TEXT)''')
            c.exec_driver_sql("INSERT INTO organization_master(org_id_pk,org_name,org_address,org_phone_primary,org_email_primary) VALUES (77,'Urban Express','Dubai UAE','+971 4 22 92500','info@example.invalid'),(88,'Other organization',NULL,NULL,NULL)")
            c.exec_driver_sql('CREATE TABLE department_master(dep_id_pk INTEGER PRIMARY KEY,dep_org_id_fk INTEGER)')
            c.exec_driver_sql('INSERT INTO department_master VALUES(20,77),(21,88)')
            for name in ('service_06_contracts_management/data/customer_master.sql',
                         'service_06_contracts_management/data/contracts_management.sql',
                         'service_08_financial_management/data/accounts_receivable.sql',
                         'service_08_financial_management/data/accounts_receivables_workflow_requests.sql'):
                c.exec_driver_sql(sql_script(SERVICES / name))
            c.exec_driver_sql('ALTER TABLE contracts_management ADD COLUMN cont_approval_status TEXT, ADD COLUMN cont_link_path TEXT, ADD COLUMN total_contract_value DOUBLE PRECISION')
            c.execute(text(sql_script(MIGRATIONS / 'migration.sql')))
            c.exec_driver_sql('''CREATE TABLE user_master(user_id_pk INTEGER PRIMARY KEY,user_principal_name TEXT,
                user_org_id_fk INTEGER,is_active BOOLEAN DEFAULT TRUE,is_deleted BOOLEAN DEFAULT FALSE,display_name TEXT,email TEXT)''')
            c.exec_driver_sql("INSERT INTO user_master(user_id_pk,user_principal_name,user_org_id_fk) VALUES(1,'requester@example.invalid',77),(2,'reviewer@example.invalid',77),(3,'foreign@example.invalid',88)")
            c.exec_driver_sql('CREATE TABLE permission_master(module_name TEXT,action_name TEXT,permission_code TEXT)')
            c.exec_driver_sql("""CREATE VIEW v_user_access_rights AS SELECT user_principal_name,'WORKFLOW'::text AS module_name,
                action_name,'test'::text AS role_name FROM user_master CROSS JOIN (VALUES('SUBMIT'),('APPROVE')) a(action_name)""")
            apply_workflow_engine_migration(c)
            definition = c.execute(text("SELECT workflow_definition_id_pk FROM workflow_definitions WHERE workflow_code='ACCOUNTS_RECEIVABLE' AND organization_id_fk=77")).scalar_one()
            version = c.execute(text("INSERT INTO workflow_definition_versions(workflow_definition_id_fk,version_number,version_status,applies_to_action) VALUES(:id,1,'PUBLISHED','ALL') RETURNING workflow_version_id_pk"), {'id': definition}).scalar_one()
            nodes = []
            for key, kind, principal in [('requester', 'REQUESTER', 'requester@example.invalid'), ('approver', 'APPROVER', 'reviewer@example.invalid'), ('end', 'END', None)]:
                nodes.append(c.execute(text('INSERT INTO workflow_nodes(workflow_version_id_fk,node_key,node_type,user_principal_name) VALUES(:v,:k,:t,:p) RETURNING workflow_node_id_pk'), {'v': version, 'k': key, 't': kind, 'p': principal}).scalar_one())
            for i, (a, b) in enumerate(zip(nodes, nodes[1:]), 1):
                c.execute(text('INSERT INTO workflow_edges(workflow_version_id_fk,source_node_id_fk,target_node_id_fk,edge_sequence) VALUES(:v,:a,:b,:i)'), {'v': version, 'a': a, 'b': b, 'i': i})

    @classmethod
    def drop_database(cls):
        cls.engine.dispose()
        with cls.admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{cls.name}" WITH (FORCE)')
        cls.admin.dispose()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # All already-imported business/workflow engine factories point only to this disposable database.
        for name, module in list(sys.modules.items()):
            if name.startswith('app_backend.services.') and hasattr(module, 'db_engine'):
                self.stack.enter_context(patch.object(module, 'db_engine', return_value=self.engine))
        self.auth = context(77, 'requester@example.invalid')
        for app in (api.app, consolidated.app):
            old = dict(app.dependency_overrides)
            self.addCleanup(self.restore_overrides, app, old)
            app.dependency_overrides[get_authenticated_context] = lambda: self.auth
        self.bucket = VersionedBucket()
        for module in (upload, download, cleanup, proposed_download):
            self.stack.enter_context(patch.object(module, 'initialize_firebase_app', return_value=None))
            self.stack.enter_context(patch.object(module, 'FIREBASE_STORAGE_BUCKET', 'task164-test-bucket'))
        self.stack.enter_context(patch.object(upload.storage, 'bucket', return_value=self.bucket))
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.run_number = 0
        with self.engine.begin() as c:
            c.exec_driver_sql('TRUNCATE accounts_receivables,accounts_receivables_workflow_requests,workflow_instances,contracts_management,customer_master,organization_tax_configuration,organization_invoice_configuration RESTART IDENTITY CASCADE')
            c.exec_driver_sql("UPDATE organization_master SET org_name='Urban Express' WHERE org_id_pk=77")
            c.exec_driver_sql("INSERT INTO customer_master(cust_id_pk,cust_org_id_fk,cust_code,cust_name,cust_category,cust_tax_registration_number,cust_billing_address,procurement_head_phone_primary) VALUES(10,77,'C10','Saraj Al Jamal Passengers Transport By Buses L.L.C','TEST','104885870600003','Dubai UAE','+971504112658'),(11,88,'C11','Foreign customer','TEST',NULL,NULL,NULL),(12,77,'C12','Second customer','TEST',NULL,NULL,NULL)")
            c.exec_driver_sql("SET LOCAL ar_rollout.urban_express_org_id='77'")
            c.execute(text(sql_script(MIGRATIONS / 'seed_urban_express.sql')))
            for contract, org, customer, dep in ((9,77,10,20), (10,88,11,21), (11,77,12,20)):
                c.execute(text("""INSERT INTO contracts_management(cont_id_pk,cont_org_id_fk,cont_cust_id_fk,cont_dep_id_fk,cont_contract_number,
                    cont_contract_name,cont_start_date,cont_end_date,cont_revenue_basis,cont_big_bus_count_gt_34,cont_big_bus_rate_pm,
                    cont_no_of_billing_months,cont_no_of_work_days_per_week,cont_no_of_round_trips_per_day,cont_approval_status)
                    VALUES(:id,:org,:cust,:dep,:number,'Structured transport contract','2026-08-18','2026-10-17','PER_BUS',1,4500,2,6,2,'APPROVED')"""),
                    {'id': contract, 'org': org, 'cust': customer, 'dep': dep, 'number': 'UE/SJPT/2026/001' if contract == 9 else str(contract)})

    @staticmethod
    def restore_overrides(app, old):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)

    def query(self, sql, params=None):
        with self.engine.connect() as c:
            return [dict(row) for row in c.execute(text(sql), params or {}).mappings()]

    def execute(self, sql, params=None):
        with self.engine.begin() as c:
            c.execute(text(sql), params or {})

    def header(self):
        self.run_number += 1
        return {'user_principal_name': 'requester@example.invalid', 'ar_org_id_fk': 77, 'ar_cust_id_fk': 10,
                'ar_invoice_number': f'EXISTING-NUMBER-{self.run_number}', 'ar_invoice_date': '2026-08-31',
                'ar_due_date': '2026-09-07', 'ar_currency_code': 'AED'}

    def prefill(self, **changes):
        request = {'ar_org_id_fk': 77, 'ar_cust_id_fk': 10, 'ar_contract_id_fk': 9, 'ar_invoice_date': '2026-08-31',
                   'ar_billing_period_start': '2026-08-01', 'ar_billing_period_end': '2026-08-31', **changes}
        return self.client.post('/api/v1/accounts-receivables/contract-prefill', json=request)

    def contract_payload(self):
        response = self.prefill()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()['data']
        header = self.header()
        header.update({k: data['header_prefill'][k] for k in ('ar_contract_id_fk','ar_contract','ar_billing_period_start','ar_billing_period_end')})
        header['lines'] = [self.line_input(data['suggested_line'])]
        header['lines'][0]['ar_line_description'] = 'Staff transportation from International City to Dubai Motor City'
        header['lines'][0]['ar_line_vehicle_description'] = '66-seater bus'
        return header

    @staticmethod
    def line_input(line):
        return {key: value for key, value in line.items() if key in aggregate.LINE_INPUT_FIELDS and value is not None}

    @staticmethod
    def manual_line(**changes):
        return {'ar_line_description': 'Additional service', 'ar_line_quantity': '2', 'ar_line_unit_rate': '10.25', **changes}

    def submit(self, payload=None, action='create', multipart=False):
        import json
        payload = payload if payload is not None else self.contract_payload()
        path = '/api/v1/accounts-receivables/' + action + ('-with-document' if multipart else '')
        return self.client.post(path, data={'payload': json.dumps(payload)}) if multipart else self.client.post(path, json=payload)

    def approve(self, pending):
        response = runtime.approve_workflow_step(workflow_code='ACCOUNTS_RECEIVABLE',
            payload={'workflow_instance_id': pending['workflow_instance_id'], 'workflow_instance_step_id': pending['workflow_instance_step_id'],
                     'acting_user_principal_name': 'reviewer@example.invalid'},
            execution_adapter=workflow.execute_approved_accounts_receivables_action,
            legacy_table_name='accounts_receivables_workflow_requests',
            final_payload_enricher=workflow._inject_final_ar_approval_metadata,
            acting_user_principal_name='reviewer@example.invalid', organization_id=77)
        return response

    def create(self, payload=None):
        submitted = self.submit(payload)
        self.assertEqual(submitted.status_code, 200, submitted.text)
        pending = submitted.json()['data']
        self.assertEqual(pending['workflow_status'], 'PENDING_APPROVAL')
        approved = self.approve(pending)
        self.assertEqual(approved['workflow_status'], 'EXECUTED', approved)
        return approved['domain_reference_id'], pending

    def detail(self, ar_id):
        response = self.client.post('/api/v1/accounts-receivables', json={'ar_id_pk': ar_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data'][0]

    def update_payload(self, ar_id):
        detail = self.detail(ar_id)
        fields = api.AccountsReceivableUpdatePayload.model_fields
        payload = {key: value for key, value in detail.items() if key in fields and key not in ('lines','ar_invoice_amount','ar_tax_amount','ar_invoice_file_path','ar_approval_status','ar_approved_at','ar_approved_by') and value is not None}
        payload.update(user_principal_name='requester@example.invalid', ar_expected_revision=detail['ar_revision'],
                       lines=[self.line_input(line) for line in detail['lines']])
        return payload

    def proposal(self, pending):
        return self.query('SELECT request_payload FROM workflow_instances WHERE workflow_instance_id_pk=:id', {'id': pending['workflow_instance_id']})[0]['request_payload']

    def pdf_bytes(self, proposal):
        attachment = proposal['workflow_document_attachment']
        return self.bucket.records[(proposal['workflow_document_staged_blob_path'], int(attachment['storage_generation']))]['bytes']

    def committed_download_bytes(self, url):
        # Existing committed-document URLs name the current object; proposed-document
        # URLs additionally pin generation. Model both SDK download contracts locally.
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(url)
        if 'generation' in parse_qs(parsed.query):
            return self.bucket.fetch(url)
        path = unquote(parsed.path).removeprefix('/' + self.bucket.sdk_bucket.name + '/')
        generations = [generation for name, generation in self.bucket.records if name == path]
        return self.bucket.records[(path, max(generations))]['bytes']
