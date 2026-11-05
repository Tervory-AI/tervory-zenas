"""tiger.price_extract — prices a page actually shows. Pure code. No network, no model."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from selectolax.parser import HTMLParser

CONFIDENCE = {"jsonld": 0.9, "microdata": 0.75, "opengraph": 0.6, "pattern": 0.45}
_JSONLD = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)
_MONEY = re.compile(
    r"(?P<sym>US\$|\$|€|£)\s?(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
    r"|(?P<code>USD|EUR|GBP|CAD|AUD)\s?(?P<code_amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
)
_SYMBOL = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP"}
_AVAIL = {
    "instock": "InStock",
    "outofstock": "OutOfStock",
    "preorder": "PreOrder",
    "discontinued": "Discontinued",
    "limitedavailability": "LimitedAvailability",
    "soldout": "OutOfStock",
}


def _load_html(payload: dict) -> str:
    html = payload.get("html")
    if isinstance(html, str) and html.strip():
        return html
    name = payload.get("fixture")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
        return ""
    path = Path(__file__).resolve().parent / "evals" / "fixtures" / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def canonical_price(raw: Any) -> str | None:
    """A price the page stated, as a two-decimal string. None when it is not a price."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        text = str(raw)
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        return None
    text = text.replace("\xa0", "").replace(" ", "")
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text or text in {"-", ".", ",", "-."}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")
        elif re.fullmatch(r"-?\d+,\d{2}", text):
            text = text.replace(",", ".")
        else:
            return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number < 0:
        return None
    return f"{number.quantize(Decimal('0.01'))}"


def availability_token(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    tail = text.rstrip("/").split("/")[-1]
    key = re.sub(r"[\s_\-]+", "", tail).lower()
    if key in _AVAIL:
        return _AVAIL[key]
    key = re.sub(r"[\s_\-]+", "", text).lower()
    return _AVAIL.get(key)


def _clean(value: Any) -> str | None:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _currency(raw: Any) -> str | None:
    text = _clean(raw)
    if not text:
        return None
    upper = text.upper()
    if upper in _SYMBOL:
        return _SYMBOL[upper]
    if upper in {"USD", "EUR", "GBP", "CAD", "AUD"} or re.fullmatch(r"[A-Z]{3}", upper):
        return upper
    return _SYMBOL.get(text)


def _http_url(value: Any) -> str | None:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        return _http_url(value.get("url")) or _http_url(value.get("@id"))
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def _gtin(node: dict) -> str | None:
    for key in ("gtin13", "gtin12", "gtin14", "gtin8", "gtin"):
        found = _clean(node.get(key))
        if found:
            return found
    return None


def _type_names(node: dict) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    names: set[str] = set()
    for item in values:
        if not item:
            continue
        names.add(str(item).rsplit("/", 1)[-1].lower())
    return names


def _item(
    name: str | None,
    sku: str | None,
    gtin: str | None,
    price: str | None,
    currency: str | None,
    availability: str | None,
    url: str | None,
    method: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "sku": sku,
        "gtin": gtin,
        "price": price,
        "currency": currency,
        "availability": availability,
        "url": url,
        "method": method,
    }


def _spec_price(offer: dict) -> str | None:
    specs = offer.get("priceSpecification")
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, list):
        return None
    sale = None
    plain = None
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        price = canonical_price(spec.get("price"))
        if price is None:
            continue
        kind = str(spec.get("priceType") or "")
        if "SalePrice" in kind:
            sale = price
        elif "ListPrice" in kind:
            continue
        elif plain is None:
            plain = price
    return sale or plain


def _offer_price(offer: dict) -> str | None:
    return _spec_price(offer) or canonical_price(offer.get("price")) or canonical_price(offer.get("lowPrice"))


def _offer_nodes(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        found: list[dict] = []
        for item in raw:
            found.extend(_offer_nodes(item))
        return found
    if not isinstance(raw, dict):
        return []
    nested = raw.get("offers")
    if "aggregateoffer" in _type_names(raw) and nested is not None:
        found = _offer_nodes(nested)
        return found or [raw]
    return [raw]


def _walk(node: Any, products: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, products)
        return
    if not isinstance(node, dict):
        return
    if "product" in _type_names(node):
        products.append(node)
    for key in ("@graph", "mainEntity", "itemListElement"):
        if key in node:
            _walk(node[key], products)


def _from_product(product: dict, page_url: str) -> list[dict]:
    name = _clean(product.get("name"))
    sku = _clean(product.get("sku")) or _clean(product.get("productID"))
    gtin = _gtin(product)
    product_url = _http_url(product.get("url")) or page_url or None
    offers = _offer_nodes(product.get("offers"))
    if not offers:
        return [_item(name, sku, gtin, None, None, None, product_url, "jsonld")]
    items = []
    for offer in offers:
        price = _offer_price(offer)
        currency = _currency(offer.get("priceCurrency"))
        if currency is None:
            currency = _currency(_spec_currency(offer))
        url = _http_url(offer.get("url")) or product_url
        items.append(_item(
            name,
            _clean(offer.get("sku")) or sku,
            _gtin(offer) or gtin,
            price,
            currency,
            availability_token(offer.get("availability")),
            url,
            "jsonld",
        ))
    return items


def _spec_currency(offer: dict) -> Any:
    specs = offer.get("priceSpecification")
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, list):
        return None
    for spec in specs:
        if isinstance(spec, dict) and spec.get("priceCurrency"):
            return spec.get("priceCurrency")
    return None


def _jsonld_items(html: str, page_url: str) -> list[dict]:
    products: list[dict] = []
    for match in _JSONLD.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        _walk(data, products)
    items: list[dict] = []
    for product in products:
        items.extend(_from_product(product, page_url))
    return items[:20]


def _prop_value(el: Any) -> str | None:
    attrs = el.attributes or {}
    for key in ("content", "href"):
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = el.text(separator=" ", strip=True) if el.text else ""
    return text or None


def _nearest_scope(node: Any) -> Any:
    current = node.parent
    while current is not None:
        attrs = current.attributes or {}
        if "itemscope" in attrs:
            return current
        current = current.parent
    return None


def _micro_items(html: str, page_url: str) -> list[dict]:
    tree = HTMLParser(html)
    items: list[dict] = []
    for product in tree.css("[itemscope]"):
        itemtype = (product.attributes or {}).get("itemtype") or ""
        if "schema.org/Product" not in itemtype and not itemtype.endswith("Product"):
            continue
        if _nearest_scope(product) is not None and "Product" in ((_nearest_scope(product).attributes or {}).get("itemtype") or ""):
            continue
        fields: dict[str, str] = {}
        offers: dict[int, dict[str, str]] = {}
        product_id = product.mem_id
        for el in product.css("[itemprop]"):
            prop = (el.attributes or {}).get("itemprop")
            if not prop:
                continue
            scope = _nearest_scope(el)
            if scope is None:
                continue
            scope_type = (scope.attributes or {}).get("itemtype") or ""
            value = _prop_value(el)
            if not value:
                continue
            if scope.mem_id != product_id and "Offer" in scope_type:
                offers.setdefault(scope.mem_id, {})[prop] = value
            elif scope.mem_id == product_id:
                fields.setdefault(prop, value)
        name = fields.get("name")
        sku = fields.get("sku")
        gtin = fields.get("gtin13") or fields.get("gtin") or fields.get("gtin12") or fields.get("gtin8")
        if not offers:
            price = canonical_price(fields.get("price"))
            items.append(_item(
                name, sku, gtin, price, _currency(fields.get("priceCurrency")),
                availability_token(fields.get("availability")), page_url or None, "microdata",
            ))
            continue
        for offer in offers.values():
            items.append(_item(
                name,
                offer.get("sku") or sku,
                offer.get("gtin13") or offer.get("gtin") or gtin,
                canonical_price(offer.get("price")),
                _currency(offer.get("priceCurrency")),
                availability_token(offer.get("availability")),
                _http_url(offer.get("url")) or page_url or None,
                "microdata",
            ))
    return items[:20]


def _meta_map(html: str) -> dict[str, str]:
    tree = HTMLParser(html)
    found: dict[str, str] = {}
    for meta in tree.css("meta"):
        attrs = meta.attributes or {}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content")
        if key and isinstance(content, str) and key not in found:
            found[key] = content.strip()
    return found


def _opengraph_items(html: str, page_url: str) -> list[dict]:
    meta = _meta_map(html)
    amount = meta.get("product:price:amount") or meta.get("og:price:amount")
    price = canonical_price(amount)
    if price is None:
        return []
    name = meta.get("og:title") or meta.get("product:title")
    url = _http_url(meta.get("og:url")) or page_url or None
    currency = _currency(meta.get("product:price:currency") or meta.get("og:price:currency"))
    availability = availability_token(meta.get("product:availability") or meta.get("og:availability"))
    sku = meta.get("product:retailer_item_id")
    return [_item(name, sku, None, price, currency, availability, url, "opengraph")]


def _title_window(tree: HTMLParser) -> tuple[str | None, str]:
    heading = tree.css_first("h1")
    if heading is not None:
        chunks = [heading.text(separator=" ", strip=True) or ""]
        sib = heading.next
        steps = 0
        while sib is not None and steps < 6:
            chunks.append(sib.text(separator=" ", strip=True) or "")
            sib = sib.next
            steps += 1
        name = chunks[0] or None
        return name, " ".join(chunks)[:800]
    title = tree.css_first("title")
    text = title.text(strip=True) if title is not None else ""
    return (text or None), text[:800]


def _pattern_items(html: str, page_url: str) -> list[dict]:
    tree = HTMLParser(html)
    name, window = _title_window(tree)
    match = _MONEY.search(window)
    if match is None:
        return []
    amount = match.group("amount") or match.group("code_amount")
    price = canonical_price(amount)
    if price is None:
        return []
    symbol = match.group("sym")
    code = match.group("code")
    currency = _currency(code) if code else _currency(symbol)
    return [_item(name, None, None, price, currency, None, page_url or None, "pattern")]


def extract(html: str, page_url: str = "") -> dict[str, Any]:
    """First method that can point at a price wins. Otherwise a product with no price."""
    unpriced: tuple[list[dict], str] | None = None
    finders = (
        ("jsonld", _jsonld_items),
        ("microdata", _micro_items),
        ("opengraph", _opengraph_items),
        ("pattern", _pattern_items),
    )
    for method, finder in finders:
        items = finder(html, page_url)
        if any(item.get("price") for item in items):
            return {"items": items, "method": method, "confidence": CONFIDENCE[method]}
        if items and unpriced is None:
            unpriced = (items, method)
    if unpriced is not None:
        items, method = unpriced
        return {"items": items, "method": method, "confidence": CONFIDENCE[method]}
    return {"items": [], "method": None, "confidence": 0}


async def run(payload: dict, ctx) -> dict:
    del ctx
    html = _load_html(payload)
    page_url = str(payload.get("url") or "")
    if not html:
        return {"items": [], "method": None, "confidence": 0}
    return extract(html, page_url)
