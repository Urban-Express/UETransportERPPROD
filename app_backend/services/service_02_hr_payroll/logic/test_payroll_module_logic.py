from decimal import Decimal
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_backend.services.service_02_hr_payroll.logic.payroll_module import (
    MonthlyPayrollRun,
)


def test_adjustment_aggregation():
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
                "adjustment_type": "SALIK_DEDUCTION",
                "adjustment_amount": Decimal("20.00"),
            },
        ]
    )

    assert adjustment_totals["overtime_amount"] == Decimal("150.00")
    assert adjustment_totals["salik_deduction"] == Decimal("20.00")
    assert adjustment_ids == [1, 2, 3]


def test_monthly_employee_snapshot_copies_fixed_salary():
    payroll_run = MonthlyPayrollRun()
    snapshot = payroll_run._prepare_employee_snapshot(
        {
            "empl_id_pk": 10,
            "employee_id": "EMP-10",
            "employee_name": "Payroll Test Employee",
            "employee_designation": "Driver",
            "monthly_basic_salary": Decimal("3000.00"),
            "monthly_allowance": Decimal("400.00"),
            "monthly_accomodation": Decimal("500.00"),
            "bank_name": "Test Bank",
            "bank_account_number": "123456",
        },
        include_fixed_salary=True,
    )

    assert snapshot["basic_salary"] == Decimal("3000.00")
    assert snapshot["monthly_allowance"] == Decimal("400.00")
    assert snapshot["accommodation_allowance"] == Decimal("500.00")
    assert snapshot["iban_number_snapshot"] is None


def test_off_cycle_employee_snapshot_keeps_fixed_salary_zero():
    payroll_run = MonthlyPayrollRun()
    snapshot = payroll_run._prepare_employee_snapshot(
        {
            "empl_id_pk": 10,
            "employee_id": "EMP-10",
            "employee_name": "Payroll Test Employee",
            "employee_designation": "Driver",
            "monthly_basic_salary": Decimal("3000.00"),
            "monthly_allowance": Decimal("400.00"),
            "monthly_accomodation": Decimal("500.00"),
            "bank_name": "Test Bank",
            "bank_account_number": "123456",
        },
        include_fixed_salary=False,
    )

    assert snapshot["basic_salary"] == Decimal("0")
    assert snapshot["monthly_allowance"] == Decimal("0")
    assert snapshot["accommodation_allowance"] == Decimal("0")


if __name__ == "__main__":
    test_adjustment_aggregation()
    test_monthly_employee_snapshot_copies_fixed_salary()
    test_off_cycle_employee_snapshot_keeps_fixed_salary_zero()
    print("Payroll module logic tests passed.")
