"""Search, fitment, and setup shapes. No database and no network.

Search ranks with token overlap (full text) and character trigrams. Postgres
runs the same two signals in SQL; this function is what evals and the memory
store use.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]+")
_BREAKDOWN = ("cost", "duty", "vat", "margin", "landed", "breakdown")


def money(value: Any) -> int | float:
    number = Decimal(str(value)).quantize(Decimal("0.01"))
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def public_product(row: dict[str, Any]) -> dict[str, Any]:
    attributes = row.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    result = {
        "id": str(row.get("id") or row.get("sku") or ""),
        "name": str(row.get("name") or ""),
        "brand": str(row.get("brand") or ""),
        "category": str(row.get("category") or ""),
        "image": str(row.get("image") or row.get("image_url") or ""),
        "price": _price(row),
        "attributes": attributes,
    }
    if isinstance(row.get("availability"), dict):
        result["availability"] = {key: row["availability"][key] for key in ("status", "eta")
                                  if key in row["availability"]}
    return result


def _price(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("price")
    if isinstance(raw, dict) and "amount" in raw:
        return {
            "amount": money(raw.get("amount")),
            "currency": str(raw.get("currency") or ""),
            "indicative": True,
        }
    return {
        "amount": money(raw or 0),
        "currency": str(row.get("currency") or ""),
        "indicative": True,
    }


def _sku(row: dict[str, Any]) -> str:
    return str(row.get("sku") or row.get("id") or "")


def _active(row: dict[str, Any]) -> bool:
    return bool(row.get("active", True))


def _haystack(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("name") or ""),
        str(row.get("brand") or ""),
        _sku(row),
        str(row.get("category") or ""),
    ]
    attributes = row.get("attributes") or {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            parts.append(str(key))
            parts.append(str(value))
    return " ".join(parts)


def _trigrams(text: str) -> set[str]:
    padded = f"  {text.lower()}  "
    if len(padded) < 3:
        return set()
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def _similarity(left: str, right: str) -> float:
    a = _trigrams(left)
    b = _trigrams(right)
    if not a or not b:
        return 0.0
    return (2 * len(a & b)) / (len(a) + len(b))


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("q") or "").strip()
    limit = _limit(payload.get("limit"))
    tenant = payload.get("tenant_id")
    rows = list(payload.get("products") or [])
    if tenant is not None and str(tenant) != "":
        rows = [row for row in rows if str(row.get("tenant_id")) == str(tenant)]
    rows = [row for row in rows if _active(row)]
    if not query:
        return {"results": [], "ids": []}
    needles = _tokens(query)
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for row in rows:
        hay = _haystack(row).lower()
        hits = sum(1 for token in needles if token in hay) if needles else 0
        token_score = (hits / len(needles)) if needles else 0.0
        sim = max(
            _similarity(query, str(row.get("name") or "")),
            _similarity(query, str(row.get("brand") or "")),
            _similarity(query, _sku(row)),
        )
        if token_score <= 0 and sim < 0.3:
            continue
        score = token_score + sim
        ranked.append((score, _sku(row), row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen = [row for _, _, row in ranked[:limit]]
    results = [public_product(row) for row in chosen]
    return {"results": results, "ids": [item["id"] for item in results]}


def _limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 12
    if number < 1:
        return 1
    if number > 50:
        return 50
    return number


def rule_matches(rule: Any, attributes: Any) -> bool:
    if not rule:
        return True
    if not isinstance(rule, dict):
        return False
    facts = attributes if isinstance(attributes, dict) else {}
    for key, expected in rule.items():
        actual = facts.get(key)
        if isinstance(expected, list) and len(expected) == 2 and all(_number(item) for item in expected):
            if not _number(actual):
                return False
            low, high = float(expected[0]), float(expected[1])
            if not low <= float(actual) <= high:
                return False
            continue
        if actual != expected:
            return False
    return True


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def approved(fit: dict[str, Any]) -> bool:
    source = str(fit.get("source") or "import")
    if source == "suggested":
        return bool(fit.get("approved"))
    if fit.get("approved") is False:
        return False
    return True


def _targets(fit: dict[str, Any], centre: dict[str, Any]) -> bool:
    sku = _sku(centre)
    if str(fit.get("fits_sku") or "") == sku and sku:
        return True
    family = str(fit.get("fits_family") or "")
    centre_family = str(centre.get("family") or "")
    return bool(family) and family == centre_family


def fitting_parts(
    centre: dict[str, Any], products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    by_sku = {_sku(row): row for row in products if _active(row)}
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for fit in fits:
        if not approved(fit) or not _targets(fit, centre):
            continue
        if not rule_matches(fit.get("rule") or {}, centre.get("attributes") or {}):
            continue
        part = by_sku.get(str(fit.get("part_sku") or ""))
        if part is None or _sku(part) == _sku(centre) or _sku(part) in seen:
            continue
        if str(part.get("currency") or "") != str(centre.get("currency") or ""):
            continue
        seen.add(_sku(part))
        found.append((part, fit))
    found.sort(key=lambda item: _sku(item[0]))
    return found


def _tiers(centre: dict[str, Any], templates: Any) -> list[dict[str, Any]]:
    raw: Any = None
    if isinstance(templates, dict):
        raw = templates.get(str(centre.get("category") or ""))
        if isinstance(raw, dict) and "roles" in raw and "tiers" not in raw:
            raw = raw.get("roles")
    elif isinstance(templates, list):
        for item in templates:
            if str(item.get("category") or "") == str(centre.get("category") or ""):
                raw = item.get("roles")
                break
    if isinstance(raw, dict) and isinstance(raw.get("tiers"), list):
        return list(raw["tiers"])
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "tier" in raw[0]:
        return list(raw)
    return [{"tier": "complete", "label": "Parts that fit", "roles": None}]


def _why(centre: dict[str, Any], chosen: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[str]:
    if not chosen:
        return [f"Fits {centre.get('name')}"]
    lines = [f"Fits {centre.get('name')}"]
    checks: list[str] = []
    notes: list[str] = []
    for _part, fit in chosen:
        rule = fit.get("rule") or {}
        if isinstance(rule, dict):
            for key, value in rule.items():
                if isinstance(value, list) and len(value) == 2:
                    checks.append(f"{key} is between {value[0]} and {value[1]}")
                else:
                    checks.append(f"{key} is {value}")
        note = str(fit.get("note") or "").strip()
        if note:
            notes.append(note)
    if checks:
        lines.append(" · ".join(checks))
    lines.extend(notes)
    return lines


def _role_parts(
    roles: Any, pool: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    if not roles:
        grouped: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for part, fit in pool:
            category = str(part.get("category") or "")
            grouped.setdefault(category, (part, fit))
        return [(category, part, fit) for category, (part, fit) in sorted(grouped.items())]
    chosen: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        name = str(role.get("role") or role.get("category") or "")
        category = str(role.get("category") or name)
        match = next((item for item in pool if str(item[0].get("category") or "") == category), None)
        if match is not None:
            chosen.append((name, match[0], match[1]))
    return chosen


def _one_setup(
    centre: dict[str, Any],
    tier: dict[str, Any],
    pool: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    picked = _role_parts(tier.get("roles"), pool)
    parts: list[dict[str, Any]] = [
        {"role": str(centre.get("category") or "item"), "product": public_product(centre)}
    ]
    chosen_fits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for role, part, fit in picked:
        parts.append({"role": role, "product": public_product(part)})
        chosen_fits.append((part, fit))
    total = sum(item["product"]["price"]["amount"] for item in parts)
    setup: dict[str, Any] = {
        "tier": str(tier.get("tier") or "complete"),
        "label": str(tier.get("label") or "Parts that fit"),
        "parts": parts,
        "total": {
            "amount": money(total),
            "currency": str(centre.get("currency") or ""),
            "indicative": True,
        },
        "why": _why(centre, chosen_fits),
    }
    badge = tier.get("badge")
    if isinstance(badge, str) and badge.strip():
        setup["badge"] = badge.strip()
    return setup


def setups(payload: dict[str, Any]) -> dict[str, Any]:
    product_id = str(payload.get("product_id") or "")
    products = [row for row in (payload.get("products") or []) if _active(row)]
    tenant = payload.get("tenant_id")
    if tenant is not None and str(tenant) != "":
        products = [row for row in products if str(row.get("tenant_id")) == str(tenant)]
    centre = next((row for row in products if _sku(row) == product_id), None)
    if centre is None:
        return {"setups": [], "part_ids": [], "totals": []}
    fits = list(payload.get("fits") or [])
    if tenant is not None and str(tenant) != "":
        fits = [fit for fit in fits if str(fit.get("tenant_id") or tenant) == str(tenant)]
    pool = fitting_parts(centre, products, fits)
    built = [_one_setup(centre, tier, pool) for tier in _tiers(centre, payload.get("templates"))]
    part_ids = [part["product"]["id"] for setup in built for part in setup["parts"]]
    return {
        "setups": built,
        "part_ids": part_ids,
        "totals": [setup["total"]["amount"] for setup in built],
    }


def categories_that_fit(
    centre: dict[str, Any], products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for part, _fit in fitting_parts(centre, products, fits):
        category = str(part.get("category") or "")
        counts[category] = counts.get(category, 0) + 1
    return [{"category": key, "count": counts[key]} for key in sorted(counts)]


def product_detail(payload: dict[str, Any]) -> dict[str, Any] | None:
    product_id = str(payload.get("product_id") or "")
    products = [row for row in (payload.get("products") or []) if _active(row)]
    centre = next((row for row in products if _sku(row) == product_id), None)
    if centre is None:
        return None
    detail = public_product(centre)
    detail["categories_that_fit"] = categories_that_fit(centre, products, list(payload.get("fits") or []))
    return detail


def graph(payload: dict[str, Any]) -> dict[str, Any] | None:
    detail = product_detail(payload)
    if detail is None:
        return None
    product_id = str(payload.get("product_id") or "")
    products = [row for row in (payload.get("products") or []) if _active(row)]
    centre = next(row for row in products if _sku(row) == product_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for part, _fit in fitting_parts(centre, products, list(payload.get("fits") or [])):
        grouped.setdefault(str(part.get("category") or ""), []).append(public_product(part))
    branches = []
    for category in sorted(grouped):
        items = grouped[category]
        branches.append({"category": category, "count": len(items), "products": items[:24]})
    return {"centre": public_product(centre), "branches": branches}


def options_for(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Other approved parts that can fill one role. The centre role has none."""
    role = str(payload.get("role") or "")
    setup = payload.get("setup") or {}
    parts = list(setup.get("parts") or [])
    if not parts:
        return []
    centre_id = str(parts[0].get("product", {}).get("id") or "")
    if role == str(parts[0].get("role") or ""):
        return []
    current = next((part for part in parts if str(part.get("role") or "") == role), None)
    current_id = str(current.get("product", {}).get("id") or "") if current else ""
    current_category = str(current.get("product", {}).get("category") or "") if current else role
    products = [row for row in (payload.get("products") or []) if _active(row)]
    centre = next((row for row in products if _sku(row) == centre_id), None)
    if centre is None:
        return []
    pool = fitting_parts(centre, products, list(payload.get("fits") or []))
    return [
        public_product(part)
        for part, _fit in pool
        if str(part.get("category") or "") == current_category and _sku(part) != current_id
    ]


def reprice(setup: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {_sku(row): row for row in products if _active(row)}
    parts_out: list[dict[str, Any]] = []
    for part in setup.get("parts") or []:
        product = part.get("product") or {}
        row = by_id.get(str(product.get("id") or ""))
        if row is None:
            raise KeyError(str(product.get("id") or ""))
        parts_out.append({"role": str(part.get("role") or ""), "product": public_product(row)})
    total = sum(part["product"]["price"]["amount"] for part in parts_out)
    currency = parts_out[0]["product"]["price"]["currency"] if parts_out else ""
    priced: dict[str, Any] = {
        "tier": str(setup.get("tier") or ""),
        "label": str(setup.get("label") or ""),
        "parts": parts_out,
        "total": {"amount": money(total), "currency": currency, "indicative": True},
        "why": [str(line) for line in (setup.get("why") or [])],
    }
    badge = setup.get("badge")
    if isinstance(badge, str) and badge.strip():
        priced["badge"] = badge.strip()
    return priced


def has_breakdown(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _BREAKDOWN or has_breakdown(child):
                return True
        return False
    if isinstance(value, list):
        return any(has_breakdown(item) for item in value)
    return False
