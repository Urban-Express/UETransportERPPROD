"""Run the accepted 131 tests plus Task196 tests; write reviewable JSON evidence.

Use python -m app_backend.services.service_06_contracts_management.logic.run_task196_tests
with CONTRACT_TASK196_TEST_DATABASE_URL naming an existing loopback *_tests DB.
Never use unittest discovery here: the historical test_contracts_management_logic
script performs imperative business CRUD at import time.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import unittest


LEGACY_MODULES = (
    "app_backend.services.service_06_contracts_management.logic.test_contracts_management_numeric_fields",
    "app_backend.services.service_06_contracts_management.logic.test_contracts_management_numeric_postgres",
    *("app_backend.services.service_07_alerts_wf_engine.tests." + name for name in (
        "test_firebase_current_pointer_corrections", "test_workflow_attachment_download",
        "test_workflow_document_cleanup", "test_workflow_engine_core", "test_workflow_safe_document_api",
        "test_workflow_shared_compatibility", "test_workflow_transaction_pending_responses",
    )),
)
NEW_MODULES = tuple("app_backend.services.service_06_contracts_management.logic." + name for name in (
    "test_contracts_management_task196", "test_contracts_management_task196_postgres",
    "test_contracts_management_task196_migration",
))


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.subcases = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def record(self, test, status, detail=None):
        self.records.append({"test": test.id(), "status": status,
                             "duration_seconds": round(time.monotonic() - getattr(self, "started", time.monotonic()), 4),
                             **({"detail": detail} if detail else {})})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "FAIL", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "ERROR", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "SKIP", reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        self.subcases.append({"test": test.id(), "case": subtest.id(), "status": "PASS" if err is None else "FAIL"})
        if err is not None:
            self.record(test, "FAIL", self._exc_info_to_string(err, test))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    from .task196_postgres_fixture import DisposableContractDatabase, test_url, MIGRATIONS, SERVICE
    # Reject absent/remote configuration before importing any test modules.
    url = test_url()
    os.environ["RAILWAY_DB_URL"] = url.render_as_string(hide_password=False)
    os.environ["CONTRACT_NUMERIC_DB_CHECKS"] = "1"
    baseline = DisposableContractDatabase()
    os.environ["RAILWAY_DB_URL"] = baseline.engine.url.render_as_string(hide_password=False)
    try:
        legacy = unittest.defaultTestLoader.loadTestsFromNames(LEGACY_MODULES)
        new = unittest.defaultTestLoader.loadTestsFromNames(NEW_MODULES)
        legacy_count, new_count = legacy.countTestCases(), new.countTestCases()
        if legacy_count != 131:
            raise RuntimeError(f"Expected all 131 retained regression tests, found {legacy_count}.")
        suite = unittest.TestSuite((legacy, new))
        result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
        from .test_contracts_management_task196_postgres import EXECUTION_EVIDENCE
        from .test_contracts_management_task196_migration import MIGRATION_EVIDENCE
        root = SERVICE.parents[2]
        files = [*SERVICE.glob("api/*.py"), *SERVICE.glob("logic/*task196*.py"),
                 *SERVICE.glob("logic/contracts_management_*.py"), SERVICE / "data/contracts_management.sql",
                 *MIGRATIONS.glob("*")]
        report = {
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "Isolated loopback PostgreSQL; real application/workflow SQL and HTTP TestClient; synthetic identity and storage fixtures",
            "production_mutations": False, "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "working_tree_tested": True, "database": {"host": url.host, "port": url.port, "database": "disposable *_tests databases"},
            "summary": {"executed": result.testsRun, "passed": sum(r["status"] == "PASS" for r in result.records),
                        "failed": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
                        "legacy_regression_tests": legacy_count, "new_task196_tests": new_count,
                        "passed_subcases": sum(r["status"] == "PASS" for r in result.subcases)},
            "tests": result.records, "subcases": result.subcases,
            "workflow_execution_evidence": EXECUTION_EVIDENCE, "migration_evidence": MIGRATION_EVIDENCE,
            "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()},
            "production_verification": "BLOCKED pending verified runtime target, approved migration/deployment and exact live test scope",
        }
    finally:
        baseline.close()
    report["test_database_cleanup"] = "PASS: test-owned databases dropped; accepted TEMP-table fixtures rolled back"
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["summary"]))
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
