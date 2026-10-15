import asyncio
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.licence import LicenceError, may_store_text, terms_recorded
from agents.zenas.pool.model import SourceSpec
from agents.zenas.pool.store import MemoryStore


def test_every_catalogue_source_has_terms():
    ids = [source.source_id for source in CATALOGUE]
    assert len(ids) == len(set(ids))
    for source in CATALOGUE:
        assert source.licence.strip()
        assert source.terms.strip()
        assert source.terms.lower() != "unknown"
        assert source.bulk_use in {"allowed", "forbidden", "unknown"}
        assert source.terms_url.startswith("https://")


def test_terms_are_information_and_enabled_is_the_only_switch():
    recorded = {
        "uncitral-cisg",
        "uncitral-model-law",
        "ali-ucc-official",
        "icc-incoterms",
        "wco-hs-notes",
    }
    found = {source.source_id for source in CATALOGUE if source.source_id in recorded}
    assert found == recorded
    for source in CATALOGUE:
        assert source.enabled is True
        assert may_store_text(source) is True
        assert terms_recorded(source)
    for source in CATALOGUE:
        if source.source_id in recorded:
            assert source.adapter == "refused"
            assert may_store_text(replace(source, enabled=False)) is False


def test_unknown_terms_are_not_recorded():
    source = SourceSpec(
        source_id="example-unknown",
        name="Unknown",
        region="us-east",
        jurisdiction="us",
        licence="",
        terms="unknown",
        terms_url="",
        bulk_use="unknown",
        store_full_text=True,
        canonical_url="https://example.invalid/",
        notes="",
        adapter="refused",
    )
    assert terms_recorded(source) is False
    assert may_store_text(source) is True

    async def _record():
        store = MemoryStore(home_region="us-east")
        with pytest.raises(LicenceError):
            await store.record_source(source)
        assert await store.get_source("example-unknown") is None

    asyncio.run(_record())


def test_disabled_source_is_recorded_and_its_bytes_are_dropped():
    asyncio.run(_disabled())


async def _disabled():
    store = MemoryStore(home_region="us-east")
    source = replace(
        next(item for item in CATALOGUE if item.source_id == "uncitral-cisg"),
        enabled=False,
    )
    with pytest.raises(LicenceError):
        await ingest_snapshot(
            store,
            source,
            [b"FIXTURE-MUST-NOT-BE-STORED"],
            on=date(2026, 10, 13),
        )
    assert await store.get_source("uncitral-cisg") is not None
    assert "FIXTURE-MUST-NOT-BE-STORED" not in await store.all_text()
    assert await store.version_count() == 0
