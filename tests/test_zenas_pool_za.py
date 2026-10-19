"""South African law loads on the ZA box only. Network calls are mocked."""

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.za_akn import expression_url
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE, sources_for_region
from agents.zenas.pool.citations import extract_citations
from agents.zenas.pool.nightly import packaged_snapshots
from agents.zenas.pool.region import RegionError
from agents.zenas.pool.store import MemoryStore

ON = date(2026, 10, 15)
MARKER = "marker-" + ("a" * 16)
ROOT = Path(__file__).resolve().parents[1]
ACT_XML = (ROOT / "agents/zenas/pool/fixtures/za/companies-act/act-71-s66.xml").read_bytes()


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


def test_sa_citation_forms_are_extracted():
    text = (
        "The court applied Act 71 of 2008, s 66 and 2019 (3) SA 123 (SCA). "
        "See also [2023] ZASCA 12, Uniform Rule 6 and Magistrates' Courts Rule 1."
    )
    found = extract_citations(text)
    lowered = [item.lower() for item in found]
    assert "act 71 of 2008, s 66" in lowered
    assert "2019 (3) sa 123 (sca)" in lowered
    assert "[2023] zasca 12" in lowered
    assert "uniform rule 6" in lowered
    assert "magistrates' courts rule 1" in lowered


def test_za_fixtures_load_citations_and_drop_criminal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAWS_AFRICA_API_TOKEN", raising=False)
    asyncio.run(_fixtures())


async def _fixtures():
    store = MemoryStore()
    snaps = packaged_snapshots("za")
    assert "saflii" in snaps
    assert "companies-act" in snaps
    assert set(snaps).isdisjoint(packaged_snapshots("us-east"))
    report = await load_wave(store, region="za", snapshots=snaps, on=ON)
    by_id = {row["source_id"]: row for row in report["sources"]}
    assert set(by_id) == {source.source_id for source in sources_for_region("za")}
    assert all(row["region"] == "za" and row["jurisdiction"] == "za" for row in by_id.values())
    assert by_id["companies-act"]["documents"] == 1
    assert by_id["saflii"]["documents"] == 2
    assert by_id["govza-gazette"]["documents"] == 1
    assert by_id["uniform-rules"]["documents"] == 1
    assert by_id["magistrates-rules"]["documents"] == 1
    assert by_id["popia"]["documents"] == 0
    assert by_id["popia"]["key_name"] == "LAWS_AFRICA_API_TOKEN"
    assert by_id["popia"]["key_set"] is False
    assert by_id["saflii"]["key_set"] is True
    arms = _source("conventional-arms-act")
    firearms = _source("firearms-control-act")
    assert arms.notes.startswith("Regulatory compliance only.")
    assert firearms.notes.startswith("Regulatory compliance only.")

    text = await store.all_text()
    assert "Dlamini" not in text
    assert "criminal appeal" not in text.lower()
    assert "bill of lading" in text.lower()
    assert "direction of its board" in text.lower()
    assert "49 U.S.C." not in text

    act = await store.find_by_citation("Act 71 of 2008, s 66", jurisdiction="za", as_at=None)
    assert act and "board" in act[0].text.lower()
    report_cite = await store.find_by_citation("2019 (3) SA 123 (SCA)", jurisdiction="za", as_at=None)
    assert report_cite and "freight" in report_cite[0].text.lower()
    appeal = await store.find_by_citation("[2023] ZASCA 12", jurisdiction="za", as_at=None)
    assert appeal
    edges = await store.cites_from(appeal[0].version_id)
    resolved = {citation: ok for citation, _doc, ok in edges}
    assert resolved["Act 71 of 2008, s 66"] is True
    assert resolved["2019 (3) SA 123 (SCA)"] is True
    assert not await store.find_by_citation("[1999] ZASCA 9999", jurisdiction="za", as_at=None)

    with pytest.raises(RegionError):
        await store.find_by_citation("49 U.S.C. § 14706", jurisdiction="us", as_at=None)

    again = await load_wave(store, region="za", snapshots=snaps, on=ON)
    again_ids = {row["source_id"]: row for row in again["sources"]}
    assert again_ids["saflii"]["documents"] == 2
    assert again_ids["companies-act"]["versions"] == 1
    assert "inserted 0" in again_ids["saflii"]["detail"]


def test_us_loader_refuses_every_za_source():
    asyncio.run(_refuse("us-east", sources_for_region("za")))


def test_za_loader_refuses_every_us_source():
    asyncio.run(_refuse("za", sources_for_region("us-east")))


async def _refuse(region: str, sources):
    for source in sources:
        seen: list[str] = []

        async def get(url, headers):
            seen.append(url)
            return b"OTHER-REGION-TEXT"

        store = MemoryStore()
        with pytest.raises(RegionError):
            await load_wave(
                store,
                region=region,
                only=[source],
                live=True,
                http_get=get,
                on=ON,
                snapshots={source.source_id: [b"OTHER-REGION-TEXT"]},
            )
        assert seen == []
        assert await store.get_source(source.source_id) is None
        assert await store.version_count() == 0
        assert "OTHER-REGION" not in await store.all_text()


def test_laws_africa_token_stays_in_the_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", MARKER)
    asyncio.run(_laws_africa())


async def _laws_africa():
    seen: list[tuple[str, dict]] = []

    async def get(url, headers):
        seen.append((url, dict(headers)))
        return ACT_XML

    store = MemoryStore()
    source = _source("companies-act")
    report = await load_wave(store, region="za", only=[source], live=True, http_get=get, on=ON)
    assert len(seen) == 1
    url, headers = seen[0]
    assert url == expression_url("akn/za/act/2008/71")
    assert MARKER not in url
    assert "token=" not in url.lower()
    assert headers["Authorization"] == f"Bearer {MARKER}"
    row = report["sources"][0]
    assert row["documents"] == 1
    assert row["key_set"] is True
    assert MARKER not in str(report)


def test_laws_africa_live_load_skips_until_the_key_is_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAWS_AFRICA_API_TOKEN", raising=False)
    asyncio.run(_laws_africa_missing())


async def _laws_africa_missing():
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return ACT_XML

    store = MemoryStore()
    report = await load_wave(
        store,
        region="za",
        only=[_source("customs-excise-act")],
        live=True,
        http_get=get,
        on=ON,
    )
    assert seen == []
    row = report["sources"][0]
    assert row["last_status"] == "not_configured"
    assert row["documents"] == 0
    assert row["key_name"] == "LAWS_AFRICA_API_TOKEN"
    assert row["key_set"] is False
    assert "LAWS_AFRICA_API_TOKEN is not set" in row["detail"]


def test_saflii_live_load_reads_the_court_pages():
    asyncio.run(_saflii_live())


async def _saflii_live():
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        assert headers["User-Agent"].startswith("TervoryZenasPool")
        return b"<html></html>"

    store = MemoryStore()
    report = await load_wave(store, region="za", only=[_source("saflii")], live=True, http_get=get, on=ON)
    assert seen
    assert all("saflii.org" in url and url.endswith("recent.html") for url in seen)
    row = report["sources"][0]
    assert row["last_status"] == "ok"
    assert row["documents"] == 0
    assert "deferred" not in row["detail"].lower()
    assert await store.all_text() == ""
