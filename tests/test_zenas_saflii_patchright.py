"""SAFLII live fetch uses patchright. A challenge page is an error and is not stored."""

import asyncio
import re
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.za_saflii import SafliiAdapter, _challenge, parse_judgment
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.http import PatchrightPages, urllib_get
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.store import MemoryStore

ON = date(2026, 10, 19)
ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "tests/fixtures/saflii"
PACKAGED = ROOT / "agents/zenas/pool/fixtures/za/saflii"
HEADLESS = (LIVE / "zasca-recent-headless.html").read_bytes()
HEADED = (LIVE / "zasca-recent-headed.html").read_bytes()
COMMERCIAL = (
    (PACKAGED / "2019-3-sa-123.html").read_bytes(),
    (PACKAGED / "2023-zasca-12.html").read_bytes(),
)


def _source():
    # Only these offline transport/parser tests enable SAFLII. The catalogue
    # stays disabled, and the Laws.Africa suite checks live loads cannot fetch it.
    return replace(next(item for item in CATALOGUE if item.source_id == "saflii"), enabled=True)


def test_saved_cloudflare_responses_are_not_judgments():
    """Live pages saved 19 Oct 2026. Both are Cloudflare. Neither is stored text."""
    source = _source()
    assert b"Attention Required" in HEADLESS
    assert b"Just a moment" in HEADED
    for blob in (HEADLESS, HEADED):
        assert _challenge(blob)
        assert parse_judgment(blob, source) == []


def test_packaged_judgments_parse_into_many_documents():
    source = _source()
    documents = []
    for blob in COMMERCIAL:
        documents.extend(parse_judgment(blob, source))
    assert len(documents) >= 2
    assert {item.citation for item in documents} >= {"2019 (3) SA 123 (SCA)", "[2023] ZASCA 12"}


def test_challenge_page_is_an_error_and_is_not_stored():
    asyncio.run(_challenge_load())


async def _challenge_load():
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        assert headers["User-Agent"].startswith("TervoryZenasPool")
        return HEADLESS

    store = MemoryStore()
    report = await load_wave(store, region="za", only=[_source()], live=True, http_get=get, on=ON)
    row = report["sources"][0]
    assert row["last_status"] == "error"
    assert "challenge" in row["detail"].lower()
    assert row["documents"] == 0
    assert row["errors"]
    assert await store.all_text() == ""
    assert seen
    assert all(url.startswith("https://www.saflii.org/") and url.endswith("recent.html") for url in seen)
    assert "Attention Required" not in await store.all_text()


def test_judgment_pages_load_into_many_documents():
    asyncio.run(_judgments())


async def _judgments():
    listing = (
        b'<html><body><a href="/za/cases/ZASCA/2019-3-sa-123.html">one</a>'
        b'<a href="/za/cases/ZASCA/2023-zasca-12.html">two</a></body></html>'
    )

    async def get(url, headers):
        del headers
        if url.endswith("ZASCA/recent.html"):
            return listing
        if url.endswith("recent.html"):
            return b"<html></html>"
        if url.endswith("2019-3-sa-123.html"):
            return COMMERCIAL[0]
        if url.endswith("2023-zasca-12.html"):
            return COMMERCIAL[1]
        return b"<html></html>"

    store = MemoryStore()
    report = await load_wave(store, region="za", only=[_source()], live=True, http_get=get, on=ON)
    row = report["sources"][0]
    assert row["last_status"] == "ok"
    assert row["documents"] >= 2
    text = await store.all_text()
    assert "bill of lading" in text.lower()
    assert "freight" in text.lower()
    assert "Attention Required" not in text
    assert "Just a moment" not in text


class _Pages:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def __aenter__(self) -> "_Pages":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        del headers
        self.urls.append(url)
        return HEADED


def test_default_fetch_uses_patchright(monkeypatch: pytest.MonkeyPatch):
    pages = _Pages()
    monkeypatch.setattr("agents.zenas.pool.http.PatchrightPages", lambda: pages)
    asyncio.run(_default_fetch(pages))


async def _default_fetch(pages: _Pages):
    store = MemoryStore()
    report = await load_wave(
        store,
        region="za",
        only=[_source()],
        live=True,
        http_get=urllib_get,
        on=ON,
    )
    row = report["sources"][0]
    assert pages.urls
    assert all("saflii.org" in url and url.endswith("recent.html") for url in pages.urls)
    assert row["last_status"] == "error"
    assert "challenge" in row["detail"].lower()
    assert await store.all_text() == ""


def test_patchright_refuses_a_url_with_a_credential():
    pages = PatchrightPages()
    with pytest.raises(FetchNotConfigured):
        asyncio.run(pages.get("https://www.saflii.org/za/cases/?api_key=example"))


def test_adapter_parse_still_returns_the_judgment():
    documents = SafliiAdapter().parse(COMMERCIAL[1], _source())
    assert len(documents) == 1
    assert documents[0].citation == "[2023] ZASCA 12"


# Docker's k, m and g suffixes are powers of 1024. 2g is the 2 GB floor.
_TWO_GB = 2 * 1024 * 1024 * 1024
_MEM_UNIT = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
}


def _mem_bytes(value: object) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]*)", text)
    assert match, value
    scale = _MEM_UNIT[match.group(2)] if match.group(2) else 1
    return int(float(match.group(1)) * scale)


def test_zenas_room_memory_is_at_least_2gb() -> None:
    for name in ("docker-compose.yml", "docker-compose.za.yml"):
        compose = yaml.safe_load((ROOT / "agents" / "zenas" / name).read_text(encoding="utf-8"))
        limit = compose["services"]["agent-zenas"]["mem_limit"]
        assert _mem_bytes(limit) >= _TWO_GB
