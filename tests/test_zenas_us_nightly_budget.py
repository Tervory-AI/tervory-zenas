"""US nightly must yield even when the bulk reader keeps making progress."""
import asyncio
from datetime import date, datetime, timezone

import pytest

from agents.zenas.pool import nightly
from agents.zenas.pool.adapters import courtlistener_bulk as bulk
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
from agents.zenas.pool.store import MemoryStore


@pytest.mark.asyncio
async def test_progressing_bulk_yields_preserves_checkpoint_and_runs_later_source(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setattr(nightly, "_US_COURTLISTENER_SECONDS", 0.02)
    source = next(row for row in CATALOGUE if row.source_id == "courtlistener")
    later = next(row for row in CATALOGUE if row.source_id == "cap")
    store = MemoryStore(home_region="us-east")
    checkpoint = "cl-bulk:2026-01-01:opinions:42"
    await store.set_checkpoint(source.source_id, checkpoint, 0)
    snapshot = bulk.Snapshot("2026-01-01", {kind: kind for kind in bulk.REQUIRED_KINDS})
    rows_read = 0
    closed = 0
    reached_later = False

    async def keys(_getter):
        return []

    async def progressing(_getter, _url, progress):
        nonlocal rows_read
        while True:
            await progress.row()
            rows_read += 1
            await asyncio.sleep(0)

    class Database:
        async def execute(self, *_args):
            pass

        async def close(self):
            nonlocal closed
            closed += 1

    async def open_database():
        return Database()

    real_update = nightly._update_source

    async def update(store, current, **kwargs):
        nonlocal reached_later
        if current.source_id == later.source_id:
            reached_later = True
            await store.record_source(current)
            await store.record_run(current.source_id, status="ok", detail="", documents=0, when=kwargs["when"])
            return
        return await real_update(store, current, **kwargs)

    async def no_network(*_args, **_kwargs):
        raise AssertionError("network or model call attempted")

    monkeypatch.setattr(bulk, "_collect_keys", keys)
    monkeypatch.setattr(bulk, "choose_snapshot", lambda _keys: snapshot)
    monkeypatch.setattr(bulk, "_read_schema", progressing)
    monkeypatch.setattr(bulk, "_open_db", open_database)
    monkeypatch.setattr(nightly, "_update_source", update)
    await asyncio.wait_for(nightly.run_nightly(
        store, region="us-east", live=True, only=[source.source_id, later.source_id],
        embedder=NoOpEmbedder(), index=NoOpVectorIndex(), http_get=no_network,
    ), timeout=1)
    assert rows_read > 1  # Progress continues: the existing no-progress watchdog cannot stop this.
    assert closed == 2
    assert reached_later
    assert store.runs[source.source_id].last_status == "stalled"
    assert store.runs[source.source_id].detail == str(bulk.BulkStalled())
    assert store.runs[later.source_id].last_status == "ok"
    assert await store.get_checkpoint(source.source_id) == checkpoint
    assert asyncio.current_task().cancelling() == 0


@pytest.mark.asyncio
async def test_unrelated_timeout_is_not_misreported_as_budget_expiry(monkeypatch):
    store = MemoryStore(home_region="us-east")
    source = next(row for row in CATALOGUE if row.source_id == "courtlistener")

    async def network_timeout(*_args, **_kwargs):
        raise TimeoutError("upstream timeout")

    monkeypatch.setattr(nightly, "_update_source", network_timeout)
    await nightly.run_nightly(store, region="us-east", live=True, only=[source.source_id],
                              embedder=NoOpEmbedder(), index=NoOpVectorIndex())
    assert store.runs[source.source_id].last_status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("region,live,snapshots", [
    ("za", True, None), ("us-east", False, None), ("us-east", True, {}),
])
async def test_za_and_offline_paths_have_no_new_deadline(monkeypatch, region, live, snapshots):
    monkeypatch.setattr(nightly, "_US_COURTLISTENER_SECONDS", 0.001)
    source = next(row for row in CATALOGUE if row.source_id == "courtlistener")
    completed = False

    async def slow(*_args, **_kwargs):
        nonlocal completed
        await asyncio.sleep(0.01)
        completed = True

    monkeypatch.setattr(nightly, "_update_source", slow)
    await nightly._update_nightly_source(
        MemoryStore(home_region=region), source, region=region, live=live, snapshots=snapshots,
        on=date(2026, 10, 30), when=datetime.now(timezone.utc),
        http_get=None, embedder=NoOpEmbedder(), index=NoOpVectorIndex(),
    )
    assert completed
