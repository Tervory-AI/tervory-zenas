"""Private statistical patterns, written and listed through SQL-first Hermes."""

from __future__ import annotations

import json
import logging
from uuid import NAMESPACE_URL, uuid5


FIELDS = {"pair_id", "upstream_source", "upstream_ref", "downstream_source",
          "lag_days", "episodes", "hits", "hit_rate"}
log = logging.getLogger(__name__)


def claim(config: dict, stats: dict) -> dict:
    # Deliberately do not copy a summary, series, watch, product or URL.
    return {
        "pair_id": config["id"],
        "upstream_source": config["source"],
        "upstream_ref": config["ref"],
        "downstream_source": config["downstream_source"],
        "lag_days": {"min": stats["lag_min"], "max": stats["lag_max"], "median": stats["lag_median"]},
        "episodes": stats["episode_count"], "hits": stats["hits"],
        # Six decimals preserve the observed frequency without presenting a long
        # decimal as a phone number to the existing publication scrubber.
        "hit_rate": round(stats["hit_rate"], 6) if stats["hit_rate"] is not None else None,
    }


async def remember(memory: object, tenant: str, config: dict, stats: dict, document_id: str) -> bool:
    value = claim(config, stats)
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    identity = str(uuid5(NAMESPACE_URL, tenant + ":warning_pattern:" + config["id"]))
    try:
        rows = await memory.records(tenant, tier="client")
        current = next((row for row in rows if str(row.get("id")) == identity
                        and str(row.get("tenant_id")) == tenant
                        and row.get("tier") == "client"), None)
        if current is not None:
            try:
                identical = json.loads(current["claim"]) == value
            except (KeyError, TypeError, ValueError):
                identical = False
            if identical:
                return True  # Also covers a successful save whose response was lost.
            # Forget exactly this tenant's client record. Published copies have
            # separate IDs and are never read or deleted by this write path.
            await memory.forget(identity, tenant)
        saved = await memory.remember(tenant, text, "warning_pattern", document_id,
                                      evidence=[document_id], record_id=identity, tenant_wide=True)
        return isinstance(saved, dict) and str(saved.get("id")) == identity
    except Exception as exc:
        # Keep the durable pending flag, including when forget succeeded but
        # save failed. The next run inserts into that same now-empty identity.
        log.warning("pattern memory pending (%s)", type(exc).__name__)
        return False


async def listing(memory: object, tenant: str, plan: dict) -> dict:
    """One latest private and published record per configured pair; no recall."""
    configs = {row["id"]: row for row in plan["pairs"]}
    result = {"patterns": [], "published": []}
    for tier, key in (("client", "patterns"), ("platform", "published")):
        seen = set()
        rows = await memory.records(tenant, tier=tier)
        for row in sorted(rows, key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))), reverse=True):
            owner = tenant if tier == "client" else "00000000-0000-0000-0000-000000000001"
            if row.get("tier") != tier or str(row.get("tenant_id")) != owner:
                continue
            try:
                value = json.loads(row["claim"])
            except (KeyError, ValueError, TypeError):
                continue
            if not isinstance(value, dict) or set(value) != FIELDS or not isinstance(value["pair_id"], str):
                continue
            episodes, hits, rate, lag = (value[field] for field in ("episodes", "hits", "hit_rate", "lag_days"))
            if type(episodes) is not int or type(hits) is not int or not 0 <= hits <= episodes:
                continue
            if (rate is None) != (episodes == 0) or (rate is not None and (type(rate) not in (int, float) or rate != round(hits / episodes, 6))):
                continue
            if not isinstance(lag, dict) or set(lag) != {"min", "max", "median"}:
                continue
            if any(v is not None and (type(v) not in (int, float) or not 0 <= v <= 36500) for v in lag.values()):
                continue
            config = configs.get(value["pair_id"])
            if config is None or value["pair_id"] in seen:
                continue
            if any(value[field] != config[config_field] for field, config_field in (
                ("upstream_source", "source"), ("upstream_ref", "ref"), ("downstream_source", "downstream_source")
            )):
                continue
            seen.add(value["pair_id"])
            result[key].append({"id": row["id"], "claim": value, "evidence": row.get("evidence") or [], "tier": tier})
    return result
