"""The 02:00 nightly ZA archive walk streams like a Load. No live host is contacted.

Before, nightly._live_archive held every judgment until the walk ended and
saved the cursor only then: a night that died kept nothing and re-read the
whole archive the next night.
"""

import asyncio
import sys
import tracemalloc
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool import bulk, nightly
from agents.zenas.pool.adapters import za_archive
from agents.zenas.pool.adapters.za_archive import ArchiveRun, accept_pdf, url_key
from agents.zenas.pool.adapters.za_sars_taxcourt import SarsTaxCourtAdapter
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.region import bind_store
from agents.zenas.pool.store import MemoryStore

SOURCE = next(item for item in CATALOGUE if item.source_id == "sars-taxcourt")
PER_PAGE = 20


def _urls(n: int) -> list[str]:
    return [f"https://www.sars.gov.za/tc/2020/p{i // PER_PAGE}/case-{i:05d}.pdf" for i in range(n)]


def _citation(url: str, text: str) -> str:
    return f"IT {int(url[-9:-4])} [2020] ZATC"


class _Walk(SarsTaxCourtAdapter):
    """A Tax Court archive of ``urls`` on pages of PER_PAGE."""

    urls: list[str] = []

    async def fetch_bulk(self, http_get, *, resume=""):
        self.run = run = ArchiveRun(resume)
        blobs: list[bytes] = []
        pages = sorted({int(url.split("/")[-2][1:]) for url in self.urls})
        for page in pages:
            if run.stopped or run.hold:
                break
            if run.behind(["tc"], "tc", "2020", str(page)):
                continue
            run.place("tc", "2020", str(page))
            for url in [u for u in self.urls if u.split("/")[-2] == f"p{page}"]:
                payload = await accept_pdf(
                    run, http_get, url, title="", body="Tax Court",
                    interval_env="SARS_MIN_INTERVAL", headers={}, citation_for=_citation,
                )
                if payload:
                    blobs.append(payload)
                if run.stopped:
                    break
        run.finish()
        return blobs, run.cursor()


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ZA_CASE_MIN_INTERVAL", "0")
    monkeypatch.setenv("SARS_MIN_INTERVAL", "0")
    monkeypatch.setenv("ZENAS_ZA_ARCHIVE_MAX", "5000")
    # Real PDF parsing is not the point; the bytes still flow through the walk.
    monkeypatch.setattr(za_archive, "pdf_text_or_none", lambda body: body[9:].decode())
    monkeypatch.setattr(nightly, "get_adapter", lambda source: _Walk())


def _getter(fetched: list[str], *, kill_at: int | None = None, body: int = 2000):
    async def get(url, headers):
        if kill_at is not None and len(fetched) == kill_at:
            raise RuntimeError("the night was killed")
        fetched.append(url)
        number = int(url[-9:-4])
        text = f"IN THE TAX COURT OF SOUTH AFRICA case {number} income tax appeal. " + "x" * body
        return b"%PDF-1.4\n" + text.encode()

    return get


async def _night(store, get):
    bind_store(store, "za")
    return await nightly._live_archive(
        store, SOURCE, on=date(2026, 10, 31), when=datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc),
        http_get=get, embedder=bulk.NoOpEmbedder(), index=bulk.NoOpVectorIndex(), region="za",
    )


def _seen(store) -> set[str]:
    cursor = store.checkpoints.get("sars-taxcourt", ("",))[0]
    lines = cursor.split("\n")
    return {part for part in lines[5].split(",") if part} if len(lines) > 5 else set()


def test_a_killed_night_keeps_what_it_stored_with_a_matching_cursor():
    asyncio.run(_killed())


async def _killed():
    _Walk.urls = _urls(60)
    store = MemoryStore()
    fetched: list[str] = []
    with pytest.raises(RuntimeError):
        await _night(store, _getter(fetched, kill_at=23))
    stored = {url_key(doc.url) for doc in store.docs.values()}
    assert len(store.docs) == 23
    assert _seen(store) == stored
    # The next night fetches only the rest.
    again: list[str] = []
    assert await _night(store, _getter(again)) is True
    assert len(again) == 60 - 23
    assert not set(again) & set(fetched)
    assert len(store.docs) == 60
    assert store.runs["sars-taxcourt"].last_status == "ok"


def test_the_night_never_writes_a_running_row():
    asyncio.run(_no_heartbeat())


async def _no_heartbeat():
    _Walk.urls = _urls(30)
    store = MemoryStore()
    statuses: list[str] = []
    original = store.record_run

    async def record_run(source_id, **kwargs):
        statuses.append(kwargs["status"])
        await original(source_id, **kwargs)

    store.record_run = record_run
    await _night(store, _getter([]))
    assert "running" not in statuses
    assert statuses[-1] == "ok"
    assert len(store.docs) == 30


def test_the_nights_memory_does_not_grow_with_the_number_of_judgments(monkeypatch: pytest.MonkeyPatch):
    kept: list[int] = []

    async def count_only(store, source, blobs, **kwargs):
        kept.extend(len(blob) for blob in blobs)
        return len(blobs)

    # Store nothing, so only what the walk itself holds is measured.
    monkeypatch.setattr(bulk, "ingest_snapshot", count_only)

    def peak(n: int) -> int:
        _Walk.urls = _urls(n)
        tracemalloc.start()
        try:
            asyncio.run(_night(MemoryStore(), _getter([], body=50_000)))
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    small, large = peak(40), peak(200)
    assert len(kept) == 240
    # Held judgments would add 160 x ~50 KB = ~8 MB.
    assert large - small < 2_000_000, (small, large)


@pytest.mark.asyncio
async def test_cancelled_nightly_flushes_acknowledged_archive_cursor(monkeypatch):
    """A source budget expires between files: keep even the last partial batch."""
    store = MemoryStore(home_region="za")
    _Walk.urls = _urls(3)
    fetched = []
    getter = _getter(fetched)
    stored = asyncio.Event()

    async def get(url, headers):
        if len(fetched) == 1:
            stored.set()
            await asyncio.Event().wait()
        return await getter(url, headers)

    task = asyncio.create_task(_night(store, get))
    await asyncio.wait_for(stored.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _seen(store) == {url_key(_Walk.urls[0])}
    assert await store.instrument_count(SOURCE.source_id) == 1
