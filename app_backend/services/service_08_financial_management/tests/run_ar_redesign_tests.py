"""Run the accepted 61-test baseline separately from new AR tests; emit evidence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import unittest


BASELINE = [
    'test_workflow_safe_document_api',
    'test_firebase_current_pointer_corrections',
    'test_workflow_transaction_pending_responses',
    'test_workflow_document_cleanup',
    'test_workflow_attachment_download',
]


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())


def main():
    if not os.getenv('AR_REDESIGN_TEST_DATABASE_URL'):
        raise SystemExit('Set AR_REDESIGN_TEST_DATABASE_URL to a disposable loopback *_tests database; skipping database checks is not acceptance.')
    # Override before importing application modules. Never fall back to the repository .env.
    os.environ['RAILWAY_DB_URL'] = 'postgresql+psycopg2://invalid:invalid@127.0.0.1:1/no_live_db'
    root = Path(os.environ.get('AR_REDESIGN_ARTIFACT_DIR', '/tmp/ue-ar-redesign-artifacts'))
    root.mkdir(parents=True, exist_ok=True)
    os.environ['AR_REDESIGN_ARTIFACT_DIR'] = str(root)
    report = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'database': 'Disposable loopback PostgreSQL; random per-suite database created and dropped',
              'cloud': 'Versioned fake bucket; installed GCS SDK signs URLs using ephemeral offline credentials',
              'production_migration_run': False, 'suites': {}}
    suites = {
        'accepted_preimplementation_baseline': ['app_backend.services.service_07_alerts_wf_engine.tests.' + name for name in BASELINE],
        'new_ar_redesign': ['app_backend.services.service_08_financial_management.tests.test_accounts_receivables_redesign'],
        'closure_approval_metadata_and_document_contract': ['app_backend.services.service_08_financial_management.tests.test_accounts_receivables_closure'],
    }
    success = True
    for label, names in suites.items():
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        start = time.monotonic()
        result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
        report['suites'][label] = {
            'tests_run': result.testsRun, 'passed_count': len(result.passed), 'passed': result.passed,
            'failures': [{'test': test.id(), 'traceback': error} for test, error in result.failures],
            'errors': [{'test': test.id(), 'traceback': error} for test, error in result.errors],
            'skipped': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
            'elapsed_seconds': round(time.monotonic()-start, 3),
        }
        success = success and result.wasSuccessful() and not result.skipped
    report['success'] = success
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    (root/'test_results.json').write_text(json.dumps(report, indent=2)+'\n')
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
