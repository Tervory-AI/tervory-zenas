"""tiger.price_ocr — a price read from a picture. Tesseract runs in the Tiger box."""

from __future__ import annotations

import re
from typing import Any

from pathlib import Path
import importlib.util

_MONEY = re.compile(
    r"(?P<sym>US\$|\$|€|£)\s?(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
    r"|(?P<code>USD|EUR|GBP|CAD|AUD)\s?(?P<code_amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
)
_CONFIDENCE = 0.35


def _extract_module():
    path = Path(__file__).resolve().parents[2] / "tiger.price_extract" / "1.0.0" / "run.py"
    spec = importlib.util.spec_from_file_location("tiger_price_extract_prices", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("price reader is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prices_from_text(text: str, page_url: str) -> list[dict[str, Any]]:
    """Currency amounts in OCR text. A bare number is not a price."""
    prices = _extract_module()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _MONEY.finditer(text or ""):
        amount = match.group("amount") or match.group("code_amount")
        price = prices.canonical_price(amount)
        if price is None or price in seen:
            continue
        seen.add(price)
        symbol = match.group("sym")
        code = match.group("code")
        currency = prices._currency(code) if code else prices._currency(symbol)
        items.append({
            "name": None,
            "sku": None,
            "gtin": None,
            "price": price,
            "currency": currency,
            "availability": None,
            "url": page_url or None,
            "method": "ocr",
        })
        if len(items) >= 20:
            break
    return items


async def _tiger_text(ctx, png_base64: str) -> str:
    env = getattr(ctx, "env", {}) if ctx is not None else {}
    base = str(env.get("TIGER_URL") or "")
    http = getattr(ctx, "http", None) if ctx is not None else None
    if not base or http is None:
        return ""
    response = await http.post(
        f"{base.rstrip('/')}/ocr",
        json={"png_base64": png_base64},
        timeout=30,
    )
    if getattr(response, "status_code", 0) != 200:
        return ""
    data = response.json()
    if not isinstance(data, dict):
        return ""
    return str(data.get("text") or "")


async def run(payload: dict, ctx) -> dict:
    page_url = str(payload.get("url") or "")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        png = payload.get("png_base64")
        if not isinstance(png, str) or not png:
            return {"items": [], "method": None, "confidence": 0}
        try:
            text = await _tiger_text(ctx, png)
        except Exception:  # noqa: BLE001 — the box is down; there is no price to report
            text = ""
    items = prices_from_text(text or "", page_url)
    return {
        "items": items,
        "method": "ocr",
        "confidence": _CONFIDENCE if items else 0.15,
    }
