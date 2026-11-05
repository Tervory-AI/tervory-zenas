"""tiger.price_extract 1.1.0 — prices a page actually shows. Pure code. No network, no model.

1.0.0 is unchanged. This version adds price elements it can point at, and it
refuses instruction text that is not a price.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from typing import Any

from selectolax.parser import HTMLParser

_V1_PATH = Path(__file__).resolve().parents[1] / "1.0.0" / "run.py"
_spec = importlib.util.spec_from_file_location("tiger_price_extract_v1", _V1_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError("tiger.price_extract 1.0.0 is missing")
v1 = importlib.util.module_from_spec(_spec)
sys.modules["tiger_price_extract_v1"] = v1
_spec.loader.exec_module(v1)

CONFIDENCE = {**v1.CONFIDENCE, "element": 0.55}
_TRICK = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions|"
    r"system prompt|disregard (the |all )?(rules|instructions)|"
    r"you are (now )?a |output the price|new instructions",
    re.I,
)
_SKIP = re.compile(
    r"old-price|was-price|compare|regular-price|msrp|rrp|list-price|original-?price|"
    r"line-through|strike|shipping|tax-amount|\bsave\b|crossed",
    re.I,
)
_SALE_PHRASE = re.compile(
    r"Sale Price\s*(?P<sym>US\$|[$€£]|USD|EUR|GBP|CAD|AUD)?\s*(?P<amount>\d[\d.,]*)",
    re.I,
)
_LIST_PHRASE = re.compile(
    r"List Price\s*(?:US\$|[$€£]|USD|EUR|GBP|CAD|AUD)?\s*(?P<amount>\d[\d.,]*)",
    re.I,
)
_SALE = re.compile(r"\b(sale|special|current-price|price-now)\b|<ins\b", re.I)
_AMOUNT_THEN = re.compile(
    r"(?P<amount>\d{1,3}(?:[.\s\u00a0]\d{3})*(?:[.,]\d{2})|\d+[.,]\d{2})"
    r"\s?(?P<sym>€|£|\$|USD|EUR|GBP|CAD|AUD)(?![A-Za-z])",
    re.I,
)
_HERO_PRICE = re.compile(
    r"data-seo-id=[\"']hero-price[\"'][^>]*>\s*(?:Now\s+)?"
    r"(?:US\$|[$€£])\s*(?P<amount>\d[\d,]*(?:\.\d{2})?)",
    re.I,
)
_JSONLD = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)
_WAS = re.compile(
    r"\b(was|rrp|msrp|list|instead of)\b[^0-9]{0,16}"
    r"(?:US\$|[$€£]|USD|EUR|GBP|CAD|AUD)?\s?\d[\d.,\s]*",
    re.I,
)
# Another product on the same page. Not the product this page is selling.
_RELATED = re.compile(
    r"ymal|you-may-also|related[-_ ]?product|product-card|recommendation|"
    r"upsell|cross-?sell|also-like|similar-product",
    re.I,
)


def _load_html(payload: dict) -> str:
    return v1._load_html(payload)


def _money_in(text: str) -> tuple[str, str | None] | None:
    if not text or _TRICK.search(text):
        return None
    chunk = text
    now = re.split(r"\b(now|sale price|special price)\b", text, maxsplit=1, flags=re.I)
    if len(now) == 3:
        chunk = now[-1]
    chunk = _WAS.sub(" ", chunk)
    match = v1._MONEY.search(chunk) or _AMOUNT_THEN.search(chunk)
    if match is None:
        return None
    amount = match.group("amount") or match.groupdict().get("code_amount")
    price = v1.canonical_price(amount)
    if price is None:
        return None
    symbol = match.groupdict().get("sym")
    code = match.groupdict().get("code")
    currency = v1._currency(code) if code else v1._currency(symbol)
    return price, currency


def _blob(node: Any) -> str:
    attrs = node.attributes or {}
    return f"{node.tag} {attrs.get('class') or ''} {attrs.get('id') or ''}"


def _related(node: Any) -> bool:
    """True when this node sits in a block of other products."""
    current = node
    steps = 0
    while current is not None and steps < 10:
        if _RELATED.search(_blob(current)):
            return True
        current = current.parent
        steps += 1
    return False


def _rejected(node: Any) -> bool:
    current = node
    steps = 0
    while current is not None and steps < 8:
        blob = _blob(current)
        if current.tag in {"nav", "footer", "del", "s", "aside"}:
            return True
        if current.tag == "header" and "product" not in blob.lower():
            return True
        if _SKIP.search(blob):
            return True
        if _TRICK.search(blob):
            return True
        current = current.parent
        steps += 1
    return False


def _attr_price(node: Any) -> tuple[str, str | None] | None:
    """A price attribute that already looks like a price. Integers of 3+ digits are left alone (often cents)."""
    attrs = node.attributes or {}
    for key in ("content", "data-price", "data-product-price", "data-price-amount"):
        value = attrs.get(key)
        if not isinstance(value, str):
            continue
        raw = value.strip()
        if re.fullmatch(r"\d{3,}", raw):
            continue
        price = v1.canonical_price(raw)
        if price is not None:
            return price, None
    return None


def _node_price(node: Any) -> tuple[str, str | None] | None:
    # Empty separator keeps "$" "19" "." "99" as "$19.99" instead of "$ 19 . 99".
    shown = node.text(separator="", strip=True) if node.text else ""
    found = _money_in(shown)
    attr = _attr_price(node)
    if found is not None and attr is not None and found[0] != attr[0]:
        # The content attribute is the price when the visible text was split.
        if "." in attr[0] and attr[0].split(".")[0] == found[0].split(".")[0]:
            return attr[0], found[1] or attr[1]
    if found is not None:
        return found
    return attr


def _member_prices(node: Any, found: set[str]) -> None:
    """Prices that apply only to a member tier. The page shows these as current."""
    if isinstance(node, list):
        for item in node:
            _member_prices(item, found)
        return
    if not isinstance(node, dict):
        return
    if node.get("validForMemberTier") is not None:
        price = v1.canonical_price(node.get("price"))
        if price:
            found.add(price)
    for value in node.values():
        if isinstance(value, (dict, list)):
            _member_prices(value, found)


def _shown_member_price(html: str) -> str | None:
    """Hero amount when it is also a member-tier price in the structured data."""
    match = _HERO_PRICE.search(html)
    if match is None:
        return None
    hero = v1.canonical_price(match.group("amount"))
    if hero is None:
        return None
    members: set[str] = set()
    for block in _JSONLD.finditer(html):
        raw = unescape(block.group(1)).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _member_prices(data, members)
    if hero not in members:
        return None
    return hero


def _jsonld_items(html: str, page_url: str) -> list[dict]:
    items = v1._jsonld_items(html, page_url)
    if not any(item.get("price") for item in items):
        plain = unescape(html)
        if plain != html:
            items = v1._jsonld_items(plain, page_url)
    shown = _shown_member_price(html)
    if shown and items and items[0].get("price"):
        try:
            lower = Decimal(shown) < Decimal(str(items[0]["price"]))
        except InvalidOperation:
            lower = False
        if lower:
            items[0]["price"] = shown
    return items


_BUY_BOX = re.compile(
    r"customerVisiblePrice\]\[amount\]\"\s+value=\"(?P<amount>\d+(?:\.\d+)?)\"",
)


def _buy_box_items(html: str, page_url: str) -> list[dict]:
    match = _BUY_BOX.search(html)
    if match is None:
        return []
    price = v1.canonical_price(match.group("amount"))
    if price is None:
        return []
    tree = HTMLParser(html)
    name, _window = v1._title_window(tree)
    return [v1._item(name, None, None, price, "USD", None, page_url or None, "element")]


def _sale_and_higher(html: str) -> bool:
    """A sale amount and a higher amount for the same buyer on one product.

    A member price is not this case. The higher figure there is the non-member
    price, and the visible member amount still stands.
    """
    sale_match = _SALE_PHRASE.search(html)
    listed = _LIST_PHRASE.search(html)
    if sale_match is None or listed is None:
        return False
    sale = v1.canonical_price(sale_match.group("amount"))
    other = v1.canonical_price(listed.group("amount"))
    if sale is None or other is None:
        return False
    try:
        return Decimal(other) > Decimal(sale)
    except InvalidOperation:
        return False


def _sale_phrase_item(html: str, page_url: str, name: str | None) -> dict | None:
    match = _SALE_PHRASE.search(html)
    if match is None:
        return None
    price = v1.canonical_price(match.group("amount"))
    if price is None or price == "0.00":
        return None
    return v1._item(
        name, None, None, price, v1._currency(match.group("sym")), None, page_url or None, "element",
    )


def _element_items(html: str, page_url: str) -> list[dict]:
    if _TRICK.search(html) and not v1._MONEY.search(html):
        return []
    tree = HTMLParser(html)
    name, _window = v1._title_window(tree)
    phrase = _sale_phrase_item(html, page_url, name)
    if phrase is not None:
        return [phrase]
    selectors = (
        "ins",
        "[itemprop='price']",
        "[property='price']",
        "[data-price]",
        "[data-product-price]",
        "[data-price-amount]",
        ".price",
        ".product-price",
        ".woocommerce-Price-amount",
        ".special-price",
        ".sales",
        ".current-price",
    )
    seen: set[int] = set()
    hits: list[tuple[bool, str, str | None]] = []
    for selector in selectors:
        for node in tree.css(selector):
            if node.mem_id in seen:
                continue
            seen.add(node.mem_id)
            if _related(node) or _rejected(node):
                continue
            found = _node_price(node)
            if found is None:
                continue
            price, currency = found
            blob = _blob(node)
            parent_blob = _blob(node.parent) if node.parent is not None else ""
            sale = bool(_SALE.search(blob) or _SALE.search(parent_blob) or node.tag == "ins")
            hits.append((sale, price, currency))
    sales = [hit for hit in hits if hit[0]]
    if sales:
        _sale, price, currency = sales[0]
        return [v1._item(name, None, None, price, currency, None, page_url or None, "element")]
    distinct = {price for _sale, price, _currency in hits}
    # Two amounts on this product, and no sale mark: do not pick one.
    if len(distinct) > 1:
        return [{"ambiguous": True}]
    if len(distinct) == 1:
        _sale, price, currency = hits[0]
        return [v1._item(name, None, None, price, currency, None, page_url or None, "element")]
    return []


def _pattern_items(html: str, page_url: str) -> list[dict]:
    tree = HTMLParser(html)
    _name, window = v1._title_window(tree)
    if _TRICK.search(window):
        return []
    return v1._pattern_items(html, page_url)


def extract(html: str, page_url: str = "") -> dict[str, Any]:
    """First method that can point at a price wins. Otherwise a product with no price."""
    if _sale_and_higher(html):
        return {"items": [], "method": None, "confidence": 0}
    unpriced: tuple[list[dict], str] | None = None
    deferred: tuple[list[dict], str] | None = None
    finders = (
        ("jsonld", _jsonld_items),
        ("microdata", v1._micro_items),
        ("opengraph", v1._opengraph_items),
        ("element", _buy_box_items),
        ("element", _element_items),
        ("pattern", _pattern_items),
    )
    for method, finder in finders:
        items = finder(html, page_url)
        if any(item.get("ambiguous") for item in items):
            return {"items": [], "method": None, "confidence": 0}
        if any(item.get("price") for item in items):
            # A wrapper with no price, then other offers, is not the price on the title.
            if items[0].get("price"):
                return {"items": items, "method": method, "confidence": CONFIDENCE[method]}
            if deferred is None:
                deferred = (items, method)
            continue
        if items and unpriced is None:
            unpriced = (items, method)
    if deferred is not None:
        items, method = deferred
        return {"items": items, "method": method, "confidence": CONFIDENCE[method]}
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
