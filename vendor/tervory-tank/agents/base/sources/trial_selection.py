"""Select platform trial records and attach independently validated list truth."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from sources.context_dev import lookup_body
from sources.vendor_ledger import PLATFORM_TENANT, decoded


def official_truth() -> dict[str, dict[str, Any]]:
    try:
        from base_gold.gold_sources import MAX_SNAPSHOT_BYTES, candidates
    except ModuleNotFoundError:
        return {}
    region = os.environ.get("TANK_REGION", "us-east")
    path = Path(os.environ.get("DATA_DIR", "/data")) / "gold" / region / "official-matches.jsonl"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_SNAPSHOT_BYTES:
        return {}
    truth = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                snapshot = json.loads(line)
                if snapshot.get("source_id") != "atf-active":
                    continue
                examples = candidates([snapshot], region=region)
                found = next((row for row in examples if row["skill_id"] == "base.find_site"), None)
                if not found:
                    continue
                official = snapshot["official"]
                truth[official["id"]] = {
                    "checked_answers": {
                        "website": found["expected"]["website"]["value"],
                        **({"phone": official["phone"]} if str(official.get("phone") or "").strip() else {}),
                        "address": ", ".join(str(official[key]) for key in ("address", "city", "state", "zip")),
                    },
                    "label_source": found["label_source"],
                }
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return truth


def select_records(rows: list[Any], truth: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from engines.ffl.atf_gold import licence_id
    except ModuleNotFoundError:
        licence_id = lambda raw: ""
    selected = []
    for row in rows:
        raw, profile = decoded(row["raw"]), decoded(row["profile"])
        identity = {"url": profile.get("website", {}).get("value") or raw.get("website") or ""}
        if not identity["url"]:
            identity = {"name": profile.get("name", {}).get("value") or raw.get("name") or ""}
        try:
            lookup_body(**identity)
        except (ValueError, TypeError, AttributeError):
            continue
        evidence = truth.get(licence_id(raw), {})
        selected.append({
            "record_id": str(row["id"]), "public": True, **identity,
            "checked_answers": evidence.get("checked_answers", {}),
            "label_source": evidence.get("label_source"),
            "base_profile": {key: profile[key] for key in ("website", "phone", "phones", "address", "city", "state", "zip") if key in profile},
        })
        if len(selected) == 50:
            break
    return selected


async def latest_trial(pool: Any) -> tuple[str | None, list[dict[str, Any]]]:
    tenant = uuid.UUID(PLATFORM_TENANT)
    async with pool.acquire() as conn:
        run = await conn.fetchval(
            "SELECT id FROM agent_base.tasks WHERE tenant_id=$1 AND status='done' "
            "ORDER BY finished_at DESC NULLS LAST, created_at DESC, id DESC LIMIT 1", tenant,
        )
        if run is None:
            return None, []
        rows = await conn.fetch(
            "SELECT id,raw,profile FROM agent_base.records WHERE tenant_id=$1 AND task_id=$2 AND status='done' "
            "AND NOT (passes ? 'context_dev') ORDER BY created_at DESC,id DESC", tenant, run,
        )
    truth = await asyncio.to_thread(official_truth)
    return str(run), select_records(rows, truth)
