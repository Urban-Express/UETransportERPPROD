"""Authoritative AR arithmetic. Gross includes tax; no production tax-rate default."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import json


def ar_json_safe(value):
    """Exact decimal strings for NEW line-mode API responses; never binary floats."""
    return json.loads(json.dumps(value, default=str))


class ARValidationError(ValueError):
    def __init__(self, message, code="AR_VALIDATION_ERROR"):
        super().__init__(message)
        self.code = code

    def response(self):
        return {"error": str(self), "error_code": self.code}


def decimal_value(value, field, *, scale=2, precision=18):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError()
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError()
        if amount >= Decimal(10) ** (precision - scale):
            raise ValueError()
        if amount != amount.quantize(Decimal(1).scaleb(-scale)):
            raise ValueError()
        return amount
    except (ValueError, InvalidOperation):
        raise ARValidationError(f"Invalid {field}: require a finite nonnegative NUMERIC({precision},{scale}) value.") from None


def date_value(value, field, *, required=False):
    if value is None or value == "":
        if required:
            raise ARValidationError(f"{field} is required.")
        return None
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ARValidationError(f"Invalid {field}: use YYYY-MM-DD.") from None


def money(value):
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return decimal_value(rounded, "calculated amount")


def calculate_line(line):
    result = dict(line)
    quantity = decimal_value(line.get("ar_line_quantity"), "ar_line_quantity", scale=4)
    rate = decimal_value(line.get("ar_line_unit_rate"), "ar_line_unit_rate")
    tax_rate = decimal_value(line.get("ar_line_tax_rate"), "ar_line_tax_rate", scale=4, precision=9)
    start = date_value(line.get("ar_line_service_period_start"), "ar_line_service_period_start")
    end = date_value(line.get("ar_line_service_period_end"), "ar_line_service_period_end")
    if start and end and end < start:
        raise ARValidationError("Invalid line service range: end precedes start.")
    method = line.get("ar_line_proration_method")
    if method not in (None, "CALENDAR_DAYS"):
        raise ARValidationError("Invalid proration method; supported method is CALENDAR_DAYS or null.")
    numerator = denominator = None
    with localcontext() as context:
        context.prec = 50
        raw_base = quantity * rate
        base = money(raw_base)
        net = base
        if method:
            if not start or not end:
                raise ARValidationError("Both service dates are required for CALENDAR_DAYS.")
            if (start.year, start.month) != (end.year, end.month):
                raise ARValidationError("Ambiguous multi-month proration: provide a service period within one calendar month.", "AR_PRORATION_RANGE_AMBIGUOUS")
            numerator = Decimal((end - start).days + 1)
            denominator = Decimal(monthrange(start.year, start.month)[1])
            net = money(raw_base * numerator / denominator)
        tax = money(net * tax_rate / Decimal(100))
        total = money(net + tax)
    calculated = {
        "ar_line_base_amount": base, "ar_line_net_amount": net,
        "ar_line_tax_amount": tax, "ar_line_total_amount": total,
        "ar_line_proration_numerator": numerator, "ar_line_proration_denominator": denominator,
    }
    # Stored proposals are independently verified too; supplied totals never drive calculation.
    for key, expected in calculated.items():
        if line.get(key) is not None and (expected is None or Decimal(str(line[key])) != expected):
            raise ARValidationError(f"Invalid {key}: does not match backend calculation.", "AR_CALCULATED_VALUE_MISMATCH")
    result.update(calculated)
    result.update(ar_line_quantity=quantity, ar_line_unit_rate=rate, ar_line_tax_rate=tax_rate,
                  ar_line_service_period_start=start, ar_line_service_period_end=end)
    return result


def calculate_totals(lines):
    with localcontext() as context:
        context.prec = 50
        subtotal = money(sum((line["ar_line_net_amount"] for line in lines), Decimal(0)))
        tax = money(sum((line["ar_line_tax_amount"] for line in lines), Decimal(0)))
        return {"ar_subtotal_amount": subtotal, "ar_tax_amount": tax, "ar_invoice_amount": money(subtotal + tax)}
