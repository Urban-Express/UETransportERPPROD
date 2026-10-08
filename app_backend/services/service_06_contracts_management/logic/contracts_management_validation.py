"""Contract commercial validation shared by request parsing and final execution."""
from decimal import Decimal, InvalidOperation
from typing import Literal, get_args


ContractRevenueBasis = Literal[
    "PER_BUS", "PER_PASSENGER", "PER_PASSENGER_AND_PER_BUS", "PER_DAY", "PER_KILOMETER"
]
CONTRACT_REVENUE_BASES = get_args(ContractRevenueBasis)
CONTRACT_DAY_KM_FIELDS = (
    "cont_no_of_days", "cont_per_day_rate", "cont_no_of_kms", "cont_per_km_rate"
)
CONTRACT_COUNT_FIELDS = (
    "cont_no_of_passengers", "cont_big_bus_count_gt_34",
    "cont_medium_bus_count_17_34", "cont_small_bus_count_lt_17",
)
CONTRACT_RATE_FIELDS = (
    "cont_per_passenger_rate_pm", "cont_big_bus_rate_pm",
    "cont_medium_bus_rate_pm", "cont_small_bus_rate_pm",
    "cont_extra_trip_charge", "cont_km_cap_pm_per_bus", "cont_extra_km_charge_per_km",
)
CONTRACT_STATUSES = ("DRAFT", "ACTIVE", "SUSPENDED", "EXPIRED", "TERMINATED", "CANCELLED")


def validate_contract_commercial_values(payload: dict, *, defer_omitted_new_fields: bool = False) -> str | None:
    """Return an actionable error without defaulting NULL or mutating the payload.

    UPDATE request parsing defers absent Day/Km fields until CRUD has read the
    existing row. CRUD validates that effective state again before submission and
    on final execution. The database remains authoritative if the row changes.
    """
    basis = payload.get("cont_revenue_basis")
    if basis not in CONTRACT_REVENUE_BASES:
        return "Invalid cont_revenue_basis; expected one of: " + ", ".join(CONTRACT_REVENUE_BASES) + "."
    status = payload.get("cont_status", "DRAFT")
    if status not in CONTRACT_STATUSES:
        return "Invalid cont_status; expected one of: " + ", ".join(CONTRACT_STATUSES) + "."

    numbers = {}
    for field in (*CONTRACT_COUNT_FIELDS, *CONTRACT_RATE_FIELDS, *CONTRACT_DAY_KM_FIELDS):
        value = payload.get(field, 0 if field in CONTRACT_COUNT_FIELDS else None)
        if value is None:
            if field in CONTRACT_COUNT_FIELDS:
                return f"Invalid {field}: NULL is not supported; supply a nonnegative integer or omit it for the existing zero default."
            continue
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return f"Invalid {field}: a numeric value is required."
        if not number.is_finite() or number < 0:
            return f"Invalid {field}: a finite nonnegative value is required."
        if field in CONTRACT_COUNT_FIELDS and number != number.to_integral_value():
            return f"Invalid {field}: a nonnegative integer is required."
        numbers[field] = number

    if status == "DRAFT":
        return None
    if basis in ("PER_PASSENGER", "PER_PASSENGER_AND_PER_BUS"):
        if numbers["cont_no_of_passengers"] <= 0:
            return "Invalid non-DRAFT passenger pricing: cont_no_of_passengers must be greater than zero."
        if payload.get("cont_per_passenger_rate_pm") is None:
            return "Invalid non-DRAFT passenger pricing: cont_per_passenger_rate_pm must not be NULL (zero is allowed)."
    if basis in ("PER_BUS", "PER_PASSENGER_AND_PER_BUS"):
        bus_pairs = zip(CONTRACT_COUNT_FIELDS[1:], CONTRACT_RATE_FIELDS[1:4])
        if sum(numbers[field] for field in CONTRACT_COUNT_FIELDS[1:]) <= 0:
            return "Invalid non-DRAFT bus pricing: the total of the three bus counts must be greater than zero."
        for count, rate in bus_pairs:
            if numbers[count] > 0 and payload.get(rate) is None:
                return f"Invalid non-DRAFT bus pricing: {rate} must not be NULL when {count} is positive (zero rate is allowed)."
    if basis in ("PER_DAY", "PER_KILOMETER"):
        quantity, rate = (CONTRACT_DAY_KM_FIELDS[:2] if basis == "PER_DAY" else CONTRACT_DAY_KM_FIELDS[2:])
        if not (defer_omitted_new_fields and quantity not in payload):
            if quantity not in numbers or numbers[quantity] <= 0:
                return f"Invalid non-DRAFT {basis} pricing: {quantity} must not be NULL and must be greater than zero."
        if not (defer_omitted_new_fields and rate not in payload):
            if payload.get(rate) is None:
                return f"Invalid non-DRAFT {basis} pricing: {rate} must not be NULL (zero is allowed)."
    return None
