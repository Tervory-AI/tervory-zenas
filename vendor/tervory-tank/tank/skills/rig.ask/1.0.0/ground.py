"""Turn a model reply into shopper text. The model cannot add parts or invent products."""

from __future__ import annotations

import json
import re
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "and", "for", "with", "this", "that", "what", "which", "when", "where",
    "will", "does", "have", "from", "your", "about", "into", "them", "they", "also",
    "please", "would", "could", "should", "there", "their", "only", "just",
}
_UNKNOWN = "I don't know"


def in_catalogue(question: str, products: list[dict[str, Any]], fits: list[dict[str, Any]]) -> bool:
    return _overlaps(question, products, fits)


def ask(payload: dict[str, Any], model_text: str) -> dict[str, Any]:
    products = list(payload.get("products") or [])
    known = {_id(row) for row in products if _id(row)}
    if not _overlaps(str(payload.get("question") or ""), products, list(payload.get("fits") or [])):
        return {"answer": _UNKNOWN, "cites": []}
    parsed = _object(model_text)
    cites = [str(item) for item in (parsed.get("cites") or []) if str(item) in known]
    answer = str(parsed.get("answer") or "").strip()
    if not cites or not answer or answer.lower() == _UNKNOWN.lower():
        return {"answer": _UNKNOWN, "cites": []}
    return {"answer": answer, "cites": cites}


def explain(payload: dict[str, Any], model_text: str) -> dict[str, Any]:
    """Why-lines only. Any parts in the model reply are ignored."""
    parsed = _object(model_text)
    lines = parsed.get("why")
    if not isinstance(lines, list):
        lines = payload.get("fallback_why") or []
    why = [str(line).strip() for line in lines if str(line).strip()]
    if not why:
        fallback = payload.get("fallback_why") or []
        why = [str(line).strip() for line in fallback if str(line).strip()]
    return {"why": why[:8]}


def fit_suggest(payload: dict[str, Any], model_text: str) -> dict[str, Any]:
    products = list(payload.get("products") or [])
    skus = {_id(row) for row in products}
    families = {str(row.get("family") or "") for row in products if row.get("family")}
    parsed = _object(model_text)
    rows = parsed.get("fits")
    if not isinstance(rows, list):
        rows = parsed.get("rows") if isinstance(parsed.get("rows"), list) else []
    fits: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        part = str(row.get("part_sku") or "")
        fits_sku = str(row.get("fits_sku") or "") or None
        fits_family = str(row.get("fits_family") or "") or None
        if part not in skus:
            continue
        if fits_sku and fits_sku not in skus:
            continue
        if fits_family and fits_family not in families:
            continue
        if bool(fits_sku) == bool(fits_family):
            continue
        rule = row.get("rule") if isinstance(row.get("rule"), dict) else {}
        try:
            confidence = float(row.get("confidence") if row.get("confidence") is not None else 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        fits.append(
            {
                "part_sku": part,
                "fits_sku": fits_sku,
                "fits_family": fits_family,
                "rule": rule,
                "note": "",
                "source": "suggested",
                "approved": False,
                "confidence": confidence,
            }
        )
    return {"fits": fits}


def ask_prompt(payload: dict[str, Any]) -> str:
    catalogue = json.dumps(
        {"products": payload.get("products") or [], "fits": payload.get("fits") or []},
        default=str,
    )
    return (
        "Answer the shopper only from the catalogue JSON. "
        'Reply with JSON: {"answer": string, "cites": [product id]}. '
        f'If the catalogue does not answer it, reply {{"answer": "{_UNKNOWN}", "cites": []}}. '
        f"Catalogue: {catalogue}\nQuestion: {payload.get('question')}"
    )


def explain_prompt(payload: dict[str, Any]) -> str:
    return (
        "Write why these parts fit. You cannot add, remove, or rename parts. "
        'Reply with JSON: {"why": [string]}. '
        f"Material: {json.dumps(payload.get('material') or [], default=str)}"
    )


def suggest_prompt(payload: dict[str, Any]) -> str:
    return (
        "Propose fit rows from product attributes. "
        'Reply with JSON: {"fits": [{"part_sku", "fits_sku" or "fits_family", "rule", "confidence"}]}. '
        "Use only the product ids you are given. "
        f"Products: {json.dumps(payload.get('products') or [], default=str)}"
    )


def _overlaps(question: str, products: list[dict[str, Any]], fits: list[dict[str, Any]]) -> bool:
    tokens = [token for token in _TOKEN.findall(question.lower()) if len(token) >= 4 and token not in _STOP]
    if not tokens:
        return False
    hay = _blob(products, fits)
    return any(token in hay for token in tokens)


def _blob(products: list[dict[str, Any]], fits: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in products:
        for key in ("id", "sku", "name", "brand", "category"):
            parts.append(str(row.get(key) or ""))
        attributes = row.get("attributes") or {}
        if isinstance(attributes, dict):
            for key, value in attributes.items():
                parts.append(str(key))
                parts.append(str(value))
    for fit in fits:
        parts.append(str(fit.get("part_sku") or ""))
        parts.append(str(fit.get("fits_sku") or ""))
        parts.append(str(fit.get("fits_family") or ""))
        parts.append(str(fit.get("note") or ""))
    return " ".join(parts).lower()


def _id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("sku") or "")


def _object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            loaded = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return loaded if isinstance(loaded, dict) else {}
