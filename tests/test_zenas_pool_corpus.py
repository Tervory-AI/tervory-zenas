"""Offline ingest of the checked-in public-domain wave. No network."""

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.config import jurisdiction_for
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
from agents.zenas.pool.nightly import packaged_snapshots, run_nightly
from agents.zenas.pool.region import RegionError
from agents.zenas.pool.search import search_meaning
from agents.zenas.pool.store import MemoryStore

ON = date(2026, 10, 13)

# Instruments in the saved wave, after a clean ingest.
EXPECTED = {
    "govinfo-uscode": 1,
    "ecfr": 4,
    "federal-register": 1,
    "usitc-htsus": 5,
    "courtlistener": 0,
    "cap": 0,
    "frcp": 1,
    "fre": 1,
    "delaware-code": 1,
    "ny-laws": 0,
    "texas-statutes": 0,
    "frap": 0,
    "uncitral-cisg": 0,
    "uncitral-model-law": 0,
    "ali-ucc-official": 0,
    "icc-incoterms": 0,
    "wco-hs-notes": 0,
}


def _snaps():
    return packaged_snapshots("us-east")


def test_us_wave_counts_licences_and_search():
    asyncio.run(_us_wave())


async def _us_wave():
    store = MemoryStore()
    report = await run_nightly(store, region="us-east", snapshots=_snaps(), on=ON)
    by_id = {row["source_id"]: row for row in report["sources"]}
    assert set(by_id) == set(EXPECTED)
    for source_id, count in EXPECTED.items():
        assert by_id[source_id]["documents"] == count
        assert by_id[source_id]["licence"]
        assert by_id[source_id]["jurisdiction"] == "us"
    for source_id in (
        "uncitral-cisg",
        "uncitral-model-law",
        "ali-ucc-official",
        "icc-incoterms",
        "wco-hs-notes",
    ):
        assert by_id[source_id]["documents"] == 0
        assert by_id[source_id]["last_status"] == "no_snapshot"
        assert by_id[source_id]["link_only"] is False
        assert by_id[source_id]["enabled"] is True
        assert by_id[source_id]["key_set"] is True

    carmack = await store.find_by_citation("49 U.S.C. § 14706(a)(1)", jurisdiction="us", as_at=None)
    assert carmack and "bill of lading" in carmack[0].text.lower()
    assert await store.find_by_citation("49 USC § 14706(a)(1)", jurisdiction="us", as_at=None)

    edition = date(2024, 1, 3)
    assert await store.find_by_citation("49 U.S.C. § 14706", jurisdiction="us", as_at=edition)
    assert not await store.find_by_citation("49 U.S.C. § 14706", jurisdiction="us", as_at=date(2020, 1, 1))

    for citation, phrase in (
        ("19 C.F.R. § 141.1(a)", "duties"),
        ("15 C.F.R. § 730.1", "Export Administration Regulations"),
        ("22 C.F.R. § 120.1(a)", "Arms Export Control Act"),
        ("31 C.F.R. § 501.101", "Foreign Assets Control"),
        ("Fed. R. Civ. P. 1", "just, speedy"),
        ("Fed. R. Evid. 401(a)", "more or less probable"),
        ("8 Del. C. § 101(a)", "certificate of incorporation"),
        ("91 FR 64840", "rail carriers"),
        ("HTSUS 8901.10.00.00", "ferry boats"),
    ):
        hits = await store.find_by_citation(citation, jurisdiction="us", as_at=None)
        assert hits, citation
        assert phrase.lower() in hits[0].text.lower()

    found = await store.search_text("bill of lading", jurisdiction="us", as_at=None)
    assert found
    with pytest.raises(RegionError):
        await store.search_text("bill of lading", jurisdiction="za", as_at=None)

    meaning = await search_meaning(
        store, NoOpEmbedder(), NoOpVectorIndex(), "carrier liability", jurisdiction="us"
    )
    assert meaning["reason"] == "embeddings unavailable"
    assert meaning["chunks"] == []
    assert meaning["collection"] == "law_us"

    again = await run_nightly(store, region="us-east", snapshots=_snaps(), on=ON)
    again_ids = {row["source_id"]: row for row in again["sources"]}
    for source_id, count in EXPECTED.items():
        assert again_ids[source_id]["documents"] == count
        assert again_ids[source_id]["versions"] == count


def test_za_region_does_not_ingest_us_sources():
    asyncio.run(_za())


async def _za():
    assert jurisdiction_for("za") == "za"
    assert jurisdiction_for("us-east") == "us"
    za_sources = sources_for_region("za")
    assert za_sources
    assert all(source.region == "za" and source.jurisdiction == "za" for source in za_sources)
    store = MemoryStore()
    report = await run_nightly(store, region="za", snapshots=_snaps(), on=ON)
    assert report["sources"]
    assert {row["source_id"] for row in report["sources"]} == {source.source_id for source in za_sources}
    assert all(row["region"] == "za" for row in report["sources"])
    assert await store.version_count() == 0


def test_chunking_does_not_split_a_section():
    asyncio.run(_chunk())


async def _chunk():
    from agents.zenas.pool.builddoc import build_document
    from agents.zenas.pool.chunk import chunk_document
    from agents.zenas.pool.model import Section

    source = next(item for item in sources_for_region("us-east") if item.source_id == "frcp")
    body = "word " * 2000
    doc = build_document(
        source,
        citation="Fed. R. Civ. P. 1",
        title="Rule 1",
        level="federal",
        kind="court_rule",
        body="Supreme Court of the United States",
        sections=[Section("", "Rule 1", body, [])],
        url="https://www.govinfo.gov/",
        status="in_force",
        doc_date=None,
        effective_from=date(2015, 12, 1),
    )
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert chunks[0].text == body.strip()
    assert "word word" in chunks[0].text
