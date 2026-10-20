import copy
import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas import app as room
from agents.zenas import gold_sources
from agents.zenas.core.engine import retrieve_passages
from agents.zenas.gold_sources import candidates, case_candidate, statute_candidate


def case(region="us-east", holdout=False):
    return {
        "region": region, "jurisdiction": "za" if region == "za" else "us",
        "tenant_id": None, "source": "case", "material_type": "public_record",
        "source_id": "saflii" if region == "za" else "courtlistener",
        "pool_doc_id": "civil-case", "pool_version_id": "v1",
        "text": "The buyer accepted delivery under the supply agreement.",
        "fact_pattern": {"facts": "The buyer accepted delivery under the supply agreement."},
        "element_findings": [{"element_id": "delivery", "finding": "met"}],
        "outcome": "met", "holdout": holdout, "as_at": "2026-10-03",
    }


def section(region="us-east"):
    return {
        "jurisdiction": "za" if region == "za" else "us",
        "source_id": "govza" if region == "za" else "govinfo-uscode",
        "kind": "statute", "text": "A civil agreement requires acceptance.",
        "citation": "Civil Act section 1", "document_id": "act", "version_id": "v1",
        "path": "1", "known_from": "2026-10-01",
    }


@pytest.mark.parametrize("region", ["za", "us-east"])
def test_public_cases_and_heldout_labels(region):
    for held in (True, False):
        result = case_candidate(case(region, held), region)
        assert set(result) == {"skill_id", "input", "expected", "label_source", "label_family", "region", "public", "holdout"}
        assert result["region"] == region
        assert result["public"] is True
        assert result["holdout"] is held
        source = "saflii" if region == "za" else "courtlistener"
        assert result["label_family"] == f"converted:{source}:2026-10"
        assert result["input"]["holdout"] is held
        assert result["label_source"].startswith("held_out:" if held else "converted:")
    assert case_candidate(case(region), "us-east" if region == "za" else "za") is None


def test_private_unknown_criminal_and_unverified_rows_are_excluded():
    for changes in (
        {"tenant_id": "private"}, {"source": "released"}, {"material_type": "client_work"},
        {"source_id": "unknown"}, {"docket_number": "1:20-cr-123"},
        {"text": "A completely different judgment."},
        {"fact_pattern": {"facts": "criminal prosecution"}},
    ):
        row = copy.deepcopy(case())
        row.update(changes)
        assert case_candidate(row, "us-east") is None


def test_statute_label_matches_retrieval_and_unknown_sources_stay_out():
    row = section()
    result = statute_candidate(row, "us-east")
    assert result["label_family"] == "statute_section:govinfo-uscode:2026-10"
    assert result["expected"]["passages"] == retrieve_passages(**result["input"])
    row["source_id"] = "client-upload"
    assert statute_candidate(row, "us-east") is None
    assert statute_candidate(section(), "za") is None


class Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetch(self, query, *args):
        if "worked_matters" in query:
            assert "w.tenant_id IS NULL" in query
            assert args[0:2] == ("us-east", "us")
            return [case()]
        assert args[0] == "us"
        return [section()]


class Pool:
    def acquire(self):
        return Connection()


class PagedPool(Connection):
    def __init__(self, cases=(), sections=()):
        self.cases = list(cases)
        self.sections = list(sections)
        self.offsets = []

    def acquire(self):
        return self

    async def fetch(self, query, *args):
        worked = "worked_matters" in query
        rows = self.cases if worked else self.sections
        rotation, limit, offset = args[-3:]
        assert "ORDER BY md5(" in query and "OFFSET" in query
        self.offsets.append(offset)
        key = "id" if worked else "chunk_id"
        ordered = sorted(rows, key=lambda row: (
            hashlib.md5((str(row[key]) + rotation).encode()).hexdigest(), row[key],
        ))
        return ordered[offset:offset + limit]


def test_full_judgment_is_not_exported_but_paragraph_references_are():
    row = case()
    facts = row["fact_pattern"]["facts"]
    row["text"] = "[1] " + "Background only. " * 50000 + "\n[42] " + facts
    result = case_candidate(row, "us-east")
    assert result["input"]["judgment"] == facts
    assert result["input"]["paragraph_references"][0]["paragraphs"] == [42]
    assert len(json.dumps(result).encode()) < 2000


@pytest.mark.parametrize("field", ["facts", "features", "element_findings"])
def test_oversized_judgment_passages_and_labels_are_skipped(field):
    row = case()
    large = "é" * (60 * 1024)
    if field == "facts":
        row["text"] = large
        row["fact_pattern"]["facts"] = large
    elif field == "features":
        row["fact_pattern"]["features"] = {"large": large}
    else:
        row["element_findings"] = [{"reasoning": large}]
    assert case_candidate(row, "us-east") is None


@pytest.mark.asyncio
async def test_filtering_pages_until_limit_and_rotates_each_day(monkeypatch):
    monkeypatch.setattr(gold_sources, "PAGE_SIZE", 2)
    rows = [dict(case(), id=str(number), pool_doc_id=str(number)) for number in range(30)]
    for row in rows[:20]:
        row["tenant_id"] = "private"
    rows[20]["fact_pattern"] = "{malformed"
    rows[21]["element_findings"] = [{"reasoning": "x" * 70000}]
    pool = PagedPool(rows)
    orders = []
    for day in (20, 21):
        class Clock:
            @staticmethod
            def now(zone):
                assert zone == timezone.utc
                return datetime(2026, 10, day, tzinfo=zone)
        monkeypatch.setattr(gold_sources, "datetime", Clock)
        result = await candidates(pool, region="us-east", limit=5)
        assert len(result) == 5
        order = [item["input"]["pool_doc_id"] for item in result]
        assert len(set(order)) == 5
        assert set(order) <= {str(number) for number in range(22, 30)}
        assert result == await candidates(pool, region="us-east", limit=5)
        orders.append(order)
    assert set(orders[0]) != set(orders[1])
    assert max(pool.offsets) >= 4


@pytest.mark.asyncio
async def test_actual_endpoint_response_stays_under_five_mb(monkeypatch):
    key = secrets.token_urlsafe(24)
    monkeypatch.setenv("TANK_KEY", key)
    monkeypatch.setenv("TANK_REGION", "us-east")
    rows = [
        dict(section(), chunk_id=str(number), path=str(number), text="Civil agreement. " + "é" * 14000)
        for number in range(200)
    ]
    monkeypatch.setattr(room.app.state, "pool", PagedPool(sections=rows), raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app), base_url="http://test") as client:
        response = await client.get("/gold/candidates?limit=200", headers={"X-Tank-Key": key})
    assert response.status_code == 200
    assert 4400000 < len(response.content) <= gold_sources.RESPONSE_MAX_BYTES < 5000000
    result = response.json()
    assert 1 < len(result) < 200
    for row in result:
        for field in ("input", "expected"):
            assert len(gold_sources._encoded(row[field])) <= gold_sources.JSON_MAX_BYTES


@pytest.mark.asyncio
async def test_explicit_holdout_cannot_create_a_training_pair():
    import training

    row = case_candidate(case(holdout=True), "us-east")
    assert row["public"] is True
    assert training.valid_auto_candidate(row, "zenas", "us-east")

    class NoWrites:
        async def execute(self, *args):
            pytest.fail("Held-out public record must not become a training pair")

    await training.pair_from_gold(NoWrites(), row)


@pytest.mark.asyncio
async def test_limit_and_mixed_sources():
    assert await candidates(Pool(), region="us-east", limit=0) == []
    assert len(await candidates(Pool(), region="us-east", limit=1)) == 1
    result = await candidates(Pool(), region="us-east", limit=200)
    assert {row["skill_id"] for row in result} == {"zenas.case_facts", "zenas.retrieve"}
    with pytest.raises(ValueError):
        await candidates(Pool(), region="eu")


@pytest.mark.asyncio
async def test_endpoint_requires_room_key_before_reading_and_fails_without_region(monkeypatch):
    key = secrets.token_urlsafe(24)
    monkeypatch.setenv("TANK_KEY", key)
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setattr(room.app.state, "pool", Pool(), raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app), base_url="http://test") as client:
        assert (await client.get("/gold/candidates")).status_code == 401
        assert (await client.get("/gold/candidates", headers={"X-Tank-Key": secrets.token_urlsafe(24)})).status_code == 401
        response = await client.get("/gold/candidates?limit=1", headers={"X-Tank-Key": key})
        assert response.status_code == 200 and len(response.json()) == 1
        assert (await client.get("/gold/candidates?limit=201", headers={"X-Tank-Key": key})).status_code == 422
        monkeypatch.delenv("TANK_REGION")
        assert (await client.get("/gold/candidates", headers={"X-Tank-Key": key})).status_code == 409
        monkeypatch.delenv("TANK_KEY")
        assert (await client.get("/gold/candidates", headers={"X-Tank-Key": key})).status_code == 401
