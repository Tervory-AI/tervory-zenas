"""A later wording is a new version. The same wording is not stored twice."""

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.chunk import chunk_document
from agents.zenas.pool.model import Section
from agents.zenas.pool.store import MemoryStore
from agents.zenas.pool.textutil import plan_version, VersionHead


def test_plan_skips_same_hash_and_closes_older_open_version():
    first = VersionHead("v1", "aaa", date(2020, 1, 1), None)
    skip = plan_version([first], content_hash="aaa", effective_from=date(2024, 1, 1), on=date(2024, 1, 1))
    assert skip.action == "skip"
    fresh = plan_version([first], content_hash="bbb", effective_from=date(2024, 1, 1), on=date(2024, 1, 1))
    assert fresh.action == "insert"
    assert fresh.close_ids == ("v1",)
    assert fresh.close_on == date(2024, 1, 1)


def test_as_at_returns_the_wording_in_force_that_day():
    asyncio.run(_as_at())


async def _as_at():
    source = next(item for item in sources_for_region("us-east") if item.source_id == "govinfo-uscode")
    store = MemoryStore()
    await store.record_source(source)

    def version(text: str, start: date):
        doc = build_document(
            source,
            citation="Fixture § 1",
            title="Version fixture",
            level="federal",
            kind="statute",
            body="Congress",
            sections=[Section("(a)", "General", text, [])],
            url="https://www.govinfo.gov/",
            status="in_force",
            doc_date=start,
            effective_from=start,
        )
        return doc

    first = version("nine month claim period", date(2020, 1, 1))
    assert await store.upsert_version(first, chunk_document(first), on=date(2020, 1, 1))
    assert not await store.upsert_version(first, chunk_document(first), on=date(2021, 1, 1))
    assert await store.version_count("govinfo-uscode") == 1

    second = version("ten month claim period", date(2024, 1, 1))
    assert await store.upsert_version(second, chunk_document(second), on=date(2024, 1, 1))
    assert await store.version_count("govinfo-uscode") == 2
    assert await store.instrument_count("govinfo-uscode") == 1

    early = await store.find_by_citation("Fixture § 1(a)", jurisdiction="us", as_at=date(2022, 6, 1))
    assert early and "nine month" in early[0].text
    later = await store.find_by_citation("Fixture § 1(a)", jurisdiction="us", as_at=date(2024, 6, 1))
    assert later and "ten month" in later[0].text
    current = await store.find_by_citation("Fixture § 1(a)", jurisdiction="us", as_at=None)
    assert current and "ten month" in current[0].text
    assert "nine month" in await store.all_text()
