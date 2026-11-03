"""Fake clocks and adapters bound the entire ZA night without network or models."""
import asyncio
import json
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.error import HTTPError

import pytest

from agents.zenas.core import convert, regulations, selftest
from agents.zenas.pool import nightly
from agents.zenas.pool.adapters import laws_africa_api
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.deadline import DeadlineReached, check_deadline, deadline_scope
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
from agents.zenas.pool.store import MemoryStore
from test_zenas_pool_nightly_archive_streaming import _Walk, _getter, _quick as _quick, _urls


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


BACKENDS = {"embedder": NoOpEmbedder(), "index": NoOpVectorIndex()}


@pytest.mark.asyncio
async def test_archive_yields_at_five_minutes_and_resumes_only_unacknowledged_files(monkeypatch, _quick):
    clock, store = Clock(), MemoryStore(home_region="za")
    _Walk.urls = _urls(4)
    fetched = []
    get = _getter(fetched)

    async def slow(url, headers):
        clock.advance(150)
        return await get(url, headers)

    await nightly.run_nightly(store, region="za", live=True, only=["sars-taxcourt"],
                              http_get=slow, clock=clock, **BACKENDS)
    assert clock.now == 300
    assert store.runs["sars-taxcourt"].last_status == "stalled"
    assert len(store.docs) == 1
    saved = await store.get_checkpoint("sars-taxcourt")
    assert saved.startswith("za-archive-2")
    first_url = fetched[0]
    clock.now = 0
    again = []
    await nightly.run_nightly(store, region="za", live=True, only=["sars-taxcourt"],
                              http_get=_getter(again), clock=clock, **BACKENDS)
    assert first_url not in again
    assert len(again) == 3 and len(store.docs) == 4
    assert store.runs["sars-taxcourt"].last_status == "ok"
    assert asyncio.current_task().cancelling() == 0


@pytest.mark.asyncio
async def test_whole_night_defers_unreached_sources_and_prioritises_them_tomorrow(monkeypatch):
    clock, store = Clock(), MemoryStore(home_region="za")
    base = next(source for source in sources_for_region("za") if source.source_id == "concourt")
    sources = [replace(base, source_id=f"placeholder-{number}") for number in range(22)]
    monkeypatch.setattr(nightly, "sources_for_region", lambda region: list(sources))
    starts = []

    async def update(store, source, **kwargs):
        starts.append(source.source_id)
        previous = await store.get_checkpoint(source.source_id)
        step = int(previous or "0")
        for _ in range(301):
            clock.advance(1)
            check_deadline()
            step += 1
            await store.set_checkpoint(source.source_id, str(step), 0)
            await asyncio.sleep(0)

    monkeypatch.setattr(nightly, "_update_source", update)
    report = await nightly.run_nightly(store, region="za", live=True, clock=clock, **BACKENDS)
    assert clock.now == nightly._ZA_RUN_SECONDS - nightly._ZA_FINISH_SECONDS
    assert clock.now < 100 * 60
    assert starts == [source.source_id for source in sources[:20]]
    deferred = [row["source_id"] for row in report["sources"] if row["last_status"] == "deferred"]
    assert deferred == [source.source_id for source in sources[20:]]
    assert await store.get_checkpoint(sources[0].source_id) == "299"
    assert await store.get_checkpoint(sources[19].source_id) == "269"
    clock.now = 0
    starts.clear()
    await nightly.run_nightly(store, region="za", live=True, clock=clock, **BACKENDS)
    assert starts[:2] == deferred
    assert int(await store.get_checkpoint(sources[0].source_id)) > 299


@pytest.mark.asyncio
async def test_convert_and_later_jobs_share_the_run_deadline(monkeypatch):
    clock, called, report = Clock(), [], {}

    async def quick(conn, *, region):
        called.append("quick")
        return {}

    async def converting(conn, *, region):
        called.append("convert")
        for _ in range(301):
            clock.advance(1)
            check_deadline()
            await asyncio.sleep(0)

    monkeypatch.setattr(regulations, "run_scheduled", quick)
    monkeypatch.setattr(convert, "run_scheduled", converting)
    monkeypatch.setattr(selftest, "run_scheduled", quick)
    await nightly._scheduled_jobs(None, report, region="za", deadline=6000, clock=clock)
    assert clock.now == 300
    assert report["deferred_jobs"] == ["convert"]
    assert called == ["quick", "convert", "quick"]
    called.clear()
    clock.now = 5970
    report = {}
    await nightly._scheduled_jobs(None, report, region="za", deadline=6000, clock=clock)
    assert called == []
    assert report["deferred_jobs"] == ["regulations", "convert", "selftest"]


@pytest.mark.asyncio
async def test_convert_does_not_start_queued_model_work_after_budget_expiry():
    clock = Clock()
    called = []

    class Model:
        parallel = 1

        async def model(self, messages, **kwargs):
            called.append(messages)
            clock.advance(300)
            return {"content": "placeholder"}

    with deadline_scope(300, clock):
        with pytest.raises(DeadlineReached):
            await convert._ask_all(Model(), ["placeholder one", "placeholder two", "placeholder three"])
    assert len(called) == 1


@pytest.mark.asyncio
async def test_main_exits_normally_when_global_deadline_defers_remaining_jobs(monkeypatch, capsys):
    import asyncpg
    from agents.zenas.pool import store as pool_store
    clock = Clock()
    conn = SimpleNamespace(close=AsyncMock())
    store = MemoryStore(home_region="za")
    store.apply_schema = AsyncMock()
    monkeypatch.setenv("DATABASE_URL", "placeholder-offline")
    monkeypatch.delenv("POOL_USE_FIXTURES", raising=False)
    monkeypatch.setattr(nightly, "current_region", lambda: "za")
    monkeypatch.setattr(nightly, "monotonic", clock)
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(pool_store, "PostgresStore", lambda connection: store)

    async def load(store, **kwargs):
        assert kwargs["deadline"] == 6000
        clock.advance(5970)
        return {"region": "za", "sources": [{"source_id": "placeholder", "last_status": "deferred"}]}

    monkeypatch.setattr(nightly, "run_nightly", load)
    assert await nightly._main() is None
    assert clock.now < 6000
    conn.close.assert_awaited_once()
    report = json.loads(capsys.readouterr().out)
    assert report["deferred_jobs"] == ["regulations", "convert", "selftest"]
    assert report["sources"][0]["last_status"] == "deferred"


@pytest.mark.asyncio
async def test_us_jobs_keep_their_original_unbounded_behaviour(monkeypatch):
    clock, called = Clock(), []

    async def slow(conn, *, region):
        clock.advance(7000)
        check_deadline()
        called.append(region)
        return {}

    for module in (regulations, convert, selftest):
        monkeypatch.setattr(module, "run_scheduled", slow)
    report = {}
    await nightly._scheduled_jobs(None, report, region="us-east", clock=clock)
    assert called == ["us-east"] * 3
    assert clock.now == 21000
    assert "deferred_jobs" not in report


@pytest.mark.asyncio
async def test_content_403_is_recorded_once_then_skipped_on_later_nights(monkeypatch):
    store = MemoryStore(home_region="za")
    calls, recorded = [], []
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "placeholder")

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(laws_africa_api.asyncio, "sleep", no_wait)

    async def refused(url, headers):
        calls.append(url)
        raise HTTPError(url, 403, "placeholder response must not be stored", {}, None)

    original = store.record_run

    async def record(source_id, **kwargs):
        recorded.append((source_id, kwargs["status"]))
        await original(source_id, **kwargs)

    store.record_run = record
    for day in (1, 2, 3):
        report = await nightly.run_nightly(store, region="za", live=True, only=["laws-africa"],
                                          on=date(2026, 11, day), http_get=refused, **BACKENDS)
        row = next(row for row in report["sources"] if row["source_id"] == "laws-africa")
        assert row["last_status"] == "not_on_plan"
        assert row["detail"] == laws_africa_api.CONTENT_API_403
    assert len(calls) == 1
    assert recorded.count(("laws-africa", "not_on_plan")) == 1
    assert await store.get_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT) == "not on plan:2026-11-01"
    assert await store.get_checkpoint("laws-africa") == ""
    assert "placeholder response" not in json.dumps(report)


@pytest.mark.asyncio
async def test_other_content_errors_do_not_disable_later_nights(monkeypatch):
    store = MemoryStore(home_region="za")
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "placeholder")
    calls = []

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(laws_africa_api.asyncio, "sleep", no_wait)

    async def refused(url, headers):
        calls.append(url)
        raise HTTPError(url, 401, "placeholder", {}, None)

    for _ in range(2):
        await nightly.run_nightly(store, region="za", live=True, only=["laws-africa"], http_get=refused, **BACKENDS)
    assert len(calls) == 2
    assert await store.get_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT) == ""


@pytest.mark.asyncio
async def test_every_live_za_adapter_gets_the_same_five_minute_cap(monkeypatch):
    from agents.zenas.pool.laws_africa import live_sources
    from datetime import datetime, timezone
    clock = Clock()

    async def slow(store, source, **kwargs):
        clock.advance(300)
        check_deadline()
        raise AssertionError("expired source continued")

    monkeypatch.setattr(nightly, "_update_source", slow)
    for source in live_sources(sources_for_region("za")):
        clock.now = 0
        store = MemoryStore(home_region="za")
        await nightly._update_nightly_source(store, source, region="za", live=True, snapshots=None,
                                             on=date(2026, 11, 3), when=datetime.now(timezone.utc),
                                             _clock=clock, **BACKENDS)
        assert clock.now == 300
        assert store.runs[source.source_id].last_status == "stalled"


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [False, True])
async def test_default_content_transport_is_preserved_under_deadline_wrapper(monkeypatch, custom):
    from agents.zenas.pool import bulk
    store = MemoryStore(home_region="za")
    seen = []
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "placeholder")

    async def supplied(url, headers):
        raise AssertionError("no HTTP in this transport-selection test")

    async def load(store, source, **kwargs):
        seen.append(kwargs["http_get"])

    monkeypatch.setattr(bulk, "load_source", load)
    await nightly.run_nightly(store, region="za", live=True, only=["laws-africa"],
                              **({"http_get": supplied} if custom else {}), **BACKENDS)
    assert len(seen) == 1
    assert (seen[0] is nightly.urllib_get) is (not custom)


@pytest.mark.asyncio
async def test_main_deadline_also_bounds_setup_and_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(nightly, "current_region", lambda: "za")
    monkeypatch.setattr(nightly, "_ZA_RUN_SECONDS", 0.01)

    async def blocked_setup(region, deadline):
        await asyncio.Event().wait()

    monkeypatch.setattr(nightly, "_night_main", blocked_setup)
    assert await asyncio.wait_for(nightly._main(), 1) is None
    assert json.loads(capsys.readouterr().out) == {"region": "za", "deadline_reached": True}


@pytest.mark.asyncio
async def test_content_plan_pause_skips_day_six_and_renews_after_one_probe_on_day_seven(monkeypatch):
    store = MemoryStore(home_region="za")
    start = date(2026, 11, 1)
    calls = []
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "placeholder")

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(laws_africa_api.asyncio, "sleep", no_wait)

    async def refused(url, headers):
        calls.append(url)
        raise HTTPError(url, 403, "placeholder", {}, None)

    async def night(day):
        return await nightly.run_nightly(store, region="za", live=True, only=["laws-africa"],
                                         on=start + timedelta(days=day), http_get=refused, **BACKENDS)

    await night(0)
    assert len(calls) == 1
    await night(6)
    assert len(calls) == 1
    assert await store.get_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT) == "not on plan:2026-11-01"
    await night(7)
    assert len(calls) == 2
    assert await store.get_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT) == "not on plan:2026-11-08"
    await night(13)
    assert len(calls) == 2
    assert store.runs["laws-africa"].last_status == "not_on_plan"


@pytest.mark.asyncio
async def test_expired_plan_pause_is_cleared_for_non_403_failure(monkeypatch):
    store = MemoryStore(home_region="za")
    await store.set_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT, "not on plan:2026-11-01", 0)
    calls = []
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", "placeholder")

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(laws_africa_api.asyncio, "sleep", no_wait)

    async def refused(url, headers):
        calls.append(url)
        raise HTTPError(url, 401, "placeholder", {}, None)

    await nightly.run_nightly(store, region="za", live=True, only=["laws-africa"],
                              on=date(2026, 11, 8), http_get=refused, **BACKENDS)
    assert len(calls) == 1
    assert await store.get_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT) == ""
    assert store.runs["laws-africa"].last_status == "not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", ["not on plan", "not on plan:placeholder", "not on plan:2099-01-01"])
async def test_legacy_or_invalid_plan_pause_cannot_block_recovery(saved):
    store = MemoryStore(home_region="za")
    await store.set_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT, saved, 0)
    await store.set_checkpoint("laws-africa", "placeholder-cursor", 0)
    assert await nightly._content_plan_paused(store, date(2026, 11, 8)) is False
    assert await store.get_checkpoint(nightly._CONTENT_PLAN_CHECKPOINT) == ""
    assert await store.get_checkpoint("laws-africa") == "placeholder-cursor"
