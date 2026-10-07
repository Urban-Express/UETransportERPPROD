from decimal import Decimal
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_02_hr_payroll.logic.payroll_module import (
    DEDUCTION_ADJUSTMENT_COLUMNS,
    EARNING_ADJUSTMENT_COLUMNS,
    MonthlyPayrollRun,
    OffCyclePayrollRun,
    PayrollBusinessValidationError,
)
from app_backend.services.service_07_alerts_wf_engine import payroll_wf
from app_backend.services.main import app as consolidated_app


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeRowcountResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class FakeMappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row


class FakeRowsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConn:
    def __init__(self, execute_results=None):
        self.execute_results = execute_results or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params or {}))
        if self.execute_results:
            return self.execute_results.pop(0)
        return FakeRowcountResult(1)


class FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeWorkflowQuery:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class FakeWorkflowConn:
    def __init__(self, payroll_run=None, workflow_request=None):
        self.payroll_run = payroll_run
        self.workflow_request = workflow_request
        self.executed = []

    def execute(self, sql, params=None):
        sql_text = str(sql)
        self.executed.append((sql_text, params or {}))
        if "from payroll_workflow_requests" in sql_text and "for update" in sql_text:
            return FakeWorkflowQuery(self.workflow_request)
        if "insert into payroll_workflow_requests" in sql_text:
            return FakeScalarResult(99)
        return FakeRowcountResult(1)


class RecordingCreatePayrollRun(MonthlyPayrollRun):
    def __init__(
        self,
        existing_run=None,
        inserted_id=101,
        insert_error=None,
        existing_runs=None,
    ):
        self.existing_run = existing_run
        self.existing_runs = list(existing_runs or [])
        self.inserted_id = inserted_id
        self.insert_error = insert_error
        self.insert_calls = 0
        self.payroll_engine = FakeEngine(FakeConn())

    def _get_existing_payroll_run_by_code_for_update(self, conn, params_insert):
        if self.existing_runs:
            return self.existing_runs.pop(0)
        return self.existing_run

    def _insert_payroll_run(self, conn, params_insert):
        self.insert_calls += 1
        if self.insert_error:
            raise self.insert_error
        return self.inserted_id


class RecordingOffCyclePayrollRun(OffCyclePayrollRun):
    def __init__(
        self,
        adjustments,
        source_net_salary=Decimal("0"),
        previously_recovered_amount=Decimal("0"),
    ):
        self.adjustments = adjustments
        self.source_net_salary = source_net_salary
        self.previously_recovered_amount = previously_recovered_amount
        self.source_resolution_payloads = []
        self.source_recovered_amount_calls = 0
        self.created_detail_payloads = []
        self.inserted_recovery_entries = []
        self.marked_adjustments = []

    def _check_employee_already_exists(self, conn, payroll_run_id, employee_id):
        return False

    def _get_employee_for_update(self, conn, employee_id, payroll_org_id_fk):
        return {
            "empl_id_pk": employee_id,
            "employee_id": f"EMP-{employee_id}",
            "employee_name": "Correction Employee",
            "employee_designation": "Driver",
            "monthly_basic_salary": Decimal("3000.00"),
            "monthly_allowance": Decimal("400.00"),
            "monthly_accomodation": Decimal("500.00"),
            "bank_name": "Test Bank",
            "bank_account_number": "123456",
        }

    def _retrieve_approved_unprocessed_adjustments(
        self,
        conn,
        payroll_run,
        employee_id,
        adjustment_ids=None,
    ):
        return self.adjustments

    def _resolve_correction_source_detail(self, conn, payroll_run, employee_id, payload):
        self.source_resolution_payloads.append(
            {
                "payroll_run": payroll_run,
                "employee_id": employee_id,
                "payload": payload,
            }
        )
        return {
            "source_payroll_run_id_fk": 30,
            "source_payroll_employee_detail_id_fk": 40,
            "source_net_salary": self.source_net_salary,
            "source_payroll_year": 2026,
            "source_payroll_month": 8,
        }

    def _get_source_recovered_amount(self, conn, source_payroll_employee_detail_id):
        self.source_recovered_amount_calls += 1
        return self.previously_recovered_amount

    def _create_payroll_employee_detail(self, conn, detail_payload):
        self.created_detail_payloads.append(detail_payload.copy())
        gross_salary = self._calculate_detail_gross_salary(detail_payload)
        total_deduction = self._calculate_detail_total_deduction(detail_payload)
        correction_net_amount = self._calculate_detail_correction_net(detail_payload)
        correction_recovery_amount = self._calculate_detail_correction_recovery(
            detail_payload
        )
        net_salary = self._calculate_detail_net_salary(detail_payload)
        if net_salary < 0:
            raise AssertionError("negative net payroll detail insert attempted")
        return {
            "payroll_employee_detail_id_pk": 99,
            "gross_salary": gross_salary,
            "total_deduction": total_deduction,
            "correction_net_amount": correction_net_amount,
            "correction_recovery_amount": correction_recovery_amount,
            "net_salary": net_salary,
        }

    def _insert_correction_recovery_entries(
        self,
        conn,
        payroll_run,
        payroll_detail_id,
        source_detail,
        recovery_entries,
        updated_by,
    ):
        self.inserted_recovery_entries.append(
            {
                "payroll_run": payroll_run,
                "payroll_detail_id": payroll_detail_id,
                "source_detail": source_detail,
                "recovery_entries": recovery_entries,
                "updated_by": updated_by,
            }
        )
        return len(recovery_entries)

    def _mark_adjustments_as_processed(self, conn, adjustment_ids, payroll_run_id, updated_by):
        self.marked_adjustments.append(
            {
                "adjustment_ids": adjustment_ids,
                "payroll_run_id": payroll_run_id,
                "updated_by": updated_by,
            }
        )
        return len(adjustment_ids)


class CorrectionSourceConn(FakeConn):
    def __init__(self, source_rows_by_employee, recovered_amount_by_source_detail=None):
        super().__init__()
        self.source_rows_by_employee = source_rows_by_employee
        self.recovered_amount_by_source_detail = recovered_amount_by_source_detail or {}
        self.source_query_params = []

    def execute(self, sql, params=None):
        params = params or {}
        sql_text = str(sql)
        self.executed.append((sql_text, params))
        normalized_sql = sql_text.lower()

        if (
            "from payroll_employee_detail ped" in normalized_sql
            and "join payroll_run pr" in normalized_sql
        ):
            self.source_query_params.append(params)
            return FakeRowsResult(
                self.source_rows_by_employee.get(params.get("employee_id"), [])
            )

        if (
            "from payroll_correction_recovery" in normalized_sql
            and "sum(recovered_amount)" in normalized_sql
        ):
            source_detail_id = params.get("source_payroll_employee_detail_id")
            return FakeMappingResult(
                {
                    "recovered_amount": self.recovered_amount_by_source_detail.get(
                        source_detail_id,
                        Decimal("0"),
                    )
                }
            )

        return FakeRowcountResult(1)


class HeaderSourceOffCyclePayrollRun(OffCyclePayrollRun):
    def __init__(
        self,
        adjustments_by_employee,
        source_rows_by_employee,
        recovered_amount_by_source_detail=None,
        payroll_run_header_overrides=None,
    ):
        self.adjustments_by_employee = adjustments_by_employee
        self.payroll_run_header = {
            "payroll_run_id_pk": 900,
            "payroll_org_id_fk": 1,
            "payroll_year": 2026,
            "payroll_month": 7,
            "payroll_run_type": "CORRECTION",
            "payroll_period_start_date": "2026-07-01",
            "payroll_period_end_date": "2026-07-31",
            "payroll_status": "DRAFT",
        }
        self.payroll_run_header.update(payroll_run_header_overrides or {})
        self.conn = CorrectionSourceConn(
            source_rows_by_employee,
            recovered_amount_by_source_detail,
        )
        self.payroll_engine = FakeEngine(self.conn)
        self.created_detail_payloads = []
        self.inserted_recovery_entries = []
        self.marked_adjustments = []
        self.status_updates = []
        self.employee_names = {
            21: "Ravi",
            22: "Suresh",
            23: "Lokesh",
        }

    def _get_payroll_run_for_update(self, conn, payroll_run_id, payroll_org_id_fk=None):
        return self.payroll_run_header.copy()

    def _check_employee_already_exists(self, conn, payroll_run_id, employee_id):
        return False

    def _get_employee_for_update(self, conn, employee_id, payroll_org_id_fk):
        return {
            "empl_id_pk": employee_id,
            "employee_id": f"EMP-{employee_id}",
            "employee_name": self.employee_names.get(
                employee_id,
                f"Employee {employee_id}",
            ),
            "employee_designation": "Driver",
            "monthly_basic_salary": Decimal("3000.00"),
            "monthly_allowance": Decimal("400.00"),
            "monthly_accomodation": Decimal("500.00"),
            "bank_name": "Test Bank",
            "bank_account_number": "123456",
        }

    def _retrieve_approved_unprocessed_adjustments(
        self,
        conn,
        payroll_run,
        employee_id,
        adjustment_ids=None,
    ):
        return self.adjustments_by_employee.get(employee_id, [])

    def _create_payroll_employee_detail(self, conn, detail_payload):
        self.created_detail_payloads.append(detail_payload.copy())
        gross_salary = self._calculate_detail_gross_salary(detail_payload)
        total_deduction = self._calculate_detail_total_deduction(detail_payload)
        correction_net_amount = self._calculate_detail_correction_net(detail_payload)
        correction_recovery_amount = self._calculate_detail_correction_recovery(
            detail_payload
        )
        net_salary = self._calculate_detail_net_salary(detail_payload)
        if net_salary < 0:
            raise AssertionError("negative net payroll detail insert attempted")
        return {
            "payroll_employee_detail_id_pk": 700 + detail_payload["payroll_employee_id_fk"],
            "gross_salary": gross_salary,
            "total_deduction": total_deduction,
            "correction_net_amount": correction_net_amount,
            "correction_recovery_amount": correction_recovery_amount,
            "net_salary": net_salary,
        }

    def _insert_correction_recovery_entries(
        self,
        conn,
        payroll_run,
        payroll_detail_id,
        source_detail,
        recovery_entries,
        updated_by,
    ):
        self.inserted_recovery_entries.append(
            {
                "payroll_run": payroll_run,
                "payroll_detail_id": payroll_detail_id,
                "source_detail": source_detail,
                "recovery_entries": recovery_entries,
                "updated_by": updated_by,
            }
        )
        return len(recovery_entries)

    def _mark_adjustments_as_processed(self, conn, adjustment_ids, payroll_run_id, updated_by):
        self.marked_adjustments.append(
            {
                "adjustment_ids": adjustment_ids,
                "payroll_run_id": payroll_run_id,
                "updated_by": updated_by,
            }
        )
        return len(adjustment_ids)

    def _recalculate_payroll_run_totals(
        self,
        conn,
        payroll_run_id,
        payroll_org_id_fk=None,
        updated_by=None,
    ):
        totals = {
            "total_employee_count": len(self.created_detail_payloads),
            "total_gross_salary": sum(
                self._calculate_detail_gross_salary(detail)
                for detail in self.created_detail_payloads
            ),
            "total_deductions": sum(
                self._calculate_detail_total_deduction(detail)
                for detail in self.created_detail_payloads
            ),
            "total_correction_net_amount": sum(
                self._calculate_detail_correction_net(detail)
                for detail in self.created_detail_payloads
            ),
            "total_correction_recovery": sum(
                self._calculate_detail_correction_recovery(detail)
                for detail in self.created_detail_payloads
            ),
            "total_net_salary": sum(
                self._calculate_detail_net_salary(detail)
                for detail in self.created_detail_payloads
            ),
            "payroll_run_type": self.payroll_run_header.get("payroll_run_type"),
        }
        return self._decorate_payroll_run_totals(totals)

    def update_payroll_run_status(self, conn, payroll_run_id, payroll_status, updated_by):
        self.status_updates.append(
            {
                "payroll_run_id": payroll_run_id,
                "payroll_status": payroll_status,
                "updated_by": updated_by,
            }
        )


class PayrollLogicTests(unittest.TestCase):
    def _adjustment(self, adjustment_id, adjustment_type, amount):
        return {
            "payroll_adjustment_id_pk": adjustment_id,
            "adjustment_type": adjustment_type,
            "adjustment_amount": Decimal(amount),
        }

    def _correction_run(self, run_type="CORRECTION"):
        return {
            "payroll_run_id_pk": 2,
            "payroll_org_id_fk": 1,
            "payroll_year": 2026,
            "payroll_month": 7,
            "payroll_run_type": run_type,
            "payroll_period_start_date": "2026-07-01",
            "payroll_period_end_date": "2026-07-31",
        }

    def _payroll_run_create_payload(self, **overrides):
        payload = {
            "payroll_org_id_fk": 1,
            "payroll_run_code": "PAY-2026-09-R1",
            "payroll_year": 2026,
            "payroll_month": 9,
            "payroll_period_start_date": "2026-09-01",
            "payroll_period_end_date": "2026-09-30",
            "payroll_run_type": "MONTHLY",
            "user_principal_name": "payroll@example.com",
        }
        payload.update(overrides)
        return payload

    def _source_detail_row(
        self,
        *,
        source_payroll_run_id=30,
        source_payroll_employee_detail_id=40,
        source_net_salary="3000.00",
        source_payroll_year=2026,
        source_payroll_month=7,
        source_payroll_period_start_date="2026-07-01",
        source_payroll_period_end_date="2026-07-31",
    ):
        return {
            "source_payroll_run_id_fk": source_payroll_run_id,
            "source_payroll_employee_detail_id_fk": source_payroll_employee_detail_id,
            "source_net_salary": Decimal(source_net_salary),
            "source_payroll_run_type": "MONTHLY",
            "source_payroll_year": source_payroll_year,
            "source_payroll_month": source_payroll_month,
            "source_payroll_period_start_date": source_payroll_period_start_date,
            "source_payroll_period_end_date": source_payroll_period_end_date,
        }

    def test_create_payroll_run_reuses_matching_draft_header(self):
        existing_run = {
            "payroll_run_id_pk": 77,
            "payroll_status": "DRAFT",
            "payroll_run_type": "MONTHLY",
            "payroll_year": 2026,
            "payroll_month": 9,
            "payroll_period_start_date": "2026-09-01",
            "payroll_period_end_date": "2026-09-30",
        }
        payroll_run = RecordingCreatePayrollRun(existing_run=existing_run)

        result = payroll_run.create_payroll_run_header(
            self._payroll_run_create_payload()
        )

        self.assertEqual(result["payroll_run_id_pk"], 77)
        self.assertEqual(result["payroll_status"], "DRAFT")
        self.assertTrue(result["reused_existing_run"])
        self.assertEqual(payroll_run.insert_calls, 0)

    def test_create_payroll_run_reuses_matching_draft_after_concurrent_insert(self):
        existing_run = {
            "payroll_run_id_pk": 80,
            "payroll_status": "DRAFT",
            "payroll_run_type": "MONTHLY",
            "payroll_year": 2026,
            "payroll_month": 9,
            "payroll_period_start_date": "2026-09-01",
            "payroll_period_end_date": "2026-09-30",
        }
        payroll_run = RecordingCreatePayrollRun(
            inserted_id=None,
            existing_runs=[None, existing_run],
        )

        result = payroll_run.create_payroll_run_header(
            self._payroll_run_create_payload()
        )

        self.assertEqual(result["payroll_run_id_pk"], 80)
        self.assertTrue(result["reused_existing_run"])
        self.assertEqual(payroll_run.insert_calls, 1)

    def test_create_payroll_run_conflicts_with_completed_duplicate_code(self):
        existing_run = {
            "payroll_run_id_pk": 78,
            "payroll_status": "PROCESSED",
            "payroll_run_type": "MONTHLY",
            "payroll_year": 2026,
            "payroll_month": 9,
            "payroll_period_start_date": "2026-09-01",
            "payroll_period_end_date": "2026-09-30",
        }
        payroll_run = RecordingCreatePayrollRun(existing_run=existing_run)

        result = payroll_run.create_payroll_run_header(
            self._payroll_run_create_payload()
        )

        self.assertEqual(result["error"], "Payroll run code already exists.")
        self.assertEqual(payroll_run.insert_calls, 0)

    def test_create_payroll_run_conflicts_with_cancelled_duplicate_code(self):
        existing_run = {
            "payroll_run_id_pk": 79,
            "payroll_status": "CANCELLED",
            "payroll_run_type": "MONTHLY",
            "payroll_year": 2026,
            "payroll_month": 9,
            "payroll_period_start_date": "2026-09-01",
            "payroll_period_end_date": "2026-09-30",
        }
        payroll_run = RecordingCreatePayrollRun(existing_run=existing_run)

        result = payroll_run.create_payroll_run_header(
            self._payroll_run_create_payload()
        )

        self.assertEqual(result["error"], "Payroll run code already exists.")
        self.assertEqual(payroll_run.insert_calls, 0)

    def test_create_payroll_run_unique_violation_returns_clean_conflict(self):
        payroll_run = RecordingCreatePayrollRun(
            insert_error=IntegrityError("insert", {}, Exception("duplicate key"))
        )

        with self.assertLogs(
            "app_backend.services.service_02_hr_payroll.logic.payroll_module",
            level="WARNING",
        ):
            result = payroll_run.create_payroll_run_header(
                self._payroll_run_create_payload()
            )

        self.assertEqual(result["error"], "Payroll run code already exists.")
        self.assertNotIn("duplicate key", result["error"])

    def test_create_payroll_run_rejects_code_longer_than_schema_limit(self):
        payroll_run = RecordingCreatePayrollRun()

        result = payroll_run.create_payroll_run_header(
            self._payroll_run_create_payload(payroll_run_code="P" * 51)
        )

        self.assertEqual(
            result["error"],
            "payroll_run_code must be 50 characters or fewer.",
        )
        self.assertEqual(payroll_run.insert_calls, 0)

    def test_monthly_payroll_with_fixed_salary_only(self):
        payroll_run = MonthlyPayrollRun()
        detail_payload, adjustment_ids = payroll_run._build_detail_payload(
            payroll_run={"payroll_run_id_pk": 1},
            employee={
                "empl_id_pk": 10,
                "employee_id": "EMP-10",
                "employee_name": "Monthly Employee",
                "employee_designation": "Driver",
                "monthly_basic_salary": Decimal("3000.00"),
                "monthly_allowance": Decimal("400.00"),
                "monthly_accomodation": Decimal("500.00"),
                "bank_name": "Test Bank",
                "bank_account_number": "123456",
            },
            adjustments=[],
            include_fixed_salary=True,
            payload={"user_principal_name": "payroll@example.com"},
        )

        self.assertEqual(detail_payload["basic_salary"], Decimal("3000.00"))
        self.assertEqual(detail_payload["monthly_allowance"], Decimal("400.00"))
        self.assertEqual(detail_payload["accommodation_allowance"], Decimal("500.00"))
        self.assertEqual(detail_payload["payroll_detail_status"], "PROCESSED")
        self.assertEqual(adjustment_ids, [])

    def test_monthly_payroll_with_overtime_and_transport_deductions(self):
        payroll_run = MonthlyPayrollRun()
        detail_payload, adjustment_ids = payroll_run._build_detail_payload(
            payroll_run={"payroll_run_id_pk": 1},
            employee={
                "empl_id_pk": 10,
                "employee_id": "EMP-10",
                "employee_name": "Monthly Employee",
                "employee_designation": "Driver",
                "monthly_basic_salary": Decimal("3000.00"),
                "monthly_allowance": Decimal("400.00"),
                "monthly_accomodation": Decimal("500.00"),
                "bank_name": "Test Bank",
                "bank_account_number": "123456",
            },
            adjustments=[
                {
                    "payroll_adjustment_id_pk": 1,
                    "adjustment_type": "OVERTIME",
                    "adjustment_amount": Decimal("100.00"),
                },
                {
                    "payroll_adjustment_id_pk": 2,
                    "adjustment_type": "SALIK_DEDUCTION",
                    "adjustment_amount": Decimal("25.00"),
                },
                {
                    "payroll_adjustment_id_pk": 3,
                    "adjustment_type": "DARB_DEDUCTION",
                    "adjustment_amount": Decimal("10.00"),
                },
                {
                    "payroll_adjustment_id_pk": 4,
                    "adjustment_type": "FUEL_DEDUCTION",
                    "adjustment_amount": Decimal("40.00"),
                },
            ],
            include_fixed_salary=True,
            payload={"user_principal_name": "payroll@example.com"},
        )

        self.assertEqual(detail_payload["overtime_amount"], Decimal("100.00"))
        self.assertEqual(detail_payload["salik_deduction"], Decimal("25.00"))
        self.assertEqual(detail_payload["darb_deduction"], Decimal("10.00"))
        self.assertEqual(detail_payload["fuel_deduction"], Decimal("40.00"))
        self.assertEqual(adjustment_ids, [1, 2, 3, 4])

    def test_multiple_adjustments_of_same_type_are_summed(self):
        payroll_run = MonthlyPayrollRun()
        adjustment_totals, adjustment_ids = payroll_run.aggregate_adjustments_by_type(
            [
                {
                    "payroll_adjustment_id_pk": 1,
                    "adjustment_type": "OVERTIME",
                    "adjustment_amount": Decimal("100.00"),
                },
                {
                    "payroll_adjustment_id_pk": 2,
                    "adjustment_type": "OVERTIME",
                    "adjustment_amount": Decimal("50.00"),
                },
                {
                    "payroll_adjustment_id_pk": 3,
                    "adjustment_type": "BONUS",
                    "adjustment_amount": Decimal("200.00"),
                },
            ]
        )

        self.assertEqual(adjustment_totals["overtime_amount"], Decimal("150.00"))
        self.assertEqual(adjustment_totals["bonus_amount"], Decimal("200.00"))
        self.assertEqual(adjustment_ids, [1, 2, 3])

    def test_off_cycle_bonus_only_keeps_fixed_salary_zero(self):
        payroll_run = OffCyclePayrollRun()
        detail_payload, adjustment_ids = payroll_run._build_detail_payload(
            payroll_run={"payroll_run_id_pk": 2},
            employee={
                "empl_id_pk": 10,
                "employee_id": "EMP-10",
                "employee_name": "Off Cycle Employee",
                "employee_designation": "Driver",
                "monthly_basic_salary": Decimal("3000.00"),
                "monthly_allowance": Decimal("400.00"),
                "monthly_accomodation": Decimal("500.00"),
                "bank_name": "Test Bank",
                "bank_account_number": "123456",
            },
            adjustments=[
                {
                    "payroll_adjustment_id_pk": 5,
                    "adjustment_type": "BONUS",
                    "adjustment_amount": Decimal("750.00"),
                }
            ],
            include_fixed_salary=False,
            payload={"user_principal_name": "payroll@example.com"},
        )

        self.assertEqual(detail_payload["basic_salary"], Decimal("0"))
        self.assertEqual(detail_payload["monthly_allowance"], Decimal("0"))
        self.assertEqual(detail_payload["accommodation_allowance"], Decimal("0"))
        self.assertEqual(detail_payload["bonus_amount"], Decimal("750.00"))
        self.assertEqual(adjustment_ids, [5])

    def test_off_cycle_earning_only_consumes_adjustment_without_fixed_salary(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [self._adjustment(5, "BONUS", "750.00")]
        )

        result = payroll_run._process_employee(
            conn=FakeConn(),
            payroll_run=self._correction_run("ONE_TIME"),
            employee_id=10,
            payload={"user_principal_name": "payroll@example.com"},
            include_fixed_salary=False,
        )

        detail_payload = payroll_run.created_detail_payloads[0]
        self.assertEqual(detail_payload["basic_salary"], Decimal("0"))
        self.assertEqual(detail_payload["bonus_amount"], Decimal("750.00"))
        self.assertEqual(result["gross_salary"], Decimal("750.00"))
        self.assertEqual(result["net_salary"], Decimal("750.00"))
        self.assertEqual(result["payable_amount"], Decimal("750.00"))
        self.assertEqual(result["recoverable_amount"], Decimal("0"))
        self.assertEqual(result["recovered_correction_amount"], Decimal("0"))
        self.assertEqual(payroll_run.source_resolution_payloads, [])
        self.assertEqual(payroll_run.inserted_recovery_entries[0]["recovery_entries"], [])
        self.assertEqual(payroll_run.marked_adjustments[0]["adjustment_ids"], [5])

    def test_all_correction_earning_types_set_signed_positive_correction_net(self):
        for index, (adjustment_type, detail_column) in enumerate(
            EARNING_ADJUSTMENT_COLUMNS.items(),
            start=1,
        ):
            with self.subTest(adjustment_type=adjustment_type):
                payroll_run = RecordingOffCyclePayrollRun(
                    [self._adjustment(50 + index, adjustment_type, "100.00")]
                )

                result = payroll_run._process_employee(
                    conn=FakeConn(),
                    payroll_run=self._correction_run(),
                    employee_id=10,
                    payload={"user_principal_name": "payroll@example.com"},
                    include_fixed_salary=False,
                )

                detail_payload = payroll_run.created_detail_payloads[0]
                self.assertEqual(detail_payload["basic_salary"], Decimal("0"))
                self.assertEqual(detail_payload[detail_column], Decimal("100.00"))
                self.assertEqual(
                    detail_payload["correction_net_amount"],
                    Decimal("100.00"),
                )
                self.assertEqual(
                    detail_payload["correction_recovery_amount"],
                    Decimal("0"),
                )
                self.assertEqual(result["gross_salary"], Decimal("100.00"))
                self.assertEqual(result["total_deduction"], Decimal("0"))
                self.assertEqual(result["correction_net_amount"], Decimal("100.00"))
                self.assertEqual(result["net_salary"], Decimal("100.00"))
                self.assertEqual(result["payable_amount"], Decimal("100.00"))
                self.assertEqual(result["recoverable_amount"], Decimal("0"))

    def test_correction_deduction_only_records_recovery_without_salary_offset(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [self._adjustment(6, "SALIK_DEDUCTION", "1000.00")],
            source_net_salary=Decimal("3000.00"),
        )

        result = payroll_run._process_employee(
            conn=FakeConn(),
            payroll_run=self._correction_run(),
            employee_id=10,
            payload={
                "user_principal_name": "payroll@example.com",
                "source_payroll_run_id": 30,
            },
            include_fixed_salary=False,
        )

        detail_payload = payroll_run.created_detail_payloads[0]
        recovery_entries = payroll_run.inserted_recovery_entries[0]["recovery_entries"]
        self.assertEqual(detail_payload["basic_salary"], Decimal("0"))
        self.assertEqual(detail_payload["monthly_allowance"], Decimal("0"))
        self.assertEqual(detail_payload["accommodation_allowance"], Decimal("0"))
        self.assertEqual(detail_payload["salik_deduction"], Decimal("1000.00"))
        self.assertEqual(detail_payload["correction_net_amount"], Decimal("-1000.00"))
        self.assertEqual(detail_payload["correction_recovery_amount"], Decimal("1000.00"))
        self.assertIn("Recovered AED 1000.00", detail_payload["remarks"])
        self.assertEqual(result["gross_salary"], Decimal("0"))
        self.assertEqual(result["total_deduction"], Decimal("1000.00"))
        self.assertEqual(result["correction_net_amount"], Decimal("-1000.00"))
        self.assertEqual(result["correction_recovery_amount"], Decimal("1000.00"))
        self.assertEqual(result["net_salary"], Decimal("0.00"))
        self.assertEqual(result["payable_amount"], Decimal("0.00"))
        self.assertEqual(result["recoverable_amount"], Decimal("1000.00"))
        self.assertEqual(result["display_net_salary"], Decimal("-1000.00"))
        self.assertEqual(result["recovered_correction_amount"], Decimal("1000.00"))
        self.assertEqual(result["source_payroll_run_id_fk"], 30)
        self.assertEqual(result["source_payroll_employee_detail_id_fk"], 40)
        self.assertEqual(payroll_run.source_recovered_amount_calls, 1)
        self.assertEqual(recovery_entries[0]["adjustment_amount"], Decimal("1000.00"))
        self.assertEqual(recovery_entries[0]["recovered_amount"], Decimal("1000.00"))
        self.assertEqual(payroll_run.marked_adjustments[0]["adjustment_ids"], [6])

    def test_all_correction_deduction_types_are_preserved_with_signed_net(self):
        for index, (adjustment_type, detail_column) in enumerate(
            DEDUCTION_ADJUSTMENT_COLUMNS.items(),
            start=1,
        ):
            with self.subTest(adjustment_type=adjustment_type):
                payroll_run = RecordingOffCyclePayrollRun(
                    [self._adjustment(100 + index, adjustment_type, "100.00")],
                    source_net_salary=Decimal("1000.00"),
                )

                result = payroll_run._process_employee(
                    conn=FakeConn(),
                    payroll_run=self._correction_run(),
                    employee_id=10,
                    payload={
                        "user_principal_name": "payroll@example.com",
                        "source_payroll_run_id": 30,
                    },
                    include_fixed_salary=False,
                )

                detail_payload = payroll_run.created_detail_payloads[0]
                recovery_entries = payroll_run.inserted_recovery_entries[0][
                    "recovery_entries"
                ]
                self.assertEqual(detail_payload["basic_salary"], Decimal("0"))
                self.assertEqual(detail_payload[detail_column], Decimal("100.00"))
                self.assertEqual(
                    detail_payload["correction_net_amount"],
                    Decimal("-100.00"),
                )
                self.assertEqual(
                    detail_payload["correction_recovery_amount"],
                    Decimal("100.00"),
                )
                self.assertEqual(result["gross_salary"], Decimal("0"))
                self.assertEqual(result["total_deduction"], Decimal("100.00"))
                self.assertEqual(result["correction_net_amount"], Decimal("-100.00"))
                self.assertEqual(
                    result["correction_recovery_amount"],
                    Decimal("100.00"),
                )
                self.assertEqual(result["net_salary"], Decimal("0.00"))
                self.assertEqual(result["payable_amount"], Decimal("0.00"))
                self.assertEqual(result["recoverable_amount"], Decimal("100.00"))
                self.assertEqual(result["display_net_salary"], Decimal("-100.00"))
                self.assertEqual(
                    recovery_entries[0]["payroll_adjustment_id_fk"],
                    100 + index,
                )
                self.assertEqual(
                    recovery_entries[0]["recovered_amount"],
                    Decimal("100.00"),
                )

    def test_correction_mixed_earning_and_deduction_recovers_only_shortfall(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [
                self._adjustment(7, "OVERTIME", "400.00"),
                self._adjustment(8, "SALIK_DEDUCTION", "1000.00"),
            ],
            source_net_salary=Decimal("3000.00"),
        )

        result = payroll_run._process_employee(
            conn=FakeConn(),
            payroll_run=self._correction_run(),
            employee_id=10,
            payload={
                "user_principal_name": "payroll@example.com",
                "source_payroll_run_id": 30,
            },
            include_fixed_salary=False,
        )

        detail_payload = payroll_run.created_detail_payloads[0]
        recovery_entries = payroll_run.inserted_recovery_entries[0]["recovery_entries"]
        self.assertEqual(detail_payload["basic_salary"], Decimal("0"))
        self.assertEqual(detail_payload["overtime_amount"], Decimal("400.00"))
        self.assertEqual(detail_payload["salik_deduction"], Decimal("1000.00"))
        self.assertEqual(detail_payload["correction_net_amount"], Decimal("-600.00"))
        self.assertEqual(detail_payload["correction_recovery_amount"], Decimal("600.00"))
        self.assertEqual(result["gross_salary"], Decimal("400.00"))
        self.assertEqual(result["total_deduction"], Decimal("1000.00"))
        self.assertEqual(result["correction_net_amount"], Decimal("-600.00"))
        self.assertEqual(result["correction_recovery_amount"], Decimal("600.00"))
        self.assertEqual(result["net_salary"], Decimal("0.00"))
        self.assertEqual(result["payable_amount"], Decimal("0.00"))
        self.assertEqual(result["recoverable_amount"], Decimal("600.00"))
        self.assertEqual(result["display_net_salary"], Decimal("-600.00"))
        self.assertEqual(result["recovered_correction_amount"], Decimal("600.00"))
        self.assertEqual(recovery_entries[0]["payroll_adjustment_id_fk"], 8)
        self.assertEqual(recovery_entries[0]["recovered_amount"], Decimal("600.00"))
        self.assertEqual(payroll_run.marked_adjustments[0]["adjustment_ids"], [7, 8])

    def test_correction_multiple_deductions_for_one_employee_are_preserved(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [
                self._adjustment(81, "FUEL_DEDUCTION", "300.00"),
                self._adjustment(82, "SALIK_DEDUCTION", "700.00"),
            ],
            source_net_salary=Decimal("3000.00"),
        )

        result = payroll_run._process_employee(
            conn=FakeConn(),
            payroll_run=self._correction_run(),
            employee_id=10,
            payload={
                "user_principal_name": "payroll@example.com",
                "source_payroll_run_id": 30,
            },
            include_fixed_salary=False,
        )

        detail_payload = payroll_run.created_detail_payloads[0]
        recovery_entries = payroll_run.inserted_recovery_entries[0]["recovery_entries"]
        self.assertEqual(detail_payload["fuel_deduction"], Decimal("300.00"))
        self.assertEqual(detail_payload["salik_deduction"], Decimal("700.00"))
        self.assertEqual(detail_payload["correction_net_amount"], Decimal("-1000.00"))
        self.assertEqual(detail_payload["correction_recovery_amount"], Decimal("1000.00"))
        self.assertEqual(result["total_deduction"], Decimal("1000.00"))
        self.assertEqual(result["correction_net_amount"], Decimal("-1000.00"))
        self.assertEqual(result["correction_recovery_amount"], Decimal("1000.00"))
        self.assertEqual(result["net_salary"], Decimal("0.00"))
        self.assertEqual(
            [entry["payroll_adjustment_id_fk"] for entry in recovery_entries],
            [81, 82],
        )
        self.assertEqual(
            [entry["recovered_amount"] for entry in recovery_entries],
            [Decimal("300.00"), Decimal("700.00")],
        )

    def test_off_cycle_zero_net_consumes_adjustments_without_prior_balance(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [
                self._adjustment(9, "OVERTIME", "1000.00"),
                self._adjustment(10, "SALIK_DEDUCTION", "1000.00"),
            ],
            source_net_salary=Decimal("0"),
        )

        result = payroll_run._process_employee(
            conn=FakeConn(),
            payroll_run=self._correction_run(),
            employee_id=10,
            payload={"user_principal_name": "payroll@example.com"},
            include_fixed_salary=False,
        )

        detail_payload = payroll_run.created_detail_payloads[0]
        self.assertEqual(detail_payload["basic_salary"], Decimal("0"))
        self.assertEqual(result["gross_salary"], Decimal("1000.00"))
        self.assertEqual(result["total_deduction"], Decimal("1000.00"))
        self.assertEqual(result["correction_net_amount"], Decimal("0.00"))
        self.assertEqual(result["correction_recovery_amount"], Decimal("0"))
        self.assertEqual(result["net_salary"], Decimal("0.00"))
        self.assertEqual(result["recovered_correction_amount"], Decimal("0"))
        self.assertEqual(payroll_run.source_resolution_payloads, [])
        self.assertEqual(payroll_run.inserted_recovery_entries[0]["recovery_entries"], [])
        self.assertEqual(payroll_run.marked_adjustments[0]["adjustment_ids"], [9, 10])

    def test_correction_deduction_greater_than_available_recovery_does_not_consume_adjustment(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [self._adjustment(11, "SALIK_DEDUCTION", "1000.00")],
            source_net_salary=Decimal("999.99"),
        )

        with self.assertRaises(PayrollBusinessValidationError):
            payroll_run._process_employee(
                conn=FakeConn(),
                payroll_run=self._correction_run(),
                employee_id=10,
                payload={
                    "user_principal_name": "payroll@example.com",
                    "source_payroll_run_id": 30,
                },
                include_fixed_salary=False,
            )

        self.assertEqual(payroll_run.source_recovered_amount_calls, 1)
        self.assertEqual(payroll_run.created_detail_payloads, [])
        self.assertEqual(payroll_run.inserted_recovery_entries, [])
        self.assertEqual(payroll_run.marked_adjustments, [])

    def test_multiple_sequential_corrections_respect_remaining_source_balance(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [self._adjustment(12, "SALIK_DEDUCTION", "400.00")],
            source_net_salary=Decimal("1000.00"),
            previously_recovered_amount=Decimal("600.00"),
        )

        result = payroll_run._process_employee(
            conn=FakeConn(),
            payroll_run=self._correction_run(),
            employee_id=10,
            payload={
                "user_principal_name": "payroll@example.com",
                "source_payroll_run_id": 30,
            },
            include_fixed_salary=False,
        )

        recovery_entries = payroll_run.inserted_recovery_entries[0]["recovery_entries"]
        self.assertEqual(result["recovered_correction_amount"], Decimal("400.00"))
        self.assertEqual(result["total_deduction"], Decimal("400.00"))
        self.assertEqual(result["correction_net_amount"], Decimal("-400.00"))
        self.assertEqual(result["correction_recovery_amount"], Decimal("400.00"))
        self.assertEqual(recovery_entries[0]["recovered_amount"], Decimal("400.00"))

        exhausted_payroll_run = RecordingOffCyclePayrollRun(
            [self._adjustment(13, "SALIK_DEDUCTION", "0.01")],
            source_net_salary=Decimal("1000.00"),
            previously_recovered_amount=Decimal("1000.00"),
        )
        with self.assertRaises(PayrollBusinessValidationError):
            exhausted_payroll_run._process_employee(
                conn=FakeConn(),
                payroll_run=self._correction_run(),
                employee_id=10,
                payload={
                    "user_principal_name": "payroll@example.com",
                    "source_payroll_run_id": 30,
                },
                include_fixed_salary=False,
            )
        self.assertEqual(exhausted_payroll_run.marked_adjustments, [])

    def test_correction_recovery_insert_failure_does_not_mark_adjustments(self):
        payroll_run = RecordingOffCyclePayrollRun(
            [self._adjustment(14, "SALIK_DEDUCTION", "1000.00")],
            source_net_salary=Decimal("3000.00"),
        )

        def failing_insert(*args, **kwargs):
            raise PayrollBusinessValidationError("Recovery ledger insert failed.")

        payroll_run._insert_correction_recovery_entries = failing_insert

        with self.assertRaises(PayrollBusinessValidationError):
            payroll_run._process_employee(
                conn=FakeConn(),
                payroll_run=self._correction_run(),
                employee_id=10,
                payload={
                    "user_principal_name": "payroll@example.com",
                    "source_payroll_run_id": 30,
                },
                include_fixed_salary=False,
            )

        self.assertEqual(payroll_run.marked_adjustments, [])

    def test_correction_source_selector_defaults_to_header_period(self):
        payroll_run = OffCyclePayrollRun()

        selector = payroll_run._correction_source_selector(
            {
                "payroll_year": 2026,
                "payroll_month": 7,
                "payroll_period_start_date": "2026-07-01",
                "payroll_period_end_date": "2026-07-31",
            },
            {
                "payroll_year": 2026,
                "payroll_month": 9,
            },
            employee_id=10,
        )

        self.assertEqual(selector["mode"], "HEADER_PERIOD")
        self.assertEqual(selector["source_payroll_year"], 2026)
        self.assertEqual(selector["source_payroll_month"], 7)
        self.assertEqual(selector["source_payroll_period_start_date"], "2026-07-01")
        self.assertEqual(selector["source_payroll_period_end_date"], "2026-07-31")

    def test_correction_header_period_source_missing_returns_clean_business_error(self):
        payroll_run = OffCyclePayrollRun()
        conn = CorrectionSourceConn(source_rows_by_employee={10: []})

        with self.assertRaises(PayrollBusinessValidationError) as context:
            payroll_run._resolve_correction_source_detail(
                conn,
                self._correction_run(),
                employee_id=10,
                payload={"user_principal_name": "payroll@example.com"},
            )

        self.assertIn(
            "No processed monthly payroll found for the correction period",
            str(context.exception),
        )

    def test_correction_header_period_source_ambiguity_requires_explicit_source(self):
        payroll_run = OffCyclePayrollRun()
        source_rows = [
            {
                "source_payroll_run_id_fk": 30,
                "source_payroll_employee_detail_id_fk": 40,
                "source_net_salary": Decimal("3000.00"),
            },
            {
                "source_payroll_run_id_fk": 31,
                "source_payroll_employee_detail_id_fk": 41,
                "source_net_salary": Decimal("3000.00"),
            },
        ]
        conn = CorrectionSourceConn(source_rows_by_employee={10: source_rows})

        with self.assertRaises(PayrollBusinessValidationError) as context:
            payroll_run._resolve_correction_source_detail(
                conn,
                self._correction_run(),
                employee_id=10,
                payload={"user_principal_name": "payroll@example.com"},
            )

        self.assertIn("ambiguous", str(context.exception))
        self.assertIn("source_payroll_run_id", str(context.exception))
        self.assertIn("is required", str(context.exception))

    def test_correction_process_uses_header_period_source_without_frontend_source_fields(self):
        payroll_run = HeaderSourceOffCyclePayrollRun(
            adjustments_by_employee={
                21: [self._adjustment(1001, "OVERTIME", "2000.00")],
                22: [self._adjustment(1002, "OTHER_DEDUCTION", "1000.00")],
            },
            source_rows_by_employee={
                22: [
                    {
                        "source_payroll_run_id_fk": 501,
                        "source_payroll_employee_detail_id_fk": 602,
                        "source_net_salary": Decimal("5000.00"),
                        "source_payroll_run_type": "MONTHLY",
                        "source_payroll_year": 2026,
                        "source_payroll_month": 7,
                        "source_payroll_period_start_date": "2026-07-01",
                        "source_payroll_period_end_date": "2026-07-31",
                    }
                ]
            },
        )

        result = payroll_run.process_selected_employees(
            {
                "payroll_run_id": 900,
                "payroll_org_id_fk": 1,
                "employee_ids": [21, 22],
                "user_principal_name": "payroll@example.com",
            }
        )

        self.assertEqual(result["payroll_status"], "PROCESSED")
        self.assertEqual(result["processed_employee_count"], 2)

        overtime_detail = payroll_run.created_detail_payloads[0]
        deduction_detail = payroll_run.created_detail_payloads[1]
        self.assertEqual(overtime_detail["payroll_employee_id_fk"], 21)
        self.assertEqual(overtime_detail["basic_salary"], Decimal("0"))
        self.assertEqual(overtime_detail["overtime_amount"], Decimal("2000.00"))
        self.assertEqual(overtime_detail["correction_net_amount"], Decimal("2000.00"))
        self.assertEqual(overtime_detail["correction_recovery_amount"], Decimal("0"))
        self.assertEqual(result["processed_employees"][0]["net_salary"], Decimal("2000.00"))
        self.assertEqual(result["processed_employees"][0]["correction_net_amount"], Decimal("2000.00"))
        self.assertEqual(result["processed_employees"][0]["payable_amount"], Decimal("2000.00"))
        self.assertEqual(result["processed_employees"][0]["recoverable_amount"], Decimal("0"))
        self.assertEqual(result["processed_employees"][0]["recovered_correction_amount"], Decimal("0"))

        self.assertEqual(deduction_detail["payroll_employee_id_fk"], 22)
        self.assertEqual(deduction_detail["basic_salary"], Decimal("0"))
        self.assertEqual(deduction_detail["monthly_allowance"], Decimal("0"))
        self.assertEqual(deduction_detail["accommodation_allowance"], Decimal("0"))
        self.assertEqual(deduction_detail["other_deduction_amount"], Decimal("1000.00"))
        self.assertEqual(deduction_detail["correction_net_amount"], Decimal("-1000.00"))
        self.assertEqual(deduction_detail["correction_recovery_amount"], Decimal("1000.00"))
        self.assertEqual(result["processed_employees"][1]["total_deduction"], Decimal("1000.00"))
        self.assertEqual(
            result["processed_employees"][1]["correction_net_amount"],
            Decimal("-1000.00"),
        )
        self.assertEqual(
            result["processed_employees"][1]["correction_recovery_amount"],
            Decimal("1000.00"),
        )
        self.assertEqual(result["processed_employees"][1]["net_salary"], Decimal("0.00"))
        self.assertEqual(result["processed_employees"][1]["payable_amount"], Decimal("0.00"))
        self.assertEqual(result["processed_employees"][1]["recoverable_amount"], Decimal("1000.00"))
        self.assertEqual(result["processed_employees"][1]["display_net_salary"], Decimal("-1000.00"))
        self.assertEqual(
            result["processed_employees"][1]["recovered_correction_amount"],
            Decimal("1000.00"),
        )
        self.assertEqual(result["processed_employees"][1]["source_payroll_run_id_fk"], 501)
        self.assertEqual(
            result["processed_employees"][1]["source_payroll_employee_detail_id_fk"],
            602,
        )

        recovery_entries = payroll_run.inserted_recovery_entries[1]["recovery_entries"]
        self.assertEqual(recovery_entries[0]["payroll_adjustment_id_fk"], 1002)
        self.assertEqual(recovery_entries[0]["adjustment_amount"], Decimal("1000.00"))
        self.assertEqual(recovery_entries[0]["recovered_amount"], Decimal("1000.00"))
        self.assertEqual(payroll_run.marked_adjustments[0]["adjustment_ids"], [1001])
        self.assertEqual(payroll_run.marked_adjustments[1]["adjustment_ids"], [1002])
        self.assertEqual(result["total_gross_salary"], Decimal("2000.00"))
        self.assertEqual(result["total_deductions"], Decimal("1000.00"))
        self.assertEqual(result["total_correction_net_amount"], Decimal("1000.00"))
        self.assertEqual(result["total_correction_recovery"], Decimal("1000.00"))
        self.assertEqual(result["total_net_salary"], Decimal("1000.00"))
        self.assertEqual(result["net_correction_amount"], Decimal("1000.00"))
        self.assertEqual(result["total_payable_amount"], Decimal("2000.00"))
        self.assertEqual(result["total_recoverable_amount"], Decimal("1000.00"))

        self.assertEqual(len(payroll_run.conn.source_query_params), 1)
        source_query_params = payroll_run.conn.source_query_params[0]
        self.assertEqual(source_query_params["source_payroll_year"], 2026)
        self.assertEqual(source_query_params["source_payroll_month"], 7)
        self.assertEqual(
            source_query_params["source_payroll_period_start_date"],
            "2026-07-01",
        )
        self.assertEqual(
            source_query_params["source_payroll_period_end_date"],
            "2026-07-31",
        )
        self.assertFalse(
            any(
                "create table" in sql.lower()
                or "create index" in sql.lower()
                or "alter table" in sql.lower()
                for sql, _ in payroll_run.conn.executed
            )
        )
        self.assertEqual(payroll_run.status_updates[0]["payroll_status"], "PROCESSED")

    def test_corr_2026_08_named_employee_totals_return_signed_net_correction(self):
        payroll_run = HeaderSourceOffCyclePayrollRun(
            adjustments_by_employee={
                21: [self._adjustment(2001, "INCENTIVE", "5000.00")],
                23: [self._adjustment(2002, "OTHER_DEDUCTION", "2000.00")],
            },
            source_rows_by_employee={
                23: [
                    self._source_detail_row(
                        source_payroll_run_id=801,
                        source_payroll_employee_detail_id=903,
                        source_net_salary="4500.00",
                        source_payroll_month=8,
                        source_payroll_period_start_date="2026-08-01",
                        source_payroll_period_end_date="2026-08-31",
                    )
                ]
            },
            payroll_run_header_overrides={
                "payroll_run_code": "CORR-2026-08",
                "payroll_year": 2026,
                "payroll_month": 8,
                "payroll_period_start_date": "2026-08-01",
                "payroll_period_end_date": "2026-08-31",
            },
        )

        result = payroll_run.process_selected_employees(
            {
                "payroll_run_id": 900,
                "payroll_org_id_fk": 1,
                "employee_ids": [21, 23],
                "user_principal_name": "payroll@example.com",
            }
        )

        self.assertEqual(result["payroll_status"], "PROCESSED")
        self.assertEqual(result["processed_employee_count"], 2)
        self.assertEqual(result["total_employee_count"], 2)
        self.assertEqual(result["total_gross_salary"], Decimal("5000.00"))
        self.assertEqual(result["total_deductions"], Decimal("2000.00"))
        self.assertEqual(result["total_correction_net_amount"], Decimal("3000.00"))
        self.assertEqual(result["net_correction_amount"], Decimal("3000.00"))
        self.assertEqual(result["total_net_salary"], Decimal("3000.00"))
        self.assertEqual(result["display_total_net_salary"], Decimal("3000.00"))
        self.assertEqual(result["total_payable_amount"], Decimal("5000.00"))
        self.assertEqual(result["total_correction_recovery"], Decimal("2000.00"))
        self.assertEqual(result["total_recoverable_amount"], Decimal("2000.00"))

        details_by_name = {
            detail["employee_name_snapshot"]: detail
            for detail in payroll_run.created_detail_payloads
        }
        ravi_detail = details_by_name["Ravi"]
        self.assertEqual(ravi_detail["basic_salary"], Decimal("0"))
        self.assertEqual(ravi_detail["incentive_amount"], Decimal("5000.00"))
        self.assertEqual(ravi_detail["correction_net_amount"], Decimal("5000.00"))
        self.assertEqual(ravi_detail["correction_recovery_amount"], Decimal("0"))

        lokesh_detail = details_by_name["Lokesh"]
        self.assertEqual(lokesh_detail["basic_salary"], Decimal("0"))
        self.assertEqual(lokesh_detail["other_deduction_amount"], Decimal("2000.00"))
        self.assertEqual(lokesh_detail["correction_net_amount"], Decimal("-2000.00"))
        self.assertEqual(lokesh_detail["correction_recovery_amount"], Decimal("2000.00"))

        ravi_result = next(
            employee
            for employee in result["processed_employees"]
            if employee["payroll_employee_id_fk"] == 21
        )
        self.assertEqual(ravi_result["gross_salary"], Decimal("5000.00"))
        self.assertEqual(ravi_result["total_deduction"], Decimal("0"))
        self.assertEqual(ravi_result["correction_net_amount"], Decimal("5000.00"))
        self.assertEqual(ravi_result["net_salary"], Decimal("5000.00"))
        self.assertEqual(ravi_result["payable_amount"], Decimal("5000.00"))
        self.assertEqual(ravi_result["recoverable_amount"], Decimal("0"))
        self.assertEqual(ravi_result["display_net_salary"], Decimal("5000.00"))

        lokesh_result = next(
            employee
            for employee in result["processed_employees"]
            if employee["payroll_employee_id_fk"] == 23
        )
        self.assertEqual(lokesh_result["gross_salary"], Decimal("0"))
        self.assertEqual(lokesh_result["total_deduction"], Decimal("2000.00"))
        self.assertEqual(lokesh_result["correction_net_amount"], Decimal("-2000.00"))
        self.assertEqual(
            lokesh_result["correction_recovery_amount"],
            Decimal("2000.00"),
        )
        self.assertEqual(lokesh_result["net_salary"], Decimal("0.00"))
        self.assertEqual(lokesh_result["payable_amount"], Decimal("0.00"))
        self.assertEqual(lokesh_result["recoverable_amount"], Decimal("2000.00"))
        self.assertEqual(lokesh_result["display_net_salary"], Decimal("-2000.00"))
        self.assertEqual(lokesh_result["source_payroll_run_id_fk"], 801)
        self.assertEqual(lokesh_result["source_payroll_employee_detail_id_fk"], 903)

    def test_cancel_recoveries_marks_applied_ledger_cancelled(self):
        payroll_run = OffCyclePayrollRun()
        conn = FakeConn()

        result = payroll_run._cancel_recoveries_for_run(
            conn,
            payroll_run_id=2,
            updated_by="payroll@example.com",
        )

        self.assertEqual(result, 1)
        self.assertTrue(
            any(
                "update payroll_correction_recovery" in sql.lower()
                and params.get("payroll_run_id") == 2
                for sql, params in conn.executed
            )
        )

    def test_cancel_off_cycle_run_releases_adjustments_and_cancels_recovery(self):
        payroll_run = OffCyclePayrollRun()
        conn = FakeConn(
            [
                FakeWorkflowQuery(
                    {
                        "payroll_run_id_pk": 2,
                        "payroll_org_id_fk": 1,
                        "payroll_status": "PROCESSED",
                    }
                ),
                FakeRowcountResult(1),
                FakeRowcountResult(1),
                FakeRowcountResult(2),
                FakeRowcountResult(1),
            ]
        )
        payroll_run.payroll_engine = FakeEngine(conn)

        result = payroll_run.cancel_payroll_run(
            {
                "payroll_run_id": 2,
                "payroll_org_id_fk": 1,
                "user_principal_name": "payroll@example.com",
                "cancellation_reason": "Correction retry",
            }
        )

        self.assertEqual(result["payroll_status"], "CANCELLED")
        self.assertEqual(result["cancelled_correction_recovery_count"], 1)
        self.assertTrue(
            any(
                "update payroll_adjustment" in sql.lower()
                and "processed_flag = false" in sql.lower()
                and "adjustment_status = 'APPROVED'" in sql
                for sql, _ in conn.executed
            )
        )
        self.assertTrue(
            any(
                "update payroll_correction_recovery" in sql.lower()
                and params.get("payroll_run_id") == 2
                for sql, params in conn.executed
            )
        )

    def test_duplicate_adjustment_processing_is_blocked(self):
        payroll_run = MonthlyPayrollRun()
        conn = FakeConn([FakeRowcountResult(1)])

        with self.assertRaises(PayrollBusinessValidationError):
            payroll_run._mark_adjustments_as_processed(
                conn=conn,
                adjustment_ids=[1, 2],
                payroll_run_id=1,
                updated_by="payroll@example.com",
            )

    def test_duplicate_employee_in_run_is_blocked(self):
        class DuplicateEmployeePayroll(MonthlyPayrollRun):
            def _check_employee_already_exists(self, conn, payroll_run_id, employee_id):
                return True

        payroll_run = DuplicateEmployeePayroll()
        with self.assertRaises(PayrollBusinessValidationError):
            payroll_run._process_employee(
                conn=FakeConn(),
                payroll_run={
                    "payroll_run_id_pk": 1,
                    "payroll_org_id_fk": 1,
                    "payroll_year": 2026,
                    "payroll_month": 7,
                },
                employee_id=10,
                payload={},
                include_fixed_salary=True,
            )

    def test_cancelled_or_paid_run_cannot_be_processed(self):
        payroll_run = MonthlyPayrollRun()

        with self.assertRaises(PayrollBusinessValidationError):
            payroll_run._validate_run_for_processing({"payroll_status": "CANCELLED"})
        with self.assertRaises(PayrollBusinessValidationError):
            payroll_run._validate_run_for_processing({"payroll_status": "PAID"})

    def test_payroll_run_totals_are_returned_from_database_generated_columns(self):
        payroll_run = MonthlyPayrollRun()
        conn = FakeConn(
            [
                FakeMappingResult(
                    {
                        "total_employee_count": 2,
                        "total_gross_salary": Decimal("8000.00"),
                        "total_deductions": Decimal("250.00"),
                        "total_correction_net_amount": Decimal("0.00"),
                        "total_correction_recovery": Decimal("100.00"),
                        "total_net_salary": Decimal("7750.00"),
                        "payroll_run_type": "MONTHLY",
                    }
                )
            ]
        )

        totals = payroll_run._recalculate_payroll_run_totals(conn, payroll_run_id=1)

        self.assertEqual(totals["total_employee_count"], 2)
        self.assertEqual(totals["total_correction_net_amount"], Decimal("0.00"))
        self.assertEqual(totals["total_correction_recovery"], Decimal("100.00"))
        self.assertEqual(totals["total_net_salary"], Decimal("7750.00"))


class PayrollWorkflowTests(unittest.TestCase):
    def test_self_approval_workflow_approves_run(self):
        conn = FakeWorkflowConn()
        with patch.object(payroll_wf, "ensure_payroll_workflow_table"), \
                patch.object(payroll_wf, "get_payroll_user_workflow_actions", return_value={"SUBMIT", "APPROVE"}), \
                patch.object(payroll_wf, "db_engine", return_value=FakeEngine(conn)), \
                patch.object(
                    payroll_wf,
                    "_get_payroll_run_for_update",
                    return_value={
                        "payroll_run_id_pk": 1,
                        "payroll_org_id_fk": 1,
                        "payroll_run_type": "MONTHLY",
                        "payroll_status": "PROCESSED",
                    },
                ), \
                patch.object(payroll_wf, "_has_pending_payroll_workflow", return_value=None), \
                patch.object(
                    payroll_wf,
                    "submit_configured_workflow",
                    return_value={
                        "workflow_confirmation": "Y",
                        "workflow_status": "EXECUTED",
                        "payroll_status": "APPROVED",
                    },
                ):
            result = payroll_wf.submit_payroll_run_for_approval(
                {
                    "payroll_run_id": 1,
                    "payroll_org_id_fk": 1,
                    "user_principal_name": "self@example.com",
                }
            )

        self.assertEqual(result["workflow_confirmation"], "Y")
        self.assertEqual(result["workflow_status"], "EXECUTED")
        self.assertEqual(result["payroll_status"], "APPROVED")

    def test_sequential_approval_workflow_creates_pending_request(self):
        conn = FakeWorkflowConn()
        with patch.object(payroll_wf, "ensure_payroll_workflow_table"), \
                patch.object(payroll_wf, "get_payroll_user_workflow_actions", return_value={"SUBMIT"}), \
                patch.object(
                    payroll_wf,
                    "get_payroll_employee_line_manager_workflow_identity",
                    return_value={
                        "approver_user_principal_name": "manager@example.com",
                        "approver_official_email": "manager.official@example.com",
                    },
                ), \
                patch.object(payroll_wf, "db_engine", return_value=FakeEngine(conn)), \
                patch.object(
                    payroll_wf,
                    "_get_payroll_run_for_update",
                    return_value={
                        "payroll_run_id_pk": 1,
                        "payroll_org_id_fk": 1,
                        "payroll_run_type": "ONE_TIME",
                        "payroll_status": "PROCESSED",
                    },
                ), \
                patch.object(payroll_wf, "_has_pending_payroll_workflow", return_value=None), \
                patch.object(
                    payroll_wf,
                    "submit_configured_workflow",
                    return_value={
                        "workflow_confirmation": "N",
                        "workflow_status": "PENDING_APPROVAL",
                        "workflow_request_id": 42,
                    },
                ):
            result = payroll_wf.submit_payroll_run_for_approval(
                {
                    "payroll_run_id": 1,
                    "payroll_org_id_fk": 1,
                    "user_principal_name": "submitter@example.com",
                }
            )

        self.assertEqual(result["workflow_confirmation"], "N")
        self.assertEqual(result["workflow_status"], "PENDING_APPROVAL")
        self.assertEqual(result["workflow_request_id"], 42)
        self.assertEqual(result["payroll_status"], "PROCESSED")

    def test_unauthorized_approval_attempt_is_rejected(self):
        with patch.object(payroll_wf, "ensure_payroll_workflow_table"), \
                patch.object(payroll_wf, "_validate_payroll_workflow_request_org", return_value=None), \
                patch.object(
                    payroll_wf,
                    "approve_configured_or_legacy_workflow",
                    return_value={
                        "workflow_confirmation": "N",
                        "error": "User does not have required WORKFLOW/APPROVE permission.",
                    },
                ):
            result = payroll_wf.approve_payroll_workflow(
                {
                    "workflow_request_id": 1,
                    "payroll_org_id_fk": 1,
                    "approver_user_principal_name": "user@example.com",
                }
            )

        self.assertEqual(result["workflow_confirmation"], "N")
        self.assertIn("WORKFLOW/APPROVE", result["error"])

    def test_rejected_workflow_keeps_payroll_run_processed(self):
        conn = FakeWorkflowConn(
            workflow_request={
                "workflow_request_id_pk": 1,
                "workflow_status": "PENDING_APPROVAL",
                "workflow_confirmation": "N",
                "approver_user_principal_name": "manager@example.com",
                "payroll_run_id_fk": 10,
            }
        )
        with patch.object(payroll_wf, "ensure_payroll_workflow_table"), \
                patch.object(payroll_wf, "_validate_payroll_workflow_request_org", return_value=None), \
                patch.object(
                    payroll_wf,
                    "reject_configured_or_legacy_workflow",
                    return_value={
                        "workflow_confirmation": "N",
                        "workflow_status": "REJECTED",
                        "payroll_status": "PROCESSED",
                    },
                ):
            result = payroll_wf.reject_payroll_workflow(
                {
                    "workflow_request_id": 1,
                    "payroll_org_id_fk": 1,
                    "approver_user_principal_name": "manager@example.com",
                    "rejection_comments": "Need correction.",
                }
            )

        self.assertEqual(result["workflow_confirmation"], "N")
        self.assertEqual(result["workflow_status"], "REJECTED")
        self.assertEqual(result["payroll_status"], "PROCESSED")
        self.assertFalse(
            any("update payroll_run" in sql.lower() for sql, _ in conn.executed)
        )


class PayrollApiTests(unittest.TestCase):
    def _auth_context(self, principal="payroll@example.com"):
        return {
            "user": {
                "user_id": 1,
                "user_principal_name": principal,
                "user_org_id_fk": 1,
            },
            "organization": {"org_id": 1},
        }

    def test_hr_payroll_api_endpoint_calls_logic_function(self):
        with patch.object(
            hr_api,
            "create_monthly_payroll_run",
            return_value={"message": "ok", "payroll_run_id_pk": 1},
        ):
            response = hr_api.create_monthly_payroll_endpoint(
                hr_api.PayrollRunCreatePayload(
                    payroll_org_id_fk=1,
                    payroll_run_code="PAY-2026-07",
                    payroll_year=2026,
                    payroll_month=7,
                    payroll_period_start_date="2026-07-01",
                    payroll_period_end_date="2026-07-31",
                ),
                auth_context=self._auth_context(),
            )

        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["payroll_run_id_pk"], 1)

    def test_hr_payroll_workflow_api_endpoint_calls_workflow_function(self):
        with patch.object(
            hr_api,
            "submit_payroll_run_for_approval",
            return_value={
                "workflow_confirmation": "N",
                "workflow_status": "PENDING_APPROVAL",
                "payroll_status": "PROCESSED",
            },
        ):
            response = hr_api.submit_payroll_workflow_endpoint(
                hr_api.PayrollWorkflowSubmitPayload(
                    payroll_run_id=1,
                    payroll_org_id_fk=1,
                    user_principal_name="submitter@example.com",
                ),
                auth_context=self._auth_context("submitter@example.com"),
            )

        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["workflow_status"], "PENDING_APPROVAL")
        self.assertEqual(response["data"]["payroll_status"], "PROCESSED")

    def test_off_cycle_source_business_errors_return_4xx(self):
        with patch.object(
            hr_api,
            "process_off_cycle_payroll_run",
            return_value={
                "error": (
                    "No processed monthly payroll found for the correction "
                    "period for employee 22."
                )
            },
        ):
            with self.assertRaises(hr_api.HTTPException) as context:
                hr_api.process_off_cycle_payroll_endpoint(
                    hr_api.OffCyclePayrollProcessPayload(
                        payroll_run_id=900,
                        payroll_org_id_fk=1,
                        employee_ids=[22],
                    ),
                    auth_context=self._auth_context(),
                )

        self.assertEqual(context.exception.status_code, 404)

        with patch.object(
            hr_api,
            "process_off_cycle_payroll_run",
            return_value={
                "error": (
                    "Correction source payroll is ambiguous for employee 22; "
                    "source_payroll_run_id or source_payroll_employee_detail_id "
                    "is required."
                )
            },
        ):
            with self.assertRaises(hr_api.HTTPException) as context:
                hr_api.process_off_cycle_payroll_endpoint(
                    hr_api.OffCyclePayrollProcessPayload(
                        payroll_run_id=900,
                        payroll_org_id_fk=1,
                        employee_ids=[22],
                    ),
                    auth_context=self._auth_context(),
                )

        self.assertEqual(context.exception.status_code, 400)

    def test_consolidated_api_contains_payroll_routes(self):
        route_paths = {
            route.path
            for route in consolidated_app.routes
            if hasattr(route, "path")
        }

        expected_routes = {
            "/api/v1/payroll/monthly/create",
            "/api/v1/payroll/monthly/process",
            "/api/v1/payroll/off-cycle/process",
            "/api/v1/payroll-adjustments/create",
            "/api/v1/payroll/workflow/submit",
            "/api/v1/workflow/payroll/approve",
        }
        self.assertTrue(expected_routes.issubset(route_paths))


if __name__ == "__main__":
    unittest.main(verbosity=2)
