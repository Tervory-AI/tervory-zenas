"""Tenant-scoped gold candidates from uploaded products and approved database fits.

Store rows remain private. The platform-wide room collector receives an empty
list unless it explicitly supplies a tenant; it cannot promote store data to
public training examples.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from rigbox.catalogue import families_from
from rigbox.shopper_attributes import shopper_rows

MAX_CANDIDATES = 200
_REGIONS = {"us-east", "za"}


async def candidates(store: Any, tenant_id: str | None, limit: int = 200, *, region: str = "us-east") -> list[dict[str, Any]]:
    if region not in _REGIONS or limit <= 0 or tenant_id is None:
        return []
    tenant = str(UUID(str(tenant_id)))
    if await store.retailer(tenant) is None:
        return []
    products = []
    for row in await store.products(tenant):
        attrs = row.get("attributes") or {}
        if (row.get("active") and not attrs.get("sample") and not attrs.get("demo")
                and await store.is_feed_product(tenant, row["sku"])):
            products.append(row)
    products = shopper_rows(products)
    by_sku = {str(row["sku"]): row for row in products}
    families = families_from(products)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fit in await store.fits(tenant):
        if fit.get("approved") is not True:
            continue
        labelled = _label_fit(fit, by_sku, families)
        if labelled is not None:
            grouped.setdefault(labelled["part_sku"], []).append(labelled)
    slim = [_product(row, tenant) for row in sorted(products, key=lambda p: p["sku"])]
    fits = [_copy_fit(row) for part in sorted(grouped) for row in _ordered(grouped[part])]
    revision = hashlib.sha256(json.dumps([slim, fits], sort_keys=True, default=str).encode()).hexdigest()[:16]
    rows = []
    for part in sorted(grouped):
        common = {"label_source": f"{tenant}:{part}", "label_family": f"fitment:{tenant}:{revision}",
                  "tenant_id": tenant, "region": region, "public": False}
        rows.extend([
            {**common, "skill_id": "rig.search",
             "input": {"q": part, "limit": 1, "tenant_id": tenant, "products": slim},
             "expected": {"ids": [part]}},
            {**common, "skill_id": "rig.fit_suggest",
             "input": {"tenant_id": tenant, "part_sku": part, "products": slim, "fits": fits},
             "expected": {"fits": _ordered(grouped[part])}},
        ])
        if len(rows) >= min(limit, MAX_CANDIDATES):
            break
    return rows[:min(limit, MAX_CANDIDATES)]


def _label_fit(
    fit: dict[str, Any],
    products: dict[str, dict[str, Any]],
    families: dict[str, list[str]],
) -> dict[str, Any] | None:
    """One supported row from the part's own list. Exact sku or a known family."""
    part = str(fit.get("part_sku") or "")
    fits_sku = fit.get("fits_sku") or None
    fits_family = fit.get("fits_family") or None
    if part not in products:
        return None
    if bool(fits_sku) == bool(fits_family):
        return None
    if fits_sku and str(fits_sku) not in products:
        return None
    if fits_family and str(fits_family) not in families:
        return None
    rule = fit.get("rule") if isinstance(fit.get("rule"), dict) else {}
    return {
        "part_sku": part,
        "fits_sku": str(fits_sku) if fits_sku else None,
        "fits_family": str(fits_family) if fits_family else None,
        "rule": rule,
    }


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exact product fits first, then the catalogue's known families."""
    seen: set[str] = set()
    exact: list[dict[str, Any]] = []
    family: list[dict[str, Any]] = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        if row.get("fits_sku"):
            exact.append(_copy_fit(row))
        else:
            family.append(_copy_fit(row))
    exact.sort(key=lambda row: str(row.get("fits_sku") or ""))
    family.sort(key=lambda row: str(row.get("fits_family") or ""))
    return exact + family


def _copy_fit(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "part_sku": row["part_sku"],
        "fits_sku": row["fits_sku"],
        "fits_family": row["fits_family"],
        "rule": dict(row["rule"]),
    }


def _product(row: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "id": str(row["sku"]),
        "sku": str(row["sku"]),
        "name": str(row.get("name") or ""),
        "brand": str(row.get("brand") or ""),
        "category": str(row.get("category") or ""),
        "price": row.get("price"),
        "currency": str(row.get("currency") or ""),
        "image_url": str(row.get("image_url") or ""),
        "attributes": row.get("attributes") if isinstance(row.get("attributes"), dict) else {},
        "active": True,
        "family": str(row.get("family") or ""),
    }
