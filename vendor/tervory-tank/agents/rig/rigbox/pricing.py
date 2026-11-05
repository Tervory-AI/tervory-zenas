"""Private landed-cost calculations. Only the final shop amount is public."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
import re

EU_COUNTRIES = frozenset("AT BE BG HR CY CZ DE DK EE ES FI FR GR HU IE IT LT LU LV MT NL PL PT RO SE SI SK".split())


def currency(value: Any) -> str:
    code = str(value).upper()
    if not re.fullmatch("[A-Z]{3}", code):
        raise ValueError("Use a three-letter currency code.")
    return code


def region(country: str) -> str:
    return "EU" if country in EU_COUNTRIES else country


def supplier_key(row: dict) -> str:
    return ":".join((row["provider"], row["site"], row.get("feed_id", "")))


def resolved_rule(settings: dict | None, row: dict) -> dict:
    if settings is None:
        raise ValueError("Set a private price rule first.")
    scopes = settings.get("scopes", {})
    country = row.get("country", "US")
    rule = (scopes.get("supplier:" + supplier_key(row)) or scopes.get("region:" + country)
            or scopes.get("region:" + region(country)))
    return rule or settings


def selling_price(amount: Any, rate: Any) -> Decimal:
    fx = number(rate, maximum="1000000")
    if not fx:
        raise ValueError("A founder-set exchange rate is required.")
    value = number(amount)
    return value if fx == 1 else number(value * fx, maximum="1000000000000").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def number(value: Any, *, maximum: str = "1000000000") -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal(maximum):
            raise ValueError()
        return result
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError("Enter a finite, non-negative amount within the allowed range.") from None


def price_rule(values: dict[str, Any]) -> dict[str, str]:
    """Freight is per item in supplier currency. Margin is a gross margin %."""
    freight = number(values.get("freight", 0))
    duty = number(values.get("duty_percent", 0), maximum="1000")
    margin = number(values.get("margin_percent", 0), maximum="99.99")
    return {"freight": str(freight), "duty_percent": str(duty), "margin_percent": str(margin)}


def indicative_price(cost: Any, rule: dict[str, Any], rate: Any) -> Decimal:
    validated = price_rule(rule)
    fx = number(rate, maximum="1000000")
    if not fx:
        raise ValueError("A founder-set exchange rate is required.")
    landed = (number(cost) + Decimal(validated["freight"])) * (1 + Decimal(validated["duty_percent"]) / 100)
    amount = landed * fx / (1 - Decimal(validated["margin_percent"]) / 100)
    return number(amount, maximum="1000000000000").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
