"""Task 164: real staging helpers and offline GCS signing with versioned fake storage."""
import copy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import unittest
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound
from google.cloud import storage as gcs
from google.oauth2 import service_account

from app_backend.services.main import app
from app_backend.services.service_07_alerts_wf_engine.api.main import app as workflow_app
from app_backend.services.firebase_file_pointer_helpers import WORKFLOW_DOCUMENT_METADATA_FIELDS, strip_untrusted_file_pointer_fields
from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data as contract_create
from app_backend.services.service_08_financial_management.logic import accounts_payables_create_data as ap_create
from app_backend.services.service_08_financial_management.logic import accounts_receivables_create_data as ar_create
from app_backend.services.service_06_contracts_management.integrations import firebase_contract_document_upload as contract_upload
from app_backend.services.service_08_financial_management.integrations import firebase_ap_invoice_document_upload as ap_upload
from app_backend.services.service_08_financial_management.integrations import firebase_ar_invoice_document_upload as ar_upload
from app_backend.services.service_07_alerts_wf_engine import workflow_document_download as download
from app_backend.services.service_07_alerts_wf_engine.api import workflow_runtime_api as runtime_api, workflow_admin_api as admin_api
from app_backend.services.service_07_alerts_wf_engine.logic import workflow_attachment_logic as attachment_logic

OLD_BYTES = b'%PDF-1.4\nOLD committed invoice: AED 100\n%%EOF\n'
NEW_BYTES = b'%PDF-1.4\nNEW proposed invoice: AED 250\n%%EOF\n'
CASES = {
    'contract': (contract_create.stage_contract_document_for_workflow, contract_upload, 'CONTRACTS_MANAGEMENT',
                 'cont_link_path', 'cont_org_id_fk', {'cont_contract_number': 'CT-164'}, 'require_contract'),
    'ap': (ap_create.stage_ap_invoice_document_for_workflow, ap_upload, 'ACCOUNTS_PAYABLE',
           'ap_invoice_file_path', 'ap_org_id_fk', {'ap_invoice_number': 'AP-164'}, 'require_ap_invoice'),
    'ar': (ar_create.stage_ar_invoice_document_for_workflow, ar_upload, 'ACCOUNTS_RECEIVABLE',
           'ar_invoice_file_path', 'ar_org_id_fk', {'ar_invoice_number': 'AR-164'}, 'require_ar_invoice'),
}


def context(org=77, principal='reviewer@example.invalid'):
    return {'authenticated': True, 'user': {'user_id': 1, 'user_principal_name': principal,
            'user_org_id_fk': org}, 'organization': {'org_id': org}}


class VersionedBucket:
    """Models immutable generations; URLs are signed by the installed GCS SDK."""
    def __init__(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        credentials = service_account.Credentials.from_service_account_info({
            'type': 'service_account', 'project_id': 'task164-test',
            'private_key': pem, 'client_email': 'offline@test.invalid',
            'token_uri': 'https://oauth2.googleapis.com/token',
        })
        self.sdk_bucket = gcs.Client(project='task164-test', credentials=credentials).bucket('task164-test-bucket')
        self.records = {}
        self.counter = 1000
        self.signatures = []

    def blob(self, path, generation=None):
        return VersionedBlob(self, path, generation)

    def fetch(self, url, now=None):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        created = datetime.strptime(query['X-Goog-Date'][0], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        if now and (now-created).total_seconds() > int(query['X-Goog-Expires'][0]):
            raise PermissionError('Expired capability')
        from urllib.parse import unquote
        path = unquote(parsed.path).split('/', 2)[2]
        return self.records[(path, int(query['generation'][0]))]['bytes']


class VersionedBlob:
    def __init__(self, bucket, path, generation=None):
        self.bucket = bucket
        self.path = path
        self.generation = generation
        self.metadata = {}
        self.size = None
        self.content_type = None

    def upload_from_file(self, stream, content_type=None, rewind=False, if_generation_match=None):
        if if_generation_match == 0 and any(path == self.path for path, _ in self.bucket.records):
            raise ValueError('Object already exists')
        if rewind:
            stream.seek(0)
        content = stream.read()
        self.bucket.counter += 1
        self.generation = self.bucket.counter
        self.size = len(content)
        self.content_type = content_type
        self.bucket.records[(self.path, self.generation)] = {
            'bytes': content, 'metadata': dict(self.metadata), 'content_type': content_type,
        }

    def reload(self, if_generation_match=None):
        if self.generation is None:
            generations = [g for path, g in self.bucket.records if path == self.path]
            if not generations:
                raise NotFound('Missing')
            self.generation = max(generations)
        row = self.bucket.records.get((self.path, int(self.generation)))
        if row is None:
            raise NotFound('Missing generation')
        if if_generation_match is not None and int(self.generation) != int(if_generation_match):
            raise ValueError('Generation mismatch')
        self.metadata = dict(row['metadata'])
        self.size = len(row['bytes'])
        self.content_type = row['content_type']

    def exists(self):
        return any(path == self.path for path, _ in self.bucket.records)

    def delete(self, if_generation_match=None):
        for key in list(self.bucket.records):
            if key[0] == self.path and (self.generation is None or key[1] == int(self.generation)):
                del self.bucket.records[key]

    def generate_signed_url(self, **kwargs):
        self.bucket.signatures.append(kwargs)
        return self.bucket.sdk_bucket.blob(self.path, generation=self.generation).generate_signed_url(**kwargs)


def stage_case(test, bucket, domain, action='CREATE', filename='NEW_FILE.pdf', data=NEW_BYTES):
    stage, module, code, pointer, org, fields, require = CASES[domain]
    payload = {**fields, org: 77, 'authenticated_org_id': 77, 'service_request_id': 'REQ-164',
               'workflow_document_attachment': {'attachment_id': 'FORGED'},
               'workflow_document_staged_bucket': 'attacker', 'workflow_document_staged_blob_path': 'attacker/file'}
    if action == 'UPDATE':
        payload[{'contract': 'cont_id', 'ap': 'ap_id', 'ar': 'ar_id'}[domain]] = 9
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, 'initialize_firebase_app'))
        stack.enter_context(patch.object(module, 'FIREBASE_STORAGE_BUCKET', 'task164-test-bucket'))
        stack.enter_context(patch.object(module.storage, 'bucket', return_value=bucket))
        if action == 'UPDATE':
            row = {**fields, pointer: 'old/OLD_FILE.pdf', {'contract':'cont_id_pk','ap':'ap_id_pk','ar':'ar_id_pk'}[domain]:9}
            if domain == 'contract':
                engine = MagicMock()
                engine.begin.return_value.__enter__.return_value.execute.return_value.mappings.return_value.one_or_none.return_value = row
                stack.enter_context(patch.object(module, 'db_engine', return_value=engine))
            else:
                stack.enter_context(patch.object(module, f'get_{domain}_by_id', return_value=row))
        staged, error = stage(payload, BytesIO(data), filename, 'application/pdf',
                              **{require: action == 'UPDATE', 'workflow_action': action})
    test.assertIsNone(error)
    return {'workflow_instance_id': 123, 'workflow_code': code, 'organization_id': 77,
            'workflow_action': action, 'workflow_status': 'PENDING_APPROVAL', 'request_payload': staged}


class WorkflowAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.bucket = VersionedBucket()
        for target, value in [('FIREBASE_STORAGE_BUCKET', 'task164-test-bucket'),
                              ('initialize_firebase_app', lambda: None)]:
            p = patch.object(download, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(download.storage, 'bucket', return_value=self.bucket)
        p.start()
        self.addCleanup(p.stop)
        self.instance = stage_case(self, self.bucket, 'ap')

    def retrieve(self, instance=None, attachment=None, version=None):
        instance = instance or self.instance
        metadata = instance['request_payload'].get('workflow_document_attachment') or {}
        return download.download_workflow_document(instance, attachment or metadata.get('attachment_id'),
                                                    version or metadata.get('version_id'))

    def assert_error(self, code, instance=None, **kwargs):
        with self.assertRaises(HTTPException) as exc:
            self.retrieve(instance, **kwargs)
        self.assertEqual(exc.exception.detail, code)
        return exc.exception.status_code

    def test_wrong_attachment(self):
        self.assertEqual(self.assert_error('WORKFLOW_ATTACHMENT_NOT_FOUND', attachment='wrong'),404)

    def test_wrong_version(self):
        self.assertEqual(self.assert_error('WORKFLOW_ATTACHMENT_VERSION_NOT_FOUND', version='wrong'),404)

    def test_wrong_workflow_attachment(self):
        other=stage_case(self,self.bucket,'ar')
        self.assert_error('WORKFLOW_ATTACHMENT_NOT_FOUND', other,
                          attachment=self.instance['request_payload']['workflow_document_attachment']['attachment_id'])

    def test_terminal_states(self):
        for status in ('EXECUTED','REJECTED','EXECUTION_FAILED','CANCELLED','ERROR'):
            with self.subTest(status=status):
                self.assert_error('WORKFLOW_DOCUMENT_NOT_PENDING',{**self.instance,'workflow_status':status})

    def test_unsupported_domain_and_action(self):
        for changed in ({'workflow_code':'PAYROLL'},{'workflow_action':'DELETE'}):
            self.assert_error('WORKFLOW_DOCUMENT_UNSUPPORTED',{**self.instance,**changed})

    def test_missing_object(self):
        self.bucket.records.clear()
        self.assertEqual(self.assert_error('WORKFLOW_DOCUMENT_UNAVAILABLE'),410)

    def test_forged_provenance(self):
        for field,value in [('workflow_document_staged_bucket','other'),
                            ('workflow_document_staged_blob_path','old/OLD_FILE.pdf'),
                            ('ap_invoice_file_path','gs://other/stolen.pdf'),
                            ('ap_org_id_fk',2),('authenticated_org_id',2),
                            ('workflow_document_staging_status','PROMOTED'),
                            ('workflow_document_cleanup_policy','KEEP')]:
            with self.subTest(field=field):
                instance=copy.deepcopy(self.instance)
                instance['request_payload'][field]=value
                self.assert_error('WORKFLOW_DOCUMENT_INTEGRITY_FAILED',instance)

    def test_forged_integrity_metadata(self):
        for field,value in [('size_bytes',0),('sha256','0'*64),('storage_generation','0'),
                            ('document_role','CONTRACT_DOCUMENT'),('file_name','old.pdf'),
                            ('content_type','text/plain')]:
            with self.subTest(field=field):
                instance=copy.deepcopy(self.instance)
                instance['request_payload']['workflow_document_attachment'][field]=value
                self.assert_error('WORKFLOW_DOCUMENT_INTEGRITY_FAILED',instance)

    def test_gcs_metadata_tampering(self):
        for key in ('workflow_sha256','workflow_attachment_id','workflow_organization_id','workflow_version_id'):
            row=next(iter(self.bucket.records.values()))
            original=row['metadata'][key]
            row['metadata'][key]='forged'
            self.assert_error('WORKFLOW_DOCUMENT_INTEGRITY_FAILED')
            row['metadata'][key]=original

    def test_legacy_requests_fail_closed_with_stable_discovery(self):
        instance=copy.deepcopy(self.instance)
        del instance['request_payload']['workflow_document_attachment']
        first=attachment_logic.attachment_summaries(instance)
        self.assertEqual(first,attachment_logic.attachment_summaries(instance))
        self.assertEqual(first[0]['availability'],'UNVERIFIED_LEGACY')
        self.assert_error('WORKFLOW_DOCUMENT_LEGACY_UNVERIFIED',instance,
                          attachment=first[0]['attachment_id'],version=first[0]['version_id'])

    def test_no_attachment(self):
        instance={**self.instance,'request_payload':{}}
        self.assertEqual(attachment_logic.attachment_summaries(instance),[])
        self.assert_error('WORKFLOW_ATTACHMENT_NOT_FOUND',instance,attachment='missing',version='missing')

    def test_safe_discovery_no_storage_references(self):
        result=attachment_logic.public_instance(self.instance)
        serialized=json.dumps(result)
        self.assertNotIn('task164-test-bucket',serialized)
        self.assertNotIn('firebase_upload_files',serialized)
        self.assertNotIn('download_url',serialized)
        self.assertNotIn('storage_generation',serialized)
        self.assertEqual(result['attachments'][0]['sha256'],hashlib.sha256(NEW_BYTES).hexdigest())

    def test_metadata_override_is_ignored_and_stripped(self):
        metadata=self.instance['request_payload']['workflow_document_attachment']
        self.assertNotEqual(metadata['attachment_id'],'FORGED')
        clean=strip_untrusted_file_pointer_fields({key:'forged' for key in WORKFLOW_DOCUMENT_METADATA_FIELDS},'ap_invoice_file_path')
        self.assertEqual(clean,{})

    def test_actual_sdk_signs_generation_and_five_minutes(self):
        from datetime import timedelta
        result=self.retrieve()
        query=parse_qs(urlparse(result['download_url']).query)
        metadata=self.instance['request_payload']['workflow_document_attachment']
        self.assertEqual(query['generation'],[metadata['storage_generation']])
        self.assertEqual(query['X-Goog-Expires'],['300'])
        self.assertTrue(query['X-Goog-Signature'][0])
        self.assertEqual(self.bucket.fetch(result['download_url']),NEW_BYTES)
        with self.assertRaises(PermissionError):
            self.bucket.fetch(result['download_url'],datetime.now(timezone.utc)+timedelta(seconds=301))

    def test_overwritten_name_keeps_original_generation(self):
        path=self.instance['request_payload']['workflow_document_staged_blob_path']
        self.bucket.blob(path).upload_from_file(BytesIO(OLD_BYTES),content_type='application/pdf')
        self.assertEqual(self.bucket.fetch(self.retrieve()['download_url']),NEW_BYTES)

    def test_missing_original_generation_never_returns_latest(self):
        payload=self.instance['request_payload']
        path=payload['workflow_document_staged_blob_path']
        del self.bucket.records[(path,int(payload['workflow_document_attachment']['storage_generation']))]
        self.bucket.blob(path).upload_from_file(BytesIO(OLD_BYTES),content_type='application/pdf')
        self.assert_error('WORKFLOW_DOCUMENT_UNAVAILABLE')

    def test_upload_metadata_failure_cleans_only_uploaded_generation(self):
        from app_backend.services.firebase_file_pointer_helpers import upload_staged_document
        blob=self.bucket.blob('new/upload.pdf')
        with patch.object(blob,'reload',side_effect=RuntimeError('Metadata unavailable')):
            with self.assertRaises(RuntimeError):
                upload_staged_document(blob,BytesIO(NEW_BYTES),'upload.pdf','application/pdf','V1','AP_INVOICE',77)
        self.assertFalse(any(path==blob.path for path,_ in self.bucket.records))

    def test_signing_precondition_failure_is_integrity_error(self):
        from google.api_core.exceptions import PreconditionFailed
        with patch.object(self.bucket,'blob',side_effect=PreconditionFailed('Generation changed')):
            self.assertEqual(self.assert_error('WORKFLOW_DOCUMENT_INTEGRITY_FAILED'),409)

    def test_storage_errors_are_controlled(self):
        with patch.object(self.bucket,'blob',side_effect=RuntimeError('PRIVATE storage path')):
            self.assertEqual(self.assert_error('WORKFLOW_DOCUMENT_STORAGE_UNAVAILABLE'),503)

    def test_http_auth_visibility_download_and_no_store(self):
        previous=dict(workflow_app.dependency_overrides)
        self.addCleanup(lambda: (workflow_app.dependency_overrides.clear(),workflow_app.dependency_overrides.update(previous)))
        with TestClient(app) as client, patch.object(admin_api, 'get_authenticated_user', return_value=context()):
            meta=self.instance['request_payload']['workflow_document_attachment']
            path=f"/api/v1/workflow-engine/instances/123/attachments/{meta['attachment_id']}/download"
            self.assertEqual(client.post(path,json={'version_id':meta['version_id']}).status_code,401)
            client.headers['Authorization']='Bearer task164-test'
            with patch.object(runtime_api,'has_workflow_admin',return_value=False), patch.object(
                runtime_api.workflow_runtime_logic,'get_instance',return_value=self.instance) as lookup:
                response=client.post(path,json={'version_id':meta['version_id']})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.headers['cache-control'],'no-store')
                self.assertEqual(lookup.call_args.kwargs['principal_organization_id'],77)
                self.assertEqual(client.post(path,json={'version_id':meta['version_id'],'bucket':'forged'}).status_code,422)
                self.assertEqual(client.get('/api/v1/workflow-engine/instances/123').json()['data']['attachments'][0]['attachment_id'],meta['attachment_id'])
            with patch.object(runtime_api,'has_workflow_admin',return_value=False), patch.object(
                runtime_api.workflow_runtime_logic,'get_instance',return_value=None):
                self.assertEqual(client.post(path,json={'version_id':meta['version_id']}).status_code,404)


def document_case(domain, action, same_name=False):
    def test(self):
        old=self.bucket.blob('committed/OLD_FILE.pdf')
        old.upload_from_file(BytesIO(OLD_BYTES),content_type='application/pdf')
        filename='SAME_FILE.pdf' if same_name else 'NEW_FILE.pdf'
        instance=stage_case(self,self.bucket,domain,action,filename)
        if same_name:
            old=self.bucket.blob(f'committed/{filename}')
            old.upload_from_file(BytesIO(OLD_BYTES),content_type='application/pdf')
        result=self.retrieve(instance)
        retrieved=self.bucket.fetch(result['download_url'])
        self.assertEqual(hashlib.sha256(retrieved).hexdigest(),hashlib.sha256(NEW_BYTES).hexdigest())
        self.assertNotEqual(hashlib.sha256(retrieved).hexdigest(),hashlib.sha256(OLD_BYTES).hexdigest())
        self.assertEqual(result['sha256'],hashlib.sha256(retrieved).hexdigest())
        self.assertEqual(result['size_bytes'],len(NEW_BYTES))
        self.assertEqual(result['file_name'],filename)
        self.assertEqual(self.bucket.records[(old.path,old.generation)]['bytes'],OLD_BYTES)
    return test

for domain in CASES:
    for action in ('CREATE','UPDATE'):
        setattr(WorkflowAttachmentTests,f'test_{domain}_{action.lower()}_exact_proposed_bytes',document_case(domain,action))
    setattr(WorkflowAttachmentTests,f'test_{domain}_same_filename_different_bytes',document_case(domain,'UPDATE',True))

if __name__=='__main__':
    unittest.main()
