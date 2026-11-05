"""Autumbra consumes Market Lab intel — it does not own a feed list.

Founder correction (wins): Market Lab is the intel backend. Autumbra must not
build a feed list or source registry. Watch/Enforce consume a versioned IOC
bundle from Market Lab and name the contributing source on every receipt.
Clients never see a feed UI.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

BUNDLE_SCHEMA = "market_lab.ioc.v1"
FORBIDDEN_SOURCES = frozenset({"youtube", "instagram", "yt", "ig"})


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def accept_ioc_bundle(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a Market Lab IOC bundle. Public / ML research sources only at launch."""
    if not isinstance(raw, dict):
        raise ValueError("IOC bundle must be an object.")
    if raw.get("schema_version") != BUNDLE_SCHEMA:
        raise ValueError(f"schema_version must be {BUNDLE_SCHEMA}")
    if raw.get("paid") is True:
        raise ValueError("paid feeds are off at launch; public + Market Lab only")
    sources = raw.get("sources") or []
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty list from Market Lab")
    indicators = raw.get("indicators") or []
    if not isinstance(indicators, list):
        raise ValueError("indicators must be a list")
    cleaned_sources: list[dict[str, str]] = []
    for src in sources:
        if not isinstance(src, dict):
            raise ValueError("each source must be an object")
        sid = str(src.get("id") or "").strip().lower()
        name = str(src.get("name") or src.get("credit") or sid).strip()
        if not sid or not name:
            raise ValueError("source needs id and name")
        if sid in FORBIDDEN_SOURCES or name.lower() in FORBIDDEN_SOURCES:
            raise ValueError("YouTube and Instagram are forbidden intel sources")
        cleaned_sources.append({
            "id": sid,
            "name": name,
            "url": str(src.get("url") or ""),
            "credit": str(src.get("credit") or name),
        })
    out_inds: list[dict[str, Any]] = []
    for ind in indicators:
        if not isinstance(ind, dict):
            continue
        kind = str(ind.get("kind") or "").strip()
        value = str(ind.get("value") or "").strip()
        if not kind or not value:
            continue
        out_inds.append({
            "kind": kind,
            "value": value,
            "source_ids": [str(s) for s in (ind.get("source_ids") or [])],
            # Keep advisory rating / component so register_advisories_from_ioc_bundle
            # can stamp critical receipts from the product's own intake.
            "rating": str(ind.get("rating") or ind.get("severity") or "").strip().lower(),
            "component": str(ind.get("component") or ind.get("stack") or "").strip(),
            "url": str(ind.get("url") or ind.get("advisory_url") or "").strip(),
        })
    return {
        "schema_version": BUNDLE_SCHEMA,
        "bundle_id": str(raw.get("bundle_id") or uuid4()),
        "published_at": str(raw.get("published_at") or now_iso()),
        "producer": "Market Lab",
        "paid": False,
        "sources": cleaned_sources,
        "indicators": out_inds,
    }


def source_credits_on_receipt(bundle: dict[str, Any], *, source_ids: list[str] | None = None) -> list[dict[str, str]]:
    """Name Market Lab sources on the receipt. Autumbra does not maintain the registry."""
    wanted = {s.lower() for s in (source_ids or [])}
    credits: list[dict[str, str]] = []
    for src in bundle.get("sources") or []:
        if wanted and src["id"] not in wanted:
            continue
        credits.append({
            "id": src["id"],
            "credit": src["credit"],
            "url": src.get("url") or "",
            "via": "Market Lab",
        })
    return credits


def receipt_for_bundle_consume(
    bundle: dict[str, Any],
    *,
    tenant_id: str,
    action: str = "intel_consume",
) -> dict[str, Any]:
    """What Autumbra did with a Market Lab bundle — never the full feed list UI."""
    return {
        "id": str(uuid4()),
        "product": "Autumbra",
        "kind": "intel_consume",
        "at": now_iso(),
        "tenant_id": tenant_id,
        "action": action,
        "producer": "Market Lab",
        "bundle_id": bundle["bundle_id"],
        "indicator_count": len(bundle.get("indicators") or []),
        "sources": source_credits_on_receipt(bundle),
        "client_visible_feed_list": False,
        "note": "Intel backend is Market Lab; Autumbra does not own a source registry.",
    }


def backend_status() -> dict[str, Any]:
    """Autumbra does not own a feed list — Market Lab does."""
    return {
        "owner": "Market Lab",
        "autumbra_owns_feed_list": False,
        "client_visible_feed_list": False,
        "note": (
            "Intel backend is Market Lab. Autumbra consumes ML IOC bundles and "
            "names the source on the receipt. Clients never see a feed list."
        ),
        "consume_module": "agents.ghost.market_lab_intel",
        "credit_helper": "agents.ghost.market_lab_intel.source_credits_on_receipt",
    }


# ---------------------------------------------------------------------------
# Public advisory catalog Market Lab provides. Autumbra matches against this
# itself — caller matched/match_id/from_match flags are never trusted.
# ---------------------------------------------------------------------------

_MARKET_LAB_ADVISORIES: list[dict[str, Any]] = []


def load_market_lab_advisories(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Replace Autumbra's in-memory Market Lab advisory catalog (tests + Watch boot)."""
    global _MARKET_LAB_ADVISORIES
    cleaned: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        advisory_id = str(row.get("advisory_id") or row.get("id") or row.get("cve_id") or "").strip()
        url = str(row.get("url") or row.get("advisory_url") or "").strip()
        rating = str(row.get("rating") or row.get("severity") or "").strip().lower()
        if not advisory_id and not url:
            continue
        cleaned.append({
            "advisory_id": advisory_id,
            "url": url,
            "rating": rating,
            "component": str(row.get("component") or row.get("stack") or "").strip(),
            "source_name": str(row.get("source_name") or row.get("name") or "Market Lab").strip(),
            "source_url": str(row.get("source_url") or url).strip(),
        })
    _MARKET_LAB_ADVISORIES = cleaned
    return list(_MARKET_LAB_ADVISORIES)


def market_lab_advisories() -> list[dict[str, Any]]:
    return list(_MARKET_LAB_ADVISORIES)


def register_advisories_from_ioc_bundle(bundle: dict[str, Any]) -> int:
    """Pull advisory-shaped indicators from a Market Lab IOC bundle into the catalog."""
    rows: list[dict[str, Any]] = []
    sources = {s["id"]: s for s in (bundle.get("sources") or []) if isinstance(s, dict)}
    for ind in bundle.get("indicators") or []:
        if not isinstance(ind, dict):
            continue
        kind = str(ind.get("kind") or "").lower()
        value = str(ind.get("value") or "").strip()
        if kind not in {"cve", "advisory", "vuln"} and not value.upper().startswith("CVE-"):
            continue
        src_ids = [str(s) for s in (ind.get("source_ids") or [])]
        src = sources.get(src_ids[0]) if src_ids else None
        rows.append({
            "advisory_id": value,
            "url": str(ind.get("url") or (src or {}).get("url") or ""),
            "rating": str(ind.get("rating") or ind.get("severity") or "").lower(),
            "component": str(ind.get("component") or ""),
            "source_name": str((src or {}).get("name") or "Market Lab"),
            "source_url": str(ind.get("url") or (src or {}).get("url") or ""),
        })
    if rows:
        # Merge with existing catalog (by advisory_id/url).
        by_key = {(r["advisory_id"], r["url"]): r for r in _MARKET_LAB_ADVISORIES}
        for r in rows:
            by_key[(r["advisory_id"], r["url"])] = r
        load_market_lab_advisories(list(by_key.values()))
    return len(rows)


def match_advisory_from_market_lab(hint: dict[str, Any] | None) -> dict[str, Any] | None:
    """Match a caller hint against Market Lab's catalog. Ignore caller match flags.

    Returns the Market Lab row (with rating/url/id) or None. Caller-supplied
    matched/match_id/matched_component/from_match are never consulted.
    """
    if not hint or not isinstance(hint, dict):
        return None
    # Explicitly ignore caller assertions — strip so callers cannot smuggle proof.
    cve = str(
        hint.get("advisory_id") or hint.get("cve_id") or ""
    ).strip()
    url = str(hint.get("advisory_url") or hint.get("url") or "").strip()
    if not cve and not url:
        return None
    for row in _MARKET_LAB_ADVISORIES:
        if cve and row["advisory_id"] and cve == row["advisory_id"]:
            return dict(row)
        if url and row["url"] and url == row["url"]:
            return dict(row)
    return None

