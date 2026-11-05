"""Read a brand's sheets and feeds into the same product and fit rows."""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import UUID, uuid5

from rigbox.feeds import map_google_merchant as _map_google_merchant
from rigbox.feeds import map_shopify as _map_shopify
from rigbox.feeds import map_woocommerce as _map_woocommerce
from rigbox.shopper_attributes import split_private

_NAMESPACE = UUID("11111111-1111-4111-8111-111111111111")
_REQUIRED = ("sku", "name", "brand", "category", "price", "currency", "image_url", "active")


def tenant_id_for(slug: str, declared: str | None) -> str:
    if declared:
        return str(UUID(str(declared)))
    return str(uuid5(_NAMESPACE, f"rig:{slug}"))


def parse_products_csv(text: str) -> dict[str, Any]:
    return _parse_table(text, _product_row)


def parse_fits_csv(text: str) -> dict[str, Any]:
    return _parse_table(text, _fit_row)


def parse_market_csv(text: str) -> dict[str, Any]:
    """Other sellers' prices. Not catalogue rows, and not used by search."""
    parsed = _parse_table(text, _market_row)
    if parsed["rejected"]:
        return parsed
    seen: set[tuple[str, str]] = set()
    for row in parsed["rows"]:
        key = (row["sku"], row["source"])
        if key in seen:
            return {"rows": [], "rejected": [{"row": row["row"], "reason": "sku and source are repeated"}]}
        seen.add(key)
    return parsed


def check_fit_refs(rows: list[dict[str, Any]], skus: set[str], families: set[str]) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for row in rows:
        if row["part_sku"] not in skus:
            rejected.append({"row": row["row"], "reason": "part_sku is not in this catalogue"})
            continue
        if row.get("fits_sku") and row["fits_sku"] not in skus:
            rejected.append({"row": row["row"], "reason": "fits_sku is not in this catalogue"})
        elif row.get("fits_family") and row["fits_family"] not in families:
            rejected.append({"row": row["row"], "reason": "fits_family is not on any product"})
    return rejected


def families_from(products: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in products:
        family = str(row.get("family") or "").strip()
        if not family:
            continue
        grouped.setdefault(family, [])
        sku = str(row.get("sku") or "")
        if sku and sku not in grouped[family]:
            grouped[family].append(sku)
    return {key: sorted(value) for key, value in grouped.items()}


def map_shopify(body: Any, currency: str) -> dict[str, Any]:
    return _map_shopify(body, currency)


def map_woocommerce(body: Any, currency: str) -> dict[str, Any]:
    return _map_woocommerce(body, currency)


def map_google_merchant(text: str) -> dict[str, Any]:
    return _map_google_merchant(text)


def price_patch(update: dict[str, Any]) -> dict[str, Any]:
    """Tiger may change price and availability. Fitment fields are dropped."""
    patched: dict[str, Any] = {}
    if "price" in update and update["price"] is not None and str(update["price"]) != "":
        patched["price"] = _money_text(update["price"])
    if "currency" in update and update["currency"]:
        patched["currency"] = str(update["currency"]).strip().upper()
    if "active" in update and update["active"] is not None:
        patched["active"] = _active_value(update["active"])
    return patched



def _parse_table(text: str, reader) -> dict[str, Any]:
    handle = io.StringIO(text)
    try:
        parsed = csv.DictReader(handle)
        fieldnames = list(parsed.fieldnames or [])
    except csv.Error as exc:
        return {"rows": [], "rejected": [{"row": 1, "reason": f"could not read the sheet ({exc})"}]}
    if not fieldnames:
        return {"rows": [], "rejected": [{"row": 1, "reason": "the sheet has no header"}]}
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(parsed, start=2):
        try:
            rows.append(reader(raw, index))
        except ValueError as exc:
            rejected.append({"row": index, "reason": str(exc)})
    if rejected:
        return {"rows": [], "rejected": rejected}
    return {"rows": rows, "rejected": []}


def _product_row(raw: dict[str, str | None], index: int) -> dict[str, Any]:
    values = {key: (value or "").strip() for key, value in raw.items() if key}
    missing = [name for name in _REQUIRED if not values.get(name)]
    if missing:
        raise ValueError("missing " + ", ".join(missing))
    attributes: dict[str, Any] = {}
    if values.get("attributes"):
        try:
            loaded = json.loads(values["attributes"])
        except json.JSONDecodeError as exc:
            raise ValueError("attributes is not JSON") from exc
        if not isinstance(loaded, dict):
            raise ValueError("attributes must be an object")
        attributes = loaded
    # Pricing inputs (Custom Nation's export_usd, any cost/landed/margin key)
    # never enter the shopper-readable attributes column.
    attributes, private = split_private(attributes)
    return {
        "row": index,
        "sku": values["sku"],
        "gtin": values.get("gtin") or None,
        "mpn": values.get("mpn") or None,
        "name": values["name"],
        "brand": values["brand"],
        "category": values["category"],
        "price": _money_text(values["price"]),
        "currency": values["currency"].upper(),
        "image_url": values["image_url"],
        "url": values.get("product_url") or values.get("url") or "",
        "family": values.get("family") or "",
        "active": _active_value(values["active"]),
        "attributes": attributes,
        "private_attributes": private,
    }


def _fit_row(raw: dict[str, str | None], index: int) -> dict[str, Any]:
    values = {key: (value or "").strip() for key, value in raw.items() if key}
    if not values.get("part_sku"):
        raise ValueError("missing part_sku")
    fits_sku = values.get("fits_sku") or ""
    fits_family = values.get("fits_family") or ""
    if bool(fits_sku) == bool(fits_family):
        raise ValueError("set fits_sku or fits_family, not both")
    rule: dict[str, Any] = {}
    if values.get("rule"):
        try:
            loaded = json.loads(values["rule"])
        except json.JSONDecodeError as exc:
            raise ValueError("rule is not JSON") from exc
        if not isinstance(loaded, dict):
            raise ValueError("rule must be an object")
        rule = loaded
    return {
        "row": index,
        "part_sku": values["part_sku"],
        "fits_sku": fits_sku or None,
        "fits_family": fits_family or None,
        "rule": rule,
        "note": values.get("note") or "",
        "source": "import",
        "approved": True,
        "confidence": 1,
    }


def _market_row(raw: dict[str, str | None], index: int) -> dict[str, Any]:
    values = {key: (value or "").strip() for key, value in raw.items() if key}
    missing = [name for name in ("sku", "source", "price", "currency") if not values.get(name)]
    if missing:
        raise ValueError("missing " + ", ".join(missing))
    currency = values["currency"].upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter code")
    return {
        "row": index,
        "sku": values["sku"],
        "source": values["source"],
        "price": _money_text(values["price"]),
        "currency": currency,
        "seen_at": values.get("seen_at") or "",
    }


def _money_text(value: Any) -> str:
    text = str(value).strip().replace(" ", "")
    if not text:
        raise ValueError("price is missing")
    try:
        number = Decimal(text)
        if not number.is_finite() or number < 0:
            raise ValueError("price is not finite")
        number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("price is not a number") from exc
    if number < 0:
        raise ValueError("price is negative")
    return f"{number:.2f}"


def _active_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"yes", "true", "1", "active"}:
        return True
    if text in {"no", "false", "0", "inactive"}:
        return False
    raise ValueError("active must be yes or no")
