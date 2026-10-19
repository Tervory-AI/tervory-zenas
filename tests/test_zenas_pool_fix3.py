"""FIX-3: the US loaders after the 19 Oct 2026 live feeds report.

Saved real responses only. No live network, no key in any file.

- courtlistener: v4 search rows carry snippets; opinion text comes from the
  opinions endpoint. A 429 keeps what was read, saves the cursor and records
  rate_limited. Skipped rows are counted by reason. Docket number and nature
  of suit are stored for convert's criminal check.
- govinfo-uscode: a document with no text is skipped and counted.
- ecfr: each title is asked for at its own latest issue; a 404 part is skipped.
- uncitral: a landing page is followed to the ebook; a page that is not the
  ebook is reported, not recorded as "ok, inserted 0".
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import urllib.error
from datetime import date
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agents.zenas.pool.adapters.cases as cases  # noqa: E402
import agents.zenas.pool.http as pool_http  # noqa: E402
from agents.zenas.core.convert import convert_cases, criminal_case, criminal_record  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.pool.adapters.cases import CourtListenerAdapter, skip_reason  # noqa: E402
from agents.zenas.pool.adapters.ecfr import EcfrAdapter, date_for_title, title_dates  # noqa: E402
from agents.zenas.pool.adapters.uncitral import CisgAdapter, ModelLawAdapter  # noqa: E402
from agents.zenas.pool.bulk import load_detail, load_wave  # noqa: E402
from agents.zenas.pool.catalogue import CATALOGUE  # noqa: E402
from agents.zenas.pool.citations import extract_citations  # noqa: E402
from agents.zenas.pool.http import RateLimited, retry_after_seconds, urllib_get  # noqa: E402
from agents.zenas.pool.store import MemoryStore, PostgresStore  # noqa: E402

ON = date(2026, 10, 19)
FIX = ROOT / "tests" / "fixtures"
SAVED = ROOT / "agents" / "zenas" / "pool" / "fixtures" / "saved"
CL = FIX / "courtlistener"
MARKER = "marker-" + ("c" * 16)


def _source(source_id: str):
    return next(item for item in CATALOGUE if item.source_id == source_id)


def _http_error(url: str, code: int, body: bytes = b"", headers: dict | None = None) -> urllib.error.HTTPError:
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(url, code, "error", message, io.BytesIO(body))


# --- CourtListener -----------------------------------------------------------


def _page() -> dict:
    return json.loads((CL / "search-v4-page.json").read_text())


def _last_page() -> dict:
    return json.loads((CL / "search-v4-last-page.json").read_text())


def _snippets() -> dict[int, str]:
    found: dict[int, str] = {}
    for payload in (_page(), _last_page()):
        for row in payload["results"]:
            for item in row["opinions"]:
                found[int(item["id"])] = item["snippet"]
    return found


def _opinion(opinion_id: int) -> bytes:
    """A v4 opinions response, in the documented shape.

    The endpoint answers 401 without a token (saved: opinion-v4-401.json), so
    the text here is that opinion's own snippet from the saved search page,
    followed by a commercial paragraph the parser can find.
    """
    text = _snippets()[opinion_id].strip() + "\n\nThe insurance contract is construed as written."
    return json.dumps({"id": opinion_id, "plain_text": text, "html_with_citations": ""}).encode()


def test_v4_search_rows_carry_snippets_not_opinion_text():
    for row in _page()["results"]:
        assert "plain_text" not in row
        assert all(set(item) >= {"id", "snippet"} for item in row["opinions"])


def test_saved_rows_fall_into_the_four_skip_reasons():
    reasons = {row["caseName"]: skip_reason(row) for row in _page()["results"]}
    assert reasons["Brettler v. Allianz Life Insurance Company of North America"] == ""
    assert reasons["Loomis v. ACE American Insurance Company"] == ""
    assert reasons["United States v. Kaid (Ahmed)"] == "criminal"  # docket "05-4470 -cr (L)"
    assert reasons["United States v. Diana Yates"] == "criminal"  # suitNature "Criminal"
    assert reasons["McGlone v. Contract Callers Inc."] == "not-appellate"  # nysd
    assert reasons["Gabbi Lemos v. County of Sonoma"] == "not-commercial"


def _fake_courtlistener(calls: list[tuple[str, dict]], *, limit_on: str = ""):
    first = json.dumps(_page()).encode()
    last = json.dumps(_last_page()).encode()
    next_url = _page()["next"]

    async def get(url: str, headers: dict) -> bytes:
        calls.append((url, dict(headers)))
        if limit_on and limit_on in url:
            raise RateLimited("www.courtlistener.com", 5)
        if "/opinions/" in url:
            opinion_id = int(url.split("/opinions/", 1)[1].split("/", 1)[0])
            return _opinion(opinion_id)
        if url == next_url:
            return last
        return first

    return get


def test_opinion_text_is_fetched_with_the_header_and_counted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", MARKER)
    monkeypatch.setenv("COURTLISTENER_MIN_INTERVAL", "0")
    monkeypatch.setenv("COURTLISTENER_MAX_PAGES", "5")
    calls: list[tuple[str, dict]] = []
    store = MemoryStore()
    report = asyncio.run(
        load_wave(
            store,
            region="us-east",
            live=True,
            on=ON,
            only=[_source("courtlistener")],
            http_get=_fake_courtlistener(calls),
        )
    )
    row = report["sources"][0]
    assert row["last_status"] == "ok"
    assert row["detail"] == "inserted 2; skipped criminal 2, not-appellate 1, not-commercial 2"
    opinion_calls = [item for item in calls if "/opinions/" in item[0]]
    # Only the two rows that passed every filter had their text fetched.
    assert sorted(int(url.split("/opinions/")[1].split("/")[0]) for url, _ in opinion_calls) == [9351120, 9925898]
    assert all(headers["Authorization"] == f"Token {MARKER}" for _, headers in calls)
    assert all(MARKER not in url and "token=" not in url.lower() for url, _ in calls)
    docs = {doc.citation: doc for doc in store.docs.values()}
    assert set(docs) == {"57 F.4th 57", "91 F.4th 565"}
    assert docs["57 F.4th 57"].docket_number == "19-87"
    assert docs["91 F.4th 565"].docket_number == "22-863"
    assert "Brettler" in docs["57 F.4th 57"].text
    assert "insurance contract" in docs["57 F.4th 57"].text
    dumped = json.dumps(report, default=str)
    assert "Kaid" not in json.dumps([doc.text for doc in store.docs.values()])
    assert MARKER not in dumped
    # The walk finished, so the next Load starts from the first page.
    assert asyncio.run(store.get_checkpoint("courtlistener")) == ""


def test_twelve_seconds_between_api_calls_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", MARKER)
    monkeypatch.delenv("COURTLISTENER_MIN_INTERVAL", raising=False)
    monkeypatch.setenv("COURTLISTENER_MAX_PAGES", "1")
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(cases.asyncio, "sleep", sleep)
    calls: list[tuple[str, dict]] = []
    asyncio.run(CourtListenerAdapter().fetch_bulk(_fake_courtlistener(calls)))
    assert len(calls) == 3  # one search page, two opinions
    assert sleeps == [12.0, 12.0, 12.0]


def test_429_keeps_pages_saves_the_cursor_and_records_rate_limited(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", MARKER)
    monkeypatch.setenv("COURTLISTENER_MIN_INTERVAL", "0")
    monkeypatch.setenv("COURTLISTENER_MAX_PAGES", "5")
    next_url = _page()["next"]
    calls: list[tuple[str, dict]] = []
    store = MemoryStore()
    source = _source("courtlistener")
    report = asyncio.run(
        load_wave(
            store,
            region="us-east",
            live=True,
            on=ON,
            only=[source],
            http_get=_fake_courtlistener(calls, limit_on="cursor="),
        )
    )
    row = report["sources"][0]
    assert row["last_status"] == "rate_limited"
    assert row["last_status"] != "not_configured"
    assert "HTTP 429 from www.courtlistener.com after 5 retries" in row["detail"]
    assert "the next Load resumes" in row["detail"]
    assert "inserted 2" in row["detail"]
    assert "skipped criminal 2, not-appellate 1, not-commercial 1" in row["detail"]
    assert "cursor=" not in row["detail"] and "http" not in row["detail"]
    assert row["documents"] == 2
    assert asyncio.run(store.get_checkpoint("courtlistener")) == next_url

    resumed: list[tuple[str, dict]] = []
    again = asyncio.run(
        load_wave(
            store,
            region="us-east",
            live=True,
            on=ON,
            only=[source],
            http_get=_fake_courtlistener(resumed),
        )
    )
    assert resumed[0][0] == next_url
    assert again["sources"][0]["last_status"] == "ok"


def test_429_on_the_first_page_is_rate_limited_not_not_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", MARKER)
    monkeypatch.setenv("COURTLISTENER_MIN_INTERVAL", "0")

    async def get(url: str, headers: dict) -> bytes:
        raise RateLimited("www.courtlistener.com", 5)

    report = asyncio.run(
        load_wave(MemoryStore(), region="us-east", live=True, on=ON, only=[_source("courtlistener")], http_get=get)
    )
    row = report["sources"][0]
    assert row["last_status"] == "rate_limited"
    assert row["documents"] == 0


def test_opinions_401_is_reported_without_the_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", MARKER)
    monkeypatch.setenv("COURTLISTENER_MIN_INTERVAL", "0")
    body = (CL / "opinion-v4-401.json").read_bytes()
    first = json.dumps(_page()).encode()

    async def get(url: str, headers: dict) -> bytes:
        if "/opinions/" in url:
            raise _http_error(url, 401, body)
        return first

    report = asyncio.run(
        load_wave(MemoryStore(), region="us-east", live=True, on=ON, only=[_source("courtlistener")], http_get=get)
    )
    row = report["sources"][0]
    assert row["last_status"] == "not_configured"
    assert row["detail"] == "HTTP 401 from www.courtlistener.com"


def test_docket_number_and_nature_of_suit_are_stored_from_v4_fields():
    row = next(item for item in _page()["results"] if item["caseName"].startswith("Gabbi Lemos"))
    # Marked commercial here only so the real row is kept; the fields are as saved.
    payload = {"results": [{**row, "commercial": True, "plain_text": row["opinions"][0]["snippet"]}]}
    adapter = CourtListenerAdapter()
    documents = adapter.parse(json.dumps(payload).encode(), _source("courtlistener"))
    assert len(documents) == 1
    assert documents[0].docket_number == "19-15222"
    assert documents[0].nature_of_suit == "Civil"
    assert documents[0].citation == "40 F.4th 1002"


def test_postgres_insert_writes_docket_and_nature(monkeypatch: pytest.MonkeyPatch):
    row = next(item for item in _page()["results"] if item["caseName"].startswith("Brettler"))
    payload = {"results": [{**row, "plain_text": _snippets()[9351120]}]}
    document = CourtListenerAdapter().parse(json.dumps(payload).encode(), _source("courtlistener"))[0]
    executed: list[tuple[str, tuple]] = []

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class _Conn:
        def transaction(self):
            return _Tx()

        async def fetch(self, *_args):
            return []

        async def execute(self, sql, *args):
            executed.append((sql, args))

        async def executemany(self, sql, rows):
            executed.append((sql, tuple(rows)))

    store = PostgresStore(_Conn(), home_region="us-east")

    async def get_source(_source_id):
        return _source("courtlistener")

    monkeypatch.setattr(store, "get_source", get_source)
    assert asyncio.run(store.upsert_version(document, [], on=ON)) is True
    sql, args = next(item for item in executed if "INSERT INTO zenas_pool.documents" in item[0])
    assert "docket_number, nature_of_suit" in sql
    assert args[-2:] == ("19-87", "")


def test_schema_has_the_case_record_columns():
    schema = (ROOT / "agents" / "zenas" / "pool" / "schema.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS docket_number text NOT NULL DEFAULT ''" in schema
    assert "ADD COLUMN IF NOT EXISTS nature_of_suit text NOT NULL DEFAULT ''" in schema


class _Doc:
    def __init__(self, doc_id: str, docket: str, nature: str) -> None:
        self.id = doc_id
        self.jurisdiction = "us"
        self.citation = "502 F.3d 43"
        self.kind = "case"
        self.status = "Published"
        self.effective_to = None
        self.source_id = "courtlistener"
        self.version_id = f"v-{doc_id}"
        self.title = doc_id
        self.docket_number = docket
        self.nature_of_suit = nature
        self.text_reads = 0

    @property
    def text(self) -> str:
        self.text_reads += 1
        return "A commercial opinion with no criminal words in it."


class _Pool:
    def __init__(self, docs: list[_Doc]) -> None:
        self.docs = {doc.id: doc for doc in docs}
        self.home_region = "us-east"


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, *, task_type, provider):
        self.calls += 1
        return "{}"


def test_convert_refuses_a_criminal_docket_or_nature_without_reading_text():
    by_docket = _Doc("cl-kaid", "05-4470 -cr (L)", "")
    by_nature = _Doc("cl-yates", "18-30183", "Criminal")
    ladder = LadderMemory()
    model = _Model()
    asyncio.run(convert_cases(_Pool([by_docket, by_nature]), ladder, model, region="us-east"))
    reasons = {row["pool_doc_id"]: row["reason"] for row in ladder.failures}
    assert reasons == {"cl-kaid": "criminal law is refused", "cl-yates": "criminal law is refused"}
    assert by_docket.text_reads == 0 and by_nature.text_reads == 0
    assert model.calls == 0


def test_criminal_record_reads_saved_docket_shapes():
    assert criminal_record("05-4470 -cr (L)", "")
    assert criminal_record("1:20-cr-00123", "")
    assert criminal_record("18-30183", "Criminal")
    assert not criminal_record("19-87", "")
    assert not criminal_record("21-1830-cv", "")
    assert not criminal_record("11 Civ. 3004 (JSR)", "")
    assert not criminal_record("19-15222", "Civil")
    assert criminal_record("Crim. No. 4", "")
    assert criminal_record("No. 19-CR-55", "")
    assert not criminal_record("", "")


def test_criminal_case_is_the_one_check_for_both_regions():
    # US: only the stored docket_number and nature_of_suit decide; the caption is not read.
    assert criminal_case({"jurisdiction": "us", "docket_number": "1:21-cr-00010", "title": "Acme v. Beta"})
    assert criminal_case({"jurisdiction": "us", "nature_of_suit": "Criminal", "title": "Acme v. Beta"})
    assert not criminal_case({"jurisdiction": "us", "title": "S v Dlamini"})
    assert not criminal_case({"jurisdiction": "us", "docket_number": "19-87", "nature_of_suit": "Contract"})
    # SA: only the stored title decides.
    assert criminal_case({"jurisdiction": "za", "title": "S v Dlamini"})
    assert criminal_case({"jurisdiction": "za", "title": "National Director of Public Prosecutions v Zuma"})
    assert criminal_case({"jurisdiction": "za", "title": "NPA v Mokoena"})
    assert not criminal_case({"jurisdiction": "za", "title": "Acme (Pty) Ltd v Beta CC", "docket_number": "1:21-cr-1"})
    # No type information at all: converts.
    assert not criminal_case({"jurisdiction": "us"})
    assert not criminal_case({"jurisdiction": "za"})


def test_convert_has_one_criminal_check_and_never_reads_judgment_text_for_it():
    source = (ROOT / "agents" / "zenas" / "core" / "convert.py").read_text(encoding="utf-8")
    assert source.count("if criminal_case(case):") == 1
    assert "ruled(" not in source
    assert "scope_rules" not in source


# --- http: 429 and Retry-After ----------------------------------------------


def test_429_waits_for_retry_after_then_raises_rate_limited(monkeypatch: pytest.MonkeyPatch):
    import urllib.request

    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    def urlopen(request, timeout=60):
        raise _http_error(request.full_url, 429, b"{}", {"Retry-After": "7"})

    monkeypatch.setattr(pool_http.asyncio, "sleep", sleep)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    url = "https://www.courtlistener.com/api/rest/v4/search/?type=o&cursor=abc"
    with pytest.raises(RateLimited) as caught:
        asyncio.run(urllib_get(url, {"Accept": "application/json"}))
    assert waits == [7.0] * 5
    assert str(caught.value) == "HTTP 429 from www.courtlistener.com after 5 retries"
    assert "cursor" not in str(caught.value)


def test_429_without_retry_after_waits_sixty_seconds_and_recovers(monkeypatch: pytest.MonkeyPatch):
    import urllib.request

    waits: list[float] = []
    calls = {"n": 0}

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    class _Response:
        headers = {"Content-Encoding": ""}

        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(request, timeout=60):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(request.full_url, 429)
        return _Response()

    monkeypatch.setattr(pool_http.asyncio, "sleep", sleep)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert asyncio.run(urllib_get("https://www.courtlistener.com/x", {})) == b"{}"
    assert waits == [60.0, 60.0]


def test_retry_after_forms():
    assert retry_after_seconds("12") == 12.0
    assert retry_after_seconds("") == 60.0
    assert retry_after_seconds("99999") == 300.0
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480.0 - 30) == 30.0


# --- GovInfo: a document with no text ---------------------------------------


def test_extract_citations_on_empty_text_does_not_raise():
    # eyecite raises "Both markup_text and plain_text are empty" on empty input.
    assert extract_citations("") == []
    assert extract_citations("   ") == []


def test_govinfo_skips_a_document_with_no_text_and_keeps_loading(monkeypatch: pytest.MonkeyPatch):
    from agents.zenas.pool.adapters import govinfo

    monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
    monkeypatch.setattr(govinfo, "USCODE_TITLES", (9, 11))
    good = b"""<uscDoc><title><num value="9">Title 9</num>
        <section><num value="2">2.</num><heading>Validity</heading>
        <content><p>A written arbitration agreement is valid.</p></content></section>
        </title></uscDoc>"""
    empty_then_good = b"""<uscDoc><title><num value="11">Title 11</num>
        <section><num value="100">100.</num><heading>Repealed</heading>
        <subsection value="a"><num>(a)</num></subsection></section>
        <section><num value="101">101.</num><heading>Definitions</heading>
        <content><p>Bankruptcy definitions for a commercial debtor.</p></content></section>
        </title></uscDoc>"""

    async def get(url: str, headers: dict) -> bytes:
        return good if "usc09" in url else empty_then_good

    store = MemoryStore()
    report = asyncio.run(
        load_wave(store, region="us-east", live=True, on=ON, only=[_source("govinfo-uscode")], http_get=get)
    )
    row = report["sources"][0]
    assert row["last_status"] == "ok"
    assert row["detail"] == "inserted 2; skipped no-text 1"
    assert {doc.citation for doc in store.docs.values()} == {"9 U.S.C. § 2", "11 U.S.C. § 101"}


def test_load_detail_orders_reasons():
    assert load_detail(3, {"no-text": 1, "criminal": 2, "unchanged": 4}) == (
        "inserted 3; unchanged 4; skipped criminal 2, no-text 1"
    )
    assert load_detail(0, {}) == "inserted 0"


# --- eCFR ------------------------------------------------------------------


def test_ecfr_dates_come_from_the_saved_titles_response(monkeypatch: pytest.MonkeyPatch):
    dates = title_dates(json.loads((FIX / "ecfr" / "titles.json").read_text()))
    assert dates["15"] == ("2026-09-24", "2026-10-15")
    assert date_for_title("15", "current", dates) == "2026-09-24"
    # A pinned date after the title is up to date 404s live; it is clamped.
    assert date_for_title("15", "2026-10-19", dates) == "2026-09-24"
    assert date_for_title("15", "2024-07-01", dates) == "2024-07-01"
    assert date_for_title("15", "current", {}) == "2024-07-01"


def test_ecfr_bulk_skips_a_404_part_and_parses_the_saved_current_xml(monkeypatch: pytest.MonkeyPatch):
    import agents.zenas.pool.adapters.ecfr as ecfr

    monkeypatch.delenv("ECFR_DATE", raising=False)
    monkeypatch.setattr(ecfr, "ECFR_TARGETS", (("15", range(730, 775)),))
    titles = (FIX / "ecfr" / "titles.json").read_bytes()
    structure = (FIX / "ecfr" / "title-15-structure.json").read_bytes()
    part_730 = (FIX / "ecfr" / "title-15-part-730-2026-09-24.xml").read_bytes()
    seen: list[str] = []

    async def get(url: str, headers: dict) -> bytes:
        seen.append(url)
        if url == ecfr.ECFR_TITLES_URL:
            return titles
        if "/structure/" in url:
            return structure
        if "part=732" in url:
            raise _http_error(url, 404, b'{"error":"not found"}')
        return part_730

    store = MemoryStore()
    report = asyncio.run(load_wave(store, region="us-east", live=True, on=ON, only=[_source("ecfr")], http_get=get))
    row = report["sources"][0]
    assert row["last_status"] == "ok"
    assert row["detail"].startswith("inserted ")
    assert row["detail"].endswith("; skipped not-found 1")
    assert all("/2026-09-24/" in url for url in seen if url != ecfr.ECFR_TITLES_URL)
    citations = {doc.citation for doc in store.docs.values()}
    assert "15 C.F.R. § 730.1" in citations
    assert len(EcfrAdapter().parse(part_730, _source("ecfr"))) >= 8


# --- UNCITRAL --------------------------------------------------------------


def _pdf(adapter) -> bytes:
    name = {"uncitral-cisg": "19-09951.pdf", "uncitral-model-law": "19-09955.pdf"}[adapter.source_id]
    return (SAVED / adapter.source_id / name).read_bytes()


@pytest.mark.parametrize("adapter,expected", [(CisgAdapter(), 101), (ModelLawAdapter(), 36)])
def test_uncitral_live_load_inserts_articles_from_the_saved_ebook(adapter, expected):
    pdf = _pdf(adapter)

    async def get(url: str, headers: dict) -> bytes:
        assert url == adapter.url
        return pdf

    store = MemoryStore()
    source = _source(adapter.source_id)
    first = asyncio.run(load_wave(store, region="us-east", live=True, on=ON, only=[source], http_get=get))
    row = first["sources"][0]
    assert row["last_status"] == "ok"
    assert row["detail"] == f"inserted {expected}"
    again = asyncio.run(load_wave(store, region="us-east", live=True, on=ON, only=[source], http_get=get))
    # A second Load says why nothing was inserted.
    assert again["sources"][0]["detail"] == f"inserted 0; unchanged {expected}"


def test_uncitral_follows_the_ebook_link_on_the_saved_landing_page():
    adapter = CisgAdapter()
    landing = (FIX / "uncitral" / "cisg-landing.html").read_bytes()
    pdf = _pdf(adapter)
    seen: list[str] = []

    async def get(url: str, headers: dict) -> bytes:
        seen.append(url)
        return landing if len(seen) == 1 else pdf

    blobs, _ = asyncio.run(adapter.fetch_bulk(get))
    assert seen[1] == adapter.url  # the CISG ebook, not the digest or other PDFs
    assert len(adapter.parse(blobs[0], _source(adapter.source_id))) == 101
    # The landing page alone is not article text.
    assert adapter.parse(landing, _source(adapter.source_id)) == []


def test_uncitral_page_that_is_not_the_ebook_is_reported_not_ok():
    blocked = (FIX / "uncitral" / "cloudfront-blocked.html").read_bytes()

    async def get(url: str, headers: dict) -> bytes:
        return blocked

    report = asyncio.run(
        load_wave(MemoryStore(), region="us-east", live=True, on=ON, only=[_source("uncitral-cisg")], http_get=get)
    )
    row = report["sources"][0]
    assert row["last_status"] == "not_configured"
    assert "uncitral.un.org returned an HTML page" in row["detail"]
    assert "not the ebook text" in row["detail"]
