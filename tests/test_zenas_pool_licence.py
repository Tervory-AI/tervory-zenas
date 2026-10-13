import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.licence import LicenceError, may_store_text
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


def test_copyrighted_model_texts_are_not_stored():
    refused = {source.source_id: source for source in CATALOGUE if not may_store_text(source)}
    assert {
        "uncitral-cisg",
        "uncitral-model-law",
        "ali-ucc-official",
        "icc-incoterms",
        "wco-hs-notes",
    } <= set(refused)
    for source in refused.values():
        assert source.store_full_text is False
        assert source.adapter == "refused"


def test_unknown_terms_are_refused():
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
    assert may_store_text(source) is False


def test_source_is_recorded_before_text_and_refused_bytes_are_dropped():
    asyncio.run(_refused())


async def _refused():
    store = MemoryStore()
    source = next(item for item in CATALOGUE if item.source_id == "uncitral-cisg")
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
