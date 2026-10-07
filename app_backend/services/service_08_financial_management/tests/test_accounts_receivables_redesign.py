from copy import deepcopy
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from app_backend.services.service_08_financial_management.tests.ar_postgres_fixture import (
    ARPostgresCase, MIGRATIONS, sql_script, api, consolidated, aggregate, upload, download,
    workflow, runtime, proposed_download,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import (
    ARValidationError, calculate_line, calculate_totals,
)
from app_backend.services.service_08_financial_management.integrations.accounts_receivables_invoice_document_generator import amount_in_words


class ARCalculationTests(unittest.TestCase):
    def test_calendar_months_inclusive_dates_and_rounding(self):
        for start, end, denominator, net in [('2026-08-18','2026-08-31',31,'2032.26'),
                                           ('2026-10-01','2026-10-17',31,'2467.74'),
                                           ('2028-02-01','2028-02-29',29,'4500.00')]:
            with self.subTest(start=start):
                line = calculate_line({'ar_line_quantity':'1','ar_line_unit_rate':'4500','ar_line_tax_rate':'5',
                    'ar_line_proration_method':'CALENDAR_DAYS','ar_line_service_period_start':start,'ar_line_service_period_end':end})
                self.assertEqual(line['ar_line_net_amount'], Decimal(net))
                self.assertEqual(line['ar_line_proration_denominator'], denominator)
        self.assertEqual(calculate_line({'ar_line_quantity':'1','ar_line_unit_rate':'.10','ar_line_tax_rate':'5'})['ar_line_tax_amount'], Decimal('.01'))

    def test_decimal_precision_and_header_sum_of_rounded_line_tax(self):
        line = calculate_line({'ar_line_quantity':'0.1','ar_line_unit_rate':'0.1','ar_line_tax_rate':'5'})
        self.assertEqual(line['ar_line_net_amount'], Decimal('.01'))
        lines = [calculate_line({'ar_line_quantity':'1','ar_line_unit_rate':'.10','ar_line_tax_rate':'5'}) for _ in range(2)]
        self.assertEqual(calculate_totals(lines), {'ar_subtotal_amount': Decimal('.20'), 'ar_tax_amount': Decimal('.02'), 'ar_invoice_amount': Decimal('.22')})

    def test_invalid_calculations_are_rejected(self):
        base = {'ar_line_quantity':'1','ar_line_unit_rate':'10','ar_line_tax_rate':'5'}
        for change in [{'ar_line_quantity':'-1'},{'ar_line_quantity':'NaN'},{'ar_line_unit_rate':'Infinity'},
                       {'ar_line_unit_rate':'1.001'},{'ar_line_tax_rate':'-1'},{'ar_line_net_amount':'999'},
                       {'ar_line_proration_method':'WORKING_DAYS'},
                       {'ar_line_service_period_start':'2026-08-31','ar_line_service_period_end':'2026-08-01'},
                       {'ar_line_proration_method':'CALENDAR_DAYS'},
                       {'ar_line_proration_method':'CALENDAR_DAYS','ar_line_service_period_start':'2026-08-31','ar_line_service_period_end':'2026-09-01'},
                       {'ar_line_quantity':'100000000000000'}]:
            with self.subTest(change=change), self.assertRaises(ARValidationError):
                calculate_line({**base, **change})

    def test_amount_in_words_aed_zero_fils_and_large_amounts(self):
        self.assertEqual(amount_in_words('2133.87'), 'TWO THOUSAND ONE HUNDRED THIRTY THREE DIRHAMS AND EIGHTY SEVEN FILS ONLY')
        self.assertEqual(amount_in_words('1.00'), 'ONE DIRHAM AND ZERO FILS ONLY')
        self.assertEqual(amount_in_words('0.01'), 'ZERO DIRHAMS AND ONE FILS ONLY')
        self.assertIn('MILLION', amount_in_words('1000000.00'))
        self.assertEqual(amount_in_words('12.34','USD'), 'USD TWELVE AND 34/100 ONLY')


class ARHeaderLinesPostgresTests(ARPostgresCase):
    def test_concurrent_approval_and_update_retry_do_not_duplicate_lines(self):
        from concurrent.futures import ThreadPoolExecutor
        pending=self.submit().json()['data']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.approve,[pending,pending]))
        self.assertTrue(all(result['workflow_status']=='EXECUTED' for result in results),results)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivables')[0]['n'],1)
        ar_id=results[0]['domain_reference_id']
        update=self.update_payload(ar_id)
        update['lines'].append(self.manual_line(ar_line_number=2))
        pending=self.submit(update,'update').json()['data']
        first=self.approve(pending); self.assertEqual(first['workflow_status'],'EXECUTED',first)
        snapshot=self.detail(ar_id)
        second=self.approve(pending); self.assertEqual(second['workflow_status'],'EXECUTED',second)
        self.assertEqual(self.detail(ar_id),snapshot)
        self.assertEqual(snapshot['line_count'],2)

    def test_concurrent_distinct_proposals_recheck_case_insensitive_invoice_number(self):
        from concurrent.futures import ThreadPoolExecutor
        first=self.contract_payload(); first['ar_invoice_number']='Same-Invoice'
        second=deepcopy(first); second['ar_invoice_number']='SAME-INVOICE'
        pending=[self.submit(payload).json()['data'] for payload in (first,second)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.approve,pending))
        self.assertEqual(sorted(result['workflow_status'] for result in results),['EXECUTED','EXECUTION_FAILED'],results)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivables')[0]['n'],1)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n'],1)
        self.assertEqual(len(self.bucket.records),1)

    def test_pdf_multiple_pages_and_literal_markup(self):
        lines=[self.manual_line(ar_line_number=i,ar_line_description=f'Service {i}: <script> & transport '+('Long service description. '*20)) for i in range(1,26)]
        ar_id,pending=self.create({**self.header(),'lines':lines})
        reader=PdfReader(BytesIO(self.pdf_bytes(self.proposal(pending))))
        self.assertGreater(len(reader.pages),1)
        content='\n'.join(page.extract_text() for page in reader.pages)
        self.assertIn('Service 25: <script> & transport',content)
        self.assertIn('TOTAL INVOICE AMOUNT',content)
        self.assertEqual(self.detail(ar_id)['ar_invoice_amount'],'538.25')

    def test_contract_price_tampering_double_contract_and_nullable_dates(self):
        original=self.contract_payload()
        for field,value in [('ar_line_quantity','2'),('ar_line_unit_rate','999'),('ar_line_service_period_start','2026-08-01')]:
            with self.subTest(field=field):
                payload=deepcopy(original); payload['lines'][0][field]=value
                self.assertEqual(self.submit(payload).status_code,400)
        original['lines'].append({**original['lines'][0],'ar_line_number':2})
        self.assertEqual(self.submit(original).status_code,400)
        manual={**self.header(),'ar_due_date':None,'lines':[self.manual_line()]}
        ar_id,_=self.create(manual)
        detail=self.detail(ar_id)
        for field in ('ar_due_date','ar_billing_period_start','ar_billing_period_end'):
            self.assertIsNone(detail[field])
        response=self.submit(self.update_payload(ar_id),'update')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.approve(response.json()['data'])['workflow_status'],'EXECUTED')

    def test_customer_change_snapshots_new_customer_and_preserves_organization(self):
        ar_id,_=self.create({**self.header(),'lines':[self.manual_line()]})
        old=self.detail(ar_id)['ar_invoice_identity_snapshot']
        self.execute("UPDATE organization_master SET org_name='Changed organization' WHERE org_id_pk=77")
        update=self.update_payload(ar_id); update['ar_cust_id_fk']=12
        response=self.submit(update,'update'); self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.approve(response.json()['data'])['workflow_status'],'EXECUTED')
        new=self.detail(ar_id)['ar_invoice_identity_snapshot']
        self.assertEqual(new['organization'],old['organization'])
        self.assertEqual(new['customer']['cust_name'],'Second customer')

    def test_legacy_multipart_and_all_remaining_routes_through_both_apps(self):
        for name,app in [('service_08',api.app),('consolidated',consolidated.app)]:
            with self.subTest(app=name), TestClient(app) as client:
                response=client.post('/api/v1/accounts-receivables/tax-options',json={'ar_invoice_date':'2026-08-31'})
                self.assertEqual(response.status_code,200,response.text)
                response=client.post('/api/v1/accounts-receivables/contract-prefill',json={
                    'ar_cust_id_fk':10,'ar_contract_id_fk':9,'ar_invoice_date':'2026-08-31',
                    'ar_billing_period_start':'2026-08-01','ar_billing_period_end':'2026-08-31'})
                self.assertEqual(response.status_code,200,response.text)
                payload={**self.header(),'ar_invoice_amount':'20000','ar_tax_amount':'1000'}
                response=client.post('/api/v1/accounts-receivables/create-with-document',data={'payload':json.dumps(payload)},files={'file':('legacy.pdf',b'original','application/pdf')})
                self.assertEqual(response.status_code,200,response.text)
                result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
                ar_id=result['domain_reference_id']; payload['ar_id_pk']=ar_id
                response=client.post('/api/v1/accounts-receivables/update-with-document',data={'payload':json.dumps(payload)},files={'file':('updated.pdf',b'updated','application/pdf')})
                self.assertEqual(response.status_code,200,response.text)
                result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
                response=client.post('/api/v1/accounts-receivables',json={'ar_id_pk':ar_id})
                self.assertEqual(response.status_code,200,response.text)
                self.assertTrue(response.json()['data'][0]['legacy_header_only'])
                self.assertEqual(client.get('/api/v1/accounts-receivables').status_code,200)
                response=client.post('/api/v1/accounts-receivables/documents/upload',data={'ar_id_pk':str(ar_id),'user_principal_name':'requester@example.invalid'},files={'file':('manual.pdf',b'manual','application/pdf')})
                self.assertEqual(response.status_code,200,response.text)
                response=client.post('/api/v1/accounts-receivables/documents/download',json={'ar_id_pk':ar_id})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(self.committed_download_bytes(response.json()['data']['download_url']),b'manual')
                response=client.post('/api/v1/accounts-receivables/delete',json={'ar_id_pk':ar_id,'user_principal_name':'requester@example.invalid'})
                self.assertEqual(response.status_code,200,response.text)
                result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)

    def test_first_migration_preserves_legacy_financial_values(self):
        # This reversal exists only inside a rolled-back transaction in the guarded,
        # disposable test database; it is not a rollout/rollback migration.
        with self.engine.connect() as conn:
            transaction=conn.begin()
            try:
                conn.exec_driver_sql('DROP TABLE accounts_receivable_lines,organization_tax_configuration,organization_invoice_configuration')
                for field in ('ar_contract_id_fk','ar_contract_number_snapshot','ar_contract_name_snapshot','ar_billing_period_start',
                              'ar_billing_period_end','ar_subtotal_amount','ar_invoice_identity_snapshot','ar_revision'):
                    conn.exec_driver_sql(f'ALTER TABLE accounts_receivables DROP COLUMN {field}')
                for index,(gross,tax) in enumerate([('20000','1000'),('25000','1250'),('200000','9997'),('1.50','0'),('1.50','0'),('1','0')],1):
                    conn.execute(text("INSERT INTO accounts_receivables(ar_org_id_fk,ar_cust_id_fk,ar_invoice_number,ar_invoice_date,ar_currency_code,ar_invoice_amount,ar_tax_amount) VALUES(77,10,:number,'2026-09-09','AED',:gross,:tax)"),{'number':f'PRE-{index}','gross':gross,'tax':tax})
                columns='ar_id_pk,ar_invoice_amount,ar_tax_amount,ar_received_amount,ar_balance_amount,created_at,updated_at'
                before=list(conn.execute(text(f'SELECT {columns} FROM accounts_receivables ORDER BY ar_id_pk')))
                conn.execute(text(sql_script(MIGRATIONS/'migration.sql')))
                self.assertEqual(list(conn.execute(text(f'SELECT {columns} FROM accounts_receivables ORDER BY ar_id_pk'))),before)
                self.assertEqual(conn.execute(text('SELECT count(*) FROM accounts_receivables WHERE ar_subtotal_amount IS NULL')).scalar_one(),6)
                self.assertEqual(conn.execute(text('SELECT count(*) FROM accounts_receivable_lines')).scalar_one(),0)
            finally:
                transaction.rollback()

    def test_postgres_error_rolls_back_savepoint_and_keeps_outer_workflow_usable(self):
        pending=self.submit().json()['data']
        self.execute("CREATE FUNCTION fail_ar_test_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Injected database failure'; END $$")
        self.execute('CREATE TRIGGER fail_ar_test_insert BEFORE INSERT ON accounts_receivable_lines FOR EACH ROW EXECUTE FUNCTION fail_ar_test_insert()')
        try:
            result=self.approve(pending)
        finally:
            self.execute('DROP TRIGGER fail_ar_test_insert ON accounts_receivable_lines')
            self.execute('DROP FUNCTION fail_ar_test_insert()')
        self.assertEqual(result['workflow_status'],'EXECUTION_FAILED',result)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivables')[0]['n'],0)
        self.assertEqual(self.query('SELECT workflow_status FROM workflow_instances')[0]['workflow_status'],'EXECUTION_FAILED')
        self.assertEqual(len(self.bucket.records),0)

    def test_export_exact_api_examples(self):
        prefill_request={'ar_cust_id_fk':10,'ar_contract_id_fk':9,'ar_invoice_date':'2026-08-31',
                         'ar_billing_period_start':'2026-08-01','ar_billing_period_end':'2026-08-31'}
        prefill_response=self.client.post('/api/v1/accounts-receivables/contract-prefill',json=prefill_request)
        self.assertEqual(prefill_response.status_code,200,prefill_response.text)
        create_request=self.contract_payload()
        pending=self.submit(create_request); self.assertEqual(pending.status_code,200,pending.text)
        approved=self.approve(pending.json()['data']); self.assertEqual(approved['workflow_status'],'EXECUTED',approved)
        ar_id=approved['domain_reference_id']
        detail_response=self.client.post('/api/v1/accounts-receivables',json={'ar_id_pk':ar_id})
        update_request=self.update_payload(ar_id); update_request['lines'].append(self.manual_line(ar_line_number=2))
        updated=self.submit(update_request,'update'); self.assertEqual(updated.status_code,200,updated.text)
        update_approved=self.approve(updated.json()['data']); self.assertEqual(update_approved['workflow_status'],'EXECUTED',update_approved)
        legacy_request={**self.header(),'ar_invoice_amount':'20000.00','ar_tax_amount':'1000.00'}
        legacy_id,_=self.create(legacy_request)
        document_download_request={'ar_id_pk':ar_id}
        document_download=self.client.post('/api/v1/accounts-receivables/documents/download',json=document_download_request)
        self.assertEqual(document_download.status_code,200,document_download.text)
        legacy_upload_fields={'ar_id_pk':str(legacy_id),'user_principal_name':'requester@example.invalid'}
        legacy_upload=self.client.post('/api/v1/accounts-receivables/documents/upload',data=legacy_upload_fields,
            files={'file':('legacy.pdf',b'legacy example invoice','application/pdf')})
        self.assertEqual(legacy_upload.status_code,200,legacy_upload.text)
        legacy_download_request={'ar_id':legacy_id}
        legacy_download=self.client.post('/api/v1/accounts-receivables/documents/download',json=legacy_download_request)
        self.assertEqual(legacy_download.status_code,200,legacy_download.text)
        number_only_request={'ar_invoice_number':create_request['ar_invoice_number']}
        number_only_download=self.client.post('/api/v1/accounts-receivables/documents/download',json=number_only_request)
        self.assertEqual(number_only_download.status_code,400,number_only_download.text)
        legacy_response=self.client.post('/api/v1/accounts-receivables',json={'ar_id_pk':legacy_id})
        invalid=self.submit({**self.header(),'lines':[]})
        tax_request={'ar_invoice_date':'2026-08-31'}
        tax_response=self.client.post('/api/v1/accounts-receivables/tax-options',json=tax_request)
        if os.getenv('AR_REDESIGN_ARTIFACT_DIR'):
            examples={'fixture_only':True,'prefill_request':prefill_request,'prefill_response':prefill_response.json(),
                      'tax_options_request':tax_request,'tax_options_response':tax_response.json(),
                      'create_request':create_request,'create_pending_response':pending.json(),
                      'create_approval_runtime_result':approved,'detail_request':{'ar_id_pk':ar_id},'detail_response':detail_response.json(),
                      'update_request':update_request,'update_pending_response':updated.json(),'update_approval_runtime_result':update_approved,
                      'legacy_create_request':legacy_request,'legacy_detail_response':legacy_response.json(),
                      'document_download_request':document_download_request,'document_download_status':document_download.status_code,
                      'legacy_document_upload_multipart_fields':legacy_upload_fields,
                      'legacy_document_upload_file_part':{'field':'file','filename':'legacy.pdf','content_type':'application/pdf'},
                      'legacy_document_upload_status':legacy_upload.status_code,
                      'legacy_document_download_request':legacy_download_request,'legacy_document_download_status':legacy_download.status_code,
                      'invoice_number_only_document_download_rejected':{'request':number_only_request,
                          'http_status':number_only_download.status_code,'body':number_only_download.json()},
                      'invalid_empty_lines_response':{'http_status':invalid.status_code,'body':invalid.json()}}
            root=Path(os.environ['AR_REDESIGN_ARTIFACT_DIR']); root.mkdir(parents=True,exist_ok=True)
            (root/'api_contract_examples.json').write_text(json.dumps(examples,indent=2,default=str)+'\n')

    def test_database_constraints_precision_indexes_and_audit(self):
        ar_id,_=self.create({**self.header(),'lines':[self.manual_line(),self.manual_line(ar_line_number=2)]})
        lines=self.query('SELECT * FROM accounts_receivable_lines ORDER BY ar_line_id_pk')
        self.assertEqual(lines[0]['ar_line_quantity'],Decimal('2.0000'))
        self.assertEqual(lines[0]['created_by'],'requester@example.invalid')
        self.assertIsNotNone(lines[0]['created_at']); self.assertIsNotNone(lines[0]['updated_at'])
        line_id=lines[0]['ar_line_id_pk']
        invalid_updates=[('ar_line_ar_id_fk',999999),('ar_line_tax_config_id_fk',999999),
                         ('ar_line_number',2),('ar_line_number',0),('ar_line_quantity',-1),('ar_line_unit_rate',-1),
                         ('ar_line_net_amount',-1),('ar_line_tax_rate',-1),('ar_line_tax_amount',-1),('ar_line_total_amount',-1),
                         ('ar_line_source_contract_id_fk',999999),('ar_line_description',''),('ar_line_source_type','FORGED')]
        for field,value in invalid_updates:
            with self.subTest(field=field), self.assertRaises(IntegrityError):
                self.execute(f'UPDATE accounts_receivable_lines SET {field}=:value WHERE ar_line_id_pk=:id',{'value':value,'id':line_id})
        with self.assertRaises(IntegrityError):
            self.execute("UPDATE accounts_receivable_lines SET ar_line_service_period_start='2026-09-01',ar_line_service_period_end='2026-08-01' WHERE ar_line_id_pk=:id",{'id':line_id})
        with self.assertRaises(IntegrityError):
            self.execute('UPDATE accounts_receivables SET ar_invoice_amount=999 WHERE ar_id_pk=:id',{'id':ar_id})
        indexes={row['indexname'] for row in self.query("SELECT indexname FROM pg_indexes WHERE tablename='accounts_receivable_lines'")}
        self.assertTrue({'uq_ar_line_number','uq_ar_one_contract_line','idx_ar_line_contract','idx_ar_line_tax_config'} <= indexes)
        self.execute('UPDATE accounts_receivable_lines SET ar_line_notes=\'Audit timestamp\' WHERE ar_line_id_pk=:id',{'id':line_id})
        self.assertGreater(self.query('SELECT updated_at FROM accounts_receivable_lines WHERE ar_line_id_pk=:id',{'id':line_id})[0]['updated_at'],lines[0]['updated_at'])
        precision=self.query("SELECT numeric_precision,numeric_scale FROM information_schema.columns WHERE table_name='accounts_receivable_lines' AND column_name='ar_line_net_amount'")[0]
        self.assertEqual((precision['numeric_precision'],precision['numeric_scale']),(18,2))

    def test_migration_rerun_preserves_six_historical_rows_and_no_backfill(self):
        for index,(gross,tax) in enumerate([('20000','1000'),('25000','1250'),('200000','9997'),('1.50','0'),('1.50','0'),('1','0')],1):
            self.execute("INSERT INTO accounts_receivables(ar_org_id_fk,ar_cust_id_fk,ar_invoice_number,ar_invoice_date,ar_currency_code,ar_invoice_amount,ar_tax_amount) VALUES(77,10,:number,'2026-09-09','AED',:gross,:tax)",{'number':f'HIST-{index}','gross':gross,'tax':tax})
        before=self.query('SELECT * FROM accounts_receivables ORDER BY ar_id_pk')
        with self.engine.begin() as c:
            c.execute(text(sql_script(MIGRATIONS/'migration.sql')))
            c.execute(text(sql_script(MIGRATIONS/'migration.sql')))
        self.assertEqual(self.query('SELECT * FROM accounts_receivables ORDER BY ar_id_pk'),before)
        self.assertEqual(sum(row['ar_invoice_amount'] for row in before),Decimal('245004.00'))
        self.assertEqual(sum(row['ar_tax_amount'] for row in before),Decimal('12247.00'))
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n'],0)
        self.assertTrue(all(row['ar_subtotal_amount'] is None for row in before))

    def test_configuration_seed_rerun_and_trade_license_conflict(self):
        self.execute("UPDATE organization_tax_configuration SET tax_rate=7 WHERE tax_code='STANDARD_VAT'")
        self.execute("UPDATE organization_invoice_configuration SET bank_name='Maintained Bank'")
        with self.engine.begin() as c:
            c.exec_driver_sql("SET LOCAL ar_rollout.urban_express_org_id='77'")
            c.execute(text(sql_script(MIGRATIONS/'seed_urban_express.sql')))
        self.assertEqual(self.query('SELECT tax_rate FROM organization_tax_configuration')[0]['tax_rate'],7)
        self.assertEqual(self.query('SELECT bank_name FROM organization_invoice_configuration')[0]['bank_name'],'Maintained Bank')
        self.assertEqual(self.query('SELECT company_registration_number FROM organization_master WHERE org_id_pk=77')[0]['company_registration_number'],'1349832')
        self.execute("UPDATE organization_master SET company_registration_number='CONFLICT' WHERE org_id_pk=77")
        from sqlalchemy.exc import DBAPIError
        with self.assertRaises(DBAPIError), self.engine.begin() as c:
            c.exec_driver_sql("SET LOCAL ar_rollout.urban_express_org_id='77'")
            c.execute(text(sql_script(MIGRATIONS/'seed_urban_express.sql')))
        self.execute("UPDATE organization_master SET company_registration_number='1349832' WHERE org_id_pk=77")

    def test_explicit_tax_change_requires_valid_current_configuration(self):
        ar_id,_=self.create({**self.header(),'lines':[self.manual_line()]})
        self.execute('UPDATE organization_tax_configuration SET tax_rate=7')
        update=self.update_payload(ar_id)
        update['lines'][0]['ar_line_tax_rate']='7'
        response=self.submit(update,'update'); self.assertEqual(response.status_code,200,response.text)
        result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
        self.assertEqual(Decimal(self.detail(ar_id)['lines'][0]['ar_line_tax_rate']),7)

    def test_generated_document_download_version_replacement_and_pending_review(self):
        ar_id,pending=self.create()
        proposal=self.proposal(pending)
        response=download.download_ar_invoice_document({'ar_id_pk':ar_id,'ar_org_id_fk':77})
        self.assertNotIn('error',response)
        old_bytes=self.committed_download_bytes(response['download_url']); self.assertEqual(old_bytes,self.pdf_bytes(proposal))
        update=self.update_payload(ar_id); update['lines'][0]['ar_line_description']='Amended service description'
        submitted=self.submit(update,'update'); self.assertEqual(submitted.status_code,200,submitted.text)
        new_pending=submitted.json()['data']; new_proposal=self.proposal(new_pending)
        attachment=new_proposal['workflow_document_attachment']
        instance={'workflow_instance_id':new_pending['workflow_instance_id'],'workflow_code':'ACCOUNTS_RECEIVABLE',
                  'workflow_action':'UPDATE','organization_id':77,'workflow_status':'PENDING_APPROVAL','request_payload':new_proposal}
        proposed=proposed_download.download_workflow_document(instance,attachment['attachment_id'],attachment['version_id'])
        self.assertEqual(self.bucket.fetch(proposed['download_url']),self.pdf_bytes(new_proposal))
        self.assertNotEqual(old_bytes,self.pdf_bytes(new_proposal))
        self.assertEqual(self.detail(ar_id)['ar_invoice_file_path'],proposal['ar_invoice_file_path'])
        result=self.approve(new_pending); self.assertEqual(result['workflow_status'],'EXECUTED',result)
        self.assertEqual(self.detail(ar_id)['ar_invoice_file_path'],new_proposal['ar_invoice_file_path'])
        self.assertEqual(len(self.bucket.records),2)

    def test_workflow_rejection_and_failed_staging_cleanup(self):
        self.execute("UPDATE workflow_definition_versions SET version_status='DRAFT'")
        response=self.submit(); self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(len(self.bucket.records),0)
        self.execute("UPDATE workflow_definition_versions SET version_status='PUBLISHED'")
        from app_backend.services.service_07_alerts_wf_engine.tests.test_workflow_attachment_download import VersionedBlob
        original=VersionedBlob.upload_from_file
        def failing_upload(blob,*args,**kwargs):
            original(blob,*args,**kwargs)
            raise RuntimeError('Injected upload completion failure')
        with patch.object(VersionedBlob,'upload_from_file',failing_upload):
            response=self.submit()
        self.assertNotEqual(response.status_code,200)
        self.assertEqual(len(self.bucket.records),0)

    def test_list_detail_include_lines_and_contract_filter_without_n_plus_one(self):
        first,_=self.create(); second,_=self.create({**self.header(),'lines':[self.manual_line()]})
        statements=[]
        def record(conn,cursor,statement,parameters,context,executemany): statements.append(statement)
        event.listen(self.engine,'before_cursor_execute',record)
        try: response=self.client.post('/api/v1/accounts-receivables',json={'include_lines':True})
        finally: event.remove(self.engine,'before_cursor_execute',record)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['data']),2)
        self.assertEqual(sum('SELECT l.*' in sql for sql in statements),1)
        self.assertTrue(all(len(row['lines'])==1 for row in response.json()['data']))
        response=self.client.get('/api/v1/accounts-receivables')
        self.assertTrue(all('lines' not in row for row in response.json()['data']))
        response=self.client.post('/api/v1/accounts-receivables',json={'ar_contract_id_fk':9})
        self.assertEqual([row['ar_id_pk'] for row in response.json()['data']],[first])
        self.auth['organization']['org_id']=88; self.auth['user']['user_org_id_fk']=88
        self.assertEqual(self.client.post('/api/v1/accounts-receivables',json={'ar_id_pk':second}).json()['data'],[])

    def test_both_apps_json_multipart_auth_and_openapi_regression(self):
        from collections import Counter
        from fastapi.routing import APIRoute
        from app_backend.services.auth_context import get_authenticated_context
        baseline_path=Path(__file__).resolve().parents[4]/'docs/ar_redesign_baseline_evidence.json'
        baseline=json.loads(baseline_path.read_text())
        evidence={}
        for name,app in [('service_08',api.app),('consolidated',consolidated.app)]:
            with self.subTest(app=name), TestClient(app) as client:
                self.assertEqual(client.get('/health').status_code,200)
                schema=client.get('/openapi.json'); self.assertEqual(schema.status_code,200)
                routes=Counter((r.path,m) for r in app.routes if isinstance(r,APIRoute) for m in r.methods)
                self.assertTrue(all(n==1 for n in routes.values()))
                ar_routes=[(p,m) for p,m in routes if p.startswith('/api/v1/accounts-receivables')]
                self.assertEqual(len(ar_routes),11)
                for row in baseline['apps'][name]['ar_routes']:
                    self.assertIn((row['path'],row['method']),ar_routes)
                models=schema.json()['components']['schemas']
                self.assertIn('lines',models['AccountsReceivablePayload']['properties'])
                self.assertIn('ar_line_id_pk',models['AccountsReceivableLinePayload']['properties'])
                frozen={k:v for k,v in schema.json()['paths'].items() if any(x in k for x in ('accounts-payables','asset','supplier'))}
                digest=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
                self.assertEqual(digest,baseline['apps'][name]['frozen_financial_openapi_sha256'])
                overrides=dict(api.app.dependency_overrides); api.app.dependency_overrides.clear()
                try:
                    for path,method in ar_routes:
                        self.assertEqual(client.request(method,path,**({'json':{}} if method=='POST' else {})).status_code,401)
                finally: api.app.dependency_overrides.update(overrides)
                wrong=self.contract_payload(); wrong['ar_org_id_fk']=88
                self.assertEqual(client.post('/api/v1/accounts-receivables/create',json=wrong).status_code,403)
                self.assertEqual(client.post('/api/v1/accounts-receivables/create',json={}).status_code,422)
                for multipart in (False,True):
                    payload=self.contract_payload()
                    path='/api/v1/accounts-receivables/create'+('-with-document' if multipart else '')
                    response=client.post(path,data={'payload':json.dumps(payload)}) if multipart else client.post(path,json=payload)
                    self.assertEqual(response.status_code,200,response.text)
                    result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
                    update=self.update_payload(result['domain_reference_id']); update['ar_notes']='API integration edit'
                    path='/api/v1/accounts-receivables/update'+('-with-document' if multipart else '')
                    response=client.post(path,data={'payload':json.dumps(update)}) if multipart else client.post(path,json=update)
                    self.assertEqual(response.status_code,200,response.text)
                    result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
                malformed=client.post('/api/v1/accounts-receivables/create-with-document',data={'payload':json.dumps({**self.header(),'lines':[{'ar_line_quantity':'bad'}]})})
                self.assertEqual(malformed.status_code,422)
                evidence[name]={'openapi_paths':len(schema.json()['paths']),'ar_routes':ar_routes,'duplicates':[],
                                'frozen_financial_openapi_sha256':digest,'json_and_multipart_workflow_execution':'PASS'}
        if os.getenv('AR_REDESIGN_ARTIFACT_DIR'):
            root=Path(os.environ['AR_REDESIGN_ARTIFACT_DIR']); root.mkdir(parents=True,exist_ok=True)
            (root/'api_evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
            (root/'ar_openapi_schemas.json').write_text(json.dumps({k:v for k,v in models.items() if k.startswith('AccountsReceivable')},indent=2)+'\n')

    def test_contract_eligibility_and_currency(self):
        for changes, status in [({'ar_contract_id_fk':99999},404),({'ar_contract_id_fk':10},404),
                                ({'ar_contract_id_fk':11},400),({'ar_org_id_fk':88},403),({'ar_cust_id_fk':11},404)]:
            with self.subTest(changes=changes):
                response=self.prefill(**changes)
                self.assertEqual(response.status_code,status,response.text)
        self.execute("UPDATE contracts_management SET cont_approval_status='PENDING_APPROVAL' WHERE cont_id_pk=9")
        self.assertEqual(self.prefill().status_code,400)
        self.execute("UPDATE contracts_management SET cont_approval_status='APPROVED',cont_status='DRAFT' WHERE cont_id_pk=9")
        self.assertEqual(self.prefill().status_code,200)
        payload=self.contract_payload(); payload['ar_currency_code']='USD'
        self.assertEqual(self.submit(payload).status_code,400)

    def test_passenger_prefill_and_multiple_component_ambiguity(self):
        self.execute("UPDATE contracts_management SET cont_revenue_basis='PER_PASSENGER',cont_no_of_passengers=20,cont_per_passenger_rate_pm=100 WHERE cont_id_pk=9")
        response=self.prefill(); self.assertEqual(response.status_code,200,response.text)
        line=response.json()['data']['suggested_line']
        self.assertEqual(Decimal(line['ar_line_quantity']),20)
        self.assertEqual(line['ar_line_uom'],'PASSENGER_MONTH')
        self.execute("UPDATE contracts_management SET cont_revenue_basis='PER_PASSENGER_AND_PER_BUS' WHERE cont_id_pk=9")
        response=self.prefill(); self.assertEqual(response.status_code,400,response.text)
        self.assertIn('Ambiguous',response.json()['error'])
        self.execute("UPDATE contracts_management SET cont_revenue_basis='PER_BUS',cont_medium_bus_count_17_34=1,cont_medium_bus_rate_pm=3000 WHERE cont_id_pk=9")
        self.assertIn('Ambiguous',self.prefill().json()['error'])

    def test_contract_end_partial_month_no_overlap_and_no_extra_usage(self):
        self.execute('UPDATE contracts_management SET cont_extra_trip_charge=500,cont_extra_km_charge_per_km=100 WHERE cont_id_pk=9')
        response=self.prefill(ar_invoice_date='2026-10-31',ar_billing_period_start='2026-10-01',ar_billing_period_end='2026-10-31')
        self.assertEqual(response.status_code,200,response.text)
        line=response.json()['data']['suggested_line']
        self.assertEqual(line['ar_line_service_period_end'],'2026-10-17')
        self.assertEqual(line['ar_line_net_amount'],'2467.74')
        self.assertEqual(line['ar_line_proration_numerator'],'17')
        response=self.prefill(ar_billing_period_start='2026-07-01',ar_billing_period_end='2026-07-31')
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.prefill(ar_billing_period_end='2026-09-30').status_code,400)

    def test_due_date_remains_manual(self):
        self.execute('UPDATE customer_master SET cust_credit_period_days=30 WHERE cust_id_pk=10')
        self.assertIsNone(self.prefill().json()['data']['header_prefill']['ar_due_date'])
        payload=self.contract_payload(); payload.pop('ar_due_date')
        ar_id,_=self.create(payload)
        self.assertIsNone(self.query('SELECT ar_due_date FROM accounts_receivables WHERE ar_id_pk=:id',{'id':ar_id})[0]['ar_due_date'])

    def test_tax_selection_options_effective_dates_and_nonstandard_treatments(self):
        for treatment in ('ZERO_RATED','EXEMPT','OUT_OF_SCOPE'):
            self.execute("INSERT INTO organization_tax_configuration(tax_org_id_fk,tax_code,tax_name,tax_treatment,tax_rate) VALUES(77,:code,:code,:code,0)",{'code':treatment})
            ar_id,_=self.create({**self.header(),'lines':[self.manual_line(ar_line_tax_code=treatment)]})
            line=self.detail(ar_id)['lines'][0]
            self.assertEqual(line['ar_line_tax_treatment'],treatment)
            self.assertEqual(line['ar_line_tax_amount'],'0.00')
        response=self.client.post('/api/v1/accounts-receivables/tax-options',json={'ar_invoice_date':'2026-08-31'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['data']['tax_options']),4)
        self.execute("UPDATE organization_tax_configuration SET effective_from='2026-09-01' WHERE tax_code='STANDARD_VAT'")
        self.assertEqual(self.prefill().status_code,400)
        self.execute("UPDATE organization_tax_configuration SET effective_from=NULL,effective_to='2026-08-30' WHERE tax_code='STANDARD_VAT'")
        self.assertEqual(self.prefill().status_code,400)
        self.execute("UPDATE organization_tax_configuration SET effective_to=NULL,is_active=FALSE WHERE tax_code='STANDARD_VAT'")
        self.assertEqual(self.prefill().status_code,400)

    def test_missing_configuration_fails_before_workflow_or_upload(self):
        self.execute('DELETE FROM organization_invoice_configuration')
        response=self.submit(self.contract_payload())
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(len(self.bucket.records),0)
        self.assertEqual(self.query('SELECT count(*) AS n FROM workflow_instances')[0]['n'],0)
        self.execute('DELETE FROM organization_tax_configuration')
        self.assertEqual(self.prefill().status_code,400)

    def test_tax_and_identity_snapshots_survive_configuration_changes(self):
        payload=self.contract_payload()
        response=self.submit(payload); self.assertEqual(response.status_code,200,response.text)
        pending=response.json()['data']; proposal=self.proposal(pending); old_pdf=self.pdf_bytes(proposal)
        self.execute("UPDATE organization_tax_configuration SET tax_rate=7 WHERE tax_code='STANDARD_VAT'")
        self.execute("UPDATE organization_invoice_configuration SET bank_name='Amended Bank'")
        self.execute("UPDATE customer_master SET cust_name='Amended Customer' WHERE cust_id_pk=10")
        self.execute("UPDATE contracts_management SET cont_contract_name='Amended Contract',cont_big_bus_rate_pm=6000 WHERE cont_id_pk=9")
        result=self.approve(pending); self.assertEqual(result['workflow_status'],'EXECUTED',result)
        ar_id=result['domain_reference_id']; detail=self.detail(ar_id)
        self.assertEqual(detail['ar_invoice_amount'],'2133.87')
        self.assertEqual(detail['ar_invoice_identity_snapshot']['invoice_configuration']['bank_name'],'Emirates Islamic Bank')
        self.assertEqual(self.pdf_bytes(proposal),old_pdf)
        update=self.update_payload(ar_id); update['ar_notes']='Edit without adopting changed master data'
        update['lines'].append(self.manual_line(ar_line_number=2))
        response=self.submit(update,'update'); self.assertEqual(response.status_code,200,response.text)
        result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
        detail=self.detail(ar_id)
        self.assertEqual([Decimal(line['ar_line_tax_rate']) for line in detail['lines']],[Decimal(5),Decimal(7)])
        self.assertEqual(detail['ar_contract_name_snapshot'],'Structured transport contract')
        self.assertEqual(detail['ar_invoice_identity_snapshot']['customer']['cust_name'],proposal['ar_invoice_identity_snapshot']['customer']['cust_name'])

    def test_contract_change_preserves_manual_line_identity_and_amounts(self):
        payload=self.contract_payload(); payload['lines'].append(self.manual_line(ar_line_number=2))
        ar_id,_=self.create(payload); old_manual=self.detail(ar_id)['lines'][1]
        self.execute('UPDATE contracts_management SET cont_cust_id_fk=10,cont_big_bus_rate_pm=6000 WHERE cont_id_pk=11')
        suggested=self.prefill(ar_contract_id_fk=11).json()['data']['suggested_line']
        update=self.update_payload(ar_id)
        contract_line_id=update['lines'][0]['ar_line_id_pk']
        update['ar_contract_id_fk']=11
        update['lines'][0]={**self.line_input(suggested),'ar_line_id_pk':contract_line_id}
        response=self.submit(update,'update'); self.assertEqual(response.status_code,200,response.text)
        result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
        detail=self.detail(ar_id)
        self.assertEqual(detail['ar_contract_id_fk'],11)
        new_manual=detail['lines'][1]
        for key in ('ar_line_id_pk','ar_line_net_amount','ar_line_tax_amount','ar_line_total_amount','ar_line_source_type'):
            self.assertEqual(old_manual[key],new_manual[key])

    def test_invalid_lines_totals_and_pointer_inputs(self):
        valid={**self.header(),'lines':[self.manual_line()]}
        cases=[({'lines':[]},400),({'lines':None},400),({'ar_invoice_amount':'999'},400),({'ar_tax_amount':'999'},400),
               ({'ar_invoice_file_path':'gs://forged/invoice.pdf'},400),({'ar_subtotal_amount':'999'},422),
               ({'ar_invoice_identity_snapshot':{}},422),({'ar_due_date':'2026-01-01'},400)]
        for change,status in cases:
            with self.subTest(change=change):
                response=self.submit({**valid,**change}); self.assertEqual(response.status_code,status,response.text)
        for change,status in [({'ar_line_quantity':'-1'},422),({'ar_line_unit_rate':'-1'},422),
                              ({'ar_line_tax_rate':'-1'},422),({'ar_line_tax_rate':'25'},400),
                              ({'ar_line_source_type':'CONTRACT_AUTOFILL'},422),({'ar_line_tax_amount':'123'},422),
                              ({'ar_org_id_fk':88},422),({'ar_line_proration_method':'WORKING_DAYS'},422),
                              ({'ar_line_service_period_start':'2026-09-01','ar_line_service_period_end':'2026-08-01'},400),
                              ({'ar_line_tax_code':'UNKNOWN'},400),({'ar_line_id_pk':90000},409)]:
            with self.subTest(change=change):
                response=self.submit({**valid,'lines':[self.manual_line(**change)]}); self.assertEqual(response.status_code,status,response.text)
        duplicate={**valid,'lines':[self.manual_line(ar_line_number=1),self.manual_line(ar_line_number=1)]}
        self.assertEqual(self.submit(duplicate).status_code,409)
        self.assertEqual(len(self.bucket.records),0)

    def test_foreign_line_ids_and_header_only_edit_are_rejected(self):
        ar_id,_=self.create(); other_id,_=self.create()
        update=self.update_payload(ar_id); update['lines'][0]['ar_line_id_pk']=self.detail(other_id)['lines'][0]['ar_line_id_pk']
        self.assertEqual(self.submit(update,'update').status_code,409)
        update.pop('lines'); update['ar_invoice_amount']='2133.87'; update.pop('ar_contract_id_fk')
        response=self.submit(update,'update'); self.assertEqual(response.status_code,400,response.text)
        self.assertIn('complete proposed line set',response.json()['error'])

    def test_external_file_conflict_and_canonical_upload_guard(self):
        payload=self.contract_payload()
        response=self.client.post('/api/v1/accounts-receivables/create-with-document',data={'payload':json.dumps(payload)},files={'file':('external.pdf',b'pdf','application/pdf')})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(len(self.bucket.records),0)
        ar_id,_=self.create(payload); original=self.detail(ar_id)['ar_invoice_file_path']
        response=upload.upload_ar_invoice_document_to_firebase({'ar_id_pk':ar_id,'ar_org_id_fk':77},BytesIO(b'evil'),'external.pdf','application/pdf')
        self.assertIn('error',response)
        self.assertEqual(self.detail(ar_id)['ar_invoice_file_path'],original)
        update=self.update_payload(ar_id)
        response=self.client.post('/api/v1/accounts-receivables/update-with-document',data={'payload':json.dumps(update)},files={'file':('external.pdf',b'pdf','application/pdf')})
        self.assertEqual(response.status_code,400,response.text)

    def test_pending_submission_replay_cleans_unused_generated_document(self):
        payload=self.contract_payload()
        first=self.submit(payload); second=self.submit(payload)
        self.assertEqual(first.status_code,200,first.text); self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(first.json()['data']['workflow_instance_id'],second.json()['data']['workflow_instance_id'])
        self.assertEqual(len(self.bucket.records),1)
        self.assertEqual(self.query('SELECT count(*) AS n FROM workflow_instances')[0]['n'],1)

    def test_failed_create_rolls_back_header_and_lines_and_cleans_document(self):
        response=self.submit(); self.assertEqual(response.status_code,200,response.text)
        pending=response.json()['data']
        def fail_line(conn,cursor,statement,parameters,context,executemany):
            if statement.startswith('INSERT INTO accounts_receivable_lines'):
                raise RuntimeError('Injected failure after header insert')
        event.listen(self.engine,'before_cursor_execute',fail_line)
        try: result=self.approve(pending)
        finally: event.remove(self.engine,'before_cursor_execute',fail_line)
        self.assertEqual(result['workflow_status'],'EXECUTION_FAILED',result)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivables')[0]['n'],0)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n'],0)
        self.assertEqual(len(self.bucket.records),0)

    def test_failed_update_rolls_back_header_line_deletions_and_pointer(self):
        ar_id,_=self.create({**self.header(),'lines':[self.manual_line(),self.manual_line(ar_line_number=2)]})
        before=self.detail(ar_id); update=self.update_payload(ar_id)
        update['lines']=[update['lines'][0]]; update['lines'][0]['ar_line_quantity']='3'
        response=self.submit(update,'update'); self.assertEqual(response.status_code,200,response.text)
        def fail_line(conn,cursor,statement,parameters,context,executemany):
            if statement.startswith('UPDATE accounts_receivable_lines'):
                raise RuntimeError('Injected failure after header update and deletion')
        event.listen(self.engine,'before_cursor_execute',fail_line)
        try: result=self.approve(response.json()['data'])
        finally: event.remove(self.engine,'before_cursor_execute',fail_line)
        self.assertEqual(result['workflow_status'],'EXECUTION_FAILED',result)
        self.assertEqual(self.detail(ar_id),before)
        self.assertEqual(len(self.bucket.records),1)

    def test_stale_proposal_cannot_overwrite_invoice(self):
        ar_id,_=self.create(); update=self.update_payload(ar_id)
        response=self.submit(update,'update'); self.assertEqual(response.status_code,200,response.text)
        self.execute('UPDATE accounts_receivables SET ar_revision=ar_revision+1 WHERE ar_id_pk=:id',{'id':ar_id})
        result=self.approve(response.json()['data'])
        self.assertEqual(result['workflow_status'],'EXECUTION_FAILED',result)
        self.assertEqual(self.detail(ar_id)['ar_invoice_amount'],'2133.87')
        self.assertEqual(self.submit(update,'update').status_code,400)

    def test_delete_pending_rejected_and_approved_cascade(self):
        ar_id,_=self.create(); other,_=self.create()
        payload={'user_principal_name':'requester@example.invalid','ar_id_pk':ar_id}
        response=self.client.post('/api/v1/accounts-receivables/delete',json=payload)
        self.assertEqual(response.status_code,200,response.text); pending=response.json()['data']
        self.assertEqual(self.detail(ar_id)['line_count'],1)
        result=runtime.reject_workflow_step(workflow_code='ACCOUNTS_RECEIVABLE',
            payload={'workflow_instance_id':pending['workflow_instance_id'],'workflow_instance_step_id':pending['workflow_instance_step_id'],
                     'acting_user_principal_name':'reviewer@example.invalid'}, legacy_table_name='accounts_receivables_workflow_requests',
            acting_user_principal_name='reviewer@example.invalid',organization_id=77)
        self.assertEqual(result['workflow_status'],'REJECTED',result)
        self.assertEqual(self.detail(ar_id)['line_count'],1)
        response=self.client.post('/api/v1/accounts-receivables/delete',json=payload)
        result=self.approve(response.json()['data']); self.assertEqual(result['workflow_status'],'EXECUTED',result)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines WHERE ar_line_ar_id_fk=:id',{'id':ar_id})[0]['n'],0)
        self.assertEqual(self.detail(other)['line_count'],1)

    def test_contract_a_pending_approval_execution_pdf_and_retry(self):
        payload = self.contract_payload()
        response = self.submit(payload)
        self.assertEqual(response.status_code, 200, response.text)
        pending = response.json()['data']
        self.assertFalse(pending['business_operation_executed'])
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivables')[0]['n'], 0)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n'], 0)
        proposal = self.proposal(pending)
        self.assertEqual(proposal['lines'][0]['ar_line_proration_numerator'], '14')
        self.assertEqual(proposal['lines'][0]['ar_line_proration_denominator'], '31')
        pdf = self.pdf_bytes(proposal)
        text_value = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        for value in ['TAX INVOICE','Saraj Al Jamal','2,032.26','101.61','2,133.87','4,500.00','14/31','66-seater',
                      'Emirates Islamic Bank','AE810340003708506470001','104382694800003','1349832',payload['ar_invoice_number']]:
            self.assertIn(value, text_value)
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), proposal['workflow_document_attachment']['sha256'])
        approved = self.approve(pending)
        self.assertEqual(approved['workflow_status'], 'EXECUTED', approved)
        ar_id = approved['domain_reference_id']
        detail = self.detail(ar_id)
        self.assertEqual([detail[k] for k in ('ar_subtotal_amount','ar_tax_amount','ar_invoice_amount','ar_balance_amount')], ['2032.26','101.61','2133.87','2133.87'])
        self.assertEqual(detail['ar_invoice_file_path'], proposal['ar_invoice_file_path'])
        self.assertFalse(detail['legacy_header_only'])
        self.assertEqual(detail['ar_invoice_number'], payload['ar_invoice_number'])
        self.assertEqual(detail['ar_due_date'], '2026-09-07')
        repeated = self.approve(pending)
        self.assertEqual(repeated['workflow_status'], 'EXECUTED', repeated)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n'], 1)
        if os.getenv('AR_REDESIGN_ARTIFACT_DIR'):
            root = Path(os.environ['AR_REDESIGN_ARTIFACT_DIR']); root.mkdir(parents=True, exist_ok=True)
            (root/'contract_a_generated_invoice.pdf').write_bytes(pdf)
            (root/'contract_a_evidence.json').write_text(json.dumps({'request':payload,'pending':pending,'approved':approved,
                'detail':detail,'pdf_sha256':hashlib.sha256(pdf).hexdigest(),'pdf_text':text_value},indent=2,default=str)+'\n')

    def test_manual_create_and_line_update_add_remove_reorder(self):
        payload = {**self.header(), 'lines':[self.manual_line(), self.manual_line(ar_line_description='Second line',ar_line_quantity='1')]}
        ar_id, pending = self.create(payload)
        old = self.detail(ar_id)
        update = self.update_payload(ar_id)
        update['lines'][0]['ar_line_quantity'] = '3'
        update['lines'][0]['ar_line_number'] = 2
        update['lines'][1]['ar_line_number'] = 1
        response = self.submit(update, 'update')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.detail(ar_id), old)
        result = self.approve(response.json()['data'])
        self.assertEqual(result['workflow_status'], 'EXECUTED', result)
        detail = self.detail(ar_id)
        self.assertEqual({line['ar_line_id_pk'] for line in detail['lines']}, {line['ar_line_id_pk'] for line in old['lines']})
        update = self.update_payload(ar_id)
        update['lines'] = [update['lines'][0], self.manual_line(ar_line_number=3,ar_line_description='New line')]
        response = self.submit(update,'update')
        self.assertEqual(response.status_code,200,response.text)
        result = self.approve(response.json()['data'])
        self.assertEqual(result['workflow_status'],'EXECUTED',result)
        self.assertEqual([line['ar_line_description'] for line in self.detail(ar_id)['lines']], ['Second line','New line'])

    def test_legacy_create_update_read_and_document_download(self):
        legacy = {**self.header(),'ar_invoice_amount':'20000.00','ar_tax_amount':'1000.00'}
        ar_id, pending = self.create(legacy)
        detail = self.detail(ar_id)
        self.assertTrue(detail['legacy_header_only'])
        self.assertEqual(detail['lines'],[])
        self.assertIsNone(detail['ar_subtotal_amount'])
        self.assertEqual(detail['ar_invoice_amount'],20000)
        self.assertEqual(detail['ar_balance_amount'],20000)
        legacy.update(ar_id_pk=ar_id,ar_notes='Legacy edit')
        response = self.submit(legacy,'update')
        self.assertEqual(response.status_code,200,response.text)
        result = self.approve(response.json()['data'])
        self.assertEqual(result['workflow_status'],'EXECUTED',result)
        uploaded = upload.upload_ar_invoice_document_to_firebase({'ar_id_pk':ar_id,'ar_org_id_fk':77},BytesIO(b'legacy-invoice'),'legacy.pdf','application/pdf')
        self.assertNotIn('error',uploaded)
        response = download.download_ar_invoice_document({'ar_id_pk':ar_id,'ar_org_id_fk':77})
        self.assertNotIn('error',response)
        self.assertEqual(self.committed_download_bytes(response['download_url']),b'legacy-invoice')
