from datetime import UTC, datetime
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import bindparam, text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_07_alerts_wf_engine import payroll_wf


TEST_PREFIX = "PAYROLL_NAMED_EMP_TEST"
ORG_ID = 1
MANISH_EMPLOYEE_ID = "manish_jani"
RAVI_EMPLOYEE_ID = "ravi_ram"
LOKESH_EMPLOYEE_ID = "lokesh_keer"
MANISH_USER_PRINCIPAL_NAME = "Manish@UrbanExpressTransportLLC.onmicrosoft.com"
MANISH_OFFICIAL_EMAIL = "cfo@urbanexpress.ae"
LOKESH_USER_PRINCIPAL_NAME = "lokesh_keer"
UNAUTHORIZED_USER = "ravi_ram"


class NamedEmployeePayrollTestFailure(Exception):
    pass


def log_step(message):
    print(f"[NAMED-LIVE-DB] {message}", flush=True)


def assert_true(condition, message):
    if not condition:
        raise NamedEmployeePayrollTestFailure(message)


def get_named_employees(conn):
    rows = conn.execute(
        text("""
            select
                empl_id_pk,
                empl_org_id_fk,
                employee_id,
                employee_name,
                employee_designation,
                reporting_to_employee_id,
                email_id_company,
                monthly_basic_salary,
                monthly_allowance,
                monthly_accomodation
            from employee_master
            where empl_org_id_fk = :org_id
            and employee_id in :employee_ids
            order by employee_id
        """).bindparams(bindparam("employee_ids", expanding=True)),
        {
            "org_id": ORG_ID,
            "employee_ids": (
                MANISH_EMPLOYEE_ID,
                RAVI_EMPLOYEE_ID,
                LOKESH_EMPLOYEE_ID,
            ),
        },
    ).mappings().all()
    return {row["employee_id"]: dict(row) for row in rows}


def cleanup_test_rows(conn):
    run_ids = [
        row[0]
        for row in conn.execute(
            text("""
                select payroll_run_id_pk
                from payroll_run
                where payroll_org_id_fk = :org_id
                and payroll_run_code like :prefix
            """),
            {"org_id": ORG_ID, "prefix": f"{TEST_PREFIX}%"},
        ).all()
    ]
    if run_ids:
        conn.execute(
            text("delete from payroll_workflow_requests where payroll_run_id_fk = any(:run_ids)"),
            {"run_ids": run_ids},
        )
        conn.execute(
            text("delete from payroll_adjustment where payroll_run_id_fk = any(:run_ids)"),
            {"run_ids": run_ids},
        )
        conn.execute(
            text("delete from payroll_employee_detail where payroll_run_id_fk = any(:run_ids)"),
            {"run_ids": run_ids},
        )
        conn.execute(
            text("delete from payroll_run where payroll_run_id_pk = any(:run_ids)"),
            {"run_ids": run_ids},
        )
    conn.execute(
        text("""
            delete from payroll_adjustment
            where payroll_adjustment_org_id_fk = :org_id
            and external_reference like :prefix
        """),
        {"org_id": ORG_ID, "prefix": f"{TEST_PREFIX}%"},
    )


def next_monthly_sequence(conn):
    return conn.execute(
        text("""
            select coalesce(max(payroll_run_sequence), 0) + 1
            from payroll_run
            where payroll_org_id_fk = :org_id
            and payroll_year = 2026
            and payroll_month = 7
            and payroll_run_type = 'MONTHLY'
        """),
        {"org_id": ORG_ID},
    ).scalar_one()


def create_approved_adjustment(conn, employee_pk, adjustment_type, amount, run_code):
    earning_deduction_flag = "EARNING" if adjustment_type in {
        "OVERTIME",
        "BONUS",
        "INCENTIVE",
        "REIMBURSEMENT",
        "OTHER_EARNING",
    } else "DEDUCTION"
    return conn.execute(
        text("""
            insert into payroll_adjustment (
                payroll_adjustment_org_id_fk,
                payroll_adjustment_empl_id_fk,
                payroll_year,
                payroll_month,
                adjustment_type,
                earning_deduction_flag,
                adjustment_description,
                adjustment_amount,
                adjustment_status,
                approved_by,
                approved_at,
                external_reference,
                created_by,
                updated_by
            ) values (
                :org_id,
                :employee_pk,
                2026,
                7,
                :adjustment_type,
                :earning_deduction_flag,
                :description,
                :amount,
                'APPROVED',
                :approved_by,
                current_timestamp,
                :external_reference,
                :created_by,
                :updated_by
            )
            returning payroll_adjustment_id_pk
        """),
        {
            "org_id": ORG_ID,
            "employee_pk": employee_pk,
            "adjustment_type": adjustment_type,
            "earning_deduction_flag": earning_deduction_flag,
            "description": f"{run_code} {adjustment_type}",
            "amount": amount,
            "approved_by": TEST_PREFIX,
            "external_reference": f"{run_code}-{employee_pk}-{adjustment_type}-{amount}",
            "created_by": TEST_PREFIX,
            "updated_by": TEST_PREFIX,
        },
    ).scalar_one()


def run_named_employee_live_tests():
    engine = db_engine()
    test_run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    run_code = f"{TEST_PREFIX}_{test_run_id}"
    results = []

    log_step("Preparing payroll workflow table and cleaning prior named-employee test rows")
    payroll_wf.ensure_payroll_workflow_table()
    with engine.begin() as conn:
        cleanup_test_rows(conn)
        employees = get_named_employees(conn)

        assert_true(set(employees) == {
            MANISH_EMPLOYEE_ID,
            RAVI_EMPLOYEE_ID,
            LOKESH_EMPLOYEE_ID,
        }, f"Expected named employees not found: {employees}")
        assert_true(employees[MANISH_EMPLOYEE_ID]["employee_designation"] == "Manager", "Manish designation mismatch")
        assert_true(employees[RAVI_EMPLOYEE_ID]["employee_designation"] == "Employee", "Ravi designation mismatch")
        assert_true(employees[LOKESH_EMPLOYEE_ID]["employee_designation"] == "Employee", "Lokesh designation mismatch")
        assert_true(employees[MANISH_EMPLOYEE_ID]["email_id_company"] == MANISH_OFFICIAL_EMAIL, "Manish official email mismatch")

        run_sequence = next_monthly_sequence(conn)

    log_step("Creating monthly payroll run through HR payroll API endpoint")
    create_response = hr_api.create_monthly_payroll_endpoint(
        hr_api.PayrollRunCreatePayload(
            payroll_org_id_fk=ORG_ID,
            payroll_run_code=run_code,
            payroll_run_description="Named employee live payroll test",
            payroll_year=2026,
            payroll_month=7,
            payroll_run_sequence=run_sequence,
            payroll_period_start_date="2026-07-01",
            payroll_period_end_date="2026-07-31",
            payroll_payment_date="2026-08-05",
            user_principal_name=TEST_PREFIX,
        )
    )
    assert_true(create_response["success"], f"API create response failed: {create_response}")
    payroll_run_id = create_response["data"]["payroll_run_id_pk"]

    log_step("Creating approved adjustments only for the named employees")
    with engine.begin() as conn:
        employees = get_named_employees(conn)
        create_approved_adjustment(conn, employees[MANISH_EMPLOYEE_ID]["empl_id_pk"], "BONUS", 500, run_code)
        create_approved_adjustment(conn, employees[RAVI_EMPLOYEE_ID]["empl_id_pk"], "OVERTIME", 125, run_code)
        create_approved_adjustment(conn, employees[RAVI_EMPLOYEE_ID]["empl_id_pk"], "SALIK_DEDUCTION", 25, run_code)

    log_step("Processing monthly payroll through HR payroll API endpoint for Manish, Ravi, and Lokesh only")
    process_response = hr_api.process_monthly_payroll_endpoint(
        hr_api.MonthlyPayrollProcessPayload(
            payroll_run_id=payroll_run_id,
            payroll_org_id_fk=ORG_ID,
            employee_ids=[
                employees[MANISH_EMPLOYEE_ID]["empl_id_pk"],
                employees[RAVI_EMPLOYEE_ID]["empl_id_pk"],
                employees[LOKESH_EMPLOYEE_ID]["empl_id_pk"],
            ],
            total_period_days=31,
            payable_days=31,
            user_principal_name=TEST_PREFIX,
        )
    )
    assert_true(process_response["success"], f"API process response failed: {process_response}")
    process_data = process_response["data"]
    assert_true(not process_data.get("error"), f"Payroll processing returned error: {process_data}")
    assert_true(process_data["payroll_status"] == "PROCESSED", "Payroll run did not move to PROCESSED")
    assert_true(process_data["total_employee_count"] == 3, "Payroll run employee count is not 3")

    log_step("Validating payroll detail rows contain only Manish, Ravi, and Lokesh")
    with engine.begin() as conn:
        detail_rows = conn.execute(
            text("""
                select *
                from payroll_employee_detail
                where payroll_run_id_fk = :run_id
            """),
            {"run_id": payroll_run_id},
        ).mappings().all()
        detail_employee_ids = {row["payroll_employee_id_fk"] for row in detail_rows}
        expected_employee_pks = {
            employees[MANISH_EMPLOYEE_ID]["empl_id_pk"],
            employees[RAVI_EMPLOYEE_ID]["empl_id_pk"],
            employees[LOKESH_EMPLOYEE_ID]["empl_id_pk"],
        }
        assert_true(detail_employee_ids == expected_employee_pks, f"Unexpected payroll employees: {detail_employee_ids}")

        detail_by_employee = {
            row["payroll_employee_id_fk"]: dict(row)
            for row in detail_rows
        }
        manish_detail = detail_by_employee[employees[MANISH_EMPLOYEE_ID]["empl_id_pk"]]
        ravi_detail = detail_by_employee[employees[RAVI_EMPLOYEE_ID]["empl_id_pk"]]
        lokesh_detail = detail_by_employee[employees[LOKESH_EMPLOYEE_ID]["empl_id_pk"]]

        assert_true(manish_detail["bonus_amount"] == 500, "Manish bonus was not included")
        assert_true(ravi_detail["overtime_amount"] == 125, "Ravi overtime was not included")
        assert_true(ravi_detail["salik_deduction"] == 25, "Ravi SALIK deduction was not included")
        assert_true(lokesh_detail["bonus_amount"] == 0, "Lokesh should not have adjustment earnings")

        assert_true(manish_detail["gross_salary"] == 19000, "Manish gross salary mismatch")
        assert_true(ravi_detail["net_salary"] == 9600, "Ravi net salary mismatch")
        assert_true(lokesh_detail["net_salary"] == 9500, "Lokesh net salary mismatch")

        run_totals = conn.execute(
            text("""
                select total_employee_count, total_gross_salary, total_deductions, total_net_salary
                from payroll_run
                where payroll_run_id_pk = :run_id
            """),
            {"run_id": payroll_run_id},
        ).mappings().one()
        assert_true(run_totals["total_employee_count"] == 3, "Run total employee count mismatch")
        assert_true(run_totals["total_net_salary"] == 38100, "Run total net salary mismatch")
    results.append(("Named employee monthly payroll run and totals", "PASS"))

    log_step("Submitting payroll workflow as Lokesh through HR payroll API endpoint")
    submit_response = hr_api.submit_payroll_workflow_endpoint(
        hr_api.PayrollWorkflowSubmitPayload(
            payroll_run_id=payroll_run_id,
            payroll_org_id_fk=ORG_ID,
            user_principal_name=LOKESH_USER_PRINCIPAL_NAME,
        )
    )
    assert_true(submit_response["success"], f"Workflow submit API response failed: {submit_response}")
    submit_data = submit_response["data"]
    assert_true(submit_data["workflow_status"] == "PENDING_APPROVAL", f"Workflow submit failed: {submit_data}")
    assert_true(
        submit_data["approver_user_principal_name"] == MANISH_USER_PRINCIPAL_NAME,
        f"Approver username mismatch: {submit_data}",
    )
    assert_true(
        submit_data["approver_official_email"] == MANISH_OFFICIAL_EMAIL,
        f"Approver official email mismatch: {submit_data}",
    )

    workflow_request_id = submit_data["workflow_request_id"]

    log_step("Verifying Ravi cannot approve the workflow")
    unauthorized_response = hr_api.approve_payroll_workflow_endpoint(
        hr_api.PayrollWorkflowApprovalPayload(
            workflow_request_id=workflow_request_id,
            approver_user_principal_name=UNAUTHORIZED_USER,
        )
    )
    assert_true(unauthorized_response["success"], f"Unauthorized API response failed: {unauthorized_response}")
    assert_true(unauthorized_response["data"].get("error"), "Ravi approval was not blocked")

    log_step("Approving payroll workflow as Manish username")
    approve_response = hr_api.approve_payroll_workflow_endpoint(
        hr_api.PayrollWorkflowApprovalPayload(
            workflow_request_id=workflow_request_id,
            approver_user_principal_name=MANISH_USER_PRINCIPAL_NAME,
            approval_comments="Named employee live approval test.",
        )
    )
    assert_true(approve_response["success"], f"Workflow approve API response failed: {approve_response}")
    approve_data = approve_response["data"]
    assert_true(approve_data["workflow_status"] == "EXECUTED", f"Workflow approval failed: {approve_data}")
    assert_true(approve_data["payroll_status"] == "APPROVED", "Payroll run did not move to APPROVED")

    log_step("Validating workflow and payroll audit values")
    with engine.begin() as conn:
        payroll_run = conn.execute(
            text("""
                select payroll_status, approved_by
                from payroll_run
                where payroll_run_id_pk = :run_id
            """),
            {"run_id": payroll_run_id},
        ).mappings().one()
        workflow_request = conn.execute(
            text("""
                select *
                from payroll_workflow_requests
                where workflow_request_id_pk = :workflow_request_id
            """),
            {"workflow_request_id": workflow_request_id},
        ).mappings().one()
        assert_true(payroll_run["payroll_status"] == "APPROVED", "DB payroll status mismatch")
        assert_true(payroll_run["approved_by"] == MANISH_USER_PRINCIPAL_NAME, "DB approved_by mismatch")
        assert_true(workflow_request["approver_user_principal_name"] == MANISH_USER_PRINCIPAL_NAME, "Workflow approver mismatch")
        assert_true(workflow_request["request_payload"]["approver_official_email"] == MANISH_OFFICIAL_EMAIL, "Workflow official email mapping mismatch")
    results.append(("Lokesh submission, Manish approval, Ravi unauthorized block, official email mapping", "PASS"))

    return {
        "test_run_id": test_run_id,
        "payroll_run_id": payroll_run_id,
        "workflow_request_id": workflow_request_id,
        "results": results,
    }


if __name__ == "__main__":
    report = run_named_employee_live_tests()
    print("NAMED EMPLOYEE LIVE PAYROLL TEST REPORT")
    print("test_run_id:", report["test_run_id"])
    print("payroll_run_id:", report["payroll_run_id"])
    print("workflow_request_id:", report["workflow_request_id"])
    for test_name, status in report["results"]:
        print(f"{status}: {test_name}")
