"""Public saved-page candidates. No crawling, client observations, or approvals.

Expected items contain price fields only: unknown metadata is not labelled.
The consumer must compare these labelled fields, not invent missing metadata.
"""
from __future__ import annotations

import base64
import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zipfile import ZipFile
from urllib.parse import urlsplit

CORPUS = Path(__file__).with_name("gold") / "public-pages.zip"
MAX_CANDIDATES = 200
# The sealed bundle has no per-page capture date. Structured matches from it
# share this month. A row's observed_on, or a date in its structured data,
# replaces it, so two capture months are two families.
SEALED_BUNDLE_MONTH = "2026-10"


def amount(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,2})?", text):
        return None
    try:
        return format(Decimal(text).quantize(Decimal(".01")), "f")
    except InvalidOperation:
        return None


class Evidence(HTMLParser):
    """Conservative agreement: a single visible amount and structured amount."""
    def __init__(self) -> None:
        super().__init__()
        self.hidden = 0
        self.stack: list[tuple[str, bool, str]] = []
        self.visible: list[str] = []
        self.structured: set[str] = set()
        self.currencies: set[str] = set()
        self.script: list[str] | None = None
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = dict(attrs)
        hide = tag in {"script", "style", "template", "noscript"} or "hidden" in attr or attr.get("aria-hidden") == "true" or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attr.get("style", ""), re.I))
        if tag == "script" and attr.get("type", "").lower() == "application/ld+json":
            self.script = []
        if tag not in {"meta", "link", "img", "br", "input", "hr", "source", "wbr"}:
            self.stack.append((tag, hide, attr.get("itemprop", "")))
            self.hidden += int(hide)
        if "priceCurrency" in attr.get("itemprop", "").split() and attr.get("content"):
            self.currencies.add(attr["content"].upper())
        if "price" in attr.get("itemprop", "").split():
            price = amount(attr.get("content", ""))
            if price:
                self.structured.add(price)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.script is not None:
            self.blocks.append("".join(self.script))
            self.script = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                self.hidden -= sum(int(hidden) for _, hidden, _ in self.stack[i:])
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self.script is not None:
            self.script.append(data)
        if not self.hidden:
            self.visible.append(data)
            if self.stack and "price" in self.stack[-1][2].split():
                price = amount(data.strip().lstrip("$£€").strip())
                if price:
                    self.structured.add(price)
            if self.stack and "priceCurrency" in self.stack[-1][2].split():
                self.currencies.add(data.strip().upper())


def _month(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value).strip()
    if len(text) < 7 or text[4] != "-" or not (text[:4].isdigit() and text[5:7].isdigit()):
        return None
    if not 1 <= int(text[5:7]) <= 12:
        return None
    return text[:7]


def _structured_month(html: str) -> str | None:
    match = re.search(
        r'"(?:datePublished|dateModified|priceValidUntil)"\s*:\s*"(\d{4}-\d{2})',
        html,
    )
    if match is None:
        return None
    return _month(match.group(1) + "-01")


def _family(row: dict[str, Any], html: str, matched: bool) -> str:
    if matched:
        month = _month(row.get("observed_on")) or _structured_month(html) or SEALED_BUNDLE_MONTH
        family = f"jsonld-match:{month}"
    else:
        family = "auto:price-gold-109"
    if row.get("synthetic") is True:
        return family.replace(":", "-synthetic:", 1)
    return family


def agreement(html: str) -> str | None:
    evidence = Evidence()
    evidence.feed(html)
    def walk(node: Any, product: bool = False) -> None:
        if isinstance(node, list):
            for value in node:
                walk(value, product)
        elif isinstance(node, dict):
            types = node.get("@type", [])
            types = types if isinstance(types, list) else [types]
            product = product or any(str(t).rsplit("/", 1)[-1] == "Product" for t in types)
            if product and node.get("priceCurrency"):
                evidence.currencies.add(str(node["priceCurrency"]).upper())
            if product and "price" in node:
                price = amount(node["price"])
                if price:
                    evidence.structured.add(price)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, product)
    for block in evidence.blocks:
        try:
            walk(json.loads(block))
        except (ValueError, RecursionError):
            continue
    visible = {amount(m) for m in re.findall(r"(?:US\$|\$|£|€|USD|GBP|EUR)\s*(\d[\d,]*(?:\.\d{1,2})?)(?!\d)", " ".join(evidence.visible))}
    visible.discard(None)
    codes = {"$": "USD", "US$": "USD", "£": "GBP", "€": "EUR"}
    seen_currencies = {codes.get(token, token) for token in re.findall(r"US\$|\$|£|€|USD|GBP|EUR", " ".join(evidence.visible))}
    if evidence.currencies and evidence.currencies != seen_currencies:
        return None
    if len(visible) == 1 and visible == evidence.structured:
        return next(iter(visible))
    return None


def candidates(limit: int = 200, *, region: str = "us-east", corpus: Path = CORPUS) -> list[dict]:
    if region not in {"us-east", "za"} or limit <= 0:
        return []
    result: list[dict] = []
    if region != "us-east":
        return result
    allowed = set(json.loads(CORPUS.with_name("allowed-hosts.json").read_text()))
    with ZipFile(corpus) as archive:
        rows = json.loads(archive.read("manifest.json"))
        for row in rows:
            if urlsplit(row["url"]).hostname not in allowed:
                continue
            if row["split"] != "build" or row["conflict"] or row["unreadable"]:
                continue
            price = amount(row["expected_price"])
            if price is None:
                continue
            html = archive.read(row["id"] + ".html").decode()
            source = "auto:price-gold-demo:" + row["id"]
            matched = agreement(html) == price
            if matched:
                source += ":visible-structured-agreement"
            family = _family(row, html, matched)
            expected = {"items": [{"price": price}]}
            if row.get("currency"):
                expected["items"][0]["currency"] = row["currency"]
            for skill, payload in (
                ("tiger.price_extract", {"html": html, "url": row["url"]}),
                ("tiger.price_ocr", {"png_base64": base64.b64encode(archive.read(row["id"] + ".png")).decode(), "url": row["url"]}),
            ):
                result.append({"skill_id": skill, "input": payload, "expected": expected,
                               "label_source": source, "label_family": family, "region": region, "public": True})
                if len(result) >= min(limit, MAX_CANDIDATES):
                    return result
    return result
