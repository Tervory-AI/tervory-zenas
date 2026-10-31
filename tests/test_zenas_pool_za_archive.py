"""Full-archive walks against saved court pages. No live host is contacted."""

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.za_case_common import is_criminal_caption
from agents.zenas.pool.adapters.za_comptrib import decision_pdfs
from agents.zenas.pool.adapters.za_concourt_oai import year_sets
from agents.zenas.pool.adapters.za_sca_jdownloads import summary_sends
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.store import MemoryStore


def tiny_pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 36 120 Td ({safe}) Tj ET\n".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)

ON = date(2026, 10, 19)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "za" / "archive"
CHALLENGE = Path(__file__).resolve().parent / "fixtures" / "saflii" / "zasca-recent-headed.html"


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ZA_CASE_MIN_INTERVAL", "0")
    monkeypatch.setenv("SCA_MIN_INTERVAL", "0")
    monkeypatch.setenv("COMPTRIB_MIN_INTERVAL", "0")
    monkeypatch.setenv("CONCOURT_MIN_INTERVAL", "0")
    monkeypatch.delenv("SCA_MAX_YEARS", raising=False)
    monkeypatch.delenv("SCA_MAX_PAGES", raising=False)
    monkeypatch.delenv("ZENAS_ZA_ARCHIVE_MAX", raising=False)


def test_year_sets_are_the_judgment_collections_only():
    rows = year_sets((FIXTURES / "concourt-listsets.xml").read_bytes())
    years = [year for year, _spec in rows]
    specs = {spec for _year, spec in rows}
    assert years[0] == 1994
    assert years[-1] == 2027
    assert len(years) == len(set(years))
    assert "com_20.500.12144_38791" not in specs  # Court Roll
    assert "col_20.500.12144_34631" not in specs  # CODESA
    assert "col_20.500.12144_1949" not in specs  # Latest Judgments, walked after the years


def test_criminal_titles_match_the_caption_not_a_commercial_name():
    assert is_criminal_caption("The State vs JG Zuma")
    assert is_criminal_caption("Court order S v JG Zuma")
    assert is_criminal_caption("Pace v The State [2010] ZASCA 162")
    assert not is_criminal_caption("Alpha Shipping (Pty) Ltd v Beta Carriers (Pty) Ltd")


def test_saved_sca_pages_walk_past_page_one_and_skip_criminal():
    asyncio.run(_sca_archive())


async def _sca_archive():
    index = (FIXTURES / "sca-judgements-index.html").read_bytes()
    page = (FIXTURES / "sca-2010.html").read_bytes()
    older = (FIXTURES / "sca-2010-start10.html").read_bytes()
    kuzwayo = (FIXTURES / "kuzwayo-2010-zasca-167.pdf").read_bytes()
    criminal = {
        send
        for blob in (page, older)
        for send, title in summary_sends(blob)
        if is_criminal_caption(title)
    }
    assert criminal
    calls: list[str] = []

    async def get(url, headers):
        calls.append(url)
        assert headers["User-Agent"].startswith("TervoryZenasPool/1.0")
        assert "app.tervory.com" in headers["User-Agent"]
        if url.rstrip("/").endswith("/judgements"):
            return index
        if "electoral" in url:
            return b"<html></html>"
        if "/send/" in url and "kuzwayo" in url:
            return kuzwayo
        if "/send/" in url:
            return tiny_pdf("A commercial appeal about a contract.")
        if "23-judgments-2010" in url and "start=10" in url:
            return older
        if "23-judgments-2010" in url and "start=" in url:
            return b"<html></html>"
        if "23-judgments-2010" in url:
            return page
        return b"<html></html>"

    store = MemoryStore()
    report = await load_wave(store, region="za", only=[_source("sca")], live=True, http_get=get, on=ON)
    assert any("start=10" in url for url in calls)
    assert not any(url in criminal for url in calls)
    assert report["sources"][0]["documents"] >= 2
    text = await store.all_text()
    assert "KUZWAYO" in text
    assert "MASILELA" in text
    newest = await store.find_by_citation("[2010] ZASCA 177", jurisdiction="za", as_at=None)
    older_case = await store.find_by_citation("[2010] ZASCA 167", jurisdiction="za", as_at=None)
    assert newest and older_case
    detail = store.runs["sca"].detail
    assert "criminal" in detail
    assert "years " in detail
    assert store.checkpoints["sca"][0].startswith("za-archive-2")


def test_saved_comptrib_pages_skip_merger_approval_letters():
    asyncio.run(_comptrib_archive())


async def _comptrib_archive():
    first = (FIXTURES / "comptrib-page1.html").read_bytes()
    second = (FIXTURES / "comptrib-page2.html").read_bytes()
    reasons = (FIXTURES / "comptrib-lm020apr26-reasons.pdf").read_bytes()
    approvals = [url for url in decision_pdfs(first) + decision_pdfs(second) if "merger approval" in unquote(url).lower()]
    assert approvals
    calls: list[str] = []

    async def get(url, headers):
        calls.append(url)
        if "page=2" in url:
            return second
        if "page=" in url:
            return b"<html></html>"
        if "decided-cases" in url:
            return first
        if "reasons-LM020Apr26" in unquote(url):
            return reasons
        return b""

    store = MemoryStore()
    await load_wave(store, region="za", only=[_source("comptrib")], live=True, http_get=get, on=ON)
    assert any("page=2" in url for url in calls)
    assert not any("merger approval" in unquote(url).lower() for url in calls)
    text = await store.all_text()
    assert "Moxy Hotels" in text
    assert "Granger Bay" in text
    found = await store.find_by_citation("CompTrib LM020APR26", jurisdiction="za", as_at=None)
    assert found
    detail = store.runs["comptrib"].detail
    assert "not a judgment" in detail


def test_resumed_sca_run_does_not_download_the_same_judgment_again(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ZENAS_ZA_ARCHIVE_MAX", "1")
    asyncio.run(_resume())


async def _resume():
    index = (FIXTURES / "sca-judgements-index.html").read_bytes()
    page = (FIXTURES / "sca-2010.html").read_bytes()
    calls: list[str] = []

    async def get(url, headers):
        calls.append(url)
        if url.rstrip("/").endswith("/judgements"):
            return index
        if "electoral" in url:
            return b"<html></html>"
        if "/send/" in url:
            return tiny_pdf("A commercial appeal about a contract.")
        if "23-judgments-2010" in url and "start=" not in url:
            return page
        return b"<html></html>"

    store = MemoryStore()
    source = _source("sca")
    first = await load_wave(store, region="za", only=[source], live=True, http_get=get, on=ON)
    assert first["sources"][0]["documents"] == 1
    cursor = store.checkpoints["sca"][0]
    assert cursor.startswith("za-archive-2")
    assert cursor.split("\n")[1] == "0"
    downloaded = [url for url in calls if "/send/" in url]
    assert len(downloaded) == 1
    citation = next(iter(store.docs.values())).citation
    again: list[str] = []

    async def get_again(url, headers):
        again.append(url)
        if url == downloaded[0]:
            raise AssertionError(url)
        return await get(url, headers)

    import os

    os.environ["ZENAS_ZA_ARCHIVE_MAX"] = "8"
    try:
        await load_wave(store, region="za", only=[source], live=True, http_get=get_again, on=ON)
    finally:
        os.environ["ZENAS_ZA_ARCHIVE_MAX"] = "1"
    found = await store.find_by_citation(citation, jurisdiction="za", as_at=None)
    assert found
    assert len({chunk.version_id for chunk in found}) == 1


def test_a_challenge_page_is_logged_and_not_stored(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING, logger="agents.zenas.pool.adapters.za_archive")
    asyncio.run(_challenge(caplog))


async def _challenge(caplog: pytest.LogCaptureFixture):
    wall = CHALLENGE.read_bytes()
    calls: list[str] = []

    async def get(url, headers):
        calls.append(url)
        return wall

    store = MemoryStore()
    report = await load_wave(store, region="za", only=[_source("sca")], live=True, http_get=get, on=ON)
    assert report["sources"][0]["documents"] == 0
    assert not any("/send/" in url for url in calls)
    assert "challenge page" in caplog.text
    assert "zasca-recent-headed" not in caplog.text
    assert "cdn-cgi" not in caplog.text
    detail = store.runs["sca"].detail
    assert "challenge page" in detail
    assert "http" not in detail
    cursor = store.checkpoints["sca"][0]
    assert cursor.split("\n")[1] == "0"
