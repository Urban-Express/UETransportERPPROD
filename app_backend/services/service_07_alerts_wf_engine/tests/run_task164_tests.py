"""Run the Task 164 safety, integration and regression suite without production DB access.

Set TASK164_TEST_DATABASE_URL to an explicit loopback PostgreSQL *_tests database
whose user can create databases. Integration tests create/drop their own database.
Set TASK164_TEST_REPORT_PATH and TASK164_EVIDENCE_PATH to capture JSON evidence.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import unittest


WORKFLOW = "app_backend.services.service_07_alerts_wf_engine.tests."
PAYROLL = "app_backend.services.service_02_hr_payroll.logic."
AUTH = "app_backend.services.service_09_user_authentication."
MODULES = [
    WORKFLOW + name for name in (
        "test_workflow_attachment_download", "test_approval_review_postgres",
        "test_workflow_safe_document_api", "test_firebase_current_pointer_corrections",
        "test_workflow_runtime_instances", "test_workflow_document_cleanup",
        "test_workflow_transaction_pending_responses", "test_workflow_shared_compatibility",
        "test_workflow_engine_core", "test_workflow_admin_api", "test_workflow_admin_corrections",
    )
] + [
    PAYROLL + "test_payroll_module_comprehensive",
    PAYROLL + "test_payroll_module_logic",
    AUTH + "api.test_authentication_api",
    AUTH + "logic.test_authentication_me_logic",
]


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        self.passed.append(test.id())
        super().addSuccess(test)


def main():
    if not os.getenv("TASK164_TEST_DATABASE_URL"):
        raise SystemExit("TASK164_TEST_DATABASE_URL is required for the complete suite.")
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    report_path = os.getenv("TASK164_TEST_REPORT_PATH")
    if report_path:
        report = {
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "tests_run": result.testsRun, "passed": len(result.passed),
            "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "modules": MODULES,
            "passed_by_module": dict(Counter(name.rsplit(".", 2)[0] for name in result.passed)),
            "cases": ([{"test": name, "status": "PASS"} for name in result.passed]
                      + [{"test": test.id(), "status": "FAIL"} for test, _ in result.failures]
                      + [{"test": test.id(), "status": "ERROR"} for test, _ in result.errors]
                      + [{"test": test.id(), "status": "SKIPPED", "reason": reason}
                         for test, reason in result.skipped]),
        }
        Path(report_path).write_text(json.dumps(report, indent=2) + "\n")
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
