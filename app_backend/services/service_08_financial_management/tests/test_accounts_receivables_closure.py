"""Narrow closure checks: approval ownership and existing-document primary keys."""
from datetime import datetime
import json
import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app_backend.services.service_08_financial_management.tests.ar_postgres_fixture import (
    ARPostgresCase, api, consolidated, workflow,
)


class ARClosurePostgresTests(ARPostgresCase):
    def record_evidence(self, section, value):
        if os.getenv('AR_REDESIGN_ARTIFACT_DIR'):
            path = Path(os.environ['AR_REDESIGN_ARTIFACT_DIR']) / 'closure_api_evidence.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            evidence = json.loads(path.read_text()) if path.exists() else {'fixture_only': True}
            evidence[section] = value
            path.write_text(json.dumps(evidence, indent=2, default=str) + '\n')

    def approve_with_database_time(self, pending):
        times = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith(('INSERT INTO accounts_receivables (', 'UPDATE accounts_receivables SET')):
                times.append(conn.execute(text('SELECT CURRENT_TIMESTAMP::timestamp')).scalar_one())

        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            result = self.approve(pending)
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(result['workflow_status'], 'EXECUTED', result)
        self.assertEqual(len(times), 1)
        row = self.query('SELECT * FROM accounts_receivables WHERE ar_id_pk=:id', {'id': result['domain_reference_id']})[0]
        self.assertEqual(row['ar_approved_at'], times[0])
        self.assertEqual(row['ar_approved_by'], 'reviewer@example.invalid')
        self.assertEqual(row['ar_approval_status'], 'APPROVED')
        return row, times[0]

    def test_create_approval_metadata_is_server_owned_through_both_apps(self):
        evidence = []
        for name, app in [('service_08', api.app), ('consolidated', consolidated.app)]:
            for multipart in (False, True):
                with self.subTest(app=name, multipart=multipart), TestClient(app) as client:
                    forged = '2099-01-01T00:00:00' if multipart else '2001-01-01T00:00:00'
                    payload = {**self.contract_payload(), 'ar_approved_at': forged,
                               'ar_approved_by': 'forged@example.invalid', 'ar_approval_status': 'APPROVED'}
                    count_before = self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n']
                    path = '/api/v1/accounts-receivables/create' + ('-with-document' if multipart else '')
                    response = client.post(path, data={'payload': json.dumps(payload)}) if multipart else client.post(path, json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                    pending = response.json()['data']
                    self.assertFalse(pending['business_operation_executed'])
                    self.assertEqual(pending['workflow_status'], 'PENDING_APPROVAL')
                    proposal = self.proposal(pending)
                    for field in ('ar_approval_status', 'ar_approved_by', 'ar_approved_at'):
                        self.assertNotIn(field, proposal)
                    self.assertEqual(self.query('SELECT ar_approved_at FROM accounts_receivables WHERE ar_invoice_number=:number',
                                                {'number': payload['ar_invoice_number']}), [])
                    self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n'], count_before)
                    earliest = self.query('SELECT clock_timestamp()::timestamp AS now')[0]['now']
                    row, database_time = self.approve_with_database_time(pending)
                    latest = self.query('SELECT clock_timestamp()::timestamp AS now')[0]['now']
                    self.assertLessEqual(earliest, row['ar_approved_at'])
                    self.assertLessEqual(row['ar_approved_at'], latest)
                    self.assertNotEqual(row['ar_approved_at'], datetime.fromisoformat(forged))
                    evidence.append({'app': name, 'multipart': multipart, 'requester_timestamp': forged,
                        'approval_fields_absent_from_pending_proposal': True, 'pending_business_records': 0,
                        'persisted_approved_by': row['ar_approved_by'], 'persisted_approval_status': row['ar_approval_status'],
                        'persisted_approved_at': row['ar_approved_at'], 'execution_database_timestamp': database_time})
        self.record_evidence('create_approval_metadata', evidence)

    def test_update_approval_metadata_is_server_owned_and_pending_keeps_current_timestamp(self):
        evidence = []
        for name, app in [('service_08', api.app), ('consolidated', consolidated.app)]:
            for multipart in (False, True):
                with self.subTest(app=name, multipart=multipart), TestClient(app) as client:
                    ar_id, _ = self.create()
                    before = self.detail(ar_id)
                    payload = self.update_payload(ar_id)
                    payload.update(ar_approved_at='2099-01-01T00:00:00', ar_approved_by='forged@example.invalid',
                                   ar_approval_status='REJECTED', ar_notes='Closure metadata check')
                    path = '/api/v1/accounts-receivables/update' + ('-with-document' if multipart else '')
                    response = client.post(path, data={'payload': json.dumps(payload)}) if multipart else client.post(path, json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                    pending = response.json()['data']
                    self.assertFalse(pending['business_operation_executed'])
                    self.assertEqual(self.detail(ar_id), before)
                    proposal = self.proposal(pending)
                    for field in ('ar_approval_status', 'ar_approved_by', 'ar_approved_at'):
                        self.assertNotIn(field, proposal)
                    row, database_time = self.approve_with_database_time(pending)
                    self.assertGreater(row['ar_approved_at'], datetime.fromisoformat(before['ar_approved_at']))
                    after = self.detail(ar_id)
                    self.assertEqual(self.approve(pending)['workflow_status'], 'EXECUTED')
                    self.assertEqual(self.detail(ar_id), after)
                    evidence.append({'app': name, 'multipart': multipart, 'pending_kept_current_record': True,
                        'previous_approved_at': before['ar_approved_at'], 'persisted_approved_at': row['ar_approved_at'],
                        'execution_database_timestamp': database_time, 'persisted_approved_by': row['ar_approved_by'],
                        'persisted_approval_status': row['ar_approval_status'], 'retry_kept_execution_timestamp': True})
        self.record_evidence('update_approval_metadata', evidence)

    def test_preclosure_pending_requester_timestamp_is_ignored_at_execution(self):
        pending = self.submit().json()['data']
        # Represent an already-pending pre-correction proposal, without changing its document.
        old_metadata = {'ar_approved_at': '2099-01-01T00:00:00', 'ar_approved_by': 'forged@example.invalid',
                        'ar_approval_status': 'APPROVED'}
        self.execute('UPDATE workflow_instances SET request_payload=request_payload || CAST(:metadata AS jsonb) WHERE workflow_instance_id_pk=:id',
                     {'metadata': json.dumps(old_metadata), 'id': pending['workflow_instance_id']})
        row, database_time = self.approve_with_database_time(pending)
        self.assertNotEqual(row['ar_approved_at'], datetime.fromisoformat(old_metadata['ar_approved_at']))
        self.record_evidence('preclosure_pending_proposal', {'requester_timestamp': old_metadata['ar_approved_at'],
            'persisted_approved_at': row['ar_approved_at'], 'execution_database_timestamp': database_time,
            'persisted_approved_by': row['ar_approved_by']})

    def test_missing_trusted_final_approver_fails_without_business_write(self):
        pending = self.submit().json()['data']
        with patch.object(workflow, '_inject_final_ar_approval_metadata', side_effect=lambda payload, *args: payload):
            result = self.approve(pending)
        self.assertEqual(result['workflow_status'], 'EXECUTION_FAILED', result)
        self.assertIn('final workflow approver is required', json.dumps(result))
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivables')[0]['n'], 0)
        self.assertEqual(self.query('SELECT count(*) AS n FROM accounts_receivable_lines')[0]['n'], 0)

    def test_legacy_header_timestamp_compatibility_is_unchanged(self):
        evidence = []
        for name, app in [('service_08', api.app), ('consolidated', consolidated.app)]:
            with self.subTest(app=name), TestClient(app) as client:
                payload = {**self.header(), 'ar_invoice_amount': '20000.00', 'ar_tax_amount': '1000.00',
                           'ar_approved_at': '2001-01-01T00:00:00'}
                response = client.post('/api/v1/accounts-receivables/create', json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                approved = self.approve(response.json()['data'])
                self.assertEqual(approved['workflow_status'], 'EXECUTED', approved)
                ar_id = approved['domain_reference_id']
                row = self.detail(ar_id)
                self.assertTrue(row['legacy_header_only'])
                self.assertEqual(row['ar_approved_at'], '2001-01-01 00:00:00')
                payload.update(ar_id_pk=ar_id, ar_approved_at='2002-01-01T00:00:00')
                response = client.post('/api/v1/accounts-receivables/update', json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.approve(response.json()['data'])['workflow_status'], 'EXECUTED')
                row = self.detail(ar_id)
                self.assertEqual(row['ar_approved_at'], '2002-01-01 00:00:00')
                self.assertEqual(row['ar_approved_by'], 'reviewer@example.invalid')
                evidence.append({'app': name, 'legacy_create_timestamp': '2001-01-01 00:00:00',
                                 'legacy_update_timestamp': row['ar_approved_at'], 'compatibility_preserved': True})
        self.record_evidence('legacy_approval_metadata', evidence)

    def test_existing_document_routes_require_primary_key_through_both_apps(self):
        evidence = []
        for name, app in [('service_08', api.app), ('consolidated', consolidated.app)]:
            with self.subTest(app=name), TestClient(app) as client:
                payload = {**self.header(), 'ar_invoice_amount': '20000', 'ar_tax_amount': '1000'}
                ar_id, _ = self.create(payload)
                number_only = {'ar_invoice_number': payload['ar_invoice_number']}
                response = client.post('/api/v1/accounts-receivables/documents/download', json=number_only)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('ar_id is required', response.json()['error'])
                response = client.post('/api/v1/accounts-receivables/documents/upload',
                    data={**number_only, 'user_principal_name': 'requester@example.invalid'},
                    files={'file': ('legacy.pdf', b'legacy fixture', 'application/pdf')})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('ar_id is required', response.json()['error'])
                for alias in ('ar_id', 'ar_id_pk'):
                    uploaded = client.post('/api/v1/accounts-receivables/documents/upload',
                        data={alias: str(ar_id), 'user_principal_name': 'requester@example.invalid'},
                        files={'file': ('legacy.pdf', b'legacy fixture', 'application/pdf')})
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)
                    downloaded = client.post('/api/v1/accounts-receivables/documents/download', json={alias: ar_id})
                    self.assertEqual(downloaded.status_code, 200, downloaded.text)
                    self.assertEqual(self.committed_download_bytes(downloaded.json()['data']['download_url']), b'legacy fixture')
                evidence.append({'app': name, 'invoice_number_only_upload_status': 400,
                    'invoice_number_only_download_status': 400, 'ar_id_upload_and_download_status': 200,
                    'ar_id_pk_upload_and_download_status': 200})
        self.record_evidence('document_primary_key', evidence)
