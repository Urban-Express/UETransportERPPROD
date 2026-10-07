from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import sys

from fastapi import HTTPException
from sqlalchemy import bindparam, text


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_02_hr_payroll.api import main as hr_api


TEST_PREFIX = "PCRLIVE"
ORG_ID = 1
RAVI_EMPLOYEE_ID = "ravi_ram"
LOKESH_EMPLOYEE_ID = "lokesh_keer"
PRINCIPAL = "bayesium_systems@UrbanExpressTransportLLC.onmicrosoft.com"


class LiveCorrectionPayrollTestFailure(Exception):
    pass


def log_step(message):
    print(f"[CORRECTION-LIVE-API] {message}", flush=True)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise LiveCorrectionPayrollTestFailure(
            f"{message}: expected {expected!r}, got {actual!r}"
        )


def assert_true(condition, message):
    if not condition:
        raise LiveCorrectionPayrollTestFailure(message)


def money(value):
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def auth_context():
    return {
        "user": {
            "user_id": 1,
            "user_principal_name": PRINCIPAL,
            "user_org_id_fk": ORG_ID,
        },
        "organization": {"org_id": ORG_ID},
    }


def call_endpoint(endpoint, payload):
    try:
        return endpoint(payload, auth_context=auth_context())
    except HTTPException as exc:
        raise LiveCorrectionPayrollTestFailure(
            f"API call failed with HTTP {exc.status_code}: {exc.detail}"
        ) from exc


def apply_correction_migration(engine):
    migration_path = (
        REPO_ROOT
        / "app_backend/services/service_02_hr_payroll/data/"
        / "20260907_add_payroll_correction_recovery.sql"
    )
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cursor:
            cursor.execute(migration_path.read_text())
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()


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
        run_param = bindparam("run_ids", expanding=True)
        conn.execute(
            text("""
                delete from payroll_correction_recovery
                where correction_payroll_run_id_fk in :run_ids
                or source_payroll_run_id_fk in :run_ids
            """).bindparams(run_param),
            {"run_ids": run_ids},
        )
        conn.execute(
            text("""
                delete from payroll_workflow_requests
                where payroll_run_id_fk in :run_ids
            """).bindparams(bindparam("run_ids", expanding=True)),
            {"run_ids": run_ids},
        )
        conn.execute(
            text("""
                delete from payroll_adjustment
                where payroll_run_id_fk in :run_ids
            """).bindparams(bindparam("run_ids", expanding=True)),
            {"run_ids": run_ids},
        )
        conn.execute(
            text("""
                delete from payroll_employee_detail
                where payroll_run_id_fk in :run_ids
            """).bindparams(bindparam("run_ids", expanding=True)),
            {"run_ids": run_ids},
        )
        conn.execute(
            text("""
                delete from payroll_run
                where payroll_run_id_pk in :run_ids
            """).bindparams(bindparam("run_ids", expanding=True)),
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


def get_named_employees(conn):
    rows = conn.execute(
        text("""
            select
                empl_id_pk,
                employee_id,
                employee_name,
                employee_designation,
                monthly_basic_salary,
                monthly_allowance,
                monthly_accomodation,
                bank_name,
                bank_account_number
            from employee_master
            where empl_org_id_fk = :org_id
            and employee_id in :employee_ids
        """).bindparams(bindparam("employee_ids", expanding=True)),
        {
            "org_id": ORG_ID,
            "employee_ids": (RAVI_EMPLOYEE_ID, LOKESH_EMPLOYEE_ID),
        },
    ).mappings().all()
    return {row["employee_id"]: dict(row) for row in rows}


def existing_august_source_details(conn, lokesh_pk):
    rows = conn.execute(
        text("""
            select
                pr.payroll_run_id_pk,
                pr.payroll_run_code,
                ped.payroll_employee_detail_id_pk,
                ped.net_salary,
                ped.net_salary
                    - coalesce((
                        select sum(recovered_amount)
                        from payroll_correction_recovery pcr
                        where pcr.source_payroll_employee_detail_id_fk =
                            ped.payroll_employee_detail_id_pk
                        and pcr.recovery_status = 'APPLIED'
                    ), 0) as available_recovery_balance
            from payroll_run pr
            join payroll_employee_detail ped
                on ped.payroll_run_id_fk = pr.payroll_run_id_pk
            where pr.payroll_org_id_fk = :org_id
            and pr.payroll_run_type = 'MONTHLY'
            and pr.payroll_year = 2026
            and pr.payroll_month = 8
            and pr.payroll_period_start_date = date '2026-08-01'
            and pr.payroll_period_end_date = date '2026-08-31'
            and pr.payroll_status in ('PROCESSED', 'APPROVED', 'PAID')
            and ped.payroll_employee_id_fk = :employee_id
            and ped.payroll_detail_status in ('PROCESSED', 'APPROVED', 'PAID')
            order by pr.payroll_run_id_pk, ped.payroll_employee_detail_id_pk
        """),
        {"org_id": ORG_ID, "employee_id": lokesh_pk},
    ).mappings().all()
    return [dict(row) for row in rows]


def next_payroll_sequence(conn, payroll_run_type):
    return conn.execute(
        text("""
            select coalesce(max(payroll_run_sequence), 0) + 1
            from payroll_run
            where payroll_org_id_fk = :org_id
            and payroll_year = 2026
            and payroll_month = 8
            and payroll_run_type = :payroll_run_type
        """),
        {"org_id": ORG_ID, "payroll_run_type": payroll_run_type},
    ).scalar_one()


def insert_test_source_monthly(conn, lokesh, test_run_id):
    gross_salary = (
        money(lokesh["monthly_basic_salary"])
        + money(lokesh["monthly_allowance"])
        + money(lokesh["monthly_accomodation"])
    )
    assert_true(
        gross_salary >= Decimal("2000.00"),
        "Lokesh does not have enough monthly salary to seed a test source payroll.",
    )

    source_run_id = conn.execute(
        text("""
            insert into payroll_run (
                payroll_org_id_fk,
                payroll_run_code,
                payroll_run_description,
                payroll_year,
                payroll_month,
                payroll_run_sequence,
                payroll_run_type,
                payroll_period_start_date,
                payroll_period_end_date,
                payroll_payment_date,
                payroll_status,
                processed_by,
                processed_at,
                created_by,
                updated_by
            ) values (
                :org_id,
                :payroll_run_code,
                :payroll_run_description,
                2026,
                8,
                :payroll_run_sequence,
                'MONTHLY',
                date '2026-08-01',
                date '2026-08-31',
                date '2026-09-05',
                'PROCESSED',
                :actor,
                current_timestamp,
                :actor,
                :actor
            )
            returning payroll_run_id_pk
        """),
        {
            "org_id": ORG_ID,
            "payroll_run_code": f"{TEST_PREFIX}_SOURCE_202608_{test_run_id}",
            "payroll_run_description": "Live correction source monthly payroll",
            "payroll_run_sequence": next_payroll_sequence(conn, "MONTHLY"),
            "actor": TEST_PREFIX,
        },
    ).scalar_one()

    source_detail_id = conn.execute(
        text("""
            insert into payroll_employee_detail (
                payroll_run_id_fk,
                payroll_employee_id_fk,
                employee_id_snapshot,
                employee_name_snapshot,
                employee_designation_snapshot,
                basic_salary,
                monthly_allowance,
                accommodation_allowance,
                payment_status,
                payroll_detail_status,
                created_by,
                updated_by
            ) values (
                :payroll_run_id,
                :employee_id,
                :employee_id_snapshot,
                :employee_name_snapshot,
                :employee_designation_snapshot,
                :basic_salary,
                :monthly_allowance,
                :accommodation_allowance,
                'NOT_PROCESSED',
                'PROCESSED',
                :actor,
                :actor
            )
            returning payroll_employee_detail_id_pk
        """),
        {
            "payroll_run_id": source_run_id,
            "employee_id": lokesh["empl_id_pk"],
            "employee_id_snapshot": lokesh["employee_id"],
            "employee_name_snapshot": lokesh["employee_name"],
            "employee_designation_snapshot": lokesh["employee_designation"],
            "basic_salary": lokesh["monthly_basic_salary"],
            "monthly_allowance": lokesh["monthly_allowance"],
            "accommodation_allowance": lokesh["monthly_accomodation"],
            "actor": TEST_PREFIX,
        },
    ).scalar_one()

    conn.execute(
        text("""
            update payroll_run
            set
                total_employee_count = 1,
                total_gross_salary = totals.gross_salary,
                total_deductions = totals.total_deduction,
                total_net_salary = totals.net_salary,
                updated_at = current_timestamp
            from (
                select gross_salary, total_deduction, net_salary
                from payroll_employee_detail
                where payroll_employee_detail_id_pk = :detail_id
            ) totals
            where payroll_run_id_pk = :run_id
        """),
        {"detail_id": source_detail_id, "run_id": source_run_id},
    )
    return {
        "payroll_run_id_pk": source_run_id,
        "payroll_employee_detail_id_pk": source_detail_id,
        "net_salary": gross_salary,
    }


def resolve_or_seed_single_source(conn, lokesh, test_run_id):
    source_rows = existing_august_source_details(conn, lokesh["empl_id_pk"])
    if len(source_rows) == 1:
        assert_true(
            money(source_rows[0]["available_recovery_balance"]) >= Decimal("2000.00"),
            "Existing Lokesh August 2026 monthly source has insufficient recovery balance.",
        )
        return source_rows[0]

    if len(source_rows) > 1:
        raise LiveCorrectionPayrollTestFailure(
            "More than one processed August 2026 monthly payroll source exists for "
            "Lokesh; the no-source-field correction workflow must return ambiguity."
        )

    return insert_test_source_monthly(conn, lokesh, test_run_id)


def create_and_approve_adjustment(employee_pk, adjustment_type, amount, test_run_id):
    create_response = call_endpoint(
        hr_api.create_payroll_adjustment_endpoint,
        hr_api.PayrollAdjustmentCreatePayload(
            payroll_adjustment_org_id_fk=ORG_ID,
            payroll_adjustment_empl_id_fk=employee_pk,
            payroll_year=2026,
            payroll_month=8,
            adjustment_date="2026-08-31",
            adjustment_type=adjustment_type,
            adjustment_amount=float(amount),
            adjustment_description=f"{TEST_PREFIX} {adjustment_type}",
            external_reference=f"{TEST_PREFIX}_{test_run_id}_{employee_pk}_{adjustment_type}",
            user_principal_name=PRINCIPAL,
        ),
    )
    assert_true(create_response["success"], f"Adjustment create failed: {create_response}")
    adjustment_id = create_response["data"]["payroll_adjustment_id_pk"]

    approve_response = call_endpoint(
        hr_api.approve_payroll_adjustment_endpoint,
        hr_api.PayrollAdjustmentStatusPayload(
            payroll_adjustment_id=adjustment_id,
            user_principal_name=PRINCIPAL,
        ),
    )
    assert_true(approve_response["success"], f"Adjustment approve failed: {approve_response}")
    return adjustment_id


def create_correction_run(engine, test_run_id, suffix):
    run_code = f"{TEST_PREFIX}_C202608_{test_run_id}_{suffix}"
    with engine.begin() as conn:
        payroll_run_sequence = next_payroll_sequence(conn, "CORRECTION")

    create_response = call_endpoint(
        hr_api.create_off_cycle_payroll_endpoint,
        hr_api.PayrollRunCreatePayload(
            payroll_org_id_fk=ORG_ID,
            payroll_run_code=run_code,
            payroll_run_description="CORR-2026-08 live signed correction test",
            payroll_year=2026,
            payroll_month=8,
            payroll_run_sequence=payroll_run_sequence,
            payroll_run_type="CORRECTION",
            payroll_period_start_date="2026-08-01",
            payroll_period_end_date="2026-08-31",
            payroll_payment_date="2026-09-05",
            user_principal_name=PRINCIPAL,
        ),
    )
    assert_true(create_response["success"], f"Correction run create failed: {create_response}")
    return create_response["data"]["payroll_run_id_pk"], run_code


def process_correction_run(correction_run_id, ravi, lokesh, ravi_adjustment_id, lokesh_adjustment_id):
    return call_endpoint(
        hr_api.process_off_cycle_payroll_endpoint,
        hr_api.OffCyclePayrollProcessPayload(
            payroll_run_id=correction_run_id,
            payroll_org_id_fk=ORG_ID,
            employee_ids=[ravi["empl_id_pk"], lokesh["empl_id_pk"]],
            adjustment_ids_by_employee={
                str(ravi["empl_id_pk"]): [ravi_adjustment_id],
                str(lokesh["empl_id_pk"]): [lokesh_adjustment_id],
            },
            user_principal_name=PRINCIPAL,
        ),
    )


def assert_correction_response(response, ravi_pk, lokesh_pk):
    assert_true(response["success"], f"Correction process failed: {response}")
    data = response["data"]
    assert_equal(data["payroll_status"], "PROCESSED", "Payroll status mismatch")
    assert_equal(data["processed_employee_count"], 2, "Processed employee count mismatch")
    assert_equal(data["total_employee_count"], 2, "Total employee count mismatch")
    assert_equal(money(data["total_gross_salary"]), Decimal("5000.00"), "Run gross mismatch")
    assert_equal(money(data["total_deductions"]), Decimal("2000.00"), "Run deductions mismatch")
    assert_equal(
        money(data["total_correction_net_amount"]),
        Decimal("3000.00"),
        "Run signed correction net mismatch",
    )
    assert_equal(
        money(data["net_correction_amount"]),
        Decimal("3000.00"),
        "Run net correction alias mismatch",
    )
    assert_equal(
        money(data["total_net_salary"]),
        Decimal("3000.00"),
        "Displayed correction net mismatch",
    )
    assert_equal(
        money(data["total_payable_amount"]),
        Decimal("5000.00"),
        "Run payable mismatch",
    )
    assert_equal(
        money(data["total_recoverable_amount"]),
        Decimal("2000.00"),
        "Run recoverable mismatch",
    )

    employees = {
        employee["payroll_employee_id_fk"]: employee
        for employee in data["processed_employees"]
    }
    ravi = employees[ravi_pk]
    lokesh = employees[lokesh_pk]
    assert_equal(money(ravi["gross_salary"]), Decimal("5000.00"), "Ravi gross mismatch")
    assert_equal(money(ravi["total_deduction"]), Decimal("0.00"), "Ravi deduction mismatch")
    assert_equal(
        money(ravi["correction_net_amount"]),
        Decimal("5000.00"),
        "Ravi correction net mismatch",
    )
    assert_equal(money(ravi["net_salary"]), Decimal("5000.00"), "Ravi payable net mismatch")
    assert_equal(money(ravi["payable_amount"]), Decimal("5000.00"), "Ravi payable alias mismatch")
    assert_equal(money(ravi["recoverable_amount"]), Decimal("0.00"), "Ravi recoverable mismatch")

    assert_equal(money(lokesh["gross_salary"]), Decimal("0.00"), "Lokesh gross mismatch")
    assert_equal(money(lokesh["total_deduction"]), Decimal("2000.00"), "Lokesh deduction mismatch")
    assert_equal(
        money(lokesh["correction_net_amount"]),
        Decimal("-2000.00"),
        "Lokesh correction net mismatch",
    )
    assert_equal(money(lokesh["net_salary"]), Decimal("0.00"), "Lokesh payable net mismatch")
    assert_equal(money(lokesh["payable_amount"]), Decimal("0.00"), "Lokesh payable alias mismatch")
    assert_equal(
        money(lokesh["recoverable_amount"]),
        Decimal("2000.00"),
        "Lokesh recoverable mismatch",
    )
    assert_equal(
        money(lokesh["display_net_salary"]),
        Decimal("-2000.00"),
        "Lokesh display net mismatch",
    )


def assert_persisted_correction(engine, correction_run_id, ravi_pk, lokesh_pk):
    with engine.begin() as conn:
        run_row = conn.execute(
            text("""
                select *
                from payroll_run
                where payroll_run_id_pk = :run_id
            """),
            {"run_id": correction_run_id},
        ).mappings().one()
        assert_equal(run_row["payroll_status"], "PROCESSED", "Persisted status mismatch")
        assert_equal(money(run_row["total_gross_salary"]), Decimal("5000.00"), "Persisted gross mismatch")
        assert_equal(money(run_row["total_deductions"]), Decimal("2000.00"), "Persisted deductions mismatch")
        assert_equal(
            money(run_row["total_correction_net_amount"]),
            Decimal("3000.00"),
            "Persisted signed correction net mismatch",
        )
        assert_equal(
            money(run_row["total_net_salary"]),
            Decimal("5000.00"),
            "Persisted payable net mismatch",
        )
        assert_equal(
            money(run_row["total_correction_recovery"]),
            Decimal("2000.00"),
            "Persisted recovery mismatch",
        )

        details = conn.execute(
            text("""
                select *
                from payroll_employee_detail
                where payroll_run_id_fk = :run_id
            """),
            {"run_id": correction_run_id},
        ).mappings().all()
        detail_by_employee = {
            row["payroll_employee_id_fk"]: dict(row)
            for row in details
        }
        ravi = detail_by_employee[ravi_pk]
        lokesh = detail_by_employee[lokesh_pk]
        assert_equal(money(ravi["incentive_amount"]), Decimal("5000.00"), "Persisted Ravi incentive mismatch")
        assert_equal(money(ravi["correction_net_amount"]), Decimal("5000.00"), "Persisted Ravi correction net mismatch")
        assert_equal(money(ravi["net_salary"]), Decimal("5000.00"), "Persisted Ravi payable mismatch")
        assert_equal(money(lokesh["other_deduction_amount"]), Decimal("2000.00"), "Persisted Lokesh deduction mismatch")
        assert_equal(money(lokesh["correction_net_amount"]), Decimal("-2000.00"), "Persisted Lokesh correction net mismatch")
        assert_equal(money(lokesh["correction_recovery_amount"]), Decimal("2000.00"), "Persisted Lokesh recovery mismatch")
        assert_equal(money(lokesh["net_salary"]), Decimal("0.00"), "Persisted Lokesh payable mismatch")

        recovery_rows = conn.execute(
            text("""
                select *
                from payroll_correction_recovery
                where correction_payroll_run_id_fk = :run_id
                order by payroll_correction_recovery_id_pk
            """),
            {"run_id": correction_run_id},
        ).mappings().all()
        assert_equal(len(recovery_rows), 1, "Recovery ledger row count mismatch")
        recovery = dict(recovery_rows[0])
        assert_equal(recovery["adjustment_type"], "OTHER_DEDUCTION", "Recovery adjustment type mismatch")
        assert_equal(money(recovery["adjustment_amount"]), Decimal("2000.00"), "Recovery adjustment amount mismatch")
        assert_equal(money(recovery["recovered_amount"]), Decimal("2000.00"), "Recovery amount mismatch")
        assert_equal(recovery["recovery_status"], "APPLIED", "Recovery status mismatch")


def assert_detail_api(engine, correction_run_id, lokesh_pk):
    detail_response = call_endpoint(
        hr_api.get_off_cycle_payroll_details_endpoint,
        hr_api.PayrollRunGetPayload(
            payroll_run_id=correction_run_id,
            payroll_org_id_fk=ORG_ID,
        ),
    )
    assert_true(detail_response["success"], f"Detail API failed: {detail_response}")
    lokesh = next(
        detail
        for detail in detail_response["data"]
        if detail["payroll_employee_id_fk"] == lokesh_pk
    )
    assert_equal(
        money(lokesh["correction_net_amount"]),
        Decimal("-2000.00"),
        "Detail API correction net mismatch",
    )
    assert_equal(
        money(lokesh["display_net_salary"]),
        Decimal("-2000.00"),
        "Detail API display net mismatch",
    )
    assert_equal(money(lokesh["payable_amount"]), Decimal("0.00"), "Detail API payable mismatch")

    run_response = call_endpoint(
        hr_api.get_off_cycle_payroll_endpoint,
        hr_api.PayrollRunGetPayload(
            payroll_run_id=correction_run_id,
            payroll_org_id_fk=ORG_ID,
        ),
    )
    assert_true(run_response["success"], f"Run API get failed: {run_response}")
    assert_equal(
        money(run_response["data"]["total_net_salary"]),
        Decimal("3000.00"),
        "Run API displayed correction net mismatch",
    )
    assert_equal(
        money(run_response["data"]["total_payable_amount"]),
        Decimal("5000.00"),
        "Run API payable mismatch",
    )


def cancel_and_assert_released(engine, correction_run_id, ravi_adjustment_id, lokesh_adjustment_id):
    cancel_response = call_endpoint(
        hr_api.cancel_off_cycle_payroll_endpoint,
        hr_api.PayrollRunCancelPayload(
            payroll_run_id=correction_run_id,
            payroll_org_id_fk=ORG_ID,
            cancellation_reason="Live correction retry validation",
            user_principal_name=PRINCIPAL,
        ),
    )
    assert_true(cancel_response["success"], f"Cancel failed: {cancel_response}")
    assert_equal(
        cancel_response["data"]["cancelled_correction_recovery_count"],
        1,
        "Cancelled recovery count mismatch",
    )

    with engine.begin() as conn:
        recovery_status = conn.execute(
            text("""
                select recovery_status
                from payroll_correction_recovery
                where correction_payroll_run_id_fk = :run_id
            """),
            {"run_id": correction_run_id},
        ).scalar_one()
        assert_equal(recovery_status, "CANCELLED", "Recovery was not cancelled")

        adjustments = conn.execute(
            text("""
                select payroll_adjustment_id_pk, adjustment_status, processed_flag, payroll_run_id_fk
                from payroll_adjustment
                where payroll_adjustment_id_pk in :adjustment_ids
            """).bindparams(bindparam("adjustment_ids", expanding=True)),
            {"adjustment_ids": [ravi_adjustment_id, lokesh_adjustment_id]},
        ).mappings().all()
        for adjustment in adjustments:
            assert_equal(adjustment["adjustment_status"], "APPROVED", "Adjustment status was not released")
            assert_equal(adjustment["processed_flag"], False, "Adjustment processed flag was not released")
            assert_equal(adjustment["payroll_run_id_fk"], None, "Adjustment run link was not released")


def run_live_correction_acceptance_test():
    engine = db_engine()
    test_run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    log_step("Applying correction migration and cleaning previous test rows")
    apply_correction_migration(engine)
    with engine.begin() as conn:
        cleanup_test_rows(conn)
        employees = get_named_employees(conn)
        assert_true(
            set(employees) == {RAVI_EMPLOYEE_ID, LOKESH_EMPLOYEE_ID},
            f"Required Ravi/Lokesh employees not found: {employees}",
        )
        ravi = employees[RAVI_EMPLOYEE_ID]
        lokesh = employees[LOKESH_EMPLOYEE_ID]
        source_detail = resolve_or_seed_single_source(conn, lokesh, test_run_id)

    correction_run_ids = []
    try:
        log_step("Creating and approving Ravi +5000 incentive and Lokesh -2000 deduction")
        ravi_adjustment_id = create_and_approve_adjustment(
            ravi["empl_id_pk"],
            "INCENTIVE",
            Decimal("5000.00"),
            test_run_id,
        )
        lokesh_adjustment_id = create_and_approve_adjustment(
            lokesh["empl_id_pk"],
            "OTHER_DEDUCTION",
            Decimal("2000.00"),
            test_run_id,
        )

        log_step("Processing CORR-2026-08 through off-cycle API without source fields")
        correction_run_id, correction_run_code = create_correction_run(
            engine,
            test_run_id,
            "A",
        )
        correction_run_ids.append(correction_run_id)
        response = process_correction_run(
            correction_run_id,
            ravi,
            lokesh,
            ravi_adjustment_id,
            lokesh_adjustment_id,
        )
        assert_correction_response(
            response,
            ravi["empl_id_pk"],
            lokesh["empl_id_pk"],
        )
        assert_persisted_correction(
            engine,
            correction_run_id,
            ravi["empl_id_pk"],
            lokesh["empl_id_pk"],
        )
        assert_detail_api(engine, correction_run_id, lokesh["empl_id_pk"])

        log_step("Cancelling the correction run and retrying the same adjustments")
        cancel_and_assert_released(
            engine,
            correction_run_id,
            ravi_adjustment_id,
            lokesh_adjustment_id,
        )
        retry_run_id, retry_run_code = create_correction_run(
            engine,
            test_run_id,
            "B",
        )
        correction_run_ids.append(retry_run_id)
        retry_response = process_correction_run(
            retry_run_id,
            ravi,
            lokesh,
            ravi_adjustment_id,
            lokesh_adjustment_id,
        )
        assert_correction_response(
            retry_response,
            ravi["empl_id_pk"],
            lokesh["empl_id_pk"],
        )
        assert_persisted_correction(
            engine,
            retry_run_id,
            ravi["empl_id_pk"],
            lokesh["empl_id_pk"],
        )
        assert_detail_api(engine, retry_run_id, lokesh["empl_id_pk"])

        return {
            "test_run_id": test_run_id,
            "source_detail": source_detail,
            "correction_run_code": correction_run_code,
            "retry_run_code": retry_run_code,
            "correction_run_ids": correction_run_ids,
            "ravi_adjustment_id": ravi_adjustment_id,
            "lokesh_adjustment_id": lokesh_adjustment_id,
            "status": "PASS",
        }
    finally:
        with engine.begin() as conn:
            cleanup_test_rows(conn)


if __name__ == "__main__":
    report = run_live_correction_acceptance_test()
    print("LIVE CORRECTION PAYROLL ACCEPTANCE REPORT")
    print("test_run_id:", report["test_run_id"])
    print("source_detail:", report["source_detail"])
    print("correction_run_code:", report["correction_run_code"])
    print("retry_run_code:", report["retry_run_code"])
    print("correction_run_ids:", report["correction_run_ids"])
    print("ravi_adjustment_id:", report["ravi_adjustment_id"])
    print("lokesh_adjustment_id:", report["lokesh_adjustment_id"])
    print("status:", report["status"])
