"""Build gold evidence from an official list and Base's independent observations."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from base_gold.gold_sources import PLATFORM_TENANT, _host, _phone, _text
from base_gold.ffl.atf_gold import BLOCKED_HOSTS, expires, fetch_list, licence_id

# Mirrors base.find_site 1.7.0; a regression checks parity without importing skill code.
NEUTRAL_SKIP_HOSTS = ('facebook.com', 'instagram.com', 'yelp.com', 'yellowpages.com', 'bbb.org', 'linkedin.com',
 'mapquest.com', 'google.com', 'youtube.com', 'twitter.com', 'x.com', 'manta.com', 'bizapedia.com',
 'opencorporates.com', 'dnb.com', 'zoominfo.com', 'apollo.io', 'wikipedia.org', 'wikimedia.org',
 'reddit.com', 'amazon.com', 'ebay.com', 'tripadvisor.com', 'justia.com', 'buzzfile.com',
 'chamberofcommerce.com', 'nextdoor.com', 'angi.com')

ADDRESS = ("address", "city", "state", "zip")


def _words(value: Any) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip() + " "


async def join_list(rows, observations, call, *, source_url: str, snapshot: date, today: date):
    if not 0 <= (today - snapshot).days <= 90:
        return []
    official_rows = {licence_id(row): row for row in rows if licence_id(row)}
    matched, seen = [], set()
    for record in observations:
        try:
            raw = record["raw"]
            observed = record["observation"]
            identity = licence_id(raw)
            if identity in seen or identity not in official_rows:
                continue
            source = official_rows[identity]
            expiry = expires(source["lic_xprdte"].upper(), snapshot)
            if expiry is None or expiry < today:
                continue
            host = _host(observed["website"])
            if any(host == blocked or host.endswith("." + blocked)
                   for blocked in (*NEUTRAL_SKIP_HOSTS, *BLOCKED_HOSTS)):
                continue
            if (
                observed.get("blocked") is not False or observed.get("status") != 200
                or not 0 <= (today - date.fromisoformat(observed["observed_on"])).days <= 30
                or not _host(observed["website"])
                or _host(observed["website"]) != _host(observed["source_url"])
            ):
                continue
            ingested = await call("ffl.ingest", source)
            if ingested.get("mapped") is not True:
                continue
            official = dict(ingested["record"])
            if not all(official.get(key) for key in ("name", *ADDRESS)):
                continue
            phones = {_phone(phone) for phone in observed.get("phones", [])} - {""}
            phone = _phone(source["voice_phone"])
            phone_match = bool(phone and phone in phones)
            addresses = observed.get("addresses") or []
            structured = any(
                all(_text(item.get(key)) == _text(official[key]) for key in ADDRESS)
                for item in addresses if isinstance(item, dict)
            )
            address_match = structured or all(
                _words(official[key]) in _words(observed.get("text")) for key in ADDRESS
            )
            if not (phone_match or address_match):
                continue
            site = {
                key: observed[key] for key in ("website", "source_url", "observed_on", "status", "blocked")
            }
            if phone_match:
                site["phone"] = source["voice_phone"]
            if address_match:
                site.update({key: official[key] for key in ADDRESS})
            site["exclusive_contacts"] = structured and len(addresses) == 1 and phones == {phone}
            matched.append({
                "tenant_id": PLATFORM_TENANT, "region": "us-east", "public": True,
                "record_kind": "business", "source_id": "atf-active",
                "official": {
                    **{key: official[key] for key in ("name", *ADDRESS)},
                    "phone": source["voice_phone"], "license_name": source["license_name"],
                    "id": identity, "source_url": source_url, "observed_on": snapshot.isoformat(),
                    "valid_until": expiry.isoformat(), "active": True,
                },
                "site": site,
            })
            seen.add(identity)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if len(matched) >= 200:
            break
    return matched


def publish(rows: list[dict], *, region: str) -> None:
    directory = Path(os.environ.get("DATA_DIR", "/data")) / "gold" / region
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "official-matches.jsonl"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


async def nightly(pool: Any, http: Any, skills: Any) -> dict[str, Any]:
    region = os.environ.get("TANK_REGION", "us-east")
    if region != "us-east":
        return {"status": "skipped", "reason": "no official list for this region"}
    today = date.today()
    async with pool.acquire() as conn:
        locked = await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended('base-gold-nightly',0))")
        if not locked:
            return {"status": "skipped", "reason": "already running"}
        try:
            records = await conn.fetch(
                "SELECT raw, passes->'2b'->'gold_observation' AS observation "
                "FROM agent_base.records WHERE tenant_id=$1 AND status='done' "
                "AND passes->'2b' ? 'gold_observation' ORDER BY updated_at DESC LIMIT 4000",
                uuid.UUID(PLATFORM_TENANT),
            )
            observations = [
                {key: json.loads(row[key]) if isinstance(row[key], str) else row[key]
                 for key in ("raw", "observation")}
                for row in records
            ]
            if not observations:
                await asyncio.to_thread(publish, [], region=region)
                return {"status": "done", "matches": 0}
            version = await skills.approved("ffl.ingest")
            if not version:
                raise ValueError("approved list ingest unavailable")
            call = skills.bind({"ffl.ingest": version}, False, PLATFORM_TENANT)
            rows, source_url, snapshot = await fetch_list(http, today)
            matches = await join_list(
                rows, observations, call, source_url=source_url, snapshot=snapshot, today=today,
            )
            await asyncio.to_thread(publish, matches, region=region)
            return {"status": "done", "matches": len(matches), "snapshot": snapshot.isoformat()}
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended('base-gold-nightly',0))")
