"""A slow ZA source must yield so later sources run within the nightly window."""
import asyncio
from datetime import date, datetime, timezone

import pytest

from agents.zenas.pool import nightly
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
from agents.zenas.pool.laws_africa import live_sources
from agents.zenas.pool.store import MemoryStore


@pytest.mark.asyncio
async def test_slow_early_source_keeps_checkpoint_and_reaches_later_court(monkeypatch):
    monkeypatch.setattr(nightly, "_ZA_SOURCE_SECONDS", 0.01)
    store = MemoryStore(home_region="za")
    saved = "durable-checkpoint"
    await store.set_checkpoint("magistrates-rules", saved, 0)
    reached = []

    async def update(store, source, **kwargs):
        reached.append(source.source_id)
        if source.source_id == "magistrates-rules":
            await asyncio.Event().wait()
        await store.record_source(source)
        await store.record_run(source.source_id, status="ok", detail="", documents=0, when=kwargs["when"])

    monkeypatch.setattr(nightly, "_update_source", update)
    report = await asyncio.wait_for(nightly.run_nightly(
        store, region="za", live=True, only=["magistrates-rules", "concourt"],
        embedder=NoOpEmbedder(), index=NoOpVectorIndex(),
    ), timeout=1)
    assert reached == ["magistrates-rules", "concourt"]
    assert store.runs["magistrates-rules"].last_status == "stalled"
    assert "saved checkpoint" in store.runs["magistrates-rules"].detail
    assert store.runs["concourt"].last_status == "ok"
    assert await store.get_checkpoint("magistrates-rules") == saved
    assert report["region"] == "za"
    assert asyncio.current_task().cancelling() == 0


@pytest.mark.asyncio
async def test_upstream_timeout_is_reported_as_error(monkeypatch):
    async def update(*args, **kwargs):
        raise TimeoutError("upstream timeout")

    monkeypatch.setattr(nightly, "_update_source", update)
    store = MemoryStore(home_region="za")
    await nightly.run_nightly(store, region="za", live=True, only=["concourt"],
                              embedder=NoOpEmbedder(), index=NoOpVectorIndex())
    assert store.runs["concourt"].last_status == "error"
    assert store.runs["concourt"].detail == "upstream timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("live,snapshots", [(False, None), (True, {})])
async def test_offline_paths_remain_unbounded(monkeypatch, live, snapshots):
    monkeypatch.setattr(nightly, "_ZA_SOURCE_SECONDS", 0.001)
    completed = []

    async def update(*args, **kwargs):
        await asyncio.sleep(0.01)
        completed.append(True)

    monkeypatch.setattr(nightly, "_update_source", update)
    source = next(s for s in sources_for_region("za") if s.source_id == "concourt")
    await nightly._update_nightly_source(MemoryStore(home_region="za"), source,
        region="za", live=live, snapshots=snapshots,
        on=date.today(), when=datetime.now(timezone.utc))
    assert completed


def test_current_catalogue_leaves_half_hour_after_source_budgets():
    assert len(live_sources(sources_for_region("za"))) * nightly._ZA_SOURCE_SECONDS <= 90 * 60
