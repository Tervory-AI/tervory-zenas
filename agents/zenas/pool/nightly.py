"""Nightly update. Tests pass saved snapshots. Live fetch is opt-in."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.config import current_region
from agents.zenas.pool.embed import Embedder, NoOpEmbedder, NoOpVectorIndex, VectorIndex, live_backends
from agents.zenas.pool.http import RateLimited
from agents.zenas.pool.ingest import ingest_snapshot, version_saved
from agents.zenas.pool.indexing import index_pending
from agents.zenas.pool.licence import FetchNotConfigured, LicenceError, refusal_reason
from agents.zenas.pool.laws_africa import is_laws_africa, live_sources
from agents.zenas.pool.region import RegionError, assert_source_region, bind_store
from agents.zenas.pool.status import pool_status
from agents.zenas.pool.store import Store

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Official statute feeds. Load now uses fetch_bulk through the pool loader.
# Nightly uses the same resume cursor and writes the feeds sentence itself.
_OFFICIAL_STATUTES = frozenset({"govza-acts", "govza-regulations"})
# The US scheduler allows two hours for the entire night. A progressing bulk
# archive must also yield; its no-progress watchdog alone cannot bound it.
_US_COURTLISTENER_SECONDS = 30 * 60


def packaged_snapshots(region: str) -> dict[str, list[bytes]]:
    root = FIXTURES / region
    if not root.is_dir():
        return {}
    snapshots: dict[str, list[bytes]] = {}
    for source_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        files = sorted(
            path for path in source_dir.iterdir() if path.is_file() and not path.name.startswith(".")
        )
        if files:
            snapshots[source_dir.name] = [path.read_bytes() for path in files]
    return snapshots


async def urllib_get(url: str, headers: dict[str, str]) -> bytes:
    """Public government URLs only. Never put a key in the URL."""
    from agents.zenas.pool.http import urllib_get as _get

    return await _get(url, headers)


async def run_nightly(
    store: Store,
    *,
    region: str | None = None,
    snapshots: dict[str, list[bytes]] | None = None,
    on: date | None = None,
    live: bool = False,
    http_get=None,
    embedder: Embedder | None = None,
    index: VectorIndex | None = None,
    only: list[str] | None = None,
) -> dict:
    """Update every source configured for this region.

    `snapshots` is the offline path (tests, and a reviewed bulk file).
    `live=True` fetches the checked-in wave URLs. Refused sources are recorded
    and linked, and any bytes handed in for them are not parsed.
    """
    region = region or current_region()
    bind_store(store, region)
    day = on or date.today()
    when = datetime.now(timezone.utc)
    if live and snapshots is None:
        embedder, index = live_backends(store, region, embedder, index)
    else:
        embedder, index = embedder or NoOpEmbedder(), index or NoOpVectorIndex()
    getter = http_get or urllib_get

    sources = sources_for_region(region)
    if only is not None:
        wanted = set(only)
        sources = [source for source in sources if source.source_id in wanted]
    if live and snapshots is None:
        sources = live_sources(sources)
    async def changed(document):
        from chassis.events import publish
        await publish("00000000-0000-0000-0000-000000000001", "zenas.regulation.changed", document.version_id, {
            "document_id": hashlib.sha256(document.id.encode()).hexdigest(),
            "version_id": hashlib.sha256(document.version_id.encode()).hexdigest()})
    token = version_saved.set(changed)
    try:
        for source in sources:
            try:
                assert_source_region(source, region)
                await _update_nightly_source(
                    store,
                    source,
                    snapshots=snapshots,
                    on=day,
                    when=when,
                    live=live,
                    http_get=getter,
                    embedder=embedder,
                    index=index,
                    region=region,
                )
            except RegionError:
                continue
            except Exception as exc:
                await _record_source_error(store, source, exc, when)
    finally:
        version_saved.reset(token)
    return await pool_status(store, region=region)


async def _update_nightly_source(store: Store, source, **kwargs) -> None:
    bounded = (
        kwargs["region"] == "us-east" and source.source_id == "courtlistener"
        and kwargs["live"] and kwargs["snapshots"] is None
    )
    if not bounded:
        await _update_source(store, source, **kwargs)
        return
    window = asyncio.timeout(_US_COURTLISTENER_SECONDS)
    try:
        async with window:
            await _update_source(store, source, **kwargs)
    except TimeoutError:
        if not window.expired():
            raise
        from agents.zenas.pool.adapters.courtlistener_bulk import BulkStalled

        # A deadline can also expire after load_bulk returns, while its blobs
        # are still being ingested. Discard the singleton adapter's speculative
        # state so the next run starts from the durable checkpoint, not a cached
        # opinion id or files_done marker ahead of acknowledged data.
        get_adapter(source)._bulk_state = None
        await store.record_source(source)
        await store.record_run(
            source.source_id, status="stalled", detail=str(BulkStalled()),
            documents=await store.instrument_count(source.source_id), when=kwargs["when"],
        )


async def _update_source(
    store: Store,
    source,
    *,
    snapshots: dict[str, list[bytes]] | None,
    on: date,
    when: datetime,
    live: bool,
    http_get,
    embedder: Embedder,
    index: VectorIndex,
    region: str,
) -> None:
    assert_source_region(source, region)
    if snapshots is None and live and source.source_id == "courtlistener" and region == "us-east":
        await index_pending(store, embedder, index, region=region, source_ids=[source.source_id])
        await courtlistener_night(
            store,
            source,
            on=on,
            when=when,
            http_get=http_get,
            embedder=embedder,
            index=index,
            region=region,
        )
        return
    reason = refusal_reason(source)
    if reason:
        await ingest_snapshot(store, source, [], on=on, region=region)
        await store.record_run(
            source.source_id,
            status="skipped",
            detail=reason,
            documents=0,
            when=when,
        )
        return
    if snapshots is None and live and is_laws_africa(source):
        from agents.zenas.pool.bulk import disk_cap_bytes, load_source
        await load_source(
            store, source, region=region, snapshots=None, on=on, when=when, live=True,
            http_get=http_get, embedder=embedder, index=index, cap=disk_cap_bytes(),
            data_dir=os.environ.get("DATA_DIR", ".") or ".",
        )
        return
    await index_pending(store, embedder, index, region=region, source_ids=[source.source_id])
    if snapshots is None and live and source.source_id in _OFFICIAL_STATUTES:
        await _update_official(
            store,
            source,
            on=on,
            when=when,
            http_get=http_get,
            embedder=embedder,
            index=index,
            region=region,
        )
        return
    blobs: list[bytes] | None
    if snapshots is not None:
        blobs = list(snapshots.get(source.source_id, []))
    elif live and await _live_archive(
        store,
        source,
        on=on,
        when=when,
        http_get=http_get,
        embedder=embedder,
        index=index,
        region=region,
    ):
        return
    elif live:
        try:
            blobs = await get_adapter(source).fetch(http_get)
        except (FetchNotConfigured, LicenceError) as exc:
            await store.record_source(source)
            await store.record_run(
                source.source_id,
                status="not_configured",
                detail=str(exc),
                documents=0,
                when=when,
            )
            return
    else:
        blobs = []
    if not blobs:
        await store.record_source(source)
        count = await store.instrument_count(source.source_id)
        await store.record_run(
            source.source_id,
            status="no_snapshot",
            detail="no saved text for this source",
            documents=count,
            when=when,
        )
        return
    inserted = await ingest_snapshot(
        store,
        source,
        blobs,
        on=on,
        embedder=embedder,
        index=index,
        region=region,
    )
    count = await store.instrument_count(source.source_id)
    await store.record_run(
        source.source_id,
        status="ok",
        detail=f"inserted {inserted}",
        documents=count,
        when=when,
    )


async def courtlistener_night(
    store: Store,
    source,
    *,
    on: date,
    when: datetime,
    http_get,
    embedder: Embedder,
    index: VectorIndex,
    region: str,
    budget=None,
    clock=None,
) -> None:
    """Finish the bulk snapshot, then ask the API only for later opinions.

    Boston only. The bulk files need no key. The token is a header on the
    update requests. A 429 keeps the cursor. Rows already stored are skipped
    by hash inside ingest.
    """
    from agents.zenas.pool.adapters.courtlistener_bulk import (
        BulkStalled,
        BulkState,
        join_cursor,
        load_bulk,
        progress_line,
        snapshot_day,
        snapshot_is_done,
        split_cursor,
    )
    from agents.zenas.pool.bulk import load_detail

    if region != "us-east":
        return
    adapter = get_adapter(source)
    resume = await store.get_checkpoint(source.source_id)
    bulk_resume, api_resume = split_cursor(resume)
    state = getattr(adapter, "_bulk_state", None)
    parsed_day = snapshot_day(bulk_resume)
    if not isinstance(state, BulkState) or not parsed_day or state.day != parsed_day:
        state = BulkState()
    adapter._bulk_state = state
    skips: Counter[str] = Counter()
    inserted = 0
    line = ""
    day = snapshot_day(bulk_resume)

    if not snapshot_is_done(bulk_resume):
        try:
            result = await load_bulk(http_get, resume=resume, state=state)
        except BulkStalled as exc:
            await store.record_source(source)
            await store.record_run(
                source.source_id, status="stalled", detail=str(exc),
                documents=await store.instrument_count(source.source_id), when=when,
            )
            return
        except (FetchNotConfigured, LicenceError) as exc:
            await store.record_source(source)
            await store.record_run(
                source.source_id,
                status="not_configured",
                detail=str(exc),
                documents=await store.instrument_count(source.source_id),
                when=when,
            )
            return
        for blob in result.blobs:
            inserted += await ingest_snapshot(
                store,
                source,
                [blob],
                on=on,
                embedder=embedder,
                index=index,
                region=region,
                skips=skips,
            )
        skips.update(result.skips)
        line = result.detail or progress_line(result.snapshot, result.file_label, result.rows_read, result.cases_kept, result.skips)
        day = result.snapshot or day
        if not snapshot_is_done(result.cursor):
            await store.set_checkpoint(source.source_id, result.cursor, await store.stored_bytes())
            await store.record_source(source)
            await store.record_run(
                source.source_id,
                status="ok",
                detail=line,
                documents=await store.instrument_count(source.source_id),
                when=when,
            )
            return
        _bulk_done, api_resume = split_cursor(result.cursor)

    since = date.fromisoformat(day) if day else None
    done_cursor = f"cl-bulk:{day}:done" if day else ""
    try:
        blobs, api_cursor = await adapter.fetch_updates(
            http_get,
            since=since,
            resume=api_resume,
            budget=budget,
            clock=clock,
        )
    except RateLimited as exc:
        await store.set_checkpoint(source.source_id, join_cursor(done_cursor, api_resume), await store.stored_bytes())
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="rate_limited",
            detail=str(exc),
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return
    except (FetchNotConfigured, LicenceError) as exc:
        await store.set_checkpoint(source.source_id, done_cursor, await store.stored_bytes())
        await store.record_source(source)
        detail = f"{line}; {exc}" if line else str(exc)
        await store.record_run(
            source.source_id,
            status="ok" if line else "not_configured",
            detail=detail,
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return
    for blob in blobs:
        inserted += await ingest_snapshot(
            store,
            source,
            [blob],
            on=on,
            embedder=embedder,
            index=index,
            region=region,
            skips=skips,
        )
    note = adapter.load_note() if hasattr(adapter, "load_note") else {}
    skips.update(note.get("skips") or {})
    await store.set_checkpoint(
        source.source_id,
        join_cursor(done_cursor, api_cursor),
        await store.stored_bytes(),
    )
    await store.record_source(source)
    detail = load_detail(inserted, skips)
    if line:
        detail = f"{line}; {detail}"
    status = "ok"
    if note.get("status") == "budget":
        status = "budget"
        phrase = str(note.get("message") or "")
        detail = f"{line}; {phrase}" if line else phrase
    elif note.get("status") == "rate_limited":
        status = "rate_limited"
        detail = f"{note.get('message') or 'HTTP 429'}; the next Load resumes; {detail}"
    await store.record_run(
        source.source_id,
        status=status,
        detail=detail,
        documents=await store.instrument_count(source.source_id),
        when=when,
    )


async def _live_archive(
    store: Store,
    source,
    *,
    on: date,
    when: datetime,
    http_get,
    embedder: Embedder,
    index: VectorIndex,
    region: str,
) -> bool:
    """Resume a ZA court archive. Other sources keep the ordinary live fetch.

    The walk streams like a Load (bulk._ArchiveStream): each judgment is
    stored as it is read and the cursor is saved every few files and on an
    early exit, so a night that dies keeps what it read and memory stays flat.
    bulk.py imports urllib_get from this module, so bulk is imported here,
    inside the function.
    """
    from agents.zenas.pool.adapters.za_archive import ZA_ARCHIVE_SOURCES, feed_detail, streaming
    from agents.zenas.pool.bulk import _ArchiveStream, disk_cap_bytes
    from agents.zenas.pool.http import RateLimited

    if source.source_id not in ZA_ARCHIVE_SOURCES:
        return False
    adapter = get_adapter(source)
    if not hasattr(adapter, "fetch_bulk"):
        return False
    cap = disk_cap_bytes()
    stream = _ArchiveStream(
        store, source, region=region, on=on, when=when, embedder=embedder, index=index,
        cap=cap, data_dir=os.environ.get("DATA_DIR", ".") or ".",
        heartbeat=False,
    )
    try:
        resume = await store.get_checkpoint(source.source_id)
        if resume.startswith("snap:"):
            resume = ""
        with streaming(stream):
            blobs, cursor = await adapter.fetch_bulk(http_get, resume=resume)
    except RateLimited as exc:
        await stream.keep_progress()
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="rate_limited",
            detail=f"{exc}; the next Load retries",
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return True
    except (FetchNotConfigured, LicenceError) as exc:
        await stream.keep_progress()
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="not_configured",
            detail=str(exc),
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return True
    except Exception:
        # The judgments already stored stay; the next night resumes after them.
        await stream.keep_progress()
        raise
    if stream.capped:
        await stream.keep_progress()
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="disk_cap",
            detail=f"pool disk cap {cap} bytes",
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return True
    note: dict = {}
    load_note = getattr(adapter, "load_note", None)
    if callable(load_note):
        try:
            loaded = load_note()
        except Exception:  # noqa: BLE001 — a note never fails the night
            loaded = {}
        if isinstance(loaded, dict):
            note = loaded
    skips: Counter[str] = Counter(note.get("skips") or {})
    skips.update(stream.skips)
    inserted = stream.inserted
    if blobs:
        inserted += await ingest_snapshot(
            store,
            source,
            list(blobs),
            on=on,
            embedder=embedder,
            index=index,
            region=region,
            skips=skips,
        )
    else:
        await store.record_source(source)
    if cursor:
        await store.set_checkpoint(source.source_id, cursor, await store.stored_bytes())
    elif resume:
        await store.set_checkpoint(source.source_id, "", await store.stored_bytes())
    count = await store.instrument_count(source.source_id)
    await store.record_run(
        source.source_id,
        status="ok",
        detail=feed_detail(inserted, skips),
        documents=count,
        when=when,
    )
    return True


async def _update_official(
    store: Store,
    source,
    *,
    on: date,
    when: datetime,
    http_get,
    embedder: Embedder,
    index: VectorIndex,
    region: str,
) -> None:
    """Resume the gov.za walk and record Acts loaded, amendments, and errors."""
    adapter = get_adapter(source)
    resume = await store.get_checkpoint(source.source_id)
    if resume.startswith("snap:"):
        resume = ""
    try:
        blobs, cursor = await adapter.fetch_bulk(http_get, resume=resume)
    except RateLimited as exc:
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="rate_limited",
            detail=str(exc),
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return
    except (FetchNotConfigured, LicenceError) as exc:
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="not_configured",
            detail=str(exc),
            documents=0,
            when=when,
        )
        return
    inserted = 0
    if blobs:
        inserted = await ingest_snapshot(
            store,
            source,
            list(blobs),
            on=on,
            embedder=embedder,
            index=index,
            region=region,
        )
    else:
        await store.record_source(source)
    detail = adapter.feeds_detail()
    errors = int(getattr(adapter.report, "errors", 0) or 0)
    if not blobs and errors == 0:
        detail = f"{detail}; unchanged"
    status = "error" if errors and inserted == 0 else "ok"
    await store.set_checkpoint(source.source_id, cursor, await store.stored_bytes())
    count = await store.instrument_count(source.source_id)
    await store.record_run(
        source.source_id,
        status=status,
        detail=detail,
        documents=count,
        when=when,
    )


async def _record_source_error(store: Store, source, exc: Exception, when: datetime) -> None:
    """One source failing does not stop the rest of the night."""
    try:
        await store.record_source(source)
    except Exception:
        return
    try:
        await store.record_run(
            source.source_id,
            status="error",
            detail=str(exc),
            documents=0,
            when=when,
        )
    except Exception:
        return


def main() -> None:
    asyncio.run(_main())


async def _main() -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        from agents.zenas.pool.store import PostgresStore

        store = PostgresStore(conn)
        await store.apply_schema()
        use_fixtures = os.environ.get("POOL_USE_FIXTURES", "").strip() in {"1", "true", "yes"}
        if use_fixtures:
            report = await run_nightly(
                store,
                snapshots=packaged_snapshots(current_region()),
                live=False,
            )
        else:
            report = await run_nightly(store, live=True)
        if not use_fixtures:
            try:
                from agents.zenas.core.regulations import run_scheduled as regulations_scheduled

                report["regulations"] = await regulations_scheduled(conn, region=current_region())
            except Exception:
                # No exception text: upstream failures can contain credentials or tenant data.
                report["regulations"] = {"unavailable": 1}
        try:
            from agents.zenas.core.convert import run_scheduled as convert_scheduled

            await convert_scheduled(conn, region=current_region())
        except Exception:
            pass
        try:
            from agents.zenas.core.selftest import run_scheduled

            await run_scheduled(conn, region=current_region())
        except Exception:
            pass
    finally:
        await conn.close()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
