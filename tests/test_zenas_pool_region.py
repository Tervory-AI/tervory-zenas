"""US and ZA pools stay on their own boxes. The loader refuses the other region."""

import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE, sources_for_region
from agents.zenas.pool.model import SourceSpec
from agents.zenas.pool.nightly import packaged_snapshots
from agents.zenas.pool.region import RegionError
from agents.zenas.pool.status import pool_status
from agents.zenas.pool.store import MemoryStore

ON = date(2026, 10, 15)


def _za_source() -> SourceSpec:
    return SourceSpec(
        source_id="za-fixture",
        name="South African fixture",
        region="za",
        jurisdiction="za",
        licence="Public record.",
        terms="Publisher terms are recorded as information.",
        terms_url="https://www.gov.za/",
        bulk_use="allowed",
        store_full_text=True,
        canonical_url="https://www.gov.za/",
        notes="",
        adapter="refused",
        enabled=True,
    )


def _us_source():
    return next(item for item in CATALOGUE if item.source_id == "frcp")


def test_us_loader_refuses_a_za_source_before_any_fetch():
    asyncio.run(_us_refuses())


async def _us_refuses():
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"ZA-TEXT-MUST-NOT-BE-READ"

    store = MemoryStore()
    with pytest.raises(RegionError):
        await load_wave(
            store,
            region="us-east",
            only=[_za_source()],
            live=True,
            http_get=get,
            on=ON,
            snapshots={"za-fixture": [b"ZA-TEXT-MUST-NOT-BE-STORED"]},
        )
    assert seen == []
    assert await store.get_source("za-fixture") is None
    assert await store.version_count() == 0
    assert "ZA-TEXT" not in await store.all_text()


def test_za_loader_refuses_a_us_source():
    asyncio.run(_za_refuses())


async def _za_refuses():
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"US-TEXT"

    store = MemoryStore()
    with pytest.raises(RegionError):
        await load_wave(
            store,
            region="za",
            only=[_us_source()],
            live=True,
            http_get=get,
            on=ON,
            snapshots={"frcp": [b"US-TEXT-MUST-NOT-CROSS"]},
        )
    assert seen == []
    assert await store.get_source("frcp") is None
    assert "US-TEXT" not in await store.all_text()


def test_a_bound_store_does_not_read_or_write_the_other_region():
    asyncio.run(_bound())


async def _bound():
    store = MemoryStore(home_region="us-east")
    await store.record_source(_us_source())
    with pytest.raises(RegionError):
        await store.record_source(_za_source())
    with pytest.raises(RegionError):
        await store.find_by_citation("Fed. R. Civ. P. 1", jurisdiction="za", as_at=None)
    with pytest.raises(RegionError):
        await store.search_text("procedure", jurisdiction="za", as_at=None)
    assert await store.get_source("za-fixture") is None

    other = MemoryStore(home_region="za")
    assert await other.all_text() == ""
    assert await other.status_rows() == []


def test_status_for_one_region_omits_the_other():
    asyncio.run(_status())


async def _status():
    us_store = MemoryStore(home_region="us-east")
    za_store = MemoryStore(home_region="za")
    await us_store.record_source(_us_source())
    await za_store.record_source(_za_source())
    with pytest.raises(RegionError):
        await us_store.record_source(_za_source())
    us = await pool_status(us_store, region="us-east")
    za = await pool_status(za_store, region="za")
    assert {row["source_id"] for row in us["sources"]} == {"frcp"}
    assert {row["source_id"] for row in za["sources"]} == {"za-fixture"}
    assert us["region"] == "us-east"
    assert za["region"] == "za"


def test_a_missing_empty_or_unknown_region_is_refused_before_fetch_or_write():
    for region in (None, "", "eu", "us"):
        asyncio.run(_bad_region(region))


async def _bad_region(region):
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"MUST-NOT-BE-FETCHED"

    store = MemoryStore()
    with pytest.raises(RegionError):
        await load_wave(
            store,
            region=region,
            only=[_us_source()],
            live=True,
            http_get=get,
            on=ON,
            snapshots={"frcp": [b"MUST-NOT-BE-STORED"]},
        )
    assert seen == []
    assert store.home_region in (None, "")
    assert await store.get_source("frcp") is None
    assert await store.version_count() == 0
    assert "MUST-NOT" not in await store.all_text()


def test_a_source_with_a_missing_or_unknown_region_is_refused():
    asyncio.run(_bad_source_region())


async def _bad_source_region():
    from dataclasses import replace

    from agents.zenas.pool.region import assert_source_region

    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"MUST-NOT-BE-FETCHED"

    for source_region in (None, "", "eu"):
        source = replace(_us_source(), region=source_region)
        with pytest.raises(RegionError):
            assert_source_region(source, "us-east")
        store = MemoryStore()
        with pytest.raises(RegionError):
            await load_wave(
                store,
                region="us-east",
                only=[source],
                live=True,
                http_get=get,
                on=ON,
                snapshots={"frcp": [b"MUST-NOT-BE-STORED"]},
            )
        assert seen == []
        assert await store.get_source("frcp") is None
        assert await store.version_count() == 0


def test_an_unbound_store_refuses_writes():
    asyncio.run(_unbound_writes())


async def _unbound_writes():
    for home in (None, "", "eu"):
        store = MemoryStore(home_region=home)
        with pytest.raises(RegionError):
            await store.record_source(_us_source())
        with pytest.raises(RegionError):
            await store.record_run(
                "frcp",
                status="ok",
                detail="no",
                documents=1,
                when=datetime.now(timezone.utc),
            )
        assert await store.get_source("frcp") is None
        assert store.runs == {}
        assert await store.version_count() == 0


def test_snapshot_directories_do_not_overlap():
    us = set(packaged_snapshots("us-east"))
    za = set(packaged_snapshots("za"))
    assert "govinfo-uscode" in us
    assert us.isdisjoint(za)
    assert [source.region for source in sources_for_region("us-east")]
    assert all(source.region == "us-east" for source in sources_for_region("us-east"))
    assert all(source.region == "za" for source in sources_for_region("za"))
