"""Read structured contract data only; never parse signed documents at runtime."""
from sqlalchemy import text

from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import (
    ARValidationError, ar_json_safe, calculate_line, calculate_totals, date_value, decimal_value,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_configuration import (
    ar_connection, customer_identity, resolve_tax,
)


def load_contract(conn, org_id, customer_id, contract_id, currency=None):
    row = conn.execute(text("SELECT * FROM contracts_management WHERE cont_id_pk=:id AND cont_org_id_fk=:org"),
                       {"id": contract_id, "org": org_id}).mappings().one_or_none()
    if not row:
        raise ARValidationError("Contract not found for the authenticated organization.")
    if row["cont_cust_id_fk"] != customer_id:
        raise ARValidationError("Invalid contract: it belongs to another customer.")
    if row["cont_approval_status"] != "APPROVED":
        raise ARValidationError("Invalid contract: an APPROVED contract is required.")
    if currency and currency != row["cont_currency_code"].strip():
        raise ARValidationError("Invalid currency: must match the selected contract; no FX conversion is available.")
    return dict(row)


def contract_line(contract, billing_start, billing_end):
    start = date_value(billing_start, "ar_billing_period_start", required=True)
    end = date_value(billing_end, "ar_billing_period_end", required=True)
    if end < start:
        raise ARValidationError("Invalid billing period: end precedes start.")
    if (start.year, start.month) != (end.year, end.month):
        raise ARValidationError("Ambiguous multi-month billing: select one calendar month for the recurring contract line.", "AR_PRORATION_RANGE_AMBIGUOUS")
    start = max(start, date_value(contract["cont_start_date"], "cont_start_date", required=True))
    if contract.get("cont_end_date"):
        end = min(end, date_value(contract["cont_end_date"], "cont_end_date"))
    if start > end:
        raise ARValidationError("Invalid billing period: it does not overlap the contract term.")
    basis = contract["cont_revenue_basis"]
    components = []
    proration_method = "CALENDAR_DAYS"
    if basis in ("PER_DAY", "PER_KILOMETER"):
        quantity_field, rate_field, uom = (
            ("cont_no_of_days", "cont_per_day_rate", "DAY") if basis == "PER_DAY"
            else ("cont_no_of_kms", "cont_per_km_rate", "KM")
        )
        if contract.get(quantity_field) is None:
            raise ARValidationError(f"Invalid contract: {quantity_field} is required and must be greater than zero.")
        quantity = decimal_value(contract[quantity_field], quantity_field, scale=4)
        if quantity <= 0:
            raise ARValidationError(f"Invalid contract: {quantity_field} must be greater than zero.")
        if contract.get(rate_field) is None:
            raise ARValidationError(f"Invalid contract: {rate_field} must not be NULL (zero is allowed).")
        rate = decimal_value(contract[rate_field], rate_field)
        components.append((quantity, rate, uom, None))
        proration_method = None
    elif basis not in ("PER_BUS", "PER_PASSENGER", "PER_PASSENGER_AND_PER_BUS"):
        raise ARValidationError(f"Unsupported contract Revenue Basis: {basis}.")
    if basis in ("PER_BUS", "PER_PASSENGER_AND_PER_BUS"):
        for stem, label in (("big_bus", "Bus (>34 seats)"), ("medium_bus", "Bus (17-34 seats)"), ("small_bus", "Bus (<17 seats)")):
            suffix = {"big_bus": "gt_34", "medium_bus": "17_34", "small_bus": "lt_17"}[stem]
            quantity = contract[f"cont_{stem}_count_{suffix}"]
            if quantity > 0:
                rate = contract[f"cont_{stem}_rate_pm"]
                if rate is None:
                    raise ARValidationError("Contract monthly rate is required for each nonzero bus class.")
                components.append((quantity, rate, "BUS_MONTH", label))
    if basis in ("PER_PASSENGER", "PER_PASSENGER_AND_PER_BUS") and contract["cont_no_of_passengers"] > 0:
        if contract.get("cont_per_passenger_rate_pm") is None:
            raise ARValidationError("Contract monthly passenger rate is required.")
        components.append((contract["cont_no_of_passengers"], contract["cont_per_passenger_rate_pm"], "PASSENGER_MONTH", None))
    if len(components) > 1:
        raise ARValidationError("Ambiguous contract billing: multiple commercial components qualify for the single automatic line. Explicit business selection/guidance is required.", "AR_CONTRACT_COMPONENT_AMBIGUOUS")
    if not components:
        raise ARValidationError("Invalid contract: no supported recurring billing component with a quantity and rate.")
    quantity, rate, uom, vehicle = components[0]
    line = {
        "ar_line_number": 1, "ar_line_source_type": "CONTRACT_AUTOFILL",
        "ar_line_source_contract_id_fk": contract["cont_id_pk"],
        "ar_line_description": contract.get("cont_contract_name") or contract["cont_contract_number"],
        "ar_line_vehicle_description": vehicle, "ar_line_quantity": decimal_value(quantity, "contract quantity", scale=4),
        "ar_line_uom": uom, "ar_line_unit_rate": decimal_value(rate, "contract rate"),
        "ar_line_service_period_start": start, "ar_line_service_period_end": end,
        "ar_line_proration_method": proration_method,
    }
    if proration_method is None:
        line.update(ar_line_proration_numerator=None, ar_line_proration_denominator=None)
    return line


def get_accounts_receivables_contract_prefill(payload):
    try:
        org = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")
        customer = payload.get("ar_cust_id_fk")
        contract_id = payload.get("ar_contract_id_fk")
        if not org or not customer or not contract_id:
            raise ARValidationError("ar_org_id_fk, ar_cust_id_fk and ar_contract_id_fk are required.")
        invoice_date = date_value(payload.get("ar_invoice_date"), "ar_invoice_date", required=True)
        with ar_connection() as conn:
            customer_identity(conn, org, customer)
            contract = load_contract(conn, org, customer, contract_id)
            line = contract_line(contract, payload.get("ar_billing_period_start"), payload.get("ar_billing_period_end"))
            line.update(resolve_tax(conn, org, invoice_date))
            line = calculate_line(line)
        return ar_json_safe({
            "contract": {key: contract.get(key) for key in ("cont_id_pk", "cont_contract_number", "cont_contract_name",
                "cont_revenue_basis", "cont_start_date", "cont_end_date", "cont_extra_trip_charge", "cont_extra_km_charge_per_km")},
            "header_prefill": {"ar_contract_id_fk": contract_id, "ar_contract": contract["cont_contract_number"],
                "ar_contract_number_snapshot": contract["cont_contract_number"], "ar_contract_name_snapshot": contract.get("cont_contract_name"),
                "ar_cust_id_fk": customer, "ar_currency_code": contract["cont_currency_code"].strip(),
                "ar_billing_period_start": date_value(payload["ar_billing_period_start"], "ar_billing_period_start"),
                "ar_billing_period_end": date_value(payload["ar_billing_period_end"], "ar_billing_period_end"), "ar_due_date": None},
            "suggested_line": line, "totals_preview": calculate_totals([line]),
            "calculation_metadata": {"proration_method": line["ar_line_proration_method"], "numerator": line["ar_line_proration_numerator"],
                "denominator": line["ar_line_proration_denominator"], "due_date_source": "MANUAL",
                "description_completion": "Only structured contract name/number and bus class are supplied; complete route/vehicle wording manually."},
        })
    except ARValidationError as exc:
        return exc.response()
