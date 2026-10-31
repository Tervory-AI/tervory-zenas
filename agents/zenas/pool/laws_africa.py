"""One resumable Laws.Africa load, shared by Load now and the nightly runner.

The existing checkpoint table holds a page queue and per-work date/URL
inventories. A work is acknowledged only after its sections are stored. No
credential or downloaded HTML is stored in a cursor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from agents.zenas.pool.adapters.laws_africa_api import API, WORKS, LawsAfricaClient, api_url, expressions, national_work, representation
from agents.zenas.pool.adapters.za_akn import ACTS
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.http import RateLimited
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.region import RegionError, assert_source_region, bind_store

SOURCE_ID = "laws-africa"
MINIMUM = {"/" + row[2]: row[0] for row in ACTS}
_LOCK_ID = 6000291026
_locks = WeakKeyDictionary()


def is_laws_africa(source) -> bool:
    return source.source_id == SOURCE_ID or source.source_id in MINIMUM.values()


def live_sources(sources):
    """The national walk includes the minimum; do not fetch those twice nightly."""
    if any(source.source_id == SOURCE_ID for source in sources):
        return [source for source in sources if source.source_id not in MINIMUM.values()]
    return sources


@asynccontextmanager
async def _run_lock(store):
    conn = getattr(store, "_conn", None)
    session = getattr(conn, "session", None)
    if session is not None:
        # A per-statement job connection: lock and unlock on one held checkout.
        async with session() as held:
            await held.execute("SELECT pg_advisory_lock($1)", _LOCK_ID)
            try:
                yield
            finally:
                await held.execute("SELECT pg_advisory_unlock($1)", _LOCK_ID)
    elif conn is not None:
        # Session lock coordinates the room and scheduled process. It is released
        # on process/connection death, without a persistent running flag.
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_ID)
        try:
            yield
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_ID)
    else:
        lock = _locks.setdefault(store, asyncio.Lock())
        async with lock:
            yield


def _work_key(frbr):
    return SOURCE_ID + ":" + hashlib.sha256(frbr.encode()).hexdigest()


def _inventory(rows):
    return {row["language"] + "@" + row["date"]: row["url"] for row in rows}


def _metadata(work):
    if not isinstance(work, dict) or not national_work(work.get("frbr_uri")):
        raise ValueError("invalid national work")
    if not isinstance(work.get("title"), str) or not work["title"].strip():
        raise ValueError("missing work title")
    return {key: work[key] for key in ("frbr_uri", "title", "publication_date", "points_in_time", "repealed") if key in work}


async def sync_source(store, source, *, region, http_get, on, when, embedder, index, cap, data_dir):
    """Returns True on the existing disk-cap stop condition."""
    from agents.zenas.pool.bulk import _over_cap, load_detail

    assert_source_region(source, region)
    bind_store(store, region)
    if region != "za":
        raise RegionError("this box does not read za law")
    await store.record_source(source)
    async with _run_lock(store):
        inserted = 0
        skipped = Counter()
        active_source = source

        async def finish(status, detail):
            for source_id in {source.source_id, active_source.source_id}:
                await store.record_run(source_id, status=status, detail=detail,
                                       documents=await store.instrument_count(source_id), when=when)

        try:
            client = LawsAfricaClient(region=region, http_get=http_get)

            async def checkpoint(key, value):
                await store.set_checkpoint(key, json.dumps(value, sort_keys=True), await store.stored_bytes())

            async def load_work(raw):
                nonlocal inserted, active_source
                frbr = national_work(raw.get("frbr_uri"))
                if not frbr:
                    return
                active_source = next(item for item in CATALOGUE if item.source_id == MINIMUM.get(frbr, SOURCE_ID))
                await store.record_source(active_source)
                work = _metadata(raw)
                if "points_in_time" not in work:
                    work = _metadata(await client.json(API + frbr.lstrip("/") + ".json"))
                    if work["frbr_uri"] != frbr:
                        raise ValueError("details belong to another work")
                rows = expressions(work, on=on)
                previous_raw = await store.get_checkpoint(_work_key(frbr))
                previous = json.loads(previous_raw) if previous_raw else {}
                known = previous.get("points", {})
                loaded = previous.get("loaded", {})
                current = {row["language"]: row for row in rows}
                targets = list(current.values()) if not known else [
                    row for row in rows if known.get(row["language"] + "@" + row["date"]) != row["url"]]
                # Also covers an interrupted first load and a new current language.
                targets = {row["language"] + "@" + row["date"]: row for row in [*targets, *current.values()]}
                work_source = active_source
                for key, expression in sorted(targets.items(), key=lambda item: (item[1]["date"], item[1]["language"])):
                    if loaded.get(key) == expression["url"]:
                        skipped["unchanged"] += 1
                        continue
                    html = await client.get(representation(expression["url"], ".html"), accept="text/html")
                    toc = json.loads(await client.get(representation(expression["url"], "/toc.json")))
                    payload = json.dumps({"work": work, "expression": expression, "toc": toc, "html": html.decode("utf-8")}).encode()
                    if _over_cap(await store.stored_bytes(), len(payload), cap, data_dir):
                        raise _DiskCap
                    added = await ingest_snapshot(store, work_source, [payload], on=on, region=region,
                                                  embedder=embedder, index=index, skips=skipped)
                    inserted += added
                    # The checkpoint follows ingestion, including all section writes.
                    loaded[key] = expression["url"]
                    await checkpoint(_work_key(frbr), {"points": known, "loaded": loaded})
                    await store.record_run(work_source.source_id, status="running", detail=load_detail(added, {}),
                                           documents=await store.instrument_count(work_source.source_id), when=when)
                    await store.record_run(source.source_id, status="running", detail=load_detail(inserted, skipped),
                                           documents=await store.instrument_count(source.source_id), when=when)
                await checkpoint(_work_key(frbr), {"points": _inventory(rows), "loaded": loaded})
                if work_source.source_id != source.source_id:
                    await store.record_run(work_source.source_id, status="ok", detail=load_detail(inserted, skipped),
                                           documents=await store.instrument_count(work_source.source_id), when=when)

            if source.source_id != SOURCE_ID:
                frbr = next(path for path, source_id in MINIMUM.items() if source_id == source.source_id)
                work = await client.json(API + frbr.lstrip("/") + ".json")
                if work.get("frbr_uri") != frbr:
                    raise ValueError("details belong to another work")
                await load_work(work)
            else:
                saved = await store.get_checkpoint(SOURCE_ID)
                state = json.loads(saved) if saved and not saved.startswith("snap:") else {}
                page = state.get("page", WORKS)
                pending = state.get("pending", [])
                missing = state.get("missing", list(MINIMUM))
                visited = state.get("visited", [])
                while page or pending:
                    if not pending:
                        page = api_url(page)
                        if page in visited:
                            raise ValueError("paging cycle")
                        listing = await client.json(page)
                        if not isinstance(listing.get("results"), list) or "next" not in listing:
                            raise ValueError("invalid listing page")
                        next_page = listing["next"]
                        if next_page is not None:
                            next_page = api_url(next_page)
                        pending = [_metadata(row) for row in listing["results"] if national_work(row.get("frbr_uri"))]
                        visited.append(page)
                        page = next_page
                        await checkpoint(SOURCE_ID, {"page": page, "pending": pending, "missing": missing, "visited": visited})
                    while pending:
                        work = pending[0]
                        await load_work(work)
                        missing = [path for path in missing if path != work["frbr_uri"]]
                        pending.pop(0)
                        await checkpoint(SOURCE_ID, {"page": page, "pending": pending, "missing": missing, "visited": visited})
                # The fixed list is a minimum, even when a listing omits an entry.
                while missing:
                    frbr = missing[0]
                    work = await client.json(API + frbr.lstrip("/") + ".json")
                    if work.get("frbr_uri") != frbr:
                        raise ValueError("minimum work is missing")
                    await load_work(work)
                    missing.pop(0)
                    await checkpoint(SOURCE_ID, {"page": None, "pending": [], "missing": missing, "visited": visited})
                await store.set_checkpoint(SOURCE_ID, "", await store.stored_bytes())
            await store.record_run(source.source_id, status="ok", detail=load_detail(inserted, skipped),
                                   documents=await store.instrument_count(source.source_id), when=when)
        except _DiskCap:
            await finish("disk_cap", f"pool disk cap {cap} bytes")
            return True
        except RateLimited as exc:
            await finish("rate_limited", f"{exc}; the next Load retries")
        except FetchNotConfigured as exc:
            await finish("not_configured", str(exc))
        except Exception:
            # Never persist an API response, request headers, exception URL or body.
            await finish("error", "the load failed")
    return False


class _DiskCap(Exception):
    pass
