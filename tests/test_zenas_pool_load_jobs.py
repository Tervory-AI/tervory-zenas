"""A load must answer at once and leave /health able to answer."""

from __future__ import annotations

import asyncio
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.pool.adapters.ecfr import EcfrAdapter  # noqa: E402
from agents.zenas.pool.api import (
    begin_load,
    create_app,
    finish_loads,
    interrupt_abandoned_loads,
)
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.nightly import packaged_snapshots
from agents.zenas.pool.region import bind_store
from agents.zenas.pool.api import register_routes  # noqa: E402
from agents.zenas.pool.store import MemoryStore  # noqa: E402


def _source(region: str, source_id: str):
    return next(source for source in sources_for_region(region) if source.source_id == source_id)


@pytest.mark.asyncio
async def test_health_answers_while_a_fixture_load_is_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    in_parse = threading.Event()
    seen: dict[str, object] = {"used": False, "ident": None, "held": False}
    original = EcfrAdapter.parse

    def gated(self, payload, source):
        if not seen["used"]:
            seen["used"] = True
            seen["ident"] = threading.get_ident()
            in_parse.set()
            entered.set()
            seen["held"] = release.wait(timeout=8)
            in_parse.clear()
        return original(self, payload, source)

    monkeypatch.setattr(EcfrAdapter, "parse", gated)
    store = MemoryStore()
    snapshots = {"ecfr": packaged_snapshots("us-east")["ecfr"]}
    app = FastAPI()
    during: dict[str, object] = {}

    @app.get("/health")
    async def health() -> dict:
        during["parsing"] = in_parse.is_set()
        during["loop"] = threading.get_ident()
        return {"ok": True}

    @asynccontextmanager
    async def open_store():
        yield store

    register_routes(app, open_store, lambda: "us-east", live=False, snapshots=snapshots)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            started = await client.post("/pool/sources/ecfr/load")
            assert started.status_code == 200
            assert started.json() == {"ok": True, "status": "running"}
            assert await asyncio.to_thread(entered.wait, 2)
            health, status = await asyncio.wait_for(
                asyncio.gather(client.get("/health"), client.get("/pool/status")),
                timeout=3,
            )
            assert health.status_code == 200
            assert health.json() == {"ok": True}
            assert during["parsing"] is True
            assert during["loop"] != seen["ident"]
            assert seen["ident"] != threading.get_ident()
            body = status.json()
            row = next(item for item in body["sources"] if item["source_id"] == "ecfr")
            assert row["last_status"] == "running"
            assert row["detail"] == "started"
            assert row["last_load"]
            again = await client.post("/pool/sources/ecfr/load")
            assert again.status_code == 200
            assert again.json() == {"ok": True, "status": "already running"}
            release.set()
            await asyncio.wait_for(finish_loads(), timeout=30)
            assert seen["held"] is True
            done = (await client.get("/pool/status")).json()
        row = next(item for item in done["sources"] if item["source_id"] == "ecfr")
        assert row["last_status"] == "ok"
    finally:
        release.set()
        await finish_loads()


@pytest.mark.asyncio
async def test_a_restart_marks_a_running_source_interrupted_and_keeps_the_start() -> None:
    store = MemoryStore()
    bind_store(store, "us-east")
    source = _source("us-east", "ecfr")
    other = _source("us-east", "frcp")
    started = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    await store.record_source(source)
    await store.record_run(source.source_id, status="running", detail="started", documents=4, when=started)
    await store.record_source(other)
    await store.record_run(other.source_id, status="ok", detail="done", documents=1, when=started)
    assert await interrupt_abandoned_loads(store) == ["ecfr"]
    assert store.runs["ecfr"].last_status == "interrupted"
    assert store.runs["ecfr"].detail == "the load was interrupted"
    assert store.runs["ecfr"].last_update == started
    assert store.runs["ecfr"].documents == 4
    assert store.runs["frcp"].last_status == "ok"
    assert await interrupt_abandoned_loads(store) == []

    app = create_app(store, region="us-east", snapshots={}, live=False)
    async with app.router.lifespan_context(app):
        pass
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        report = (await client.get("/pool/status")).json()
    row = next(item for item in report["sources"] if item["source_id"] == "ecfr")
    assert row["last_status"] == "interrupted"
    assert row["detail"] == "the load was interrupted"
    assert row["last_load"] == started.isoformat()


@pytest.mark.asyncio
async def test_interrupt_on_postgres_does_not_change_the_started_time() -> None:
    class Conn:
        def __init__(self) -> None:
            self.sql = ""

        async def fetch(self, sql: str):
            self.sql = sql
            return [{"source_id": "courtlistener"}]

    class Store:
        def __init__(self) -> None:
            self._conn = Conn()

    store = Store()
    assert await interrupt_abandoned_loads(store) == ["courtlistener"]  # type: ignore[arg-type]
    folded = " ".join(store._conn.sql.split())
    assert "SET last_status = 'interrupted'" in folded
    assert "detail = 'the load was interrupted'" in folded
    assert "WHERE last_status = 'running'" in folded
    assert "last_update" not in folded


def test_the_request_does_not_await_the_wave() -> None:
    api = (ROOT / "agents" / "zenas" / "pool" / "api.py").read_text(encoding="utf-8")
    handler = api.split("async def load_pool_source", 1)[1]
    assert "await begin_load" in handler
    assert "await load_wave" not in handler
    ingest = (ROOT / "agents" / "zenas" / "pool" / "ingest.py").read_text(encoding="utf-8")
    assert "asyncio.to_thread" in ingest
    room = (ROOT / "agents" / "zenas" / "app.py").read_text(encoding="utf-8")
    assert "await interrupt_abandoned_loads(store)" in room


@pytest.mark.asyncio
async def test_begin_load_answers_already_running_for_the_same_source() -> None:
    store = MemoryStore()
    bind_store(store, "za")
    source = _source("za", "companies-act")
    release = asyncio.Event()

    @asynccontextmanager
    async def opener():
        yield store

    snapshots = packaged_snapshots("za")

    async def hold_wave(*_args, **_kwargs):
        await release.wait()
        return {}

    monkey = pytest.MonkeyPatch()
    monkey.setattr("agents.zenas.pool.api.load_wave", hold_wave)
    try:
        first = await begin_load(opener, source, "za", live=False, http_get=None, snapshots=snapshots)
        assert first == {"ok": True, "status": "running"}
        assert store.runs["companies-act"].last_status == "running"
        assert store.runs["companies-act"].detail == "started"
        second = await begin_load(opener, source, "za", live=False, http_get=None, snapshots=snapshots)
        assert second == {"ok": True, "status": "already running"}
        release.set()
        await finish_loads()
        # The stand-in wave does not record a final status. A real wave does.
        assert store.runs["companies-act"].last_status == "running"
    finally:
        release.set()
        monkey.undo()
        await finish_loads()
