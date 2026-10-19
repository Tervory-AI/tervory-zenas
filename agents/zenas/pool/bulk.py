"""One-shot loader. Resumable, idempotent, one region, disk capped.

Run with ``python -m agents.zenas.pool.bulk``. The compose service is
``pool-loader`` and is not started with the room. Low priority is ``nice``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import date, datetime, timezone

from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.config import current_region
from agents.zenas.pool.embed import Embedder, NoOpEmbedder, NoOpVectorIndex, VectorIndex
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.licence import FetchNotConfigured, LicenceError, refusal_reason
from agents.zenas.pool.model import SourceSpec
from agents.zenas.pool.nightly import urllib_get
from agents.zenas.pool.region import RegionError, assert_source_region, bind_store
from agents.zenas.pool.status import pool_status
from agents.zenas.pool.store import Store, key_is_set

DISK_CAP_DEFAULT = 40 * 1024 * 1024 * 1024


def disk_cap_bytes(cap: int | None = None) -> int:
    if cap is not None:
        return cap
    raw = os.environ.get("POOL_DISK_CAP_BYTES", "").strip()
    if not raw:
        return DISK_CAP_DEFAULT
    return int(raw)


async def load_wave(
    store: Store,
    *,
    region: str | None,
    snapshots: dict[str, list[bytes]] | None = None,
    on: date | None = None,
    live: bool = False,
    http_get=None,
    embedder: Embedder | None = None,
    index: VectorIndex | None = None,
    only: list[SourceSpec] | None = None,
    cap: int | None = None,
    data_dir: str | None = None,
) -> dict:
    """Load this region's sources. A source from another region is refused."""
    bind_store(store, region)
    day = on or date.today()
    when = datetime.now(timezone.utc)
    embedder = embedder or NoOpEmbedder()
    index = index or NoOpVectorIndex()
    getter = http_get or urllib_get
    budget = disk_cap_bytes(cap)
    root = data_dir or os.environ.get("DATA_DIR", ".") or "."
    sources = list(only) if only is not None else sources_for_region(region)
    for source in sources:
        try:
            assert_source_region(source, region)
        except RegionError:
            raise
        try:
            stop = await load_source(
                store,
                source,
                region=region,
                snapshots=snapshots,
                on=day,
                when=when,
                live=live,
                http_get=getter,
                embedder=embedder,
                index=index,
                cap=budget,
                data_dir=root,
            )
        except RegionError:
            raise
        except Exception as exc:
            await _record_error(store, source, exc, when)
            continue
        if stop:
            break
    return await pool_status(store, region=region)


async def load_source(
    store: Store,
    source: SourceSpec,
    *,
    region: str,
    snapshots: dict[str, list[bytes]] | None,
    on: date,
    when: datetime,
    live: bool,
    http_get,
    embedder: Embedder,
    index: VectorIndex,
    cap: int,
    data_dir: str,
) -> bool:
    """Load one source. Returns True when the disk cap says to stop the wave."""
    assert_source_region(source, region)
    bind_store(store, region)
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
        return False

    has_snapshot = bool(snapshots and snapshots.get(source.source_id))
    if source.key_required and not has_snapshot and not key_is_set(source.key_name):
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="not_configured",
            detail=f"{source.key_name} is not set",
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return False

    if snapshots is not None:
        blobs = list(snapshots.get(source.source_id, []))
        cursor = await store.get_checkpoint(source.source_id)
        start = _snap_cursor(cursor)
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
            return False
        inserted = 0
        for index_no, blob in enumerate(blobs):
            if index_no < start:
                continue
            if _over_cap(await store.stored_bytes(), len(blob), cap, data_dir):
                await store.record_source(source)
                await store.record_run(
                    source.source_id,
                    status="disk_cap",
                    detail=f"pool disk cap {cap} bytes",
                    documents=await store.instrument_count(source.source_id),
                    when=when,
                )
                return True
            inserted += await ingest_snapshot(
                store,
                source,
                [blob],
                on=on,
                embedder=embedder,
                index=index,
                region=region,
            )
            await store.set_checkpoint(source.source_id, f"snap:{index_no + 1}", await store.stored_bytes())
        count = await store.instrument_count(source.source_id)
        await store.record_run(
            source.source_id,
            status="ok",
            detail=f"inserted {inserted}",
            documents=count,
            when=when,
        )
        return False

    if not live:
        await store.record_source(source)
        count = await store.instrument_count(source.source_id)
        await store.record_run(
            source.source_id,
            status="no_snapshot",
            detail="no saved text for this source",
            documents=count,
            when=when,
        )
        return False

    try:
        adapter = get_adapter(source)
        resume = await store.get_checkpoint(source.source_id)
        if resume.startswith("snap:"):
            resume = ""
        blobs, next_cursor = await _live_blobs(adapter, http_get, resume)
    except (FetchNotConfigured, LicenceError) as exc:
        await store.record_source(source)
        await store.record_run(
            source.source_id,
            status="not_configured",
            detail=str(exc),
            documents=await store.instrument_count(source.source_id),
            when=when,
        )
        return False

    inserted = 0
    for blob in blobs:
        if _over_cap(await store.stored_bytes(), len(blob), cap, data_dir):
            await store.record_source(source)
            await store.record_run(
                source.source_id,
                status="disk_cap",
                detail=f"pool disk cap {cap} bytes",
                documents=await store.instrument_count(source.source_id),
                when=when,
            )
            return True
        inserted += await ingest_snapshot(
            store,
            source,
            [blob],
            on=on,
            embedder=embedder,
            index=index,
            region=region,
        )
    if next_cursor:
        await store.set_checkpoint(source.source_id, next_cursor, await store.stored_bytes())
    await store.record_source(source)
    count = await store.instrument_count(source.source_id)
    await store.record_run(
        source.source_id,
        status="ok",
        detail=f"inserted {inserted}",
        documents=count,
        when=when,
    )
    return False


async def _live_blobs(adapter, http_get, resume: str) -> tuple[list[bytes], str]:
    if hasattr(adapter, "fetch_bulk"):
        result = await adapter.fetch_bulk(http_get, resume=resume)
        if isinstance(result, tuple):
            blobs, cursor = result
            return list(blobs), str(cursor or "")
        return list(result), ""
    return list(await adapter.fetch(http_get)), ""


def _snap_cursor(cursor: str) -> int:
    if cursor.startswith("snap:"):
        try:
            return int(cursor.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


def _over_cap(used: int, incoming: int, cap: int, data_dir: str) -> bool:
    if used + incoming > cap:
        return True
    try:
        free = shutil.disk_usage(data_dir).free
    except OSError:
        return False
    return incoming > free


async def _record_error(store: Store, source: SourceSpec, exc: Exception, when: datetime) -> None:
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
    try:
        os.nice(19)
    except OSError:
        pass
    asyncio.run(_main())


async def _main() -> None:
    import json

    if os.environ.get("POOL_LOAD", "").strip().lower() not in {"1", "true", "yes"}:
        print("pool-loader is idle. Set POOL_LOAD=1 to run one wave.")
        return
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    region = current_region()
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        from agents.zenas.pool.nightly import packaged_snapshots
        from agents.zenas.pool.store import PostgresStore

        store = PostgresStore(conn, home_region=region)
        await store.apply_schema()
        use_fixtures = os.environ.get("POOL_USE_FIXTURES", "").strip() in {"1", "true", "yes"}
        if use_fixtures:
            report = await load_wave(
                store,
                region=region,
                snapshots=packaged_snapshots(region),
                live=False,
            )
        else:
            report = await load_wave(store, region=region, live=True)
    finally:
        await conn.close()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
