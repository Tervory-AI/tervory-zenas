"""Official gov.za Acts and regulations. Fixture PDFs only; no live fetch."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.za_gazette import GovzaRegulationsAdapter
from agents.zenas.pool.adapters.za_govza import (
    GovzaActsAdapter,
    Report,
    documents_from_text,
    english_text,
    index_rows,
    is_regulation_row,
    parse_robots,
    polite_get,
    public_ref,
)
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.nightly import run_nightly
from agents.zenas.pool.region import RegionError, bind_store
from agents.zenas.pool.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1] / "agents/zenas/pool/fixtures/saved"
ACTS = ROOT / "govza-acts"
REGS = ROOT / "govza-regulations"
ON = date(2026, 10, 19)
ACT_PDF = (ACTS / "ert-act-6-2024.pdf").read_bytes()
AMEND_PDF = (ACTS / "ert-amendment-10-2025.pdf").read_bytes()
REG_PDF = (REGS / "dar282-gn-r8022.pdf").read_bytes()
ROBOTS = (ACTS / "robots.txt").read_bytes()
ACT_INDEX = (ACTS / "amendment-index-row.html").read_bytes()
ACT_PAGE = (ACTS / "amendment-document.html").read_bytes()
REG_INDEX = (REGS / "dar282-index-rows.html").read_bytes()
REG_PAGE = (REGS / "dar282-document.html").read_bytes()
AMEND_PDF_URL = (
    "https://www.gov.za/sites/default/files/gcis_document/202603/"
    "economic-regulation-transport-amendment-act-10-2025.pdf"
)
REG_PDF_URL = (
    "https://www.gov.za/sites/default/files/gcis_document/202610/55544reg12060gon8022.pdf"
)


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOVZA_STATUTE_MIN_INTERVAL", "0")
    monkeypatch.setenv("GOVZA_ACTS_MAX_PAGES", "1")
    monkeypatch.setenv("GOVZA_ACTS_MAX_FILES", "5")
    monkeypatch.setenv("GOVZA_REG_MAX_PAGES", "1")
    monkeypatch.setenv("GOVZA_REG_MAX_FILES", "5")
    monkeypatch.setenv("GOVZA_STATUTE_WEEK_DAYS", "7")
    monkeypatch.setenv("GOVZA_ACTS_AS_OF", "2026-10-19")
    monkeypatch.delenv("TANK_REGION", raising=False)


def test_robots_allows_the_document_paths_and_refuses_search():
    robots = parse_robots(ROBOTS.decode("utf-8"))
    assert robots.allows("/documents/acts")
    assert robots.allows("/documents/notices")
    assert robots.allows("/sites/default/files/gcis_document/202603/act.pdf")
    assert robots.allows("/search/") is False
    assert robots.allows("/admin/") is False
    assert robots.allows("/user/login") is False


def test_saved_notice_rows_keep_rules_and_drop_a_forfeiture():
    rows = index_rows(REG_INDEX.decode("utf-8"))
    kept = [row for row in rows if is_regulation_row(row)]
    assert len(kept) == 1
    assert "Amendment of Rules" in kept[0].title
    assert kept[0].gazette_number == "55544"
    assert kept[0].gazette_date == "2026-10-16"
    assert kept[0].regulation_gazette == "12060"
    assert all("Forfeiture" not in row.title for row in kept)


def test_act_and_amendment_pdfs_load_many_sections_with_sources():
    source = _source("govza-acts")
    act = GovzaActsAdapter().parse(ACT_PDF, source)
    amendment = GovzaActsAdapter().parse(AMEND_PDF, source)
    passed = [doc for doc in act if doc.status == "as_passed" and ", s " in doc.citation]
    assert len(passed) >= 20
    section = next(doc for doc in passed if doc.citation == "Act 6 of 2024, s 1")
    assert "access" in section.text.lower()
    assert "Not a consolidated text" not in section.text
    for doc in act:
        assert doc.url.startswith("https://www.gov.za/")
        assert "Act 6 of 2024" in doc.citation or "Act 6 of 2024" in doc.title
        assert doc.date == date(2024, 6, 11)
        assert "Gazette 50799" in doc.title
        assert doc.status == "as_passed" or doc.status.startswith("as_amended_by: [")
        assert doc.status not in {"in_force", "consolidated"}
        assert "consolidated text" not in doc.text.lower() or doc.text.startswith(
            "Amending words only. Not a consolidated text."
        )
    own = {doc.citation: doc for doc in amendment if doc.status == "as_passed"}
    assert "Act 10 of 2025, s 1" in own
    assert "Act 10 of 2025, s 2" in own
    record = next(doc for doc in amendment if doc.status.startswith("as_amended_by"))
    assert record.status == "as_amended_by: [Act 10 of 2025]"
    assert record.citation == "Act 6 of 2024, Schedule 1, amended by Act 10 of 2025"
    assert record.text.startswith("Amending words only. Not a consolidated text.")
    assert record.date == date(2026, 3, 3)
    assert "Gazette 54242" in record.title
    assert record.url.startswith("https://www.gov.za/")
    assert "access seeker" not in record.text.lower()


def test_regulation_pdf_keeps_the_gazette_and_drops_the_form(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    source = _source("govza-regulations")
    documents = GovzaRegulationsAdapter().parse(REG_PDF, source)
    assert documents
    assert all(doc.kind == "regulation" for doc in documents)
    assert all(doc.date == date(2026, 10, 16) for doc in documents)
    assert all("Gazette 55544" in doc.title for doc in documents)
    assert any(doc.citation == "Act 91 of 1964, GN R. 8022" and doc.status == "as_passed" for doc in documents)
    assert all("Act 91 of 1964" in doc.citation for doc in documents)
    assert all("PPeettrroolleeuumm" not in doc.text for doc in documents)
    record = next(doc for doc in documents if doc.status.startswith("as_amended_by"))
    assert record.status == "as_amended_by: [GN R. 8022]"
    assert record.citation == "Act 91 of 1964, rules, amended by GN R. 8022"
    assert record.text.startswith("Amending words only. Not a consolidated text.")


def test_unchanged_pdf_is_not_stored_again():
    source = _source("govza-acts")
    text = english_text(AMEND_PDF)
    payload = json.dumps(
        {
            "source": "govza-act",
            "url": AMEND_PDF_URL,
            "page_url": "https://www.gov.za/documents/acts/economic-regulation-transport-amendment-act-10-2025",
            "title": "Economic Regulation of Transport Amendment Act 10 of 2025",
            "act_number": "10",
            "act_year": "2025",
            "gazette_number": "54242",
            "gazette_date": "2026-03-03",
            "notice": "7185",
            "text": text,
        }
    ).encode("utf-8")
    asyncio.run(_unchanged(source, payload))


async def _unchanged(source, payload: bytes) -> None:
    store = MemoryStore()
    bind_store(store, "za")
    first = await ingest_snapshot(store, source, [payload], on=ON, region="za")
    assert first >= 2
    versions = await store.version_count(source.source_id)
    stored = [doc for doc in store.docs.values() if doc.source_id == source.source_id]
    assert all(doc.url == AMEND_PDF_URL for doc in stored)
    assert any(doc.citation == "Act 10 of 2025, s 1" for doc in stored)
    counted: Counter[str] = Counter()
    second = await ingest_snapshot(store, source, [payload], on=ON, region="za", skips=counted)
    assert second == 0
    assert counted["unchanged"] == first
    assert await store.version_count(source.source_id) == versions


def test_a_known_pdf_is_not_downloaded_again(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    asyncio.run(_hash_skip())


async def _hash_skip() -> None:
    seen: list[str] = []

    async def get(url, headers):
        seen.append(urlsplit(url).path)
        assert headers["User-Agent"].startswith("TervoryZenasPool")
        assert "token=" not in url.lower()
        return _body_for(url)

    adapter = GovzaActsAdapter()
    blobs, cursor = await adapter.fetch_bulk(get, resume="")
    assert blobs
    assert any(path.endswith(".pdf") for path in seen)
    assert not any(path.startswith("/search") for path in seen)
    seen.clear()
    again, _cursor = await adapter.fetch_bulk(get, resume=cursor)
    assert again == []
    assert not any(path.endswith(".pdf") for path in seen)
    assert "/documents/acts" in seen
    assert adapter.feeds_detail() == "Acts loaded 0; amendments recorded 0; errors 0"


def test_weekly_check_reads_the_newest_page_and_keeps_the_backfill(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    asyncio.run(_weekly())


async def _weekly() -> None:
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return _body_for(url)

    resume = json.dumps({"page": 4, "full_on": "2026-10-19", "files": {}})
    _blobs, cursor = await GovzaActsAdapter().fetch_bulk(get, resume=resume)
    state = json.loads(cursor)
    assert state["page"] == 4
    assert state["full_on"] == "2026-10-19"
    assert all("page=4" not in url for url in seen)
    assert any(url.rstrip("/").endswith("/documents/acts") for url in seen)


def test_robots_and_a_challenge_page_are_not_stored(monkeypatch: pytest.MonkeyPatch, caplog):
    _env(monkeypatch)
    asyncio.run(_challenge(caplog))


async def _challenge(caplog) -> None:
    seen: list[str] = []

    async def get(url, headers):
        seen.append(urlsplit(url).path)
        if urlsplit(url).path.endswith("/robots.txt"):
            return ROBOTS
        return b"<html><title>Just a moment...</title>cf-challenge</html>"

    adapter = GovzaActsAdapter()
    with caplog.at_level(logging.ERROR):
        blobs, _cursor = await adapter.fetch_bulk(get, resume="")
    assert blobs == []
    assert adapter.report.errors >= 1
    assert "not stored" in caplog.text
    assert "Just a moment" not in caplog.text
    assert not any(path.startswith("/search") for path in seen)
    joined = " ".join(caplog.messages)
    assert "token=" not in joined
    assert "?" not in public_ref("https://www.gov.za/documents/acts?page=1")


def test_us_box_does_not_fetch_govza(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    monkeypatch.setenv("TANK_REGION", "us-east")
    asyncio.run(_us_refused())


async def _us_refused() -> None:
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return ROBOTS

    with pytest.raises(RegionError):
        await GovzaActsAdapter().fetch_bulk(get, resume="")
    with pytest.raises(RegionError):
        await GovzaRegulationsAdapter().fetch_bulk(get, resume="")
    assert seen == []


def test_a_credential_in_the_url_is_refused(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    asyncio.run(_credential())


async def _credential() -> None:
    async def get(url, headers):
        raise AssertionError(url)

    with pytest.raises(FetchNotConfigured):
        await polite_get(
            get,
            "https://www.gov.za/documents/acts?token=not-a-real-secret",
            accept="text/html",
            robots=None,
            report=Report(),
        )


def test_nightly_feeds_line_and_a_second_night_is_unchanged(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    asyncio.run(_nightly())


async def _nightly() -> None:
    seen: list[str] = []

    async def get(url, headers):
        seen.append(urlsplit(url).path)
        assert headers["User-Agent"].startswith("TervoryZenasPool")
        return _body_for(url)

    store = MemoryStore()
    report = await run_nightly(
        store, region="za", live=True, http_get=get, on=ON, only=["govza-acts"]
    )
    row = next(item for item in report["sources"] if item["source_id"] == "govza-acts")
    assert "Acts loaded" in row["detail"]
    assert "amendments recorded" in row["detail"]
    assert "errors" in row["detail"]
    assert row["last_status"] == "ok"
    assert row["documents"] >= 2
    assert any(path.endswith(".pdf") for path in seen)
    seen.clear()
    again = await run_nightly(
        store, region="za", live=True, http_get=get, on=ON, only=["govza-acts"]
    )
    second = next(item for item in again["sources"] if item["source_id"] == "govza-acts")
    assert "unchanged" in second["detail"]
    assert second["documents"] == row["documents"]
    assert second["versions"] == row["versions"]
    assert not any(path.endswith(".pdf") for path in seen)


def test_nightly_records_a_challenge_as_an_error(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    asyncio.run(_nightly_error())


async def _nightly_error() -> None:
    async def get(url, headers):
        if urlsplit(url).path.endswith("/robots.txt"):
            return ROBOTS
        return b"<html>Attention required</html>"

    store = MemoryStore()
    report = await run_nightly(
        store, region="za", live=True, http_get=get, on=ON, only=["govza-acts"]
    )
    row = next(item for item in report["sources"] if item["source_id"] == "govza-acts")
    assert row["last_status"] == "error"
    assert row["documents"] == 0
    assert row["errors"]
    assert "Acts loaded" in row["errors"][0]
    assert "errors" in row["errors"][0]


def test_envelope_text_is_what_gets_stored_for_the_act_url():
    source = _source("govza-acts")
    text = english_text(ACT_PDF)
    documents = documents_from_text(
        source,
        text=text,
        url=(
            "https://www.gov.za/sites/default/files/gcis_document/202406/"
            "50799economicregulationtransportact-62024.pdf"
        ),
        title="Economic Regulation of Transport Act 6 of 2024",
        act_number="6",
        act_year="2024",
        gazette_number="50799",
        gazette_date="2024-06-11",
        notice="4958",
        kind="statute",
    )
    section = next(doc for doc in documents if doc.citation == "Act 6 of 2024, s 1")
    assert section.url.endswith("50799economicregulationtransportact-62024.pdf")
    assert section.status == "as_passed"
    ports = next(doc for doc in documents if doc.citation == "Act 12 of 2005, s 1, amended by Act 6 of 2024")
    assert ports.status == "as_amended_by: [Act 6 of 2024]"
    assert ports.text.startswith("Amending words only. Not a consolidated text.")
    assert "access seeker" not in ports.text.lower()


def _body_for(url: str) -> bytes:
    path = urlsplit(url).path
    if path.endswith("/robots.txt"):
        return ROBOTS
    if "55544reg12060" in path:
        return REG_PDF
    if path.endswith(".pdf"):
        return AMEND_PDF
    if "customs-and-excise-act-amendment-rules" in path:
        return REG_PAGE
    if "economic-regulation-transport-amendment-act" in path:
        return ACT_PAGE
    if path.startswith("/documents/notices"):
        return REG_INDEX
    if path.startswith("/documents/acts"):
        return ACT_INDEX
    raise AssertionError(path)


def test_regulation_fetch_skips_the_forfeiture_row(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    asyncio.run(_reg_fetch())


async def _reg_fetch() -> None:
    seen: list[str] = []

    async def get(url, headers):
        seen.append(urlsplit(url).path)
        return _body_for(url)

    blobs, _cursor = await GovzaRegulationsAdapter().fetch_bulk(get, resume="")
    assert blobs
    assert any("55544reg12060" in path for path in seen)
    assert not any("forfeiture" in path for path in seen)
    payload = json.loads(blobs[0].decode("utf-8"))
    assert payload["url"] == REG_PDF_URL
    assert payload["gazette_number"] == "55544"
    assert payload["gazette_date"] == "2026-10-16"
    assert "PPeettrroolleeuumm" not in payload["text"]
