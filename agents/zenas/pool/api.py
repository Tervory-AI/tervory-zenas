"""Pool status for this box, and a load of one source.

The routes are scoped to TANK_REGION. A source from the other region is
refused. Key values are never written into a response.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE, sources_for_region
from agents.zenas.pool.model import SourceSpec
from agents.zenas.pool.region import (
    RegionError,
    assert_box_region,
    assert_source_region,
    bind_store,
)
from agents.zenas.pool.status import pool_status
from agents.zenas.pool.store import Store, key_is_set

log = logging.getLogger("zenas.pool")

# One in-process load per (region, source). The process dying drops the task;
# startup marks a leftover "running" row interrupted.
_load_tasks: dict[tuple[str, str], asyncio.Task] = {}
_load_guard = asyncio.Lock()


def _blank_row(source: SourceSpec) -> dict:
    return {
        "source_id": source.source_id,
        "name": source.name,
        "region": source.region,
        "jurisdiction": source.jurisdiction,
        "licence": source.licence,
        "terms_url": source.terms_url,
        "bulk_use": source.bulk_use,
        "enabled": source.enabled,
        "key_name": source.key_name,
        "key_required": source.key_required,
        "key_set": key_is_set(source.key_name),
        "documents": 0,
        "versions": 0,
        "last_update": None,
        "last_load": None,
        "last_status": "never",
        "detail": "",
        "errors": [],
        "link_only": False,
    }


async def status_for_box(store: Store, region: str | None) -> dict:
    """Every catalogue source for this region, with counts from the store.

    A missing or unknown region is refused. It does not come back as an empty list.
    """
    box = assert_box_region(region)
    report = await pool_status(store, region=box)
    by_id = {row["source_id"]: row for row in report["sources"]}
    sources = []
    for source in sources_for_region(box):
        row = by_id.get(source.source_id) or _blank_row(source)
        if source.key_name == "LAWS_AFRICA_API_TOKEN" and not row["key_set"]:
            row = {**row, "last_status": "not_configured", "detail": f"{source.key_name} is not set"}
        sources.append(row)
    return {"region": box, "sources": sources}


def _find(source_id: str) -> SourceSpec | None:
    for source in CATALOGUE:
        if source.source_id == source_id:
            return source
    return None


def _http_region(region: str | None) -> str:
    """A route answers 409 when the box region is missing or unknown."""
    try:
        return assert_box_region(region)
    except RegionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def interrupt_abandoned_loads(store: Store) -> list[str]:
    """A room that died mid-load must not stay "running". The started time stays.

    Postgres keeps the row in ``zenas_pool.updates``. A memory store keeps it on
    the run record. Neither path changes ``last_update``.
    """
    conn = getattr(store, "_conn", None)
    fetch = getattr(conn, "fetch", None) if conn is not None else None
    if fetch is not None:
        rows = await fetch(
            """
            UPDATE zenas_pool.updates
            SET last_status = 'interrupted',
                detail = 'the load was interrupted'
            WHERE last_status = 'running'
            RETURNING source_id
            """
        )
        return [str(row["source_id"]) for row in rows]
    runs = getattr(store, "runs", None)
    if not isinstance(runs, dict):
        return []
    found: list[str] = []
    for source_id, run in runs.items():
        if getattr(run, "last_status", None) != "running":
            continue
        run.last_status = "interrupted"
        run.detail = "the load was interrupted"
        found.append(str(source_id))
    return found


async def _load_one(
    store: Store,
    source: SourceSpec,
    region: str,
    *,
    live: bool,
    http_get,
    snapshots: dict[str, list[bytes]] | None,
) -> None:
    try:
        await load_wave(
            store,
            region=region,
            only=[source],
            live=live,
            http_get=http_get,
            snapshots=snapshots,
        )
    except Exception as exc:
        log.warning("load failed for %s: %s", source.source_id, type(exc).__name__)
        try:
            await store.record_run(
                source.source_id,
                status="error",
                detail="the load failed",
                documents=await store.instrument_count(source.source_id),
                when=datetime.now(timezone.utc),
            )
        except Exception as inner:
            log.warning("load status failed for %s: %s", source.source_id, type(inner).__name__)


async def begin_load(
    open_store: Callable[[], AbstractAsyncContextManager[Store]],
    source: SourceSpec,
    region: str,
    *,
    live: bool,
    http_get,
    snapshots: dict[str, list[bytes]] | None,
) -> dict:
    """Start one load and return at once.

    The answer is {"ok": true, "status": "running"}, or "already running" when
    this source already has a job. The running row is written before the task
    is scheduled, so /pool/status shows it immediately.
    """
    key = (region, source.source_id)
    async with _load_guard:
        existing = _load_tasks.get(key)
        if existing is not None and not existing.done():
            return {"ok": True, "status": "already running"}
        async with open_store() as store:
            bind_store(store, region)
            await store.record_source(source)
            documents = await store.instrument_count(source.source_id)
            await store.record_run(
                source.source_id,
                status="running",
                detail="started",
                documents=documents,
                when=datetime.now(timezone.utc),
            )

        async def runner() -> None:
            try:
                async with open_store() as job_store:
                    bind_store(job_store, region)
                    await _load_one(
                        job_store,
                        source,
                        region,
                        live=live,
                        http_get=http_get,
                        snapshots=snapshots,
                    )
            except Exception as exc:
                log.warning("load failed for %s: %s", source.source_id, type(exc).__name__)
                try:
                    async with open_store() as job_store:
                        bind_store(job_store, region)
                        await job_store.record_run(
                            source.source_id,
                            status="error",
                            detail="the load failed",
                            documents=0,
                            when=datetime.now(timezone.utc),
                        )
                except Exception as inner:
                    log.warning("load status failed for %s: %s", source.source_id, type(inner).__name__)
            finally:
                task = asyncio.current_task()
                async with _load_guard:
                    if _load_tasks.get(key) is task:
                        _load_tasks.pop(key, None)

        try:
            task = asyncio.create_task(runner())
        except Exception as exc:
            log.warning("load failed for %s: %s", source.source_id, type(exc).__name__)
            async with open_store() as store:
                bind_store(store, region)
                await store.record_run(
                    source.source_id,
                    status="error",
                    detail="the load failed",
                    documents=documents,
                    when=datetime.now(timezone.utc),
                )
            raise
        _load_tasks[key] = task
    return {"ok": True, "status": "running"}


async def finish_loads() -> None:
    """Wait for loads started in this process. Tests use this."""
    async with _load_guard:
        tasks = [task for task in _load_tasks.values() if not task.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(
    store: Store,
    *,
    region: str | None,
    http_get=None,
    snapshots: dict[str, list[bytes]] | None = None,
    live: bool | None = None,
) -> FastAPI:
    """HTTP surface for the feeds switchboard. `region` is this box.

    Only `za` and `us-east` are accepted. Anything else raises before a route
    can answer, and each route still refuses a bad region with 409.
    """
    box = assert_box_region(region)
    bind_store(store, box)
    use_live = live if live is not None else snapshots is None

    @asynccontextmanager
    async def _same():
        yield store

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await interrupt_abandoned_loads(store)
        yield

    app = FastAPI(lifespan=_lifespan)

    @app.get("/pool/status")
    async def get_status() -> dict:
        home = _http_region(box)
        return await status_for_box(store, home)

    @app.post("/pool/sources/{source_id}/load")
    async def load_one(source_id: str) -> dict:
        home = _http_region(box)
        source = _find(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="unknown source")
        try:
            assert_source_region(source, home)
        except RegionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await begin_load(
            _same,
            source,
            home,
            live=use_live,
            http_get=http_get,
            snapshots=snapshots,
        )

    return app


def register_routes(
    app: FastAPI,
    open_store: Callable[[], AbstractAsyncContextManager[Store]],
    region: Callable[[], str | None],
    *,
    live: bool = True,
    http_get=None,
    snapshots: dict[str, list[bytes]] | None = None,
) -> None:
    """Add /pool/status and /pool/sources/{id}/load to an existing app (the Zenas room).

    `open_store` yields a store bound to this box for one request; `region` is
    read per request, so a missing or unknown TANK_REGION answers 409.
    """

    @app.get("/pool/status")
    async def get_pool_status() -> dict:
        home = _http_region(region())
        async with open_store() as store:
            bind_store(store, home)
            return await status_for_box(store, home)

    @app.post("/pool/sources/{source_id}/load")
    async def load_pool_source(source_id: str) -> dict:
        home = _http_region(region())
        source = _find(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="unknown source")
        try:
            assert_source_region(source, home)
        except RegionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await begin_load(
            open_store,
            source,
            home,
            live=live,
            http_get=http_get,
            snapshots=snapshots,
        )
