"""Map one fetched catalogue feed onto product rows.

Shopify ``products.json``, a WooCommerce REST product list, and a Google
Merchant RSS file all become the same columns as the spreadsheet. A bad
row rejects the whole feed. Nothing here fetches a URL or schedules a run.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

_FORMATS = ("shopify", "woocommerce", "google", "csv", "tsv", "json", "xml", "xlsx")


def register(url: str, fmt: str, currency: str = "") -> dict[str, str]:
    """Check one feed and return the record a caller stores. Call it once per feed."""
    text = str(url or "").strip()
    kind = str(fmt or "").strip().lower()
    if kind not in _FORMATS:
        raise ValueError("feed format must be " + ", ".join(_FORMATS))
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("feed url must be https")
    code = str(currency or "").strip().upper()
    if kind != "google" and not code:
        raise ValueError("currency is required")
    if code and not (len(code) == 3 and code.isalpha()):
        raise ValueError("currency must be a three-letter code")
    return {"format": kind, "url": text, "currency": code}


def pull(record: dict[str, Any], body: Any) -> dict[str, Any]:
    """Map a body the caller already has. Does not fetch and does not schedule."""
    if not isinstance(record, dict):
        return _feed_error("feed is not registered")
    kind = str(record.get("format") or "")
    if record.get("columns") or kind in {"csv", "tsv", "json", "xml", "xlsx"}:
        import csv
        import zipfile
        from .catalogue import map_catalogue
        try:
            return map_catalogue({**record, "format": "xml" if kind == "google" else kind}, body)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, UnicodeError, csv.Error, ET.ParseError, zipfile.BadZipFile, RuntimeError):
            return _feed_error("Check the file format and confirmed field names.")
    if kind == "shopify":
        return map_shopify(body, str(record.get("currency") or ""))
    if kind == "woocommerce":
        return map_woocommerce(body, str(record.get("currency") or ""))
    if kind == "google":
        return map_google_merchant(body)
    return _feed_error("unknown feed format")


def map_shopify(body: Any, currency: str) -> dict[str, Any]:
    loaded, problem = _load_json(body)
    if problem:
        return _feed_error("shopify feed is not JSON")
    products = loaded.get("products") if isinstance(loaded, dict) else None
    if not isinstance(products, list):
        return _feed_error("shopify feed has no products list")
    if not products:
        return {"rows": [], "rejected": []}
    code = _text(currency).upper()
    if not code:
        return _feed_error("missing currency")
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(products, start=1):
        if not isinstance(item, dict):
            rejected.append({"row": index, "reason": "product is not an object"})
            continue
        name = _text(item.get("title"))
        brand = _text(item.get("vendor"))
        category = _text(item.get("product_type"))
        missing = _missing_label(name=name, brand=brand, category=category)
        if missing:
            rejected.append({"row": index, "reason": missing})
            continue
        variants = item.get("variants") if isinstance(item.get("variants"), list) else []
        if not variants:
            rejected.append({"row": index, "reason": "missing sku"})
            continue
        active = _flag(item.get("status"), default="active", yes={"active", "publish", "yes"})
        image = _first_image(item.get("images"))
        link = _text(item.get("handle"))
        for offset, variant in enumerate(variants, start=1):
            prefix = f"variant {offset}: " if len(variants) > 1 else ""
            if not isinstance(variant, dict):
                rejected.append({"row": index, "reason": prefix + "variant is not an object"})
                continue
            row, reason = _product(
                sku=_text(variant.get("sku")),
                name=name,
                brand=brand,
                category=category,
                price=variant.get("price"),
                currency=code,
                image_url=_variant_image(variant) or image,
                url=link,
                gtin=_text(variant.get("barcode")),
                active=active and variant.get("available") is not False and variant.get("inventory_quantity", 1) != 0,
            )
            if row is None:
                rejected.append({"row": index, "reason": prefix + (reason or "missing sku")})
                continue
            if row["sku"] in seen:
                rejected.append({"row": index, "reason": prefix + "sku is repeated"})
                continue
            seen.add(str(row["sku"]))
            rows.append(row)
    return _finish(rows, rejected)


def map_woocommerce(body: Any, currency: str) -> dict[str, Any]:
    loaded, problem = _load_json(body)
    if problem:
        return _feed_error("woocommerce feed is not JSON")
    items = _woo_items(loaded)
    if items is None:
        return _feed_error("woocommerce feed has no products list")
    if not items:
        return {"rows": [], "rejected": []}
    code = _text(currency).upper()
    if not code:
        return _feed_error("missing currency")
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            rejected.append({"row": index, "reason": "product is not an object"})
            continue
        row, reason = _product(
            sku=_text(item.get("sku")),
            name=_text(item.get("name")),
            brand=_woo_brand(item),
            category=_woo_category(item),
            price=_woo_price(item),
            currency=code,
            image_url=_first_image(item.get("images")),
            url=_text(item.get("permalink")),
            gtin=_text(item.get("global_unique_id") or item.get("gtin")),
            active=_flag(item.get("status"), default="publish", yes={"publish", "active", "yes"}) and item.get("stock_status") != "outofstock",
        )
        if row is None:
            rejected.append({"row": index, "reason": reason or "missing sku"})
            continue
        if row["sku"] in seen:
            rejected.append({"row": index, "reason": "sku is repeated"})
            continue
        seen.add(str(row["sku"]))
        rows.append(row)
    return _finish(rows, rejected)


def map_google_merchant(text: str | bytes) -> dict[str, Any]:
    raw = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text)
    # Merchant catalogues never need DTDs or entity declarations. Reject them
    # before ElementTree can expand even a small malicious document in memory.
    if "\x00" in raw or "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        return _feed_error("merchant feed declarations are not allowed")
    try:
        root = ET.fromstring(raw.lstrip("\ufeff").strip())
    except ET.ParseError:
        return _feed_error("merchant feed is not XML")
    items = [element for element in root.iter() if _local(element.tag) == "item"]
    if not items:
        return _feed_error("merchant feed has no items")
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        fields = _item_fields(item)
        amount, code = _split_price(fields.get("price") or "")
        row, reason = _product(
            sku=fields.get("id") or "",
            name=fields.get("title") or "",
            brand=fields.get("brand") or "",
            category=fields.get("product_type") or fields.get("google_product_category") or "",
            price=amount,
            currency=code,
            image_url=fields.get("image_link") or "",
            url=fields.get("link") or "",
            gtin=fields.get("gtin") or "",
            active=_in_stock(fields.get("availability") or ""),
        )
        if row is None:
            rejected.append({"row": index, "reason": reason or "missing sku"})
            continue
        if row["sku"] in seen:
            rejected.append({"row": index, "reason": "sku is repeated"})
            continue
        seen.add(str(row["sku"]))
        rows.append(row)
    return _finish(rows, rejected)


def _woo_items(loaded: Any) -> list[Any] | None:
    if isinstance(loaded, list):
        return loaded
    if isinstance(loaded, dict) and isinstance(loaded.get("products"), list):
        return loaded["products"]
    return None


def _product(
    *,
    sku: str,
    name: str,
    brand: str,
    category: str,
    price: Any,
    currency: str,
    image_url: str,
    url: str,
    gtin: str,
    active: bool,
) -> tuple[dict[str, Any] | None, str]:
    missing = _missing_label(sku=sku, name=name, brand=brand, category=category)
    if missing:
        return None, missing
    if not image_url:
        return None, "missing image_url"
    try:
        money = _money(price)
    except ValueError as exc:
        return None, str(exc)
    code = _text(currency).upper()
    if not code:
        return None, "missing currency"
    return (
        {
            "sku": sku,
            "gtin": gtin or None,
            "name": name,
            "brand": brand,
            "category": category,
            "price": money,
            "currency": code,
            "image_url": image_url,
            "url": url,
            "family": "",
            "active": active,
            "attributes": {},
        },
        "",
    )


def _missing_label(**fields: str) -> str:
    labels = ("sku", "name", "brand", "category")
    missing = [label for label in labels if label in fields and not fields[label]]
    if not missing:
        return ""
    if len(missing) == 1:
        return "missing " + missing[0]
    return "missing " + ", ".join(missing)


def _finish(rows: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> dict[str, Any]:
    if rejected:
        return {"rows": [], "rejected": rejected}
    return {"rows": rows, "rejected": []}


def _feed_error(reason: str) -> dict[str, Any]:
    return {"rows": [], "rejected": [{"row": 0, "reason": reason}]}


def _load_json(body: Any) -> tuple[Any, str]:
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            return json.loads(body), ""
        except json.JSONDecodeError:
            return None, "not JSON"
    return body, ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _money(value: Any) -> str:
    text = _text(value).replace(" ", "")
    if not text:
        raise ValueError("price is missing")
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError("price is not a number") from exc
    if number < 0:
        raise ValueError("price is negative")
    return f"{number:.2f}"


def _flag(value: Any, *, default: str, yes: set[str]) -> bool:
    text = _text(value).lower() or default
    return text in yes


def _in_stock(value: str) -> bool:
    text = value.strip().lower() or "in stock"
    return text.startswith("in")


def _first_image(images: Any) -> str:
    if not isinstance(images, list) or not images:
        return ""
    first = images[0]
    if isinstance(first, dict):
        return _text(first.get("src") or first.get("url"))
    return _text(first)


def _variant_image(variant: dict[str, Any]) -> str:
    featured = variant.get("featured_image")
    if isinstance(featured, dict):
        return _text(featured.get("src") or featured.get("url"))
    return ""


def _woo_price(item: dict[str, Any]) -> Any:
    price = item.get("price")
    if price is None or _text(price) == "":
        return item.get("regular_price")
    return price


def _woo_category(item: dict[str, Any]) -> str:
    categories = item.get("categories")
    if isinstance(categories, list):
        for entry in categories:
            if isinstance(entry, dict):
                name = _text(entry.get("name"))
                if name:
                    return name
    return ""


def _woo_brand(item: dict[str, Any]) -> str:
    brands = item.get("brands")
    if isinstance(brands, list):
        for entry in brands:
            if isinstance(entry, dict):
                name = _text(entry.get("name"))
                if name:
                    return name
    brand = item.get("brand")
    if isinstance(brand, dict):
        name = _text(brand.get("name"))
        if name:
            return name
    if isinstance(brand, str) and brand.strip():
        return brand.strip()
    attributes = item.get("attributes")
    if isinstance(attributes, list):
        for entry in attributes:
            if not isinstance(entry, dict):
                continue
            if _text(entry.get("name")).lower() != "brand":
                continue
            options = entry.get("options")
            if isinstance(options, list) and options:
                return _text(options[0])
    return ""


def _split_price(value: str) -> tuple[str, str]:
    parts = value.split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return value, ""


def _item_fields(item: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for child in list(item):
        name = _local(child.tag)
        text = (child.text or "").strip()
        if name not in fields or "}" in child.tag:
            fields[name] = text
    return fields


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag
