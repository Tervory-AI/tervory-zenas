"""US library at scale. Fixtures and mocked HTTP only. No live network."""

import asyncio
import io
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.cases import CourtListenerAdapter
from agents.zenas.pool.adapters.govinfo import UsCodeAdapter, govinfo_uscode_url, uscode_bulk_url
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.embed import collection_name
from agents.zenas.pool.nightly import packaged_snapshots
from agents.zenas.pool.search import search_meaning
from agents.zenas.pool.store import MemoryStore

ON = date(2026, 10, 15)

_CITATIONS = (
    ("49 U.S.C. § 14706", "bill of lading"),
    ("19 C.F.R. § 141.1", "duties"),
    ("15 C.F.R. § 730.1", "Export Administration Regulations"),
    ("22 C.F.R. § 120.1", "Arms Export Control Act"),
    ("31 C.F.R. § 501.101", "Foreign Assets Control"),
    ("Fed. R. Civ. P. 1", "just, speedy"),
    ("Fed. R. Evid. 401(a)", "more or less probable"),
    ("8 Del. C. § 101", "certificate of incorporation"),
    ("91 FR 64840", "rail carriers"),
    ("HTSUS 8901.10.00.00", "ferry boats"),
    ("9 U.S.C. § 2", "arbitration agreement"),
    ("11 U.S.C. § 101", "bankruptcy definitions"),
    ("15 U.S.C. § 1", "restraint of trade"),
    ("17 U.S.C. § 102", "original works of authorship"),
    ("19 U.S.C. § 1304", "marking of articles"),
    ("35 U.S.C. § 101", "patentable invention"),
    ("46 U.S.C. § 30701", "Carriage of Goods by Sea"),
    ("50 U.S.C. § 4801", "export control policy"),
    ("Fed. R. App. P. 1", "courts of appeals"),
    ("600 F.3d 10", "bill of lading"),
)


def _uslm(sections: list[tuple[str, str, str, str]]) -> bytes:
    body = []
    for title, number, heading, text in sections:
        body.append(
            f"""<title><num value="{title}">Title {title}</num>
            <section effective="2024-01-03">
              <num value="{number}">§ {number}.</num>
              <heading>{heading}</heading>
              <subsection value="a"><num>(a)</num>
                <content><p>{text}</p></content>
              </subsection>
            </section></title>"""
        )
    xml = "<uscDoc>" + "".join(body) + "</uscDoc>"
    return xml.encode()


def _frap() -> bytes:
    return b"""<html><head><title>U.S.C. Title 28 - JUDICIARY</title></head><body>
<h3 class="section-head">Rule 1. Scope of Rules</h3>
<!-- field-start:statute -->
<p class="statutory-body">These rules govern procedure in the United States courts of appeals.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p>eff. Dec. 1, 2015.</p>
<!-- field-end:sourcecredit -->
</body></html>"""


def _opinions() -> bytes:
    payload = {
        "results": [
            {
                "court": "ca2",
                "case_name": "Fixture Carrier v. Fixture Shipper",
                "date_filed": "2010-05-01",
                "nature_of_suit": "120 Marine Contract",
                "precedential_status": "Published",
                "citations": [{"volume": 600, "reporter": "F.3d", "page": "10"}],
                "plain_text": (
                    "The bill of lading is governed by 49 U.S.C. § 14706. "
                    "See also 999 U.S.C. § 99999."
                ),
                "absolute_url": "/opinion/1/fixture/",
            },
            {
                "court": "ca2",
                "case_name": "Fixture People",
                "date_filed": "2011-01-01",
                "nature_of_suit": "Criminal",
                "citations": [{"volume": 1, "reporter": "F.3d", "page": "9"}],
                "plain_text": "CRIMINAL-OPINION-MUST-NOT-STORE",
            },
            {
                "court": "nysd",
                "case_name": "Fixture District",
                "date_filed": "2012-01-01",
                "nature_of_suit": "Contract",
                "citations": [{"cite": "1 F. Supp. 3d 1"}],
                "plain_text": "DISTRICT-MUST-NOT-STORE",
            },
            {
                "court": "delch",
                "case_name": "Fixture Chancery",
                "date_filed": "2013-04-01",
                "nature_of_suit": "Contract",
                "commercial": True,
                "citations": [{"cite": "12 A.3d 100"}],
                "plain_text": "CHANCERY-FIXTURE-PARAGRAPH about a certificate of incorporation.",
            },
        ]
    }
    return json.dumps(payload).encode()


def _scale_snapshots() -> dict[str, list[bytes]]:
    snaps = packaged_snapshots("us-east")
    snaps["govinfo-uscode"] = list(snaps.get("govinfo-uscode", [])) + [
        _uslm(
            [
                ("9", "2", "Validity of agreement", "A written arbitration agreement is valid."),
                ("11", "101", "Definitions", "Bankruptcy definitions for a commercial debtor."),
                ("15", "1", "Trusts", "Every contract in restraint of trade is illegal."),
                ("17", "102", "Subject matter", "Copyright subsists in original works of authorship."),
                ("19", "1304", "Marking", "Marking of articles imported into the United States."),
                ("35", "101", "Inventions", "A patentable invention is a new and useful process."),
                ("46", "30701", "Bills of lading", "Carriage of Goods by Sea. A carrier shall issue a bill."),
                ("50", "4801", "Export controls", "It is the export control policy of the United States."),
            ]
        )
    ]
    snaps["frap"] = [_frap()]
    snaps["courtlistener"] = [_opinions()]
    return snaps


class _BagEmbedder:
    name = "bag"

    async def embed(self, texts: list[str]):
        return [_vec(text) for text in texts]


class _BagIndex:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, list[float]]] = []

    async def upsert(self, jurisdiction, chunks, vectors, model) -> None:
        for chunk, vector in zip(chunks, vectors):
            self.rows.append((jurisdiction, chunk.chunk_id, vector))

    async def query(self, jurisdiction, vector, *, limit: int) -> list[str]:
        scored = []
        for jur, chunk_id, stored in self.rows:
            if jur != jurisdiction:
                continue
            score = sum(left * right for left, right in zip(vector, stored))
            scored.append((score, chunk_id))
        scored.sort(key=lambda item: -item[0])
        return [chunk_id for score, chunk_id in scored[:limit] if score > 0]


def _vec(text: str) -> list[float]:
    dims = [0.0] * 1024
    for token in text.lower().split():
        if len(token) < 4:
            continue
        dims[hash(token) % 1024] += 1.0
    return dims


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


def test_twenty_citations_resolve_by_citation_and_by_meaning():
    asyncio.run(_twenty())


async def _twenty():
    store = MemoryStore()
    embedder = _BagEmbedder()
    index = _BagIndex()
    report = await load_wave(
        store,
        region="us-east",
        snapshots=_scale_snapshots(),
        on=ON,
        embedder=embedder,
        index=index,
    )
    by_id = {row["source_id"]: row for row in report["sources"]}
    assert by_id["courtlistener"]["documents"] == 2
    assert by_id["govinfo-uscode"]["documents"] >= 9
    text = await store.all_text()
    assert "CRIMINAL-OPINION-MUST-NOT-STORE" not in text
    assert "DISTRICT-MUST-NOT-STORE" not in text
    assert "CHANCERY-FIXTURE-PARAGRAPH" in text
    assert len(_CITATIONS) == 20
    for citation, phrase in _CITATIONS:
        hits = await store.find_by_citation(citation, jurisdiction="us", as_at=None)
        assert hits, citation
        blob = " ".join(hit.text for hit in hits).lower()
        assert phrase.lower() in blob, citation
    assert not await store.find_by_citation("999 U.S.C. § 99999", jurisdiction="us", as_at=None)

    opinion = next(doc for doc in store.docs.values() if doc.citation == "600 F.3d 10")
    assert opinion.status == "Published"
    linked = await store.cites_from(opinion.version_id)
    resolved = [row for row in linked if "14706" in row[0]]
    assert resolved and resolved[0][2] is True
    unresolved = [row for row in linked if "99999" in row[0]]
    assert unresolved and unresolved[0][2] is False

    chancery = next(doc for doc in store.docs.values() if doc.citation == "12 A.3d 100")
    assert chancery.level == "state"
    assert chancery.kind == "case"

    meaning = await search_meaning(
        store, embedder, index, "bill of lading", jurisdiction="us"
    )
    assert meaning["collection"] == collection_name("us")
    assert meaning["reason"] == "ok"
    assert meaning["chunks"]
    assert "lading" in meaning["chunks"][0].text.lower()

    before = await store.version_count()
    again = await load_wave(
        store,
        region="us-east",
        snapshots=_scale_snapshots(),
        on=ON,
        embedder=embedder,
        index=index,
    )
    assert await store.version_count() == before
    again_ids = {row["source_id"]: row for row in again["sources"]}
    assert again_ids["courtlistener"]["documents"] == 2
    assert "inserted 0" in again_ids["frap"]["detail"]


def test_disk_cap_stops_before_a_document_is_stored():
    asyncio.run(_cap())


async def _cap():
    store = MemoryStore()
    blob = _uslm([("9", "2", "Validity", "A written arbitration agreement is valid and enforceable.")])
    report = await load_wave(
        store,
        region="us-east",
        snapshots={"govinfo-uscode": [blob]},
        on=ON,
        only=[_source("govinfo-uscode")],
        cap=20,
    )
    assert await store.version_count() == 0
    row = report["sources"][0]
    assert row["last_status"] == "disk_cap"
    assert row["documents"] == 0


def test_a_second_run_resumes_after_a_pause():
    asyncio.run(_resume())


async def _resume():
    class Pause(MemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.left = 1

        async def upsert_version(self, doc, chunks, *, on):
            if self.left <= 0:
                raise RuntimeError("paused")
            self.left -= 1
            return await super().upsert_version(doc, chunks, on=on)

    first = _uslm([("9", "2", "Validity", "A written arbitration agreement is valid.")])
    second = _uslm([("35", "101", "Inventions", "A patentable invention is a new and useful process.")])
    store = Pause()
    snaps = {"govinfo-uscode": [first, second]}
    await load_wave(
        store,
        region="us-east",
        snapshots=snaps,
        on=ON,
        only=[_source("govinfo-uscode")],
    )
    assert await store.version_count() == 1
    assert await store.get_checkpoint("govinfo-uscode") == "snap:1"
    store.left = 5
    await load_wave(
        store,
        region="us-east",
        snapshots=snaps,
        on=ON,
        only=[_source("govinfo-uscode")],
    )
    assert await store.version_count() == 2
    assert await store.find_by_citation("9 U.S.C. § 2", jurisdiction="us", as_at=None)
    assert await store.find_by_citation("35 U.S.C. § 101", jurisdiction="us", as_at=None)


def test_pdf_members_of_a_zip_are_not_stored():
    asyncio.run(_zip())


async def _zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("scan.pdf", b"%PDF-1.4 fixture image bytes")
        archive.writestr(
            "title9.xml",
            _uslm([("9", "2", "Validity", "A written arbitration agreement is valid.")]),
        )
    store = MemoryStore()
    await load_wave(
        store,
        region="us-east",
        snapshots={"govinfo-uscode": [buffer.getvalue()]},
        on=ON,
        only=[_source("govinfo-uscode")],
    )
    text = await store.all_text()
    assert "arbitration agreement" in text
    assert b"%PDF" not in text.encode()
    assert "fixture image" not in text


def test_courtlistener_token_stays_out_of_the_url(monkeypatch: pytest.MonkeyPatch):
    asyncio.run(_token(monkeypatch))


async def _token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COURTLISTENER_API_TOKEN", raising=False)
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"{}"

    source = _source("courtlistener")
    quiet = await load_wave(
        MemoryStore(),
        region="us-east",
        live=True,
        snapshots=None,
        on=ON,
        only=[source],
        http_get=get,
    )
    assert seen == []
    row = quiet["sources"][0]
    assert row["last_status"] == "not_configured"
    assert row["key_name"] == "COURTLISTENER_API_TOKEN"
    assert row["key_set"] is False
    assert "COURTLISTENER_API_TOKEN is not set" in row["detail"]

    marker = "marker-" + ("a" * 16)
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", marker)
    monkeypatch.setenv("COURTLISTENER_MIN_INTERVAL", "0")
    monkeypatch.setenv("COURTLISTENER_MAX_PAGES", "1")
    calls: list[tuple[str, dict]] = []

    async def paged(url, headers):
        calls.append((url, dict(headers)))
        if "page=2" in url:
            return b'{"results":[],"next":null}'
        nxt = "https://www.courtlistener.com/api/rest/v4/search/?type=o&page=2"
        return json.dumps({"results": [], "next": nxt}).encode()

    store = MemoryStore()
    await load_wave(
        store,
        region="us-east",
        live=True,
        snapshots=None,
        on=ON,
        only=[source],
        http_get=paged,
    )
    assert calls
    assert calls[0][1]["Authorization"] == f"Token {marker}"
    assert marker not in calls[0][0]
    assert "token=" not in calls[0][0].lower()
    assert await store.get_checkpoint("courtlistener") == (
        "https://www.courtlistener.com/api/rest/v4/search/?type=o&page=2"
    )
    dumped = json.dumps(await store.status_rows())
    assert marker not in dumped

    await load_wave(
        store,
        region="us-east",
        live=True,
        snapshots=None,
        on=ON,
        only=[source],
        http_get=paged,
    )
    assert "page=2" in calls[-1][0]
    assert marker not in calls[-1][0]


def test_govinfo_key_is_a_header_and_public_download_needs_none(monkeypatch: pytest.MonkeyPatch):
    asyncio.run(_govinfo(monkeypatch))


async def _govinfo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
    urls: list[str] = []

    async def public(url, headers):
        urls.append(url)
        return b"<uscDoc></uscDoc>"

    await load_wave(
        MemoryStore(),
        region="us-east",
        live=True,
        snapshots=None,
        on=ON,
        only=[_source("govinfo-uscode")],
        http_get=public,
    )
    assert urls
    assert all(url.startswith("https://uscode.house.gov/") for url in urls)
    assert uscode_bulk_url(9) in urls

    marker = "marker-" + ("b" * 16)
    monkeypatch.setenv("GOVINFO_API_KEY", marker)
    keyed: list[tuple[str, dict]] = []

    async def api(url, headers):
        keyed.append((url, dict(headers)))
        return b"<uscDoc></uscDoc>"

    report = await load_wave(
        MemoryStore(),
        region="us-east",
        live=True,
        snapshots=None,
        on=ON,
        only=[_source("govinfo-uscode")],
        http_get=api,
    )
    assert keyed
    assert all(url == govinfo_uscode_url(title) for (url, _), title in zip(keyed, (9, 11, 15, 17, 19, 35, 46, 49, 50)))
    assert all(headers.get("X-Api-Key") == marker for _, headers in keyed)
    blob = json.dumps(report)
    assert marker not in blob
    assert all(marker not in url for url, _ in keyed)


def test_criminal_marker_on_the_adapter_still_drops_the_row():
    source = _source("courtlistener")
    adapter = CourtListenerAdapter()
    documents = adapter.parse(_opinions(), source)
    assert {doc.citation for doc in documents} == {"600 F.3d 10", "12 A.3d 100"}
    assert any(item.startswith("criminal:") for item in adapter.skipped)
    assert any(item.startswith("not-appellate:") for item in adapter.skipped)


def test_uscode_adapter_parses_uslm_without_touching_html():
    source = _source("govinfo-uscode")
    documents = UsCodeAdapter().parse(
        _uslm([("46", "30701", "Bills of lading", "Carriage of Goods by Sea.")]),
        source,
    )
    assert documents[0].citation == "46 U.S.C. § 30701"
    assert documents[0].kind == "statute"
    html = (Path(__file__).resolve().parents[1] / "agents/zenas/pool/fixtures/us-east/govinfo-uscode/49-usc-14706.html").read_bytes()
    parsed = UsCodeAdapter().parse(html, source)
    assert parsed[0].citation == "49 U.S.C. § 14706"
