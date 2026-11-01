"""An archive walk stores each judgment as it reads it. No live host is contacted.

Before, the walk held every judgment in memory and stored none until the
whole archive had been read: counts sat still for hours in a 2 GB room, and a
restart threw the walk away because the cursor was saved only at the end.
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool import bulk
from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.adapters.za_archive import _SINK, streaming, url_key
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.store import MemoryStore

def tiny_pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 36 120 Td ({safe}) Tj ET\n".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


ON = date(2026, 10, 19)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "za" / "archive"
INDEX = (FIXTURES / "sca-judgements-index.html").read_bytes()
PAGE = (FIXTURES / "sca-2010.html").read_bytes()


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch):
    for name in ("ZA_CASE_MIN_INTERVAL", "SCA_MIN_INTERVAL"):
        monkeypatch.setenv(name, "0")
    monkeypatch.delenv("SCA_MAX_YEARS", raising=False)
    monkeypatch.delenv("SCA_MAX_PAGES", raising=False)
    monkeypatch.setenv("ZENAS_ZA_ARCHIVE_MAX", "8")
    monkeypatch.setattr(bulk, "STREAM_SAVE_EVERY", 2)


def _getter(calls: list[str], on_send=None):
    async def get(url, headers):
        calls.append(url)
        if url.rstrip("/").endswith("/judgements"):
            return INDEX
        if "/send/" in url:
            if on_send is not None:
                await on_send(url)
            return tiny_pdf(f"A commercial appeal about a contract. {url[-40:]}")
        if "23-judgments-2010" in url and "start=" not in url:
            return PAGE
        return b"<html></html>"

    return get


def _sends(calls: list[str]) -> list[str]:
    return [url for url in calls if "/send/" in url]


def test_judgments_are_stored_and_the_cursor_saved_while_the_walk_goes_on():
    asyncio.run(_during_the_walk())


async def _during_the_walk():
    store = MemoryStore()
    calls: list[str] = []
    seen_then: list[tuple[int, str, str]] = []

    async def on_send(url):
        run = store.runs.get("sca")
        seen_then.append((len(store.docs), store.checkpoints.get("sca", ("",))[0], run.detail if run else ""))

    report = await load_wave(
        store, region="za", only=[_source("sca")], live=True, http_get=_getter(calls, on_send), on=ON
    )
    downloads = _sends(calls)
    assert len(downloads) >= 5
    # By the last download the earlier judgments are already in the store,
    # the cursor names them, and the running row says how far the walk is.
    docs_then, cursor_then, detail_then = seen_then[-1]
    assert docs_then >= len(downloads) - 2
    assert cursor_then.startswith("za-archive-2")
    assert url_key(downloads[0]) in cursor_then
    assert detail_then.startswith("read ")
    assert "judgment(s) so far" in detail_then
    # The walk ends as before: ok, every judgment counted.
    assert store.runs["sca"].last_status == "ok"
    assert report["sources"][0]["documents"] == len(store.docs)
    assert f"inserted {len(store.docs)}" in store.runs["sca"].detail


def test_a_walk_that_dies_keeps_what_it_stored_and_the_next_load_resumes():
    asyncio.run(_dies_then_resumes())


async def _dies_then_resumes():
    store = MemoryStore()
    calls: list[str] = []
    count = {"n": 0}

    async def die_on_sixth(url):
        count["n"] += 1
        if count["n"] == 6:
            raise RuntimeError("the room restarted")

    await load_wave(store, region="za", only=[_source("sca")], live=True, http_get=_getter(calls, die_on_sixth), on=ON)
    assert store.runs["sca"].last_status == "error"
    stored = len(store.docs)
    assert stored == 5
    cursor = store.checkpoints["sca"][0]
    first_five = _sends(calls)[:5]
    assert all(url_key(url) in cursor for url in first_five)

    again: list[str] = []
    await load_wave(store, region="za", only=[_source("sca")], live=True, http_get=_getter(again), on=ON)
    assert store.runs["sca"].last_status == "ok"
    assert not set(first_five) & set(_sends(again))
    assert len(store.docs) > stored
    citations = [doc.citation for doc in store.docs.values()]
    assert len(citations) == len(set(citations))


def test_a_rate_limited_walk_saves_its_cursor(monkeypatch: pytest.MonkeyPatch):
    asyncio.run(_rate_limited())


async def _rate_limited():
    from agents.zenas.pool.http import RateLimited

    store = MemoryStore()
    calls: list[str] = []
    count = {"n": 0}

    async def limit_on_fourth(url):
        count["n"] += 1
        if count["n"] == 4:
            raise RateLimited("www.supremecourtofappeal.org.za", 5)

    await load_wave(store, region="za", only=[_source("sca")], live=True, http_get=_getter(calls, limit_on_fourth), on=ON)
    assert store.runs["sca"].last_status == "rate_limited"
    cursor = store.checkpoints["sca"][0]
    assert all(url_key(url) in cursor for url in _sends(calls)[:3])
    assert url_key(_sends(calls)[3]) not in cursor


def test_a_judgment_over_the_disk_cap_is_not_marked_handled():
    asyncio.run(_disk_cap())


async def _disk_cap():
    store = MemoryStore()
    calls: list[str] = []
    report_stop = await bulk.load_source(
        store,
        _source("sca"),
        region="za",
        snapshots=None,
        on=ON,
        when=bulk.datetime.now(bulk.timezone.utc),
        live=True,
        http_get=_getter(calls),
        embedder=bulk.NoOpEmbedder(),
        index=bulk.NoOpVectorIndex(),
        cap=1,
        data_dir=".",
    )
    assert report_stop is True
    assert store.runs["sca"].last_status == "disk_cap"
    assert not store.docs
    downloads = _sends(calls)
    assert len(downloads) == 1
    cursor = store.checkpoints.get("sca", ("",))[0]
    assert url_key(downloads[0]) not in cursor


def test_with_a_sink_the_walk_holds_no_judgments_in_memory():
    asyncio.run(_no_buffer())


async def _no_buffer():
    kept: list[int] = []

    async def sink(run, payload):
        kept.append(len(payload))
        return True

    adapter = get_adapter(_source("sca"))
    calls: list[str] = []
    with streaming(sink):
        blobs, cursor = await adapter.fetch_bulk(_getter(calls), resume="")
    assert blobs == []
    assert len(kept) == len(_sends(calls)) >= 5
    assert cursor.startswith("za-archive-2")
    # Without a sink the walk returns its judgments as before.
    plain, _ = await get_adapter(_source("sca")).fetch_bulk(_getter([]), resume="")
    assert len(plain) == len(kept)


def test_side_by_side_loads_each_see_only_their_own_sink():
    asyncio.run(_isolated())


async def _isolated():
    async def one(name):
        async def sink(run, payload):
            return True

        sink.name = name
        with streaming(sink):
            await asyncio.sleep(0)
            return _SINK.get().name

    assert await asyncio.gather(one("a"), one("b")) == ["a", "b"]
    assert _SINK.get() is None
