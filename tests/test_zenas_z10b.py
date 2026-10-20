import asyncio
import io
import threading
import time
import sys
from pathlib import Path
from datetime import date, datetime, timezone
from contextlib import nullcontext

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters import courtlistener_bulk as bulk


@pytest.mark.asyncio
async def test_full_queue_finishes_without_losing_or_duplicating_rows():
    stop = threading.Event()
    result = []
    async for row in bulk._aiter_thread(lambda: iter(range(100)), stop):
        result.append(row)
        await asyncio.sleep(0.001)
    assert result == list(range(100))
    assert stop.is_set()


@pytest.mark.asyncio
async def test_reader_failure_propagates_after_queued_rows():
    def worker():
        yield "one"
        raise TimeoutError("socket timed out")

    result = []
    with pytest.raises(bulk.BulkStalled, match="resumable"):
        async for row in bulk._aiter_thread(worker, threading.Event()):
            result.append(row)
    assert result == ["one"]


@pytest.mark.asyncio
async def test_stalled_reader_and_cancel_are_bounded(monkeypatch):
    monkeypatch.setattr(bulk, "STALL_SECONDS", 0.02)
    stop = threading.Event()

    def worker():
        stop.wait(2)
        return iter([])

    started = time.monotonic()
    with pytest.raises(bulk.BulkStalled):
        async for _row in bulk._aiter_thread(worker, stop):
            pass
    assert time.monotonic() - started < 1
    assert stop.is_set()


def test_rate_in_detail():
    progress = bulk._Progress(None, "2026-01-01")
    progress.rows_read = 10
    assert "rows/second" in progress.line()


@pytest.mark.asyncio
async def test_heartbeat_marks_a_hung_load_stalled_and_keeps_resume(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setattr(bulk, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(bulk, "STALL_SECONDS", 0.03)
    snapshot = bulk.Snapshot("2026-01-01", {kind: kind for kind in bulk.REQUIRED_KINDS})
    notes = []

    async def keys(_getter):
        return []

    async def hung(*_args):
        await asyncio.sleep(10)

    class Database:
        async def execute(self, _query, detail, _source):
            await asyncio.sleep(0.001)
            notes.append(detail)

        async def close(self):
            pass

    async def open_database():
        return Database()

    monkeypatch.setattr(bulk, "_collect_keys", keys)
    monkeypatch.setattr(bulk, "choose_snapshot", lambda _keys: snapshot)
    monkeypatch.setattr(bulk, "_read_schema", hung)
    monkeypatch.setattr(bulk, "_open_db", open_database)
    state = bulk.BulkState(day=snapshot.day, last_opinion_id=42)
    with pytest.raises(bulk.BulkStalled, match="resumable"):
        await bulk.load_bulk(None, state=state)
    assert len(notes) >= 2
    assert state.last_opinion_id == 42
    assert not state.files_done
    assert asyncio.current_task().cancelling() == 0


class SocketStream(io.RawIOBase):
    def __init__(self, chunks, interval, timeout):
        self.chunks = iter(chunks)
        self.interval = interval
        self.timeout = timeout

    def readable(self):
        return True

    def readinto(self, buffer):
        chunk = next(self.chunks, None)
        if chunk is None:
            time.sleep(self.timeout)
            raise TimeoutError("socket timed out")
        time.sleep(self.interval)
        buffer[:len(chunk)] = chunk
        return len(chunk)


@pytest.mark.asyncio
@pytest.mark.parametrize("trickle", [False, True])
async def test_socket_stream_timeout_and_slow_trickle(monkeypatch, trickle):
    monkeypatch.setattr(bulk, "SOCKET_TIMEOUT", 0.08)
    monkeypatch.setattr(bulk, "STALL_SECONDS", 0.15)
    monkeypatch.setattr(bulk, "be_polite", lambda: None)
    chunks = [b"id,name\n"] + ([b"1,case\n"] * 20 if trickle else [])
    stream = SocketStream(chunks + ([b""] if trickle else []), 0.02, bulk.SOCKET_TIMEOUT)

    def open_stream(_request, timeout):
        assert timeout == bulk.SOCKET_TIMEOUT
        return stream

    monkeypatch.setattr(bulk.urllib.request, "urlopen", open_stream)
    stop = threading.Event()
    rows = []
    started = time.monotonic()
    outcome = nullcontext() if trickle else pytest.raises(bulk.BulkStalled, match="stalled; resumable")
    with outcome:
        async for row in bulk._aiter_thread(
            lambda: bulk._iter_http_rows("https://example.org/dockets.csv", stop), stop
        ):
            rows.append(row)
    assert len(rows) == len(chunks)
    assert time.monotonic() - started < len(chunks) * 0.02 + bulk.SOCKET_TIMEOUT + 0.3
    assert stream.closed
    assert stop.is_set()


@pytest.mark.asyncio
async def test_200000_dockets_with_slow_database_and_heartbeats(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setattr(bulk, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(bulk, "STALL_SECONDS", 1.0)
    monkeypatch.setattr(bulk, "be_polite", lambda: None)
    monkeypatch.setattr(bulk, "_uses_buffer", lambda _getter: False)
    monkeypatch.setattr(bulk, "FILE_ORDER", ("dockets",))
    snapshot = bulk.Snapshot("2026-01-01", {"dockets": "dockets.csv"})
    content = "id,court_id,docket_number,nature_of_suit,case_name\n" + "".join(
        f"{number},ca9,{number},contract,Commercial dispute\n" for number in range(1, 200001)
    )
    monkeypatch.setattr(bulk.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(content.encode()))
    monkeypatch.setattr(bulk, "choose_snapshot", lambda _keys: snapshot)

    async def keys(_getter):
        return []

    class Database:
        def __init__(self):
            self.ids = []
            self.notes = []
            self.writing = False
            self.heartbeat_during_write = False

        async def executemany(self, _query, rows):
            self.writing = True
            await asyncio.sleep(1.1 if not self.ids else 0.015)
            self.ids.extend(row[3] for row in rows)
            self.writing = False

        async def execute(self, query, *args):
            if "SET detail" in query:
                self.notes.append(args[0])
                self.heartbeat_during_write |= self.writing
            else:
                await asyncio.sleep(1.1)

        async def close(self):
            pass

    database = Database()

    async def open_database():
        return database

    monkeypatch.setattr(bulk, "_collect_keys", keys)
    monkeypatch.setattr(bulk, "_open_db", open_database)
    state = bulk.BulkState()
    result = await bulk.load_bulk(None, state=state)
    assert result.rows_read == 200000
    assert database.ids == list(range(1, 200001))
    assert len(state.dockets) == 200000
    assert database.heartbeat_during_write
    assert "dockets" in state.files_done
    assert any("rows read 200000;" in note for note in database.notes)


@pytest.mark.asyncio
async def test_progress_written_every_10000_rows_even_without_heartbeat():
    notes = []

    class Database:
        async def execute(self, _query, detail, _source):
            notes.append(detail)

    progress = bulk._Progress(Database(), "2026-01-01")
    for _number in range(20000):
        await progress.row()
    assert len(notes) == 2
    assert "rows read 10000;" in notes[0]
    assert "rows read 20000;" in notes[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("nightly", [False, True])
async def test_stalled_status_and_saved_checkpoint_survive_both_load_paths(monkeypatch, nightly):
    from agents.zenas.pool.bulk import load_wave
    from agents.zenas.pool.catalogue import CATALOGUE
    from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
    from agents.zenas.pool.nightly import courtlistener_night
    from agents.zenas.pool.store import MemoryStore

    async def stalled(*args, **kwargs):
        raise bulk.BulkStalled("rows read 19533; 0.0 rows/second")

    async def no_network(*args, **kwargs):
        raise AssertionError("network called")

    monkeypatch.setattr(bulk, "load_bulk", stalled)
    source = next(item for item in CATALOGUE if item.source_id == "courtlistener")
    store = MemoryStore(home_region="us-east")
    checkpoint = "cl-bulk:2026-01-01:opinions:42"
    await store.set_checkpoint("courtlistener", checkpoint, 0)
    if nightly:
        await courtlistener_night(
            store, source, http_get=no_network, on=date(2026, 1, 2),
            when=datetime(2026, 1, 2, tzinfo=timezone.utc),
            embedder=NoOpEmbedder(), index=NoOpVectorIndex(), region="us-east",
        )
    else:
        await load_wave(store, region="us-east", live=True, only=[source], http_get=no_network)
    assert store.runs["courtlistener"].last_status == "stalled"
    assert "resumable" in store.runs["courtlistener"].detail
    assert await store.get_checkpoint("courtlistener") == checkpoint
