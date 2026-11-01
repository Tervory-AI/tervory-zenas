"""Real gov.za adapters and saved official PDFs must survive the nightly deadline."""
import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from agents.zenas.pool import nightly
from agents.zenas.pool import ingest as ingestion
from agents.zenas.pool.adapters.za_gazette import GovzaRegulationsAdapter, NOTICES_INDEX
from agents.zenas.pool.adapters.za_govza import ACTS_INDEX, ROBOTS_URL, GovzaActsAdapter, index_rows, pdf_links
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
from agents.zenas.pool.store import MemoryStore

SAVED = Path(__file__).resolve().parents[1] / "agents/zenas/pool/fixtures/saved"
DAY = date(2026, 10, 19)


@pytest.fixture(params=["govza-acts", "govza-regulations"])
def feed(request, monkeypatch):
    source_id = request.param
    acts = source_id == "govza-acts"
    root = SAVED / source_id
    listing = (root / ("amendment-index-row.html" if acts else "dar282-index-rows.html")).read_bytes()
    page = (root / ("amendment-document.html" if acts else "dar282-document.html")).read_bytes()
    pdf = (root / ("ert-amendment-10-2025.pdf" if acts else "dar282-gn-r8022.pdf")).read_bytes()
    index = ACTS_INDEX if acts else NOTICES_INDEX
    listing += f'<a href="{index}?page=1">Next</a>'.encode()
    document_url = index_rows(listing.decode())[0].page_url
    pdf_url = pdf_links(page.decode())[0]
    adapter = GovzaActsAdapter() if acts else GovzaRegulationsAdapter()
    monkeypatch.setattr(nightly, "get_adapter", lambda _source: adapter)
    monkeypatch.setattr(ingestion, "get_adapter", lambda _source: adapter)
    # Use the real PDF extractor (the regulation PDF takes seconds). Expire
    # the real deadline at the intended interruption point, not during parsing.
    real_timeout = asyncio.timeout
    windows = []

    def timeout(seconds):
        window = real_timeout(seconds)
        windows.append(window)
        return window

    def expire():
        windows[-1].reschedule(asyncio.get_running_loop().time() + 0.02)

    monkeypatch.setattr(nightly.asyncio, "timeout", timeout)
    monkeypatch.setattr(nightly, "_ZA_SOURCE_SECONDS", 60)
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("GOVZA_STATUTE_MIN_INTERVAL", "0")
    monkeypatch.setenv("GOVZA_ACTS_AS_OF", DAY.isoformat())
    monkeypatch.setenv("GOVZA_ACTS_MAX_PAGES", "20")
    monkeypatch.setenv("GOVZA_REG_MAX_PAGES", "20")
    return source_id, index, document_url, pdf_url, listing, page, pdf, expire


async def run(store, source_id, get):
    return await nightly.run_nightly(store, region="za", live=True, only=[source_id],
        http_get=get, on=DAY, embedder=NoOpEmbedder(), index=NoOpVectorIndex())


@pytest.mark.asyncio
async def test_actual_adapter_resumes_stored_prefix_after_deadline(feed):
    source_id, index, document_url, pdf_url, listing, page, pdf, expire = feed
    store = MemoryStore(home_region="za")
    downloads = []
    stalled = True

    async def get(url, headers):
        if url == ROBOTS_URL:
            return (SAVED / "govza-acts/robots.txt").read_bytes()
        if url == index:
            return listing
        if url == document_url:
            return page
        if url == pdf_url:
            downloads.append(url)
            return pdf
        if url == index + "?page=1":
            if stalled:
                expire()
                await asyncio.Event().wait()
            return b"<html>No further documents</html>"
        raise AssertionError("unexpected network destination")

    await run(store, source_id, get)
    assert store.runs[source_id].last_status == "stalled"
    count = await store.instrument_count(source_id)
    assert count > 0
    saved = json.loads(await store.get_checkpoint(source_id))
    assert document_url in saved["files"]
    assert not saved["full_on"]
    versions = await store.version_count(source_id)

    stalled = False
    await run(store, source_id, get)
    assert store.runs[source_id].last_status == "ok"
    resumed = json.loads(await store.get_checkpoint(source_id))
    assert resumed["full_on"] == DAY.isoformat()
    assert downloads == [pdf_url]  # The first night's stored PDF is never fetched again.
    assert await store.instrument_count(source_id) == count
    assert await store.version_count(source_id) == versions


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_cancelled_ingest_does_not_acknowledge_unstored_pdf(feed, monkeypatch, partial):
    source_id, index, document_url, pdf_url, listing, page, pdf, expire = feed
    store = MemoryStore(home_region="za")
    downloads = []
    original = nightly.ingest_snapshot
    upsert = store.upsert_version
    blocked = True

    async def ingest(*args, **kwargs):
        if blocked and not partial:
            expire()
            await asyncio.Event().wait()
        return await original(*args, **kwargs)

    async def save(*args, **kwargs):
        added = await upsert(*args, **kwargs)
        if blocked and partial:
            expire()
            await asyncio.Event().wait()
        return added

    monkeypatch.setattr(nightly, "ingest_snapshot", ingest)
    monkeypatch.setattr(store, "upsert_version", save)

    async def get(url, headers):
        if url == ROBOTS_URL:
            return (SAVED / "govza-acts/robots.txt").read_bytes()
        if url == index:
            return listing
        if url == document_url:
            return page
        if url == pdf_url:
            downloads.append(url)
            return pdf
        if url == index + "?page=1":
            return b"<html>No further documents</html>"
        raise AssertionError("unexpected network destination")

    await run(store, source_id, get)
    assert store.runs[source_id].last_status == "stalled"
    assert not await store.get_checkpoint(source_id)
    assert await store.instrument_count(source_id) == int(partial)
    blocked = False
    await run(store, source_id, get)
    assert store.runs[source_id].last_status == "ok"
    assert await store.instrument_count(source_id) > 0
    assert await store.version_count(source_id) == await store.instrument_count(source_id)
    assert downloads == [pdf_url, pdf_url]  # Unacknowledged work must be retried.
