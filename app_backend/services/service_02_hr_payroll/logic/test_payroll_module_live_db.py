from datetime import UTC, datetime
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_02_hr_payroll.logic import payroll_module
from app_backend.services.service_07_alerts_wf_engine import payroll_wf


TEST_PREFIX = "PAYROLL_LIVE_TEST"
SELF_APPROVER = "bayesium_systems@UrbanExpressTransportLLC.onmicrosoft.com"
SUBMITTER = "lokesh_keer"
APPROVER = "Manish@UrbanExpressTransportLLC.onmicrosoft.com"
UNAUTHORIZED_USER = "payroll_live_test_unauthorized"


class LivePayrollTestFailure(Exception):
    pass


def assert_true(condition, message):
    if not condition:
        raise LivePayrollTestFailure(message)


def log_step(message):
    print(f"[LIVE-DB] {message}", flush=True)


def cleanup_test_data(conn):
    run_ids = [
        row[0]
        for row in conn.execute(
            text("""
                select payroll_run_id_pk
                from payroll_run
                where payroll_run_code like :run_code
            """),
            {"run_code": f"{TEST_PREFIX}%"},
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
            where external_reference like :external_reference
            or adjustment_description like :adjustment_description
        """),
        {
            "external_reference": f"{TEST_PREFIX}%",
            "adjustment_description": f"{TEST_PREFIX}%",
        },
    )


def get_existing_payroll_context(conn):
    rows = conn.execute(
        text("""
            select
                om.org_id_pk,
                em.empl_id_pk,
                em.employee_id,
                em.employee_name,
                coalesce(em.monthly_basic_salary, 0) as monthly_basic_salary,
                coalesce(em.monthly_allowance, 0) as monthly_allowance,
                coalesce(em.monthly_accomodation, 0) as monthly_accomodation
            from organization_master om
            join employee_master em
                on em.empl_org_id_fk = om.org_id_pk
            where coalesce(em.monthly_basic_salary, 0) >= 0
            and coalesce(em.monthly_allowance, 0) >= 0
            and coalesce(em.monthly_accomodation, 0) >= 0
            order by om.org_id_pk, em.empl_id_pk
        """),
    ).mappings().all()

    employees_by_org = {}
    for row in rows:
        employees_by_org.setdefault(row["org_id_pk"], []).append(dict(row))

    for org_id, employees in employees_by_org.items():
        if len(employees) >= 2:
            return {
                "payroll_org_id": org_id,
                "emp_fixed": employees[0]["empl_id_pk"],
                "emp_adjusted": employees[1]["empl_id_pk"],
                "emp_adjusted_basic": employees[1]["monthly_basic_salary"],
                "emp_adjusted_allowance": employees[1]["monthly_allowance"],
                "emp_adjusted_accommodation": employees[1]["monthly_accomodation"],
            }

    raise LivePayrollTestFailure(
        "No existing organization with at least two employee_master rows was found. "
        "Please create/identify employees first; this test no longer creates organizations or employees."
    )


def get_existing_workflow_context(conn):
    row = conn.execute(
        text("""
            select requester_employee.empl_org_id_fk as workflow_org_id
            from employee_master requester_employee
            join employee_master manager_employee
                on manager_employee.empl_org_id_fk = requester_employee.empl_org_id_fk
                and (
                    lower(manager_employee.employee_id) = lower(requester_employee.reporting_to_employee_id)
                    or manager_employee.empl_id_pk::text = requester_employee.reporting_to_employee_id
                )
            where (
                lower(requester_employee.email_id_company) = lower(:submitter)
                or lower(requester_employee.email_id_personal) = lower(:submitter)
                or lower(requester_employee.employee_id) = lower(:submitter)
            )
            and (
                lower(manager_employee.email_id_company) = lower(:approver)
                or lower(manager_employee.email_id_personal) = lower(:approver)
                or lower(manager_employee.employee_id) = lower(:approver)
            )
            limit 1
        """),
        {
            "submitter": SUBMITTER,
            "approver": APPROVER,
        },
    ).mappings().first()

    if row:
        return row["workflow_org_id"]

    raise LivePayrollTestFailure(
        "No existing employee workflow context was found for sequential workflow users. "
        "Please update SUBMITTER/APPROVER/SELF_APPROVER to match existing employee_master data."
    )


def latest_test_payroll_run(conn):
    return conn.execute(
        text("""
            select *
            from payroll_run
            where payroll_run_code like :run_code
            order by payroll_run_id_pk desc
            limit 1
        """),
        {"run_code": f"{TEST_PREFIX}%"},
    ).mappings().first()


def latest_test_payroll_detail(conn):
    return conn.execute(
        text("""
            select ped.*
            from payroll_employee_detail ped
            join payroll_run pr
                on pr.payroll_run_id_pk = ped.payroll_run_id_fk
            where pr.payroll_run_code like :run_code
            order by ped.payroll_employee_detail_id_pk desc
            limit 1
        """),
        {"run_code": f"{TEST_PREFIX}%"},
    ).mappings().first()


def latest_test_workflow_request(conn):
    return conn.execute(
        text("""
            select pwr.*
            from payroll_workflow_requests pwr
            join payroll_run pr
                on pr.payroll_run_id_pk = pwr.payroll_run_id_fk
            where pr.payroll_run_code like :run_code
            order by pwr.workflow_request_id_pk desc
            limit 1
        """),
        {"run_code": f"{TEST_PREFIX}%"},
    ).mappings().first()


def create_monthly_run(org_id, run_code):
    run_sequence = int(run_code[-6:])
    return payroll_module.create_monthly_payroll_run(
        {
            "payroll_org_id_fk": org_id,
            "payroll_run_code": run_code,
            "payroll_run_description": "Live DB monthly payroll test",
            "payroll_year": 2026,
            "payroll_month": 7,
            "payroll_run_sequence": run_sequence,
            "payroll_period_start_date": "2026-07-01",
            "payroll_period_end_date": "2026-07-31",
            "payroll_payment_date": "2026-08-05",
            "user_principal_name": TEST_PREFIX,
        }
    )


def approve_adjustment(adjustment_id):
    result = payroll_module.approve_payroll_adjustment(
        {
            "payroll_adjustment_id": adjustment_id,
            "user_principal_name": TEST_PREFIX,
        }
    )
    assert_true(not result.get("error"), f"Adjustment approval failed: {result}")


def create_approved_adjustment(conn, org_id, employee_id, adjustment_type, amount, run_code):
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
                :employee_id,
                2026,
                7,
                :adjustment_type,
                :earning_deduction_flag,
                :adjustment_description,
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
            "org_id": org_id,
            "employee_id": employee_id,
            "adjustment_type": adjustment_type,
            "earning_deduction_flag": earning_deduction_flag,
            "adjustment_description": f"{run_code} {adjustment_type}",
            "amount": amount,
            "approved_by": TEST_PREFIX,
            "external_reference": f"{run_code}-{adjustment_type}-{amount}",
            "created_by": TEST_PREFIX,
            "updated_by": TEST_PREFIX,
        },
    ).scalar_one()


def run_live_tests():
    engine = db_engine()
    test_run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    results = []

    log_step("Preparing schema, cleaning prior payroll live test rows, and discovering existing org/employees")
    with engine.begin() as conn:
        payroll_wf.ensure_payroll_workflow_table()
        cleanup_test_data(conn)
        payroll_context = get_existing_payroll_context(conn)
        payroll_org_id = payroll_context["payroll_org_id"]
        emp_fixed = payroll_context["emp_fixed"]
        emp_adjusted = payroll_context["emp_adjusted"]
        emp_adjusted_basic = payroll_context["emp_adjusted_basic"]
        emp_adjusted_allowance = payroll_context["emp_adjusted_allowance"]
        emp_adjusted_accommodation = payroll_context["emp_adjusted_accommodation"]
        workflow_org_id = get_existing_workflow_context(conn)

    run_code = f"{TEST_PREFIX}_MONTHLY_{test_run_id}"
    log_step("Creating monthly payroll run through payroll logic")
    create_run_result = create_monthly_run(payroll_org_id, run_code)
    assert_true(not create_run_result.get("error"), f"Monthly run creation failed: {create_run_result}")
    monthly_run_id = create_run_result["payroll_run_id_pk"]

    log_step("Creating approved monthly adjustments")
    with engine.begin() as conn:
        create_approved_adjustment(conn, payroll_org_id, emp_adjusted, "OVERTIME", 100, run_code)
        create_approved_adjustment(conn, payroll_org_id, emp_adjusted, "OVERTIME", 50, run_code)
        create_approved_adjustment(conn, payroll_org_id, emp_adjusted, "SALIK_DEDUCTION", 20, run_code)
        create_approved_adjustment(conn, payroll_org_id, emp_adjusted, "DARB_DEDUCTION", 10, run_code)
        create_approved_adjustment(conn, payroll_org_id, emp_adjusted, "FUEL_DEDUCTION", 40, run_code)

    log_step("Processing monthly payroll run through payroll logic")
    process_result = payroll_module.process_monthly_payroll_run(
        {
            "payroll_run_id": monthly_run_id,
            "payroll_org_id_fk": payroll_org_id,
            "user_principal_name": TEST_PREFIX,
            "employee_ids": [emp_fixed, emp_adjusted],
            "total_period_days": 31,
            "payable_days": 31,
        }
    )
    assert_true(not process_result.get("error"), f"Monthly processing failed: {process_result}")
    assert_true(process_result["payroll_status"] == "PROCESSED", "Monthly run not marked PROCESSED")
    assert_true(process_result["total_employee_count"] == 2, "Monthly run employee count mismatch")

    log_step("Validating monthly payroll rows and generated totals")
    with engine.begin() as conn:
        detail_rows = conn.execute(
            text("""
                select *
                from payroll_employee_detail
                where payroll_run_id_fk = :run_id
                order by payroll_employee_id_fk
            """),
            {"run_id": monthly_run_id},
        ).mappings().all()
        assert_true(len(detail_rows) == 2, "Monthly detail row count mismatch")
        adjusted_detail = [
            row for row in detail_rows
            if row["payroll_employee_id_fk"] == emp_adjusted
        ][0]
        assert_true(adjusted_detail["overtime_amount"] == 150, "Overtime aggregation mismatch")
        assert_true(adjusted_detail["salik_deduction"] == 20, "SALIK deduction mismatch")
        assert_true(adjusted_detail["darb_deduction"] == 10, "DARB deduction mismatch")
        assert_true(adjusted_detail["fuel_deduction"] == 40, "Fuel deduction mismatch")
        expected_gross_salary = (
            emp_adjusted_basic
            + emp_adjusted_allowance
            + emp_adjusted_accommodation
            + 150
        )
        assert_true(adjusted_detail["gross_salary"] == expected_gross_salary, "Gross salary mismatch")
        assert_true(adjusted_detail["total_deduction"] == 70, "Total deduction mismatch")
        assert_true(adjusted_detail["net_salary"] == expected_gross_salary - 70, "Net salary mismatch")
        processed_adjustments = conn.execute(
            text("""
                select count(*)
                from payroll_adjustment
                where payroll_adjustment_org_id_fk = :org_id
                and processed_flag = true
                and adjustment_status = 'PROCESSED'
                and payroll_run_id_fk = :run_id
            """),
            {"org_id": payroll_org_id, "run_id": monthly_run_id},
        ).scalar()
        assert_true(processed_adjustments == 5, "Processed adjustment count mismatch")

    log_step("Verifying duplicate monthly processing is blocked")
    duplicate_result = payroll_module.process_monthly_payroll_run(
        {
            "payroll_run_id": monthly_run_id,
            "payroll_org_id_fk": payroll_org_id,
            "user_principal_name": TEST_PREFIX,
        }
    )
    assert_true(duplicate_result.get("error"), "Duplicate monthly processing was not blocked")
    results.append(("Monthly payroll with fixed salary, adjustments, totals, and duplicate prevention", "PASS"))

    off_cycle_code = f"{TEST_PREFIX}_OFFCYCLE_{test_run_id}"
    log_step("Creating off-cycle payroll run through payroll logic")
    off_cycle_create = payroll_module.create_off_cycle_payroll_run(
        {
            "payroll_org_id_fk": payroll_org_id,
            "payroll_run_code": off_cycle_code,
            "payroll_run_description": "Live DB off-cycle payroll test",
            "payroll_year": 2026,
            "payroll_month": 7,
            "payroll_run_sequence": int(test_run_id[-6:]) + 1,
            "payroll_run_type": "ONE_TIME",
            "payroll_period_start_date": "2026-07-01",
            "payroll_period_end_date": "2026-07-31",
            "payroll_payment_date": "2026-08-05",
            "user_principal_name": TEST_PREFIX,
        }
    )
    assert_true(not off_cycle_create.get("error"), f"Off-cycle run creation failed: {off_cycle_create}")
    off_cycle_run_id = off_cycle_create["payroll_run_id_pk"]
    log_step("Creating approved off-cycle bonus adjustment")
    with engine.begin() as conn:
        bonus_adjustment_id = create_approved_adjustment(
            conn,
            payroll_org_id,
            emp_fixed,
            "BONUS",
            750,
            off_cycle_code,
        )
    log_step("Processing off-cycle payroll run through payroll logic")
    off_cycle_process = payroll_module.process_off_cycle_payroll_run(
        {
            "payroll_run_id": off_cycle_run_id,
            "payroll_org_id_fk": payroll_org_id,
            "employee_ids": [emp_fixed],
            "adjustment_ids_by_employee": {str(emp_fixed): [bonus_adjustment_id]},
            "user_principal_name": TEST_PREFIX,
        }
    )
    assert_true(not off_cycle_process.get("error"), f"Off-cycle processing failed: {off_cycle_process}")
    with engine.begin() as conn:
        off_cycle_detail = conn.execute(
            text("""
                select *
                from payroll_employee_detail
                where payroll_run_id_fk = :run_id
            """),
            {"run_id": off_cycle_run_id},
        ).mappings().one()
        assert_true(off_cycle_detail["basic_salary"] == 0, "Off-cycle basic salary should be zero")
        assert_true(off_cycle_detail["monthly_allowance"] == 0, "Off-cycle allowance should be zero")
        assert_true(off_cycle_detail["accommodation_allowance"] == 0, "Off-cycle accommodation should be zero")
        assert_true(off_cycle_detail["bonus_amount"] == 750, "Off-cycle bonus mismatch")
        assert_true(off_cycle_detail["net_salary"] == 750, "Off-cycle net salary mismatch")
    results.append(("Off-cycle bonus-only payroll", "PASS"))

    api_code = f"{TEST_PREFIX}_API_{test_run_id}"
    log_step("Creating monthly payroll run through HR payroll API endpoint function")
    api_create = hr_api.create_monthly_payroll_endpoint(
        hr_api.PayrollRunCreatePayload(
            payroll_org_id_fk=payroll_org_id,
            payroll_run_code=api_code,
            payroll_run_description="Live DB API payroll test",
            payroll_year=2026,
            payroll_month=7,
            payroll_run_sequence=int(test_run_id[-6:]) + 2,
            payroll_period_start_date="2026-07-01",
            payroll_period_end_date="2026-07-31",
            payroll_payment_date="2026-08-05",
            user_principal_name=TEST_PREFIX,
        )
    )
    assert_true(api_create["success"], f"API create wrapper failed: {api_create}")
    results.append(("HR payroll API create endpoint function", "PASS"))

    log_step("Creating processed run for sequential workflow using existing employee/user data")
    workflow_run = payroll_module.create_off_cycle_payroll_run(
        {
            "payroll_org_id_fk": workflow_org_id,
            "payroll_run_code": f"{TEST_PREFIX}_WF_SEQ_{test_run_id}",
            "payroll_year": 2026,
            "payroll_month": 7,
            "payroll_run_sequence": int(test_run_id[-6:]) + 3,
            "payroll_run_type": "ONE_TIME",
            "payroll_period_start_date": "2026-07-01",
            "payroll_period_end_date": "2026-07-31",
            "user_principal_name": TEST_PREFIX,
        }
    )
    assert_true(not workflow_run.get("error"), f"Workflow run creation failed: {workflow_run}")
    workflow_run_id = workflow_run["payroll_run_id_pk"]
    with engine.begin() as conn:
        conn.execute(
            text("""
                update payroll_run
                set payroll_status = 'PROCESSED',
                    processed_by = :processed_by,
                    processed_at = current_timestamp
                where payroll_run_id_pk = :run_id
            """),
            {"run_id": workflow_run_id, "processed_by": TEST_PREFIX},
        )

    log_step("Submitting sequential payroll workflow")
    sequential_submit = payroll_wf.submit_payroll_run_for_approval(
        {
            "payroll_run_id": workflow_run_id,
            "payroll_org_id_fk": workflow_org_id,
            "user_principal_name": SUBMITTER,
        }
    )
    assert_true(
        sequential_submit.get("workflow_status") == "PENDING_APPROVAL",
        f"Sequential submit failed: {sequential_submit}",
    )
    log_step("Verifying unauthorized approval is blocked")
    unauthorized_approval = payroll_wf.approve_payroll_workflow(
        {
            "workflow_request_id": sequential_submit["workflow_request_id"],
            "approver_user_principal_name": UNAUTHORIZED_USER,
        }
    )
    assert_true(unauthorized_approval.get("error"), "Unauthorized approval was not blocked")
    log_step("Rejecting sequential payroll workflow")
    reject_result = payroll_wf.reject_payroll_workflow(
        {
            "workflow_request_id": sequential_submit["workflow_request_id"],
            "approver_user_principal_name": APPROVER,
            "rejection_comments": "Live DB rejection test.",
        }
    )
    assert_true(reject_result.get("workflow_status") == "REJECTED", f"Reject failed: {reject_result}")
    assert_true(reject_result.get("payroll_status") == "PROCESSED", "Rejected run did not stay PROCESSED")
    log_step("Resubmitting rejected payroll workflow")
    resubmit_result = payroll_wf.submit_payroll_run_for_approval(
        {
            "payroll_run_id": workflow_run_id,
            "payroll_org_id_fk": workflow_org_id,
            "user_principal_name": SUBMITTER,
        }
    )
    assert_true(
        resubmit_result.get("workflow_status") == "PENDING_APPROVAL",
        f"Resubmit after rejection failed: {resubmit_result}",
    )
    log_step("Approving resubmitted payroll workflow")
    approve_result = payroll_wf.approve_payroll_workflow(
        {
            "workflow_request_id": resubmit_result["workflow_request_id"],
            "approver_user_principal_name": APPROVER,
            "approval_comments": "Live DB approval test.",
        }
    )
    assert_true(approve_result.get("workflow_status") == "EXECUTED", f"Approval failed: {approve_result}")
    assert_true(approve_result.get("payroll_status") == "APPROVED", "Approved run status mismatch")
    results.append(("Sequential workflow, rejection, resubmission, approval, unauthorized block", "PASS"))

    log_step("Creating processed run for self-approval workflow")
    self_workflow_run = payroll_module.create_off_cycle_payroll_run(
        {
            "payroll_org_id_fk": workflow_org_id,
            "payroll_run_code": f"{TEST_PREFIX}_WF_SELF_{test_run_id}",
            "payroll_year": 2026,
            "payroll_month": 7,
            "payroll_run_sequence": int(test_run_id[-6:]) + 4,
            "payroll_run_type": "ONE_TIME",
            "payroll_period_start_date": "2026-07-01",
            "payroll_period_end_date": "2026-07-31",
            "user_principal_name": TEST_PREFIX,
        }
    )
    self_workflow_run_id = self_workflow_run["payroll_run_id_pk"]
    with engine.begin() as conn:
        conn.execute(
            text("""
                update payroll_run
                set payroll_status = 'PROCESSED',
                    processed_by = :processed_by,
                    processed_at = current_timestamp
                where payroll_run_id_pk = :run_id
            """),
            {"run_id": self_workflow_run_id, "processed_by": TEST_PREFIX},
        )
    log_step("Submitting self-approval payroll workflow")
    self_approval = payroll_wf.submit_payroll_run_for_approval(
        {
            "payroll_run_id": self_workflow_run_id,
            "payroll_org_id_fk": workflow_org_id,
            "user_principal_name": SELF_APPROVER,
        }
    )
    assert_true(self_approval.get("workflow_status") == "EXECUTED", f"Self approval failed: {self_approval}")
    assert_true(self_approval.get("payroll_status") == "APPROVED", "Self approved run status mismatch")
    results.append(("Self-approval workflow", "PASS"))

    log_step("Collecting live table summary")
    with engine.begin() as conn:
        summary = {
            "payroll_run": conn.execute(
                text("""
                    select count(*)
                    from payroll_run pr
                    where pr.payroll_run_code like :run_code
                """),
                {"run_code": f"{TEST_PREFIX}%"},
            ).scalar(),
            "payroll_adjustment": conn.execute(
                text("""
                    select count(*)
                    from payroll_adjustment pa
                    where pa.external_reference like :external_reference
                    or pa.adjustment_description like :adjustment_description
                """),
                {
                    "external_reference": f"{TEST_PREFIX}%",
                    "adjustment_description": f"{TEST_PREFIX}%",
                },
            ).scalar(),
            "payroll_employee_detail": conn.execute(
                text("""
                    select count(*)
                    from payroll_employee_detail ped
                    join payroll_run pr
                        on pr.payroll_run_id_pk = ped.payroll_run_id_fk
                    where pr.payroll_run_code like :run_code
                """),
                {"run_code": f"{TEST_PREFIX}%"},
            ).scalar(),
            "payroll_workflow_requests": conn.execute(
                text("""
                    select count(*)
                    from payroll_workflow_requests pwr
                    join payroll_run pr
                        on pr.payroll_run_id_pk = pwr.payroll_run_id_fk
                    where pr.payroll_run_code like :run_code
                """),
                {"run_code": f"{TEST_PREFIX}%"},
            ).scalar(),
            "latest_payroll_run": dict(latest_test_payroll_run(conn)),
            "latest_payroll_detail": dict(latest_test_payroll_detail(conn)),
            "latest_workflow_request": dict(latest_test_workflow_request(conn)),
        }

    return {
        "test_run_id": test_run_id,
        "results": results,
        "summary": summary,
    }


if __name__ == "__main__":
    report = run_live_tests()
    print("LIVE PAYROLL TEST REPORT")
    print("test_run_id:", report["test_run_id"])
    for test_name, status in report["results"]:
        print(f"{status}: {test_name}")
    print("summary:", report["summary"])
