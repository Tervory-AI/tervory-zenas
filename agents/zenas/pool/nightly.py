"""Nightly update. Tests pass saved snapshots. Live fetch is opt-in."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.config import current_region
from agents.zenas.pool.embed import Embedder, NoOpEmbedder, NoOpVectorIndex, VectorIndex
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.licence import FetchNotConfigured, LicenceError, refusal_reason
from agents.zenas.pool.region import RegionError, assert_source_region, bind_store
from agents.zenas.pool.status import pool_status
from agents.zenas.pool.store import Store

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
    embedder = embedder or NoOpEmbedder()
    index = index or NoOpVectorIndex()
    getter = http_get or urllib_get

    for source in sources_for_region(region):
        try:
            assert_source_region(source, region)
            await _update_source(
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
    return await pool_status(store, region=region)


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
    blobs: list[bytes] | None
    if snapshots is not None:
        blobs = list(snapshots.get(source.source_id, []))
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
