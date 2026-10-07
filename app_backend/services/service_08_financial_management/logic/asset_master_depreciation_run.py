import calendar
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_08_financial_management.logic.asset_master_common import (
    get_asset_id_from_payload,
)
from app_backend.services.service_08_financial_management.logic.asset_master_update_data import (
    update_asset_nbv_internal,
)


def _to_date(value, field_name: str):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format.") from exc


def _to_decimal(value, field_name: str):
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field_name} must be numeric.") from exc


def _add_months(source_date: date, months: int):
    target_month = source_date.month - 1 + months
    target_year = source_date.year + target_month // 12
    target_month = target_month % 12 + 1
    target_day = min(
        source_date.day,
        calendar.monthrange(target_year, target_month)[1]
    )
    return date(target_year, target_month, target_day)


def _calculate_elapsed_months(depreciation_start_date: date, run_date: date):
    if run_date < depreciation_start_date:
        return Decimal("0")

    if run_date == depreciation_start_date:
        return Decimal("0")

    full_months = (
        (run_date.year - depreciation_start_date.year) * 12
        + run_date.month
        - depreciation_start_date.month
    )
    anchor_date = _add_months(depreciation_start_date, full_months)

    if anchor_date > run_date:
        full_months -= 1
        anchor_date = _add_months(depreciation_start_date, full_months)

    next_anchor_date = _add_months(anchor_date, 1)
    days_in_current_depreciation_month = (next_anchor_date - anchor_date).days
    partial_days = (run_date - anchor_date).days
    if partial_days > 0:
        partial_days += 1

    return (
        Decimal(full_months)
        + Decimal(partial_days) / Decimal(days_in_current_depreciation_month)
    )


def _numeric_response(value):
    if value is None:
        return None

    return float(value)


def run_asset_master_depreciation(payload: dict):
    try:
        asset_id = get_asset_id_from_payload(payload)
        asset_org_id_fk = payload.get("asset_org_id_fk") or payload.get("authenticated_org_id")
        if not asset_id:
            return {"error": "asset_id is required."}
        if not asset_org_id_fk:
            return {"error": "asset_org_id_fk is required."}

        run_date = _to_date(payload.get("run_date") or date.today(), "run_date")
        updated_by = payload.get("updated_by") or payload.get("user_principal_name")

        asset_engine = db_engine()
        get_asset_query = text("""
            select *
            from asset_master
            where asset_id_pk = :asset_id
            and asset_org_id_fk = :asset_org_id_fk
            for update
        """)

        with asset_engine.begin() as conn:
            df_asset = pd.read_sql(
                sql=get_asset_query,
                con=conn,
                params={
                    "asset_id": asset_id,
                    "asset_org_id_fk": asset_org_id_fk
                }
            )

            if df_asset.empty:
                return {
                    "success": False,
                    "status": "FAILED",
                    "asset_id_pk": asset_id,
                    "run_date": str(run_date),
                    "error": "Asset ID not found."
                }

            asset = df_asset.iloc[0].to_dict()

            acquisition_cost = _to_decimal(
                asset.get("asset_acquisition_cost"),
                "asset_acquisition_cost"
            )
            salvage_value = _to_decimal(
                asset.get("asset_salvage_value"),
                "asset_salvage_value"
            )
            useful_life_months = _to_decimal(
                asset.get("asset_useful_life"),
                "asset_useful_life"
            )
            depreciation_start = _to_date(
                asset.get("depreciation_start_date"),
                "depreciation_start_date"
            )
            previous_nbv = _to_decimal(asset.get("asset_nbv"), "asset_nbv")

            if acquisition_cost is None:
                return {"success": False, "status": "FAILED", "error": "asset_acquisition_cost is required."}

            if salvage_value is None:
                return {"success": False, "status": "FAILED", "error": "asset_salvage_value is required."}

            if useful_life_months is None:
                return {"success": False, "status": "FAILED", "error": "asset_useful_life is required."}

            if useful_life_months <= 0:
                return {"success": False, "status": "FAILED", "error": "asset_useful_life must be greater than zero."}

            if depreciation_start is None:
                return {"success": False, "status": "FAILED", "error": "depreciation_start_date is required."}

            if acquisition_cost < 0:
                return {"success": False, "status": "FAILED", "error": "asset_acquisition_cost cannot be negative."}

            if salvage_value < 0:
                return {"success": False, "status": "FAILED", "error": "asset_salvage_value cannot be negative."}

            if salvage_value > acquisition_cost:
                return {
                    "success": False,
                    "status": "FAILED",
                    "error": "asset_salvage_value cannot exceed asset_acquisition_cost."
                }

            depreciable_base = acquisition_cost - salvage_value
            monthly_depreciation = depreciable_base / useful_life_months
            elapsed_months = _calculate_elapsed_months(depreciation_start, run_date)
            depreciable_elapsed_months = min(elapsed_months, useful_life_months)
            accumulated_depreciation = monthly_depreciation * depreciable_elapsed_months
            resulting_nbv = acquisition_cost - accumulated_depreciation

            if resulting_nbv < salvage_value:
                resulting_nbv = salvage_value

            update_result = update_asset_nbv_internal(
                asset_id=asset_id,
                asset_nbv=resulting_nbv,
                updated_by=updated_by,
                asset_org_id_fk=asset_org_id_fk,
                conn=conn
            )

            if update_result.rowcount == 0:
                raise Exception("Asset NBV update did not modify any row.")

        return {
            "success": True,
            "status": "SUCCESS",
            "asset_id_pk": asset_id,
            "asset_name": asset.get("asset_name"),
            "asset_acquisition_cost": _numeric_response(acquisition_cost),
            "asset_salvage_value": _numeric_response(salvage_value),
            "asset_useful_life": _numeric_response(useful_life_months),
            "asset_useful_life_unit": "MONTHS",
            "asset_acquisition_date": str(asset.get("asset_acquisition_date")),
            "depreciation_start_date": str(depreciation_start),
            "run_date": str(run_date),
            "calculated_depreciation": _numeric_response(monthly_depreciation),
            "calculated_accumulated_depreciation": _numeric_response(accumulated_depreciation),
            "elapsed_months": _numeric_response(elapsed_months),
            "previous_nbv": _numeric_response(previous_nbv),
            "resulting_nbv": _numeric_response(resulting_nbv),
        }

    except Exception as e:
        return {
            "success": False,
            "status": "FAILED",
            "error": f"Failed to run asset depreciation. Error Message: {str(e)}"
        }


def run_asset_depreciation(payload: dict):
    return run_asset_master_depreciation(payload)
