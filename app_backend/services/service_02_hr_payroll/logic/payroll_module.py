import logging
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import bindparam
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


logger = logging.getLogger(__name__)


EARNING_ADJUSTMENT_COLUMNS = {
    "OVERTIME": "overtime_amount",
    "BONUS": "bonus_amount",
    "INCENTIVE": "incentive_amount",
    "REIMBURSEMENT": "reimbursement_amount",
    "OTHER_EARNING": "other_earning_amount",
}

DEDUCTION_ADJUSTMENT_COLUMNS = {
    "UNPAID_LEAVE": "unpaid_leave_deduction",
    "LOAN_DEDUCTION": "loan_deduction",
    "ADVANCE_DEDUCTION": "advance_deduction",
    "FINE_DEDUCTION": "fine_deduction",
    "FUEL_DEDUCTION": "fuel_deduction",
    "SALIK_DEDUCTION": "salik_deduction",
    "DARB_DEDUCTION": "darb_deduction",
    "CHARGING_COST_DEDUCTION": "charging_cost_deduction",
    "OTHER_DEDUCTION": "other_deduction_amount",
}

ADJUSTMENT_COLUMNS = {
    **EARNING_ADJUSTMENT_COLUMNS,
    **DEDUCTION_ADJUSTMENT_COLUMNS,
}

PAYROLL_DETAIL_AMOUNT_COLUMNS = [
    "basic_salary",
    "monthly_allowance",
    "accommodation_allowance",
    "overtime_amount",
    "bonus_amount",
    "incentive_amount",
    "reimbursement_amount",
    "other_earning_amount",
    "unpaid_leave_deduction",
    "loan_deduction",
    "advance_deduction",
    "fine_deduction",
    "fuel_deduction",
    "salik_deduction",
    "darb_deduction",
    "charging_cost_deduction",
    "other_deduction_amount",
    "penalty_deduction",
]

FIXED_PAYROLL_DETAIL_AMOUNT_COLUMNS = [
    "basic_salary",
    "monthly_allowance",
    "accommodation_allowance",
]

EARNING_PAYROLL_DETAIL_AMOUNT_COLUMNS = list(EARNING_ADJUSTMENT_COLUMNS.values())

# A directly supplied Payroll component; deliberately not an adjustment mapping.
DEDUCTION_PAYROLL_DETAIL_AMOUNT_COLUMNS = [
    *DEDUCTION_ADJUSTMENT_COLUMNS.values(),
    "penalty_deduction",
]

PAYROLL_DETAIL_INSERT_COLUMNS = [
    "payroll_run_id_fk",
    "payroll_employee_id_fk",
    "employee_id_snapshot",
    "employee_name_snapshot",
    "employee_designation_snapshot",
    "basic_salary",
    "monthly_allowance",
    "accommodation_allowance",
    "overtime_amount",
    "bonus_amount",
    "incentive_amount",
    "reimbursement_amount",
    "other_earning_amount",
    "unpaid_leave_deduction",
    "loan_deduction",
    "advance_deduction",
    "fine_deduction",
    "fuel_deduction",
    "salik_deduction",
    "darb_deduction",
    "charging_cost_deduction",
    "other_deduction_amount",
    "penalty_deduction",
    "correction_net_amount",
    "correction_recovery_amount",
    "total_period_days",
    "payable_days",
    "unpaid_leave_days",
    "bank_name_snapshot",
    "bank_account_number_snapshot",
    "iban_number_snapshot",
    "payment_method",
    "payment_status",
    "payroll_detail_status",
    "exception_flag",
    "exception_description",
    "remarks",
    "created_by",
    "updated_by",
    "field_flex_field_1",
    "field_flex_field_2",
    "field_flex_field_3",
    "field_flex_field_4",
]


def _dataframe_to_response(df_value: pd.DataFrame):
    date_columns = [
        "payroll_period_start_date",
        "payroll_period_end_date",
        "payroll_payment_date",
        "created_at",
        "processed_at",
        "approved_at",
        "paid_at",
        "updated_at",
        "adjustment_date",
        "rejected_at",
        "payment_processed_at",
    ]
    for date_column in date_columns:
        if date_column in df_value.columns:
            df_value[date_column] = df_value[date_column].astype(str)

    return df_value, df_value.to_json(orient="records")


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _trusted_org_id(payload: dict | None, field_name: str) -> int | None:
    payload = payload or {}
    value = payload.get(field_name) or payload.get("authenticated_org_id")
    if value is None or value == "":
        return None
    return int(value)


class PayrollBusinessValidationError(Exception):
    pass


class _PayrollRunBase:
    payroll_run_type = "MONTHLY"

    def __init__(self):
        self.payroll_engine = db_engine()

    def _build_payroll_run_insert_params(self, payload: dict):
        payroll_run_type = payload.get("payroll_run_type", self.payroll_run_type)
        return {
            "payroll_org_id_fk": _trusted_org_id(payload, "payroll_org_id_fk"),
            "payroll_run_code": payload.get("payroll_run_code"),
            "payroll_run_description": payload.get("payroll_run_description"),
            "payroll_year": payload.get("payroll_year"),
            "payroll_month": payload.get("payroll_month"),
            "payroll_run_sequence": payload.get("payroll_run_sequence", 1),
            "payroll_run_type": payroll_run_type,
            "payroll_period_start_date": payload.get("payroll_period_start_date"),
            "payroll_period_end_date": payload.get("payroll_period_end_date"),
            "payroll_payment_date": payload.get("payroll_payment_date"),
            "created_by": payload.get("created_by") or payload.get("user_principal_name"),
            "updated_by": payload.get("updated_by") or payload.get("user_principal_name"),
            "field_flex_field_1": payload.get("field_flex_field_1"),
            "field_flex_field_2": payload.get("field_flex_field_2"),
            "field_flex_field_3": payload.get("field_flex_field_3"),
            "field_flex_field_4": payload.get("field_flex_field_4"),
        }

    def _validate_payroll_run_insert_params(self, params_insert: dict):
        if params_insert.get("payroll_run_type") not in self.allowed_run_types():
            raise PayrollBusinessValidationError(
                f"Unsupported payroll_run_type: {params_insert.get('payroll_run_type')}"
            )

        required_fields = [
            "payroll_org_id_fk",
            "payroll_run_code",
            "payroll_year",
            "payroll_month",
            "payroll_period_start_date",
            "payroll_period_end_date",
        ]
        missing_fields = [field for field in required_fields if params_insert.get(field) is None]
        if missing_fields:
            raise PayrollBusinessValidationError(
                f"Missing required fields: {', '.join(missing_fields)}"
            )

        if len(str(params_insert.get("payroll_run_code"))) > 50:
            raise PayrollBusinessValidationError(
                "payroll_run_code must be 50 characters or fewer."
            )

    def _period_value_matches(self, existing_value, requested_value) -> bool:
        if existing_value is None or requested_value is None:
            return existing_value is None and requested_value is None
        return str(existing_value) == str(requested_value)

    def _payroll_run_matches_retry_identity(self, existing_run: dict, params_insert: dict) -> bool:
        identity_fields = [
            "payroll_run_type",
            "payroll_year",
            "payroll_month",
            "payroll_period_start_date",
            "payroll_period_end_date",
        ]
        return all(
            self._period_value_matches(existing_run.get(field), params_insert.get(field))
            for field in identity_fields
        )

    def _integrity_constraint_name(self, exc: IntegrityError | None = None):
        orig = getattr(exc, "orig", None) if exc is not None else None
        diag = getattr(orig, "diag", None)
        if diag is not None:
            return getattr(diag, "constraint_name", None)
        return None

    def _payroll_run_unique_conflict_message(self, exc: IntegrityError | None = None) -> str:
        constraint_name = self._integrity_constraint_name(exc)
        if constraint_name == "uq_payroll_run_sequence":
            return "Payroll run sequence already exists for this payroll period."
        return "Payroll run code already exists."

    def _reuse_or_conflict_existing_payroll_run(self, existing_run: dict, params_insert: dict):
        if (
            existing_run.get("payroll_status") == "DRAFT"
            and self._payroll_run_matches_retry_identity(existing_run, params_insert)
        ):
            return {
                "message": "Reusing existing draft payroll run.",
                "payroll_run_id_pk": existing_run.get("payroll_run_id_pk"),
                "payroll_run_type": existing_run.get("payroll_run_type"),
                "payroll_status": existing_run.get("payroll_status"),
                "reused_existing_run": True,
            }

        return {"error": self._payroll_run_unique_conflict_message()}

    def _get_existing_payroll_run_by_code_for_update(self, conn, params_insert: dict):
        get_existing = text("""
            select *
            from payroll_run
            where payroll_org_id_fk = :payroll_org_id_fk
            and payroll_run_code = :payroll_run_code
            for update
        """)
        row = conn.execute(
            get_existing,
            {
                "payroll_org_id_fk": params_insert.get("payroll_org_id_fk"),
                "payroll_run_code": params_insert.get("payroll_run_code"),
            },
        ).mappings().first()
        return dict(row) if row else None

    def _insert_payroll_run(self, conn, params_insert: dict):
        insert_payroll_run = text("""
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
                created_by,
                updated_by,
                field_flex_field_1,
                field_flex_field_2,
                field_flex_field_3,
                field_flex_field_4
            ) values (
                :payroll_org_id_fk,
                :payroll_run_code,
                :payroll_run_description,
                :payroll_year,
                :payroll_month,
                :payroll_run_sequence,
                :payroll_run_type,
                :payroll_period_start_date,
                :payroll_period_end_date,
                :payroll_payment_date,
                'DRAFT',
                :created_by,
                :updated_by,
                :field_flex_field_1,
                :field_flex_field_2,
                :field_flex_field_3,
                :field_flex_field_4
            )
            on conflict (payroll_org_id_fk, payroll_run_code) do nothing
            returning payroll_run_id_pk
        """)
        return conn.execute(insert_payroll_run, params_insert).scalar_one_or_none()

    def create_payroll_run_header(self, payload: dict):
        try:
            params_insert = self._build_payroll_run_insert_params(payload)
            self._validate_payroll_run_insert_params(params_insert)

            with self.payroll_engine.begin() as conn:
                existing_run = self._get_existing_payroll_run_by_code_for_update(
                    conn,
                    params_insert,
                )
                if existing_run:
                    return self._reuse_or_conflict_existing_payroll_run(
                        existing_run,
                        params_insert,
                    )

                payroll_run_id = self._insert_payroll_run(conn, params_insert)
                if payroll_run_id is None:
                    existing_run = self._get_existing_payroll_run_by_code_for_update(
                        conn,
                        params_insert,
                    )
                    if existing_run:
                        return self._reuse_or_conflict_existing_payroll_run(
                            existing_run,
                            params_insert,
                        )
                    raise PayrollBusinessValidationError("Payroll run code already exists.")

            return {
                "message": "Successfully created payroll run.",
                "payroll_run_id_pk": payroll_run_id,
                "payroll_run_type": params_insert.get("payroll_run_type"),
                "payroll_status": "DRAFT",
                "reused_existing_run": False,
            }
        except PayrollBusinessValidationError as e:
            return {"error": str(e)}
        except IntegrityError as e:
            logger.warning("Payroll run unique conflict", exc_info=True)
            return {"error": self._payroll_run_unique_conflict_message(e)}
        except Exception as e:
            logger.exception("Failed to create payroll run header")
            return {"error": f"Failed to create payroll run. Error Message: {str(e)}"}

    def get_payroll_run(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id:
                return {"error": "payroll_run_id is required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            df_payroll_run = self._get_payroll_run_dataframe(
                payroll_run_id,
                payroll_org_id_fk,
            )
            if df_payroll_run.empty:
                return {"error": "Payroll run not found."}

            response = df_payroll_run.iloc[0].to_dict()
            response["workflow_status"] = self.get_latest_workflow_status(payroll_run_id)
            return self._decorate_payroll_run_record(response)
        except Exception as e:
            logger.exception("Failed to get payroll run")
            return {"error": f"Failed to get payroll run. Error Message: {str(e)}"}

    def list_payroll_runs(self, payload: dict | None = None):
        try:
            payload = payload or {}
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            list_payroll_runs_query = text("""
                select *
                from payroll_run
                where payroll_org_id_fk = :payroll_org_id_fk
                and (:payroll_year is null or payroll_year = :payroll_year)
                and (:payroll_month is null or payroll_month = :payroll_month)
                and (:payroll_status is null or payroll_status = :payroll_status)
                and (:payroll_run_type is null or payroll_run_type = :payroll_run_type)
                order by payroll_run_id_pk desc
            """)

            with self.payroll_engine.begin() as conn:
                df_payroll_runs = pd.read_sql(
                    sql=list_payroll_runs_query,
                    con=conn,
                    params={
                        "payroll_org_id_fk": payroll_org_id_fk,
                        "payroll_year": payload.get("payroll_year"),
                        "payroll_month": payload.get("payroll_month"),
                        "payroll_status": payload.get("payroll_status"),
                        "payroll_run_type": payload.get("payroll_run_type", self.payroll_run_type),
                    },
                )

            df_payroll_runs = self._decorate_payroll_run_dataframe(df_payroll_runs)
            return _dataframe_to_response(df_payroll_runs)
        except Exception as e:
            logger.exception("Failed to list payroll runs")
            df_payroll_runs = pd.DataFrame()
            return df_payroll_runs, {"Failed to list payroll runs. Error Message: ": {e}}

    def validate_run_organization(self, payroll_run: dict, payroll_org_id_fk: int | None):
        if payroll_org_id_fk is None:
            raise PayrollBusinessValidationError("payroll_org_id_fk is required.")
        if int(payroll_run.get("payroll_org_id_fk")) != int(payroll_org_id_fk):
            raise PayrollBusinessValidationError("Payroll run belongs to another organization.")

    def update_payroll_run_status(
        self,
        conn,
        payroll_run_id: int,
        payroll_status: str,
        updated_by: str | None = None,
    ):
        update_status = text("""
            update payroll_run
            set
                payroll_status = :payroll_status,
                processed_by = case
                    when :payroll_status = 'PROCESSED' then :updated_by
                    else processed_by
                end,
                processed_at = case
                    when :payroll_status = 'PROCESSED' then current_timestamp
                    else processed_at
                end,
                approved_by = case
                    when :payroll_status = 'APPROVED' then :updated_by
                    else approved_by
                end,
                approved_at = case
                    when :payroll_status = 'APPROVED' then current_timestamp
                    else approved_at
                end,
                updated_by = :updated_by,
                updated_at = current_timestamp
            where payroll_run_id_pk = :payroll_run_id
        """)
        conn.execute(
            update_status,
            {
                "payroll_run_id": payroll_run_id,
                "payroll_status": payroll_status,
                "updated_by": updated_by,
            },
        )

    def _cancel_recoveries_for_run(self, conn, payroll_run_id: int, updated_by: str | None):
        return 0

    def cancel_payroll_run(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id:
                return {"error": "payroll_run_id is required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            with self.payroll_engine.begin() as conn:
                payroll_run = self._get_payroll_run_for_update(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                )
                self.validate_run_organization(payroll_run, payroll_org_id_fk)
                if payroll_run.get("payroll_status") in ("APPROVED", "PAID", "CANCELLED"):
                    return {"error": "Approved, paid, or cancelled payroll runs cannot be cancelled."}

                cancel_run = text("""
                    update payroll_run
                    set
                        payroll_status = 'CANCELLED',
                        cancellation_reason = :cancellation_reason,
                        updated_by = :updated_by,
                        updated_at = current_timestamp
                    where payroll_run_id_pk = :payroll_run_id
                """)
                cancel_details = text("""
                    update payroll_employee_detail
                    set
                        payroll_detail_status = 'CANCELLED',
                        payment_status = 'CANCELLED',
                        updated_by = :updated_by,
                        updated_at = current_timestamp
                    where payroll_run_id_fk = :payroll_run_id
                    and payroll_detail_status not in ('PAID', 'CANCELLED')
                """)
                release_adjustments = text("""
                    update payroll_adjustment
                    set
                        payroll_run_id_fk = null,
                        processed_flag = false,
                        processed_at = null,
                        adjustment_status = 'APPROVED',
                        updated_by = :updated_by,
                        updated_at = current_timestamp
                    where payroll_run_id_fk = :payroll_run_id
                    and processed_flag = true
                """)
                params = {
                    "payroll_run_id": payroll_run_id,
                    "cancellation_reason": payload.get("cancellation_reason"),
                    "updated_by": payload.get("updated_by") or payload.get("user_principal_name"),
                }
                conn.execute(cancel_run, params)
                conn.execute(cancel_details, params)
                conn.execute(release_adjustments, params)
                cancelled_recovery_count = self._cancel_recoveries_for_run(
                    conn,
                    payroll_run_id,
                    params.get("updated_by"),
                )

            return {
                "message": "Successfully cancelled payroll run.",
                "payroll_run_id_pk": payroll_run_id,
                "payroll_status": "CANCELLED",
                "cancelled_correction_recovery_count": cancelled_recovery_count,
            }
        except PayrollBusinessValidationError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Failed to cancel payroll run")
            return {"error": f"Failed to cancel payroll run. Error Message: {str(e)}"}

    def recalculate_payroll_run_totals(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id:
                return {"error": "payroll_run_id is required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            with self.payroll_engine.begin() as conn:
                payroll_run = self._get_payroll_run_for_update(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                )
                self.validate_run_organization(payroll_run, payroll_org_id_fk)
                if payroll_run.get("payroll_status") in ("APPROVED", "PAID", "CANCELLED"):
                    return {"error": "Approved, paid, or cancelled payroll runs cannot be recalculated."}
                totals = self._recalculate_payroll_run_totals(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                    payload.get("updated_by") or payload.get("user_principal_name"),
                )

            return {
                "message": "Successfully recalculated payroll run totals.",
                "payroll_run_id_pk": payroll_run_id,
                **totals,
            }
        except PayrollBusinessValidationError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Failed to recalculate payroll run totals")
            return {"error": f"Failed to recalculate payroll run totals. Error Message: {str(e)}"}

    def retrieve_payroll_details_for_run(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id:
                return {"error": "payroll_run_id is required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            get_details = text("""
                select
                    ped.*,
                    pr.payroll_run_type
                from payroll_employee_detail ped
                join payroll_run pr
                    on pr.payroll_run_id_pk = ped.payroll_run_id_fk
                where ped.payroll_run_id_fk = :payroll_run_id
                and pr.payroll_org_id_fk = :payroll_org_id_fk
                order by ped.payroll_employee_detail_id_pk
            """)
            with self.payroll_engine.begin() as conn:
                df_details = pd.read_sql(
                    sql=get_details,
                    con=conn,
                    params={
                        "payroll_run_id": payroll_run_id,
                        "payroll_org_id_fk": payroll_org_id_fk,
                    },
                )

            df_details = self._decorate_payroll_detail_dataframe(df_details)
            return _dataframe_to_response(df_details)
        except Exception as e:
            logger.exception("Failed to retrieve payroll details")
            df_details = pd.DataFrame()
            return df_details, {"Failed to retrieve payroll details. Error Message: ": {e}}

    def retrieve_employee_payroll_detail(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            employee_id = payload.get("payroll_employee_id_fk") or payload.get("empl_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id or not employee_id:
                return {"error": "payroll_run_id and payroll_employee_id_fk are required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            get_detail = text("""
                select
                    ped.*,
                    pr.payroll_run_type
                from payroll_employee_detail ped
                join payroll_run pr
                    on pr.payroll_run_id_pk = ped.payroll_run_id_fk
                where ped.payroll_run_id_fk = :payroll_run_id
                and ped.payroll_employee_id_fk = :employee_id
                and pr.payroll_org_id_fk = :payroll_org_id_fk
            """)
            with self.payroll_engine.begin() as conn:
                df_detail = pd.read_sql(
                    sql=get_detail,
                    con=conn,
                    params={
                        "payroll_run_id": payroll_run_id,
                        "employee_id": employee_id,
                        "payroll_org_id_fk": payroll_org_id_fk,
                    },
                )

            if df_detail.empty:
                return {"error": "Employee payroll detail not found."}

            return self._decorate_payroll_detail_record(df_detail.iloc[0].to_dict())
        except Exception as e:
            logger.exception("Failed to retrieve employee payroll detail")
            return {"error": f"Failed to retrieve employee payroll detail. Error Message: {str(e)}"}

    def update_employee_payroll_detail(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            employee_id = payload.get("payroll_employee_id_fk") or payload.get("empl_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id or not employee_id:
                return {"error": "payroll_run_id and payroll_employee_id_fk are required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            update_values = {
                column: payload.get(column)
                for column in PAYROLL_DETAIL_AMOUNT_COLUMNS
                if payload.get(column) is not None
            }
            if not update_values:
                return {"error": "At least one payroll detail amount column is required."}

            for column, value in update_values.items():
                if _decimal(value) < 0:
                    return {"error": f"{column} cannot be negative."}

            set_clause = ",\n".join([f"{column} = :{column}" for column in update_values])
            update_detail = text(f"""
                update payroll_employee_detail
                set
                    {set_clause},
                    updated_by = :updated_by,
                    updated_at = current_timestamp
                where payroll_run_id_fk = :payroll_run_id
                and payroll_employee_id_fk = :employee_id
                and exists (
                    select 1
                    from payroll_run pr
                    where pr.payroll_run_id_pk = payroll_employee_detail.payroll_run_id_fk
                    and pr.payroll_org_id_fk = :payroll_org_id_fk
                )
            """)

            with self.payroll_engine.begin() as conn:
                payroll_run = self._get_payroll_run_for_update(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                )
                self.validate_run_organization(payroll_run, payroll_org_id_fk)
                if (
                    payroll_run.get("payroll_run_type") == "CORRECTION"
                    and "penalty_deduction" in update_values
                ):
                    raise PayrollBusinessValidationError(
                        "Invalid direct edit: penalty_deduction cannot be edited directly on a CORRECTION run "
                        "because the current correction mechanism does not recalculate "
                        "the correction ledger/adjustment amounts."
                    )
                if payroll_run.get("payroll_status") not in ("DRAFT", "PROCESSED"):
                    return {"error": "Only draft or processed payroll runs can be edited."}

                params = {
                    **update_values,
                    "updated_by": payload.get("updated_by") or payload.get("user_principal_name"),
                    "payroll_run_id": payroll_run_id,
                    "employee_id": employee_id,
                    "payroll_org_id_fk": payroll_org_id_fk,
                }
                result = conn.execute(update_detail, params)
                if result.rowcount == 0:
                    return {"error": "Employee payroll detail not found."}
                totals = self._recalculate_payroll_run_totals(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                    payload.get("updated_by") or payload.get("user_principal_name"),
                )

            return {
                "message": "Successfully updated employee payroll detail.",
                "payroll_run_id_pk": payroll_run_id,
                "payroll_employee_id_fk": employee_id,
                **totals,
            }
        except PayrollBusinessValidationError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Failed to update employee payroll detail")
            return {"error": f"Failed to update employee payroll detail. Error Message: {str(e)}"}

    def create_payroll_adjustment(self, payload: dict):
        try:
            payroll_adjustment_org_id_fk = _trusted_org_id(
                payload,
                "payroll_adjustment_org_id_fk",
            )
            adjustment_type = payload.get("adjustment_type")
            earning_deduction_flag = payload.get("earning_deduction_flag")
            adjustment_amount = _decimal(payload.get("adjustment_amount"))

            if adjustment_type not in ADJUSTMENT_COLUMNS:
                return {"error": f"Unsupported adjustment_type: {adjustment_type}"}
            if adjustment_amount <= 0:
                return {"error": "adjustment_amount must be greater than zero."}
            expected_flag = "EARNING" if adjustment_type in EARNING_ADJUSTMENT_COLUMNS else "DEDUCTION"
            if earning_deduction_flag and earning_deduction_flag != expected_flag:
                return {"error": f"{adjustment_type} must use earning_deduction_flag {expected_flag}."}

            if not payroll_adjustment_org_id_fk:
                return {"error": "payroll_adjustment_org_id_fk is required."}

            insert_adjustment = text("""
                insert into payroll_adjustment (
                    payroll_adjustment_org_id_fk,
                    payroll_adjustment_empl_id_fk,
                    payroll_year,
                    payroll_month,
                    adjustment_date,
                    adjustment_type,
                    earning_deduction_flag,
                    adjustment_description,
                    adjustment_amount,
                    adjustment_status,
                    external_reference,
                    created_by,
                    updated_by,
                    field_flex_field_1,
                    field_flex_field_2,
                    field_flex_field_3,
                    field_flex_field_4
                ) values (
                    :payroll_adjustment_org_id_fk,
                    :payroll_adjustment_empl_id_fk,
                    :payroll_year,
                    :payroll_month,
                    coalesce(:adjustment_date, current_date),
                    :adjustment_type,
                    :earning_deduction_flag,
                    :adjustment_description,
                    :adjustment_amount,
                    coalesce(:adjustment_status, 'DRAFT'),
                    :external_reference,
                    :created_by,
                    :updated_by,
                    :field_flex_field_1,
                    :field_flex_field_2,
                    :field_flex_field_3,
                    :field_flex_field_4
                )
                returning payroll_adjustment_id_pk
            """)
            params = {
                "payroll_adjustment_org_id_fk": payroll_adjustment_org_id_fk,
                "payroll_adjustment_empl_id_fk": payload.get("payroll_adjustment_empl_id_fk"),
                "payroll_year": payload.get("payroll_year"),
                "payroll_month": payload.get("payroll_month"),
                "adjustment_date": payload.get("adjustment_date"),
                "adjustment_type": adjustment_type,
                "earning_deduction_flag": expected_flag,
                "adjustment_description": payload.get("adjustment_description"),
                "adjustment_amount": adjustment_amount,
                "adjustment_status": payload.get("adjustment_status"),
                "external_reference": payload.get("external_reference"),
                "created_by": payload.get("created_by") or payload.get("user_principal_name"),
                "updated_by": payload.get("updated_by") or payload.get("user_principal_name"),
                "field_flex_field_1": payload.get("field_flex_field_1"),
                "field_flex_field_2": payload.get("field_flex_field_2"),
                "field_flex_field_3": payload.get("field_flex_field_3"),
                "field_flex_field_4": payload.get("field_flex_field_4"),
            }
            required_fields = [
                "payroll_adjustment_org_id_fk",
                "payroll_adjustment_empl_id_fk",
                "payroll_year",
                "payroll_month",
            ]
            missing_fields = [field for field in required_fields if params.get(field) is None]
            if missing_fields:
                return {"error": f"Missing required fields: {', '.join(missing_fields)}"}

            with self.payroll_engine.begin() as conn:
                self._get_employee_for_update(
                    conn,
                    params["payroll_adjustment_empl_id_fk"],
                    payroll_adjustment_org_id_fk,
                )
                adjustment_id = conn.execute(insert_adjustment, params).scalar_one()

            return {
                "message": "Successfully created payroll adjustment.",
                "payroll_adjustment_id_pk": adjustment_id,
                "adjustment_status": params.get("adjustment_status") or "DRAFT",
            }
        except Exception as e:
            logger.exception("Failed to create payroll adjustment")
            return {"error": f"Failed to create payroll adjustment. Error Message: {str(e)}"}

    def get_payroll_adjustment(self, payload: dict):
        try:
            adjustment_id = (
                payload.get("payroll_adjustment_id")
                or payload.get("payroll_adjustment_id_pk")
            )
            payroll_adjustment_org_id_fk = _trusted_org_id(
                payload,
                "payroll_adjustment_org_id_fk",
            )
            if not adjustment_id:
                return {"error": "payroll_adjustment_id is required."}
            if not payroll_adjustment_org_id_fk:
                return {"error": "payroll_adjustment_org_id_fk is required."}

            get_adjustment = text("""
                select *
                from payroll_adjustment
                where payroll_adjustment_id_pk = :adjustment_id
                and payroll_adjustment_org_id_fk = :payroll_adjustment_org_id_fk
            """)
            with self.payroll_engine.begin() as conn:
                df_adjustment = pd.read_sql(
                    sql=get_adjustment,
                    con=conn,
                    params={
                        "adjustment_id": adjustment_id,
                        "payroll_adjustment_org_id_fk": payroll_adjustment_org_id_fk,
                    },
                )

            if df_adjustment.empty:
                return {"error": "Payroll adjustment not found."}
            return df_adjustment.iloc[0].to_dict()
        except Exception as e:
            logger.exception("Failed to get payroll adjustment")
            return {"error": f"Failed to get payroll adjustment. Error Message: {str(e)}"}

    def list_payroll_adjustments(self, payload: dict | None = None):
        try:
            payload = payload or {}
            payroll_adjustment_org_id_fk = _trusted_org_id(
                payload,
                "payroll_adjustment_org_id_fk",
            )
            if not payroll_adjustment_org_id_fk:
                return {"error": "payroll_adjustment_org_id_fk is required."}

            list_adjustments = text("""
                select *
                from payroll_adjustment
                where payroll_adjustment_org_id_fk = :payroll_adjustment_org_id_fk
                and (:payroll_adjustment_empl_id_fk is null
                    or payroll_adjustment_empl_id_fk = :payroll_adjustment_empl_id_fk)
                and (:payroll_year is null or payroll_year = :payroll_year)
                and (:payroll_month is null or payroll_month = :payroll_month)
                and (:adjustment_status is null or adjustment_status = :adjustment_status)
                and (:processed_flag is null or processed_flag = :processed_flag)
                order by payroll_adjustment_id_pk desc
            """)
            with self.payroll_engine.begin() as conn:
                df_adjustments = pd.read_sql(
                    sql=list_adjustments,
                    con=conn,
                    params={
                        "payroll_adjustment_org_id_fk": payload.get(
                            "payroll_adjustment_org_id_fk"
                        ) or payroll_adjustment_org_id_fk,
                        "payroll_adjustment_empl_id_fk": payload.get(
                            "payroll_adjustment_empl_id_fk"
                        ),
                        "payroll_year": payload.get("payroll_year"),
                        "payroll_month": payload.get("payroll_month"),
                        "adjustment_status": payload.get("adjustment_status"),
                        "processed_flag": payload.get("processed_flag"),
                    },
                )

            return _dataframe_to_response(df_adjustments)
        except Exception as e:
            logger.exception("Failed to list payroll adjustments")
            df_adjustments = pd.DataFrame()
            return df_adjustments, {"Failed to list payroll adjustments. Error Message: ": {e}}

    def update_payroll_adjustment(self, payload: dict):
        try:
            adjustment_id = (
                payload.get("payroll_adjustment_id")
                or payload.get("payroll_adjustment_id_pk")
            )
            payroll_adjustment_org_id_fk = _trusted_org_id(
                payload,
                "payroll_adjustment_org_id_fk",
            )
            if not adjustment_id:
                return {"error": "payroll_adjustment_id is required."}
            if not payroll_adjustment_org_id_fk:
                return {"error": "payroll_adjustment_org_id_fk is required."}

            editable_columns = [
                "payroll_year",
                "payroll_month",
                "adjustment_date",
                "adjustment_type",
                "earning_deduction_flag",
                "adjustment_description",
                "adjustment_amount",
                "external_reference",
                "field_flex_field_1",
                "field_flex_field_2",
                "field_flex_field_3",
                "field_flex_field_4",
            ]
            update_values = {
                column: payload.get(column)
                for column in editable_columns
                if payload.get(column) is not None
            }
            if not update_values:
                return {"error": "No editable payroll adjustment fields supplied."}
            if "adjustment_amount" in update_values and _decimal(update_values["adjustment_amount"]) <= 0:
                return {"error": "adjustment_amount must be greater than zero."}

            set_clause = ",\n".join([f"{column} = :{column}" for column in update_values])
            update_adjustment = text(f"""
                update payroll_adjustment
                set
                    {set_clause},
                    updated_by = :updated_by,
                    updated_at = current_timestamp
                where payroll_adjustment_id_pk = :adjustment_id
                and payroll_adjustment_org_id_fk = :payroll_adjustment_org_id_fk
                and adjustment_status = 'DRAFT'
                and processed_flag = false
            """)
            params = {
                **update_values,
                "adjustment_id": adjustment_id,
                "payroll_adjustment_org_id_fk": payroll_adjustment_org_id_fk,
                "updated_by": payload.get("updated_by") or payload.get("user_principal_name"),
            }
            with self.payroll_engine.begin() as conn:
                result = conn.execute(update_adjustment, params)

            if result.rowcount == 0:
                return {"error": "Payroll adjustment not found or not editable."}
            return {"message": "Successfully updated payroll adjustment."}
        except Exception as e:
            logger.exception("Failed to update payroll adjustment")
            return {"error": f"Failed to update payroll adjustment. Error Message: {str(e)}"}

    def approve_payroll_adjustment(self, payload: dict):
        return self._set_payroll_adjustment_status(payload, "APPROVED")

    def reject_payroll_adjustment(self, payload: dict):
        return self._set_payroll_adjustment_status(payload, "REJECTED")

    def cancel_payroll_adjustment(self, payload: dict):
        return self._set_payroll_adjustment_status(payload, "CANCELLED")

    def pending_approved_adjustments(self, payload: dict):
        payload = {
            **payload,
            "adjustment_status": "APPROVED",
            "processed_flag": False,
        }
        return self.list_payroll_adjustments(payload)

    def aggregate_adjustments_by_type(self, adjustments: list[dict]):
        adjustment_totals = {column: Decimal("0") for column in ADJUSTMENT_COLUMNS.values()}
        adjustment_ids = []
        for adjustment in adjustments:
            adjustment_type = adjustment.get("adjustment_type")
            adjustment_column = ADJUSTMENT_COLUMNS.get(adjustment_type)
            if adjustment_column:
                adjustment_totals[adjustment_column] += _decimal(adjustment.get("adjustment_amount"))
                adjustment_ids.append(adjustment.get("payroll_adjustment_id_pk"))
        return adjustment_totals, adjustment_ids

    def map_adjustments_to_payroll_detail(self, adjustments: list[dict]):
        adjustment_totals, adjustment_ids = self.aggregate_adjustments_by_type(adjustments)
        return adjustment_totals, adjustment_ids

    def _sum_detail_amounts(self, detail_payload: dict, columns: list[str]) -> Decimal:
        return sum(
            (_decimal(detail_payload.get(column)) for column in columns),
            Decimal("0"),
        )

    def _calculate_detail_gross_salary(self, detail_payload: dict) -> Decimal:
        return self._sum_detail_amounts(
            detail_payload,
            FIXED_PAYROLL_DETAIL_AMOUNT_COLUMNS + EARNING_PAYROLL_DETAIL_AMOUNT_COLUMNS,
        )

    def _calculate_detail_total_deduction(self, detail_payload: dict) -> Decimal:
        return self._sum_detail_amounts(
            detail_payload,
            DEDUCTION_PAYROLL_DETAIL_AMOUNT_COLUMNS,
        )

    def _calculate_detail_correction_recovery(self, detail_payload: dict) -> Decimal:
        return _decimal(detail_payload.get("correction_recovery_amount"))

    def _calculate_detail_correction_net(self, detail_payload: dict) -> Decimal:
        return _decimal(detail_payload.get("correction_net_amount"))

    def _calculate_detail_net_salary(self, detail_payload: dict) -> Decimal:
        correction_net_amount = self._calculate_detail_correction_net(detail_payload)
        correction_recovery_amount = self._calculate_detail_correction_recovery(
            detail_payload
        )
        if correction_net_amount != 0 or correction_recovery_amount != 0:
            return max(correction_net_amount, Decimal("0"))
        return (
            self._calculate_detail_gross_salary(detail_payload)
            - self._calculate_detail_total_deduction(detail_payload)
        )

    def _decorate_payroll_run_totals(self, totals: dict) -> dict:
        return totals

    def _decorate_payroll_run_record(self, row: dict) -> dict:
        return row

    def _decorate_payroll_run_dataframe(self, df_value: pd.DataFrame) -> pd.DataFrame:
        return df_value

    def _decorate_payroll_detail_record(self, row: dict) -> dict:
        return row

    def _decorate_payroll_detail_dataframe(self, df_value: pd.DataFrame) -> pd.DataFrame:
        return df_value

    def get_latest_workflow_status(self, payroll_run_id: int):
        get_latest_workflow = text("""
            select workflow_status
            from payroll_workflow_requests
            where payroll_run_id_fk = :payroll_run_id
            order by workflow_request_id_pk desc
            limit 1
        """)
        try:
            with self.payroll_engine.begin() as conn:
                df_workflow = pd.read_sql(
                    sql=get_latest_workflow,
                    con=conn,
                    params={"payroll_run_id": payroll_run_id},
                )
        except Exception:
            return None

        if df_workflow.empty:
            return None
        return df_workflow.iloc[0]["workflow_status"]

    def _set_payroll_adjustment_status(self, payload: dict, adjustment_status: str):
        try:
            adjustment_id = (
                payload.get("payroll_adjustment_id")
                or payload.get("payroll_adjustment_id_pk")
            )
            payroll_adjustment_org_id_fk = _trusted_org_id(
                payload,
                "payroll_adjustment_org_id_fk",
            )
            if not adjustment_id:
                return {"error": "payroll_adjustment_id is required."}
            if not payroll_adjustment_org_id_fk:
                return {"error": "payroll_adjustment_org_id_fk is required."}

            update_status = text("""
                update payroll_adjustment
                set
                    adjustment_status = :adjustment_status,
                    approved_by = case
                        when :adjustment_status = 'APPROVED' then :updated_by
                        else approved_by
                    end,
                    approved_at = case
                        when :adjustment_status = 'APPROVED' then current_timestamp
                        else approved_at
                    end,
                    rejected_by = case
                        when :adjustment_status = 'REJECTED' then :updated_by
                        else rejected_by
                    end,
                    rejected_at = case
                        when :adjustment_status = 'REJECTED' then current_timestamp
                        else rejected_at
                    end,
                    rejection_reason = case
                        when :adjustment_status = 'REJECTED' then :rejection_reason
                        else rejection_reason
                    end,
                    updated_by = :updated_by,
                    updated_at = current_timestamp
                where payroll_adjustment_id_pk = :adjustment_id
                and payroll_adjustment_org_id_fk = :payroll_adjustment_org_id_fk
                and processed_flag = false
                and adjustment_status in ('DRAFT', 'APPROVED')
            """)
            with self.payroll_engine.begin() as conn:
                result = conn.execute(
                    update_status,
                    {
                        "adjustment_id": adjustment_id,
                        "payroll_adjustment_org_id_fk": payroll_adjustment_org_id_fk,
                        "adjustment_status": adjustment_status,
                        "updated_by": payload.get("updated_by") or payload.get("user_principal_name"),
                        "rejection_reason": payload.get("rejection_reason"),
                    },
                )

            if result.rowcount == 0:
                return {"error": "Payroll adjustment not found, processed, or not editable."}
            return {
                "message": f"Successfully updated payroll adjustment status to {adjustment_status}.",
                "adjustment_status": adjustment_status,
            }
        except Exception as e:
            logger.exception("Failed to update payroll adjustment status")
            return {"error": f"Failed to update payroll adjustment status. Error Message: {str(e)}"}

    def _get_payroll_run_dataframe(
        self,
        payroll_run_id: int,
        payroll_org_id_fk: int | None = None,
    ):
        get_payroll_run_query = text("""
            select *
            from payroll_run
            where payroll_run_id_pk = :payroll_run_id
            and (:payroll_org_id_fk is null or payroll_org_id_fk = :payroll_org_id_fk)
        """)
        with self.payroll_engine.begin() as conn:
            return pd.read_sql(
                sql=get_payroll_run_query,
                con=conn,
                params={
                    "payroll_run_id": payroll_run_id,
                    "payroll_org_id_fk": payroll_org_id_fk,
                },
            )

    def _get_payroll_run_for_update(
        self,
        conn,
        payroll_run_id: int,
        payroll_org_id_fk: int | None = None,
    ):
        get_payroll_run = text("""
            select *
            from payroll_run
            where payroll_run_id_pk = :payroll_run_id
            and (:payroll_org_id_fk is null or payroll_org_id_fk = :payroll_org_id_fk)
            for update
        """)
        row = conn.execute(
            get_payroll_run,
            {
                "payroll_run_id": payroll_run_id,
                "payroll_org_id_fk": payroll_org_id_fk,
            }
        ).mappings().first()
        if not row:
            raise PayrollBusinessValidationError("Payroll run not found.")
        return self._decorate_payroll_run_totals(dict(row))

    def _get_employee_for_update(self, conn, employee_id: int, payroll_org_id_fk: int):
        get_employee = text("""
            select
                empl_id_pk,
                empl_org_id_fk,
                employee_id,
                employee_name,
                employee_designation,
                monthly_basic_salary,
                monthly_allowance,
                monthly_accomodation,
                bank_name,
                bank_account_number
            from employee_master
            where empl_id_pk = :employee_id
            and empl_org_id_fk = :payroll_org_id_fk
            for share
        """)
        row = conn.execute(
            get_employee,
            {"employee_id": employee_id, "payroll_org_id_fk": payroll_org_id_fk},
        ).mappings().first()
        if not row:
            raise PayrollBusinessValidationError("Employee not found for payroll organization.")
        return dict(row)

    def _validate_run_for_processing(self, payroll_run: dict):
        if payroll_run.get("payroll_status") in ("CANCELLED", "PAID", "APPROVED"):
            raise PayrollBusinessValidationError(
                "Approved, paid, or cancelled payroll runs cannot be processed."
            )

    def _validate_employee_compensation(self, employee: dict, include_fixed_salary: bool):
        if not include_fixed_salary:
            return
        for column in ["monthly_basic_salary", "monthly_allowance", "monthly_accomodation"]:
            if _decimal(employee.get(column)) < 0:
                raise PayrollBusinessValidationError(f"Employee {column} cannot be negative.")

    def _prepare_employee_snapshot(self, employee: dict, include_fixed_salary: bool):
        snapshot = {
            "payroll_employee_id_fk": employee.get("empl_id_pk"),
            "employee_id_snapshot": employee.get("employee_id"),
            "employee_name_snapshot": employee.get("employee_name"),
            "employee_designation_snapshot": employee.get("employee_designation"),
            "basic_salary": Decimal("0"),
            "monthly_allowance": Decimal("0"),
            "accommodation_allowance": Decimal("0"),
            "bank_name_snapshot": employee.get("bank_name"),
            "bank_account_number_snapshot": employee.get("bank_account_number"),
            "iban_number_snapshot": None,
        }
        if include_fixed_salary:
            snapshot["basic_salary"] = _decimal(employee.get("monthly_basic_salary"))
            snapshot["monthly_allowance"] = _decimal(employee.get("monthly_allowance"))
            snapshot["accommodation_allowance"] = _decimal(employee.get("monthly_accomodation"))
        return snapshot

    def _retrieve_approved_unprocessed_adjustments(
        self,
        conn,
        payroll_run: dict,
        employee_id: int,
        adjustment_ids: list[int] | None = None,
    ):
        adjustment_filter = ""
        params = {
            "payroll_org_id_fk": payroll_run.get("payroll_org_id_fk"),
            "employee_id": employee_id,
            "payroll_year": payroll_run.get("payroll_year"),
            "payroll_month": payroll_run.get("payroll_month"),
        }
        if adjustment_ids:
            adjustment_filter = "and payroll_adjustment_id_pk in :adjustment_ids"
            params["adjustment_ids"] = adjustment_ids

        get_adjustments = text(f"""
            select *
            from payroll_adjustment
            where payroll_adjustment_org_id_fk = :payroll_org_id_fk
            and payroll_adjustment_empl_id_fk = :employee_id
            and payroll_year = :payroll_year
            and payroll_month = :payroll_month
            and adjustment_status = 'APPROVED'
            and processed_flag = false
            {adjustment_filter}
            order by payroll_adjustment_id_pk
            for update
        """)
        if adjustment_ids:
            get_adjustments = get_adjustments.bindparams(
                bindparam("adjustment_ids", expanding=True)
            )
        return [dict(row) for row in conn.execute(get_adjustments, params).mappings().all()]

    def _check_employee_already_exists(self, conn, payroll_run_id: int, employee_id: int):
        check_detail = text("""
            select payroll_employee_detail_id_pk
            from payroll_employee_detail
            where payroll_run_id_fk = :payroll_run_id
            and payroll_employee_id_fk = :employee_id
        """)
        row = conn.execute(
            check_detail,
            {"payroll_run_id": payroll_run_id, "employee_id": employee_id},
        ).first()
        return row is not None

    def _create_payroll_employee_detail(self, conn, detail_payload: dict):
        # Preserve supplied values; normalize absent/null only for this insert.
        detail_payload = {
            **detail_payload,
            "penalty_deduction": _decimal(detail_payload.get("penalty_deduction")),
        }
        insert_columns = ", ".join(PAYROLL_DETAIL_INSERT_COLUMNS)
        insert_values = ", ".join([f":{column}" for column in PAYROLL_DETAIL_INSERT_COLUMNS])
        insert_detail = text(f"""
            insert into payroll_employee_detail ({insert_columns})
            values ({insert_values})
            returning
                payroll_employee_detail_id_pk,
                gross_salary,
                total_deduction,
                correction_net_amount,
                correction_recovery_amount,
                net_salary
        """)
        row = conn.execute(insert_detail, detail_payload).mappings().one()
        return dict(row)

    def _mark_adjustments_as_processed(self, conn, adjustment_ids: list[int], payroll_run_id: int, updated_by: str | None):
        if not adjustment_ids:
            return 0

        mark_adjustments = text("""
            update payroll_adjustment
            set
                payroll_run_id_fk = :payroll_run_id,
                processed_flag = true,
                processed_at = current_timestamp,
                adjustment_status = 'PROCESSED',
                updated_by = :updated_by,
                updated_at = current_timestamp
            where payroll_adjustment_id_pk in :adjustment_ids
            and adjustment_status = 'APPROVED'
            and processed_flag = false
        """).bindparams(bindparam("adjustment_ids", expanding=True))
        result = conn.execute(
            mark_adjustments,
            {
                "payroll_run_id": payroll_run_id,
                "adjustment_ids": adjustment_ids,
                "updated_by": updated_by,
            },
        )
        if result.rowcount != len(adjustment_ids):
            raise PayrollBusinessValidationError(
                "One or more payroll adjustments were already processed or are no longer approved."
            )
        return result.rowcount

    def _update_payroll_detail_status(self, conn, payroll_run_id: int, payroll_detail_status: str, updated_by: str | None):
        update_details = text("""
            update payroll_employee_detail
            set
                payroll_detail_status = :payroll_detail_status,
                payment_status = case
                    when :payroll_detail_status = 'APPROVED' then 'READY'
                    when :payroll_detail_status = 'CANCELLED' then 'CANCELLED'
                    else payment_status
                end,
                updated_by = :updated_by,
                updated_at = current_timestamp
            where payroll_run_id_fk = :payroll_run_id
        """)
        conn.execute(
            update_details,
            {
                "payroll_run_id": payroll_run_id,
                "payroll_detail_status": payroll_detail_status,
                "updated_by": updated_by,
            },
        )

    def _recalculate_payroll_run_totals(
        self,
        conn,
        payroll_run_id: int,
        payroll_org_id_fk: int | None = None,
        updated_by: str | None = None,
    ):
        update_totals = text("""
            update payroll_run
            set
                total_employee_count = totals.total_employee_count,
                total_gross_salary = totals.total_gross_salary,
                total_deductions = totals.total_deductions,
                total_correction_net_amount = totals.total_correction_net_amount,
                total_correction_recovery = totals.total_correction_recovery,
                total_net_salary = totals.total_payable_amount,
                updated_by = coalesce(:updated_by, payroll_run.updated_by),
                updated_at = current_timestamp
            from (
                select
                    count(*)::integer as total_employee_count,
                    coalesce(sum(gross_salary), 0)::numeric(18,2) as total_gross_salary,
                    coalesce(sum(total_deduction), 0)::numeric(18,2) as total_deductions,
                    coalesce(sum(correction_net_amount), 0)::numeric(18,2) as total_correction_net_amount,
                    coalesce(sum(correction_recovery_amount), 0)::numeric(18,2) as total_correction_recovery,
                    coalesce(sum(net_salary), 0)::numeric(18,2) as total_payable_amount
                from payroll_employee_detail
                where payroll_run_id_fk = :payroll_run_id
            ) totals
            where payroll_run.payroll_run_id_pk = :payroll_run_id
            and (:payroll_org_id_fk is null or payroll_run.payroll_org_id_fk = :payroll_org_id_fk)
            returning
                payroll_run.total_employee_count,
                payroll_run.total_gross_salary,
                payroll_run.total_deductions,
                payroll_run.total_correction_net_amount,
                payroll_run.total_correction_recovery,
                payroll_run.total_net_salary,
                payroll_run.payroll_run_type
        """)
        row = conn.execute(
            update_totals,
            {
                "payroll_run_id": payroll_run_id,
                "payroll_org_id_fk": payroll_org_id_fk,
                "updated_by": updated_by,
            },
        ).mappings().one()
        return self._decorate_payroll_run_totals(dict(row))

    def _build_detail_payload(
        self,
        payroll_run: dict,
        employee: dict,
        adjustments: list[dict],
        include_fixed_salary: bool,
        payload: dict,
    ):
        self._validate_employee_compensation(employee, include_fixed_salary)
        snapshot = self._prepare_employee_snapshot(employee, include_fixed_salary)
        adjustment_totals, adjustment_ids = self.map_adjustments_to_payroll_detail(adjustments)
        detail_payload = {
            "payroll_run_id_fk": payroll_run.get("payroll_run_id_pk"),
            **snapshot,
            **adjustment_totals,
            "penalty_deduction": Decimal("0"),
            "correction_net_amount": Decimal("0"),
            "correction_recovery_amount": Decimal("0"),
            "total_period_days": payload.get("total_period_days"),
            "payable_days": payload.get("payable_days"),
            "unpaid_leave_days": payload.get("unpaid_leave_days", 0),
            "payment_method": payload.get("payment_method", "BANK_TRANSFER"),
            "payment_status": payload.get("payment_status", "NOT_PROCESSED"),
            "payroll_detail_status": "PROCESSED",
            "exception_flag": payload.get("exception_flag", False),
            "exception_description": payload.get("exception_description"),
            "remarks": payload.get("remarks"),
            "created_by": payload.get("created_by") or payload.get("user_principal_name"),
            "updated_by": payload.get("updated_by") or payload.get("user_principal_name"),
            "field_flex_field_1": payload.get("field_flex_field_1"),
            "field_flex_field_2": payload.get("field_flex_field_2"),
            "field_flex_field_3": payload.get("field_flex_field_3"),
            "field_flex_field_4": payload.get("field_flex_field_4"),
        }
        return detail_payload, adjustment_ids

    def _process_employee(
        self,
        conn,
        payroll_run: dict,
        employee_id: int,
        payload: dict,
        include_fixed_salary: bool,
        adjustment_ids: list[int] | None = None,
    ):
        if self._check_employee_already_exists(conn, payroll_run.get("payroll_run_id_pk"), employee_id):
            raise PayrollBusinessValidationError(
                f"Employee already exists in payroll run: {employee_id}"
            )

        employee = self._get_employee_for_update(
            conn,
            employee_id=employee_id,
            payroll_org_id_fk=payroll_run.get("payroll_org_id_fk"),
        )
        adjustments = self._retrieve_approved_unprocessed_adjustments(
            conn,
            payroll_run=payroll_run,
            employee_id=employee_id,
            adjustment_ids=adjustment_ids,
        )
        detail_payload, consumed_adjustment_ids = self._build_detail_payload(
            payroll_run=payroll_run,
            employee=employee,
            adjustments=adjustments,
            include_fixed_salary=include_fixed_salary,
            payload=payload,
        )
        detail_result = self._create_payroll_employee_detail(conn, detail_payload)
        processed_adjustment_count = self._mark_adjustments_as_processed(
            conn,
            consumed_adjustment_ids,
            payroll_run.get("payroll_run_id_pk"),
            payload.get("updated_by") or payload.get("user_principal_name"),
        )
        return {
            **detail_result,
            "payroll_employee_id_fk": employee_id,
            "processed_adjustment_count": processed_adjustment_count,
        }

    @classmethod
    def allowed_run_types(cls):
        return {cls.payroll_run_type}


class MonthlyPayrollRun(_PayrollRunBase):
    payroll_run_type = "MONTHLY"

    def retrieve_eligible_employees(self, conn, payroll_org_id_fk: int):
        get_employees = text("""
            select
                empl_id_pk
            from employee_master
            where empl_org_id_fk = :payroll_org_id_fk
            order by empl_id_pk
        """)
        return [
            row[0]
            for row in conn.execute(
                get_employees,
                {"payroll_org_id_fk": payroll_org_id_fk},
            ).all()
        ]

    def process_employee(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            employee_id = payload.get("payroll_employee_id_fk") or payload.get("empl_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id or not employee_id:
                return {"error": "payroll_run_id and payroll_employee_id_fk are required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            with self.payroll_engine.begin() as conn:
                payroll_run = self._get_payroll_run_for_update(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                )
                self._validate_run_for_processing(payroll_run)
                if payroll_run.get("payroll_run_type") != "MONTHLY":
                    return {"error": "Payroll run is not a monthly payroll run."}
                self.validate_run_organization(payroll_run, payroll_org_id_fk)
                employee_result = self._process_employee(
                    conn,
                    payroll_run=payroll_run,
                    employee_id=employee_id,
                    payload=payload,
                    include_fixed_salary=True,
                )
                totals = self._recalculate_payroll_run_totals(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                    payload.get("updated_by") or payload.get("user_principal_name"),
                )

            return {
                "message": "Successfully processed employee payroll.",
                **employee_result,
                **totals,
            }
        except PayrollBusinessValidationError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Failed to process monthly employee payroll")
            return {"error": f"Failed to process monthly employee payroll. Error Message: {str(e)}"}

    def process_payroll_run(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_run_id:
                return {"error": "payroll_run_id is required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            processed_employees = []
            with self.payroll_engine.begin() as conn:
                payroll_run = self._get_payroll_run_for_update(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                )
                self._validate_run_for_processing(payroll_run)
                if payroll_run.get("payroll_run_type") != "MONTHLY":
                    return {"error": "Payroll run is not a monthly payroll run."}
                self.validate_run_organization(payroll_run, payroll_org_id_fk)

                employee_ids = payload.get("employee_ids") or self.retrieve_eligible_employees(
                    conn,
                    payroll_run.get("payroll_org_id_fk"),
                )
                if not employee_ids:
                    return {"error": "No eligible employees found for payroll run."}

                for employee_id in employee_ids:
                    processed_employees.append(
                        self._process_employee(
                            conn,
                            payroll_run=payroll_run,
                            employee_id=employee_id,
                            payload=payload,
                            include_fixed_salary=True,
                        )
                    )

                totals = self._recalculate_payroll_run_totals(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                    payload.get("updated_by") or payload.get("user_principal_name"),
                )
                self.update_payroll_run_status(
                    conn,
                    payroll_run_id,
                    "PROCESSED",
                    payload.get("processed_by") or payload.get("user_principal_name"),
                )

            return {
                "message": "Successfully processed monthly payroll run.",
                "payroll_run_id_pk": payroll_run_id,
                "payroll_status": "PROCESSED",
                "processed_employee_count": len(processed_employees),
                "processed_employees": processed_employees,
                **totals,
            }
        except PayrollBusinessValidationError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Failed to process monthly payroll run")
            return {"error": f"Failed to process monthly payroll run. Error Message: {str(e)}"}


class OffCyclePayrollRun(_PayrollRunBase):
    payroll_run_type = "ONE_TIME"

    @classmethod
    def allowed_run_types(cls):
        return {"ONE_TIME", "CORRECTION", "FINAL_SETTLEMENT"}

    def _decorate_payroll_run_totals(self, totals: dict) -> dict:
        result = dict(totals)
        payable_amount = _decimal(
            result.get("total_payable_amount", result.get("total_net_salary"))
        )
        recoverable_amount = _decimal(result.get("total_correction_recovery"))
        correction_net_amount = _decimal(result.get("total_correction_net_amount"))
        result["total_payable_amount"] = payable_amount
        result["total_recoverable_amount"] = recoverable_amount
        result["net_correction_amount"] = correction_net_amount
        result["display_total_net_salary"] = payable_amount
        if result.get("payroll_run_type") == "CORRECTION":
            result["display_total_net_salary"] = correction_net_amount
            result["total_net_salary"] = correction_net_amount
        return result

    def _decorate_payroll_run_record(self, row: dict) -> dict:
        return self._decorate_payroll_run_totals(row)

    def _decorate_payroll_run_dataframe(self, df_value: pd.DataFrame) -> pd.DataFrame:
        if df_value.empty:
            return df_value

        df_result = df_value.copy()
        if "total_payable_amount" not in df_result.columns and "total_net_salary" in df_result.columns:
            df_result["total_payable_amount"] = df_result["total_net_salary"]
        if "total_net_salary" in df_result.columns:
            df_result["display_total_net_salary"] = df_result["total_net_salary"]
        if "total_correction_recovery" in df_result.columns:
            df_result["total_recoverable_amount"] = df_result[
                "total_correction_recovery"
            ]
        if "total_correction_net_amount" in df_result.columns:
            df_result["net_correction_amount"] = df_result[
                "total_correction_net_amount"
            ]
        if (
            "payroll_run_type" in df_result.columns
            and "total_correction_net_amount" in df_result.columns
            and "total_net_salary" in df_result.columns
        ):
            correction_rows = df_result["payroll_run_type"] == "CORRECTION"
            df_result.loc[correction_rows, "display_total_net_salary"] = (
                df_result.loc[correction_rows, "total_correction_net_amount"]
            )
            df_result.loc[correction_rows, "total_net_salary"] = (
                df_result.loc[correction_rows, "total_correction_net_amount"]
            )
        return df_result

    def _decorate_payroll_detail_record(self, row: dict) -> dict:
        result = dict(row)
        payable_amount = _decimal(result.get("payable_amount", result.get("net_salary")))
        recoverable_amount = _decimal(result.get("correction_recovery_amount"))
        correction_net_amount = _decimal(result.get("correction_net_amount"))
        result["payable_amount"] = payable_amount
        result["recoverable_amount"] = recoverable_amount
        result["net_correction_amount"] = correction_net_amount
        result["display_net_salary"] = payable_amount
        if result.get("payroll_run_type") == "CORRECTION":
            result["correction_earning_amount"] = _decimal(
                result.get("gross_salary")
            )
            result["correction_deduction_amount"] = _decimal(
                result.get("total_deduction")
            )
            result["display_net_salary"] = correction_net_amount
        return result

    def _decorate_payroll_detail_dataframe(self, df_value: pd.DataFrame) -> pd.DataFrame:
        if df_value.empty:
            return df_value

        df_result = df_value.copy()
        if "payable_amount" not in df_result.columns and "net_salary" in df_result.columns:
            df_result["payable_amount"] = df_result["net_salary"]
        if "net_salary" in df_result.columns:
            df_result["display_net_salary"] = df_result["net_salary"]
        if "correction_recovery_amount" in df_result.columns:
            df_result["recoverable_amount"] = df_result["correction_recovery_amount"]
        if "correction_net_amount" in df_result.columns:
            df_result["net_correction_amount"] = df_result["correction_net_amount"]
        if "payroll_run_type" in df_result.columns:
            correction_rows = df_result["payroll_run_type"] == "CORRECTION"
            if "gross_salary" in df_result.columns:
                df_result.loc[correction_rows, "correction_earning_amount"] = (
                    df_result.loc[correction_rows, "gross_salary"]
                )
            if "total_deduction" in df_result.columns:
                df_result.loc[correction_rows, "correction_deduction_amount"] = (
                    df_result.loc[correction_rows, "total_deduction"]
                )
            if "correction_net_amount" in df_result.columns:
                df_result.loc[correction_rows, "display_net_salary"] = (
                    df_result.loc[correction_rows, "correction_net_amount"]
                )
        return df_result

    def _cancel_recoveries_for_run(self, conn, payroll_run_id: int, updated_by: str | None):
        cancel_recoveries = text("""
            update payroll_correction_recovery
            set
                recovery_status = 'CANCELLED',
                updated_by = :updated_by,
                updated_at = current_timestamp
            where correction_payroll_run_id_fk = :payroll_run_id
            and recovery_status = 'APPLIED'
        """)
        result = conn.execute(
            cancel_recoveries,
            {
                "payroll_run_id": payroll_run_id,
                "updated_by": updated_by,
            },
        )
        return result.rowcount

    def _int_payload_value(self, value, field_name: str) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise PayrollBusinessValidationError(
                f"{field_name} must be an integer."
            ) from exc

    def _employee_mapping_value(
        self,
        payload: dict,
        field_names: list[str],
        employee_id: int,
    ) -> int | None:
        for field_name in field_names:
            mapping = payload.get(field_name)
            if not isinstance(mapping, dict):
                continue

            value = mapping.get(str(employee_id))
            if value is None:
                value = mapping.get(employee_id)
            if value is not None and value != "":
                return self._int_payload_value(value, field_name)
        return None

    def _first_int_payload_value(self, payload: dict, field_names: list[str]) -> int | None:
        for field_name in field_names:
            value = payload.get(field_name)
            if value is not None and value != "":
                return self._int_payload_value(value, field_name)
        return None

    def _correction_source_selector(
        self,
        payroll_run: dict,
        payload: dict,
        employee_id: int,
    ):
        source_detail_id = self._employee_mapping_value(
            payload,
            [
                "source_payroll_detail_ids_by_employee",
                "source_payroll_employee_detail_ids_by_employee",
            ],
            employee_id,
        ) or self._first_int_payload_value(
            payload,
            [
                "source_payroll_employee_detail_id",
                "source_payroll_employee_detail_id_fk",
                "source_payroll_detail_id",
            ],
        )
        if source_detail_id:
            return {
                "mode": "DETAIL",
                "source_payroll_employee_detail_id": source_detail_id,
            }

        source_run_id = self._employee_mapping_value(
            payload,
            [
                "source_payroll_run_ids_by_employee",
                "correction_source_payroll_run_ids_by_employee",
            ],
            employee_id,
        ) or self._first_int_payload_value(
            payload,
            [
                "source_payroll_run_id",
                "source_payroll_run_id_fk",
                "correction_source_payroll_run_id",
            ],
        )
        if source_run_id:
            return {
                "mode": "RUN",
                "source_payroll_run_id": source_run_id,
            }

        required_header_fields = [
            "payroll_year",
            "payroll_month",
            "payroll_period_start_date",
            "payroll_period_end_date",
        ]
        missing_fields = [
            field for field in required_header_fields if payroll_run.get(field) is None
        ]
        if missing_fields:
            raise PayrollBusinessValidationError(
                "Correction payroll run header is missing required period fields: "
                f"{', '.join(missing_fields)}."
            )

        return {
            "mode": "HEADER_PERIOD",
            "source_payroll_year": payroll_run.get("payroll_year"),
            "source_payroll_month": payroll_run.get("payroll_month"),
            "source_payroll_period_start_date": payroll_run.get(
                "payroll_period_start_date"
            ),
            "source_payroll_period_end_date": payroll_run.get(
                "payroll_period_end_date"
            ),
        }

    def _resolve_correction_source_detail(
        self,
        conn,
        payroll_run: dict,
        employee_id: int,
        payload: dict,
    ):
        selector = self._correction_source_selector(payroll_run, payload, employee_id)
        params = {
            "payroll_org_id_fk": payroll_run.get("payroll_org_id_fk"),
            "current_payroll_run_id": payroll_run.get("payroll_run_id_pk"),
            "employee_id": employee_id,
            "source_payroll_employee_detail_id": selector.get(
                "source_payroll_employee_detail_id"
            ),
            "source_payroll_run_id": selector.get("source_payroll_run_id"),
            "source_payroll_year": selector.get("source_payroll_year"),
            "source_payroll_month": selector.get("source_payroll_month"),
            "source_payroll_period_start_date": selector.get(
                "source_payroll_period_start_date"
            ),
            "source_payroll_period_end_date": selector.get(
                "source_payroll_period_end_date"
            ),
        }

        if selector["mode"] == "DETAIL":
            source_filter = "and ped.payroll_employee_detail_id_pk = :source_payroll_employee_detail_id"
        elif selector["mode"] == "RUN":
            source_filter = "and pr.payroll_run_id_pk = :source_payroll_run_id"
        else:
            source_filter = """
                and pr.payroll_year = :source_payroll_year
                and pr.payroll_month = :source_payroll_month
                and pr.payroll_period_start_date = :source_payroll_period_start_date
                and pr.payroll_period_end_date = :source_payroll_period_end_date
            """

        get_source_detail = text(f"""
            select
                ped.payroll_employee_detail_id_pk as source_payroll_employee_detail_id_fk,
                ped.payroll_run_id_fk as source_payroll_run_id_fk,
                ped.net_salary as source_net_salary,
                pr.payroll_run_type as source_payroll_run_type,
                pr.payroll_year as source_payroll_year,
                pr.payroll_month as source_payroll_month,
                pr.payroll_period_start_date as source_payroll_period_start_date,
                pr.payroll_period_end_date as source_payroll_period_end_date
            from payroll_employee_detail ped
            join payroll_run pr
                on pr.payroll_run_id_pk = ped.payroll_run_id_fk
            where pr.payroll_org_id_fk = :payroll_org_id_fk
            and pr.payroll_run_id_pk <> :current_payroll_run_id
            and pr.payroll_run_type = 'MONTHLY'
            and ped.payroll_employee_id_fk = :employee_id
            and pr.payroll_status in ('PROCESSED', 'APPROVED', 'PAID')
            and ped.payroll_detail_status in ('PROCESSED', 'APPROVED', 'PAID')
            {source_filter}
            order by pr.payroll_run_id_pk, ped.payroll_employee_detail_id_pk
            for update of ped
        """)
        rows = [
            dict(row)
            for row in conn.execute(get_source_detail, params).mappings().all()
        ]
        if not rows:
            if selector["mode"] == "HEADER_PERIOD":
                raise PayrollBusinessValidationError(
                    "No processed monthly payroll found for the correction "
                    f"period for employee {employee_id}."
                )
            raise PayrollBusinessValidationError(
                "Correction source payroll detail not found for employee "
                f"{employee_id}."
            )
        if len(rows) > 1:
            raise PayrollBusinessValidationError(
                "Correction source payroll is ambiguous for employee "
                f"{employee_id}; source_payroll_run_id or "
                "source_payroll_employee_detail_id is required."
            )
        return rows[0]

    def _get_source_recovered_amount(self, conn, source_payroll_employee_detail_id: int):
        get_recovered = text("""
            select coalesce(sum(recovered_amount), 0) as recovered_amount
            from payroll_correction_recovery
            where source_payroll_employee_detail_id_fk = :source_payroll_employee_detail_id
            and recovery_status = 'APPLIED'
        """)
        row = conn.execute(
            get_recovered,
            {
                "source_payroll_employee_detail_id": source_payroll_employee_detail_id,
            },
        ).mappings().one()
        return _decimal(row.get("recovered_amount"))

    def _available_source_recovery_balance(self, conn, source_detail: dict):
        recovered_amount = self._get_source_recovered_amount(
            conn,
            source_detail.get("source_payroll_employee_detail_id_fk"),
        )
        return max(
            _decimal(source_detail.get("source_net_salary")) - recovered_amount,
            Decimal("0"),
        )

    def _append_correction_recovery_remark(
        self,
        existing_remarks: str | None,
        recovered_amount: Decimal,
        source_detail: dict,
    ) -> str:
        recovery_note = (
            "Recovered AED "
            f"{recovered_amount.quantize(Decimal('0.01'))} "
            "against source payroll detail "
            f"{source_detail.get('source_payroll_employee_detail_id_fk')}."
        )
        if existing_remarks:
            return f"{existing_remarks} | {recovery_note}"
        return recovery_note

    def _allocate_correction_recovery(
        self,
        detail_payload: dict,
        adjustments: list[dict],
        recovery_amount: Decimal,
    ):
        recovery_entries = []
        remaining_recovery = recovery_amount
        deduction_adjustments = [
            adjustment
            for adjustment in adjustments
            if adjustment.get("adjustment_type") in DEDUCTION_ADJUSTMENT_COLUMNS
        ]

        for adjustment in deduction_adjustments:
            if remaining_recovery <= 0:
                break

            adjustment_type = adjustment.get("adjustment_type")
            adjustment_amount = _decimal(adjustment.get("adjustment_amount"))
            recovered_amount = min(adjustment_amount, remaining_recovery)

            adjustment_id = adjustment.get("payroll_adjustment_id_pk")
            if adjustment_id is None:
                raise PayrollBusinessValidationError(
                    "Recovered correction deduction is missing payroll_adjustment_id_pk."
                )

            recovery_entries.append(
                {
                    "payroll_adjustment_id_fk": adjustment_id,
                    "adjustment_type": adjustment_type,
                    "adjustment_amount": adjustment_amount,
                    "recovered_amount": recovered_amount,
                }
            )
            remaining_recovery -= recovered_amount

        if remaining_recovery > 0:
            raise PayrollBusinessValidationError(
                "Correction recovery could not be allocated to deduction adjustments."
            )
        return recovery_entries

    def _prepare_correction_recovery(
        self,
        conn,
        payroll_run: dict,
        employee_id: int,
        payload: dict,
        adjustments: list[dict],
        detail_payload: dict,
    ):
        gross_salary = self._calculate_detail_gross_salary(detail_payload)
        total_deduction = self._calculate_detail_total_deduction(detail_payload)

        if payroll_run.get("payroll_run_type") == "CORRECTION":
            correction_net_amount = gross_salary - total_deduction
            recovery_amount = max(-correction_net_amount, Decimal("0"))
            detail_payload["correction_net_amount"] = correction_net_amount
            detail_payload["correction_recovery_amount"] = Decimal("0")
            if recovery_amount <= 0:
                return {
                    "recovered_correction_amount": Decimal("0"),
                    "source_detail": None,
                    "recovery_entries": [],
                }
        else:
            recovery_amount = total_deduction - gross_salary
            detail_payload["correction_net_amount"] = Decimal("0")
            detail_payload["correction_recovery_amount"] = Decimal("0")
            if recovery_amount > 0:
                raise PayrollBusinessValidationError(
                    "Off-cycle deductions exceed payable earnings for employee "
                    f"{employee_id}. Use a CORRECTION run with an explicit source "
                    "payroll recovery basis for deduction-only corrections."
                )
            return {
                "recovered_correction_amount": Decimal("0"),
                "source_detail": None,
                "recovery_entries": [],
            }

        source_detail = self._resolve_correction_source_detail(
            conn,
            payroll_run,
            employee_id,
            payload,
        )
        available_balance = self._available_source_recovery_balance(conn, source_detail)
        if recovery_amount > available_balance:
            raise PayrollBusinessValidationError(
                "Correction recovery exceeds available source payroll balance "
                f"for employee {employee_id}. Required recovery: "
                f"{recovery_amount.quantize(Decimal('0.01'))}; available "
                f"source balance: {available_balance.quantize(Decimal('0.01'))}."
            )

        recovery_entries = self._allocate_correction_recovery(
            detail_payload,
            adjustments,
            recovery_amount,
        )
        detail_payload["correction_recovery_amount"] = recovery_amount
        detail_payload["remarks"] = self._append_correction_recovery_remark(
            detail_payload.get("remarks"),
            recovery_amount,
            source_detail,
        )
        return {
            "recovered_correction_amount": recovery_amount,
            "source_detail": source_detail,
            "recovery_entries": recovery_entries,
        }

    def _insert_correction_recovery_entries(
        self,
        conn,
        payroll_run: dict,
        payroll_detail_id: int,
        source_detail: dict | None,
        recovery_entries: list[dict],
        updated_by: str | None,
    ):
        if not recovery_entries:
            return 0

        insert_recovery = text("""
            insert into payroll_correction_recovery (
                payroll_org_id_fk,
                correction_payroll_run_id_fk,
                correction_payroll_employee_detail_id_fk,
                source_payroll_run_id_fk,
                source_payroll_employee_detail_id_fk,
                payroll_adjustment_id_fk,
                adjustment_type,
                adjustment_amount,
                recovered_amount,
                recovery_status,
                recovery_reason,
                created_by,
                updated_by
            ) values (
                :payroll_org_id_fk,
                :correction_payroll_run_id_fk,
                :correction_payroll_employee_detail_id_fk,
                :source_payroll_run_id_fk,
                :source_payroll_employee_detail_id_fk,
                :payroll_adjustment_id_fk,
                :adjustment_type,
                :adjustment_amount,
                :recovered_amount,
                'APPLIED',
                :recovery_reason,
                :created_by,
                :updated_by
            )
        """)
        for recovery_entry in recovery_entries:
            conn.execute(
                insert_recovery,
                {
                    "payroll_org_id_fk": payroll_run.get("payroll_org_id_fk"),
                    "correction_payroll_run_id_fk": payroll_run.get("payroll_run_id_pk"),
                    "correction_payroll_employee_detail_id_fk": payroll_detail_id,
                    "source_payroll_run_id_fk": source_detail.get(
                        "source_payroll_run_id_fk"
                    ),
                    "source_payroll_employee_detail_id_fk": source_detail.get(
                        "source_payroll_employee_detail_id_fk"
                    ),
                    "payroll_adjustment_id_fk": recovery_entry.get(
                        "payroll_adjustment_id_fk"
                    ),
                    "adjustment_type": recovery_entry.get("adjustment_type"),
                    "adjustment_amount": recovery_entry.get("adjustment_amount"),
                    "recovered_amount": recovery_entry.get("recovered_amount"),
                    "recovery_reason": "OFF_CYCLE_CORRECTION_RECOVERY",
                    "created_by": updated_by,
                    "updated_by": updated_by,
                },
            )
        return len(recovery_entries)

    def list_payroll_runs(self, payload: dict | None = None):
        try:
            payload = payload or {}
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}

            payroll_run_type = payload.get("payroll_run_type")
            if payroll_run_type and payroll_run_type not in self.allowed_run_types():
                return {"error": f"Unsupported off-cycle payroll_run_type: {payroll_run_type}"}

            type_filter = "= :payroll_run_type" if payroll_run_type else "in ('ONE_TIME', 'CORRECTION', 'FINAL_SETTLEMENT')"
            list_payroll_runs_query = text(f"""
                select *
                from payroll_run
                where payroll_org_id_fk = :payroll_org_id_fk
                and (:payroll_year is null or payroll_year = :payroll_year)
                and (:payroll_month is null or payroll_month = :payroll_month)
                and (:payroll_status is null or payroll_status = :payroll_status)
                and payroll_run_type {type_filter}
                order by payroll_run_id_pk desc
            """)

            with self.payroll_engine.begin() as conn:
                df_payroll_runs = pd.read_sql(
                    sql=list_payroll_runs_query,
                    con=conn,
                    params={
                        "payroll_org_id_fk": payroll_org_id_fk,
                        "payroll_year": payload.get("payroll_year"),
                        "payroll_month": payload.get("payroll_month"),
                        "payroll_status": payload.get("payroll_status"),
                        "payroll_run_type": payroll_run_type,
                    },
                )

            df_payroll_runs = self._decorate_payroll_run_dataframe(df_payroll_runs)
            return _dataframe_to_response(df_payroll_runs)
        except Exception as e:
            logger.exception("Failed to list off-cycle payroll runs")
            df_payroll_runs = pd.DataFrame()
            return df_payroll_runs, {"Failed to list off-cycle payroll runs. Error Message: ": {e}}

    def _process_employee(
        self,
        conn,
        payroll_run: dict,
        employee_id: int,
        payload: dict,
        include_fixed_salary: bool,
        adjustment_ids: list[int] | None = None,
    ):
        if self._check_employee_already_exists(conn, payroll_run.get("payroll_run_id_pk"), employee_id):
            raise PayrollBusinessValidationError(
                f"Employee already exists in payroll run: {employee_id}"
            )

        employee = self._get_employee_for_update(
            conn,
            employee_id=employee_id,
            payroll_org_id_fk=payroll_run.get("payroll_org_id_fk"),
        )
        adjustments = self._retrieve_approved_unprocessed_adjustments(
            conn,
            payroll_run=payroll_run,
            employee_id=employee_id,
            adjustment_ids=adjustment_ids,
        )
        detail_payload, consumed_adjustment_ids = self._build_detail_payload(
            payroll_run=payroll_run,
            employee=employee,
            adjustments=adjustments,
            include_fixed_salary=include_fixed_salary,
            payload=payload,
        )
        recovery_result = self._prepare_correction_recovery(
            conn,
            payroll_run,
            employee_id,
            payload,
            adjustments,
            detail_payload,
        )
        detail_result = self._create_payroll_employee_detail(conn, detail_payload)
        correction_recovery_entry_count = self._insert_correction_recovery_entries(
            conn,
            payroll_run,
            detail_result.get("payroll_employee_detail_id_pk"),
            recovery_result.get("source_detail"),
            recovery_result.get("recovery_entries"),
            payload.get("updated_by") or payload.get("user_principal_name"),
        )
        processed_adjustment_count = self._mark_adjustments_as_processed(
            conn,
            consumed_adjustment_ids,
            payroll_run.get("payroll_run_id_pk"),
            payload.get("updated_by") or payload.get("user_principal_name"),
        )
        result = {
            **detail_result,
            "payroll_employee_id_fk": employee_id,
            "processed_adjustment_count": processed_adjustment_count,
            "recovered_correction_amount": recovery_result.get(
                "recovered_correction_amount",
                Decimal("0"),
            ),
            "correction_recovery_entry_count": correction_recovery_entry_count,
            "source_payroll_run_id_fk": (
                recovery_result.get("source_detail") or {}
            ).get("source_payroll_run_id_fk"),
            "source_payroll_employee_detail_id_fk": (
                recovery_result.get("source_detail") or {}
            ).get("source_payroll_employee_detail_id_fk"),
        }
        result["payroll_run_type"] = payroll_run.get("payroll_run_type")
        return self._decorate_payroll_detail_record(result)

    def process_selected_employees(self, payload: dict):
        try:
            payroll_run_id = payload.get("payroll_run_id") or payload.get("payroll_run_id_pk")
            payroll_org_id_fk = _trusted_org_id(payload, "payroll_org_id_fk")
            employee_ids = payload.get("employee_ids") or []
            adjustment_ids_by_employee = payload.get("adjustment_ids_by_employee") or {}
            if not payroll_run_id:
                return {"error": "payroll_run_id is required."}
            if not payroll_org_id_fk:
                return {"error": "payroll_org_id_fk is required."}
            if not employee_ids:
                return {"error": "employee_ids is required for off-cycle payroll."}

            processed_employees = []
            with self.payroll_engine.begin() as conn:
                payroll_run = self._get_payroll_run_for_update(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                )
                self._validate_run_for_processing(payroll_run)
                if payroll_run.get("payroll_run_type") not in self.allowed_run_types():
                    return {"error": "Payroll run is not an off-cycle payroll run."}
                self.validate_run_organization(payroll_run, payroll_org_id_fk)

                for employee_id in employee_ids:
                    selected_adjustment_ids = adjustment_ids_by_employee.get(str(employee_id))
                    if selected_adjustment_ids is None:
                        selected_adjustment_ids = adjustment_ids_by_employee.get(employee_id)
                    processed_employees.append(
                        self._process_employee(
                            conn,
                            payroll_run=payroll_run,
                            employee_id=employee_id,
                            payload=payload,
                            include_fixed_salary=False,
                            adjustment_ids=selected_adjustment_ids,
                        )
                    )

                totals = self._recalculate_payroll_run_totals(
                    conn,
                    payroll_run_id,
                    payroll_org_id_fk,
                    payload.get("updated_by") or payload.get("user_principal_name"),
                )
                self.update_payroll_run_status(
                    conn,
                    payroll_run_id,
                    "PROCESSED",
                    payload.get("processed_by") or payload.get("user_principal_name"),
                )

            return {
                "message": "Successfully processed off-cycle payroll run.",
                "payroll_run_id_pk": payroll_run_id,
                "payroll_status": "PROCESSED",
                "processed_employee_count": len(processed_employees),
                "processed_employees": processed_employees,
                **totals,
            }
        except PayrollBusinessValidationError as e:
            return {"error": str(e)}
        except IntegrityError as e:
            logger.warning("Correction recovery conflict", exc_info=True)
            if self._integrity_constraint_name(e) == "uq_payroll_run_employee":
                return {"error": "Employee already exists in payroll run."}
            return {
                "error": (
                    "Correction recovery has already been applied for one or "
                    "more selected payroll adjustments."
                )
            }
        except Exception as e:
            logger.exception("Failed to process off-cycle payroll run")
            return {"error": f"Failed to process off-cycle payroll run. Error Message: {str(e)}"}


monthly_payroll_run = MonthlyPayrollRun()
off_cycle_payroll_run = OffCyclePayrollRun()


def create_monthly_payroll_run(payload: dict):
    return monthly_payroll_run.create_payroll_run_header({**payload, "payroll_run_type": "MONTHLY"})


def get_monthly_payroll_run(payload: dict):
    return monthly_payroll_run.get_payroll_run(payload)


def list_monthly_payroll_runs(payload: dict | None = None):
    payload = payload or {}
    return monthly_payroll_run.list_payroll_runs({**payload, "payroll_run_type": "MONTHLY"})


def process_monthly_payroll_run(payload: dict):
    return monthly_payroll_run.process_payroll_run(payload)


def process_monthly_employee_payroll(payload: dict):
    return monthly_payroll_run.process_employee(payload)


def get_monthly_payroll_details(payload: dict):
    return monthly_payroll_run.retrieve_payroll_details_for_run(payload)


def get_monthly_employee_payroll_detail(payload: dict):
    return monthly_payroll_run.retrieve_employee_payroll_detail(payload)


def update_monthly_employee_payroll_detail(payload: dict):
    return monthly_payroll_run.update_employee_payroll_detail(payload)


def recalculate_monthly_payroll_run_totals(payload: dict):
    return monthly_payroll_run.recalculate_payroll_run_totals(payload)


def cancel_monthly_payroll_run(payload: dict):
    return monthly_payroll_run.cancel_payroll_run(payload)


def create_off_cycle_payroll_run(payload: dict):
    return off_cycle_payroll_run.create_payroll_run_header(payload)


def get_off_cycle_payroll_run(payload: dict):
    return off_cycle_payroll_run.get_payroll_run(payload)


def list_off_cycle_payroll_runs(payload: dict | None = None):
    return off_cycle_payroll_run.list_payroll_runs(payload or {})


def process_off_cycle_payroll_run(payload: dict):
    return off_cycle_payroll_run.process_selected_employees(payload)


def get_off_cycle_payroll_details(payload: dict):
    return off_cycle_payroll_run.retrieve_payroll_details_for_run(payload)


def get_off_cycle_employee_payroll_detail(payload: dict):
    return off_cycle_payroll_run.retrieve_employee_payroll_detail(payload)


def cancel_off_cycle_payroll_run(payload: dict):
    return off_cycle_payroll_run.cancel_payroll_run(payload)


def create_payroll_adjustment(payload: dict):
    return monthly_payroll_run.create_payroll_adjustment(payload)


def get_payroll_adjustment(payload: dict):
    return monthly_payroll_run.get_payroll_adjustment(payload)


def list_payroll_adjustments(payload: dict | None = None):
    return monthly_payroll_run.list_payroll_adjustments(payload)


def update_payroll_adjustment(payload: dict):
    return monthly_payroll_run.update_payroll_adjustment(payload)


def approve_payroll_adjustment(payload: dict):
    return monthly_payroll_run.approve_payroll_adjustment(payload)


def reject_payroll_adjustment(payload: dict):
    return monthly_payroll_run.reject_payroll_adjustment(payload)


def cancel_payroll_adjustment(payload: dict):
    return monthly_payroll_run.cancel_payroll_adjustment(payload)


def get_pending_approved_payroll_adjustments(payload: dict):
    return monthly_payroll_run.pending_approved_adjustments(payload)
