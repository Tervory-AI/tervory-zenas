"""Fit answers from catalogue rows. The model cannot add a part."""

from __future__ import annotations

import json
import re
from typing import Any

_FALLBACK = "These parts are listed as compatible in the catalogue."


def supported_fits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Exact product fits first, then families the catalogue already names.

    A row is kept only when the part and its target are in the product list.
    ``part_sku`` keeps that part's own list. ``focus_sku`` asks what fits one
    product: an exact row, then a family that product belongs to.
    """
    products = [row for row in _rows(payload.get("products")) if _active(row)]
    known = {_sku(row) for row in products if _sku(row)}
    families: dict[str, list[str]] = {}
    for row in products:
        family = str(row.get("family") or "").strip()
        sku = _sku(row)
        if not family or not sku:
            continue
        members = families.setdefault(family, [])
        if sku not in members:
            members.append(sku)
    part = str(payload.get("part_sku") or "").strip()
    focus = "" if part else str(payload.get("focus_sku") or "").strip()
    seen: set[str] = set()
    exact: list[dict[str, Any]] = []
    family_rows: list[dict[str, Any]] = []
    for raw in _rows(payload.get("fits")):
        labelled = _label(raw, known, families)
        if labelled is None:
            continue
        if part and labelled["part_sku"] != part:
            continue
        if focus:
            if labelled["fits_sku"]:
                if labelled["fits_sku"] != focus:
                    continue
            elif focus not in families.get(labelled["fits_family"] or "", []):
                continue
        key = json.dumps(labelled, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        if labelled["fits_sku"]:
            exact.append(labelled)
        else:
            family_rows.append(labelled)
    exact.sort(key=lambda row: (row["part_sku"], row["fits_sku"] or ""))
    family_rows.sort(key=lambda row: (row["part_sku"], row["fits_family"] or ""))
    return exact + family_rows


def one_sentence(text: str) -> str:
    """The first sentence only. A JSON reply is not an explanation.

    A mark ends a sentence only when whitespace or the end of the text follows
    it. A dot inside a number or in ``e.g.`` does not. The result is at most
    200 characters.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned or cleaned[0] in "{[":
        return ""
    end = len(cleaned)
    for index, char in enumerate(cleaned):
        if char not in ".!?":
            continue
        nxt = cleaned[index + 1] if index + 1 < len(cleaned) else ""
        if nxt and not nxt.isspace():
            continue
        if char == "." and _keeps_dot(cleaned, index):
            continue
        end = index + 1
        break
    return cleaned[:end][:200]


def ground_explanation(sentence: str, products: list[Any], fits: list[dict[str, Any]]) -> str:
    """Replace a sentence that names a product the fit list does not return."""
    if not sentence:
        return ""
    allowed: set[str] = set()
    for fit in fits:
        for key in ("part_sku", "fits_sku"):
            token = str(fit.get(key) or "").strip()
            if token:
                allowed.add(token.casefold())
    for product in _rows(products):
        sku = _sku(product)
        if sku and sku.casefold() in allowed:
            name = str(product.get("name") or "").strip()
            if name:
                allowed.add(name.casefold())
    for product in _rows(products):
        sku = _sku(product)
        name = str(product.get("name") or "").strip()
        if sku and sku.casefold() not in allowed and _mentions(sentence, sku):
            return _FALLBACK
        if name and name.casefold() not in allowed and _mentions(sentence, name):
            return _FALLBACK
    return sentence


def _label(
    raw: dict[str, Any],
    known: set[str],
    families: dict[str, list[str]],
) -> dict[str, Any] | None:
    part = str(raw.get("part_sku") or "").strip()
    fits_sku = str(raw.get("fits_sku") or "").strip()
    fits_family = str(raw.get("fits_family") or "").strip()
    if not part or part not in known:
        return None
    if bool(fits_sku) == bool(fits_family):
        return None
    if fits_sku and fits_sku not in known:
        return None
    if fits_family and fits_family not in families:
        return None
    if not _approved(raw):
        return None
    rule = raw.get("rule") if isinstance(raw.get("rule"), dict) else {}
    return {
        "part_sku": part,
        "fits_sku": fits_sku or None,
        "fits_family": fits_family or None,
        "rule": dict(rule),
    }


def _approved(fit: dict[str, Any]) -> bool:
    """Same rule as the room: a suggestion counts only after a person accepts it."""
    source = str(fit.get("source") or "import")
    if source == "suggested":
        return bool(fit.get("approved"))
    if fit.get("approved") is False:
        return False
    return True


def _keeps_dot(text: str, index: int) -> bool:
    nxt = text[index + 1] if index + 1 < len(text) else ""
    prev = text[index - 1] if index else ""
    if nxt.isdigit() or (prev.isdigit() and nxt.isdigit()):
        return True
    return text[max(0, index - 3) : index + 1].lower() == "e.g."


def _mentions(text: str, token: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _sku(row: dict[str, Any]) -> str:
    return str(row.get("sku") or row.get("id") or "").strip()


def _active(row: dict[str, Any]) -> bool:
    return bool(row.get("active", True))
