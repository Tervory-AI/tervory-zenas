"""Z8: decided cases become worked matters, one region at a time.

Cite-check must pass. One stable fifth is held out. A US run does not read an
SA case, and the reverse.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core.convert import (  # noqa: E402
    _convert_running,
    begin_convert,
    convert_cases,
    convert_limit,
    convert_running,
    conversion_report,
)
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.selftest import is_holdout  # noqa: E402

_QUOTE = re.compile(r"The court stated fact number \d+ for this sale of goods\.")


class Doc:
    def __init__(
        self,
        *,
        id: str,
        jurisdiction: str,
        citation: str,
        text: str,
        kind: str = "case",
        status: str = "in_force",
        effective_to: object = None,
        source_id: str = "cap",
        title: str | None = None,
        docket: str = "",
        nature_of_suit: str = "",
    ) -> None:
        self.id = id
        self.jurisdiction = jurisdiction
        self.citation = citation
        self._text = text
        self.kind = kind
        self.status = status
        self.effective_to = effective_to
        self.source_id = source_id
        self.version_id = f"v-{id}"
        self.title = id if title is None else title
        self.docket_number = docket  # the stored pool column
        self.nature_of_suit = nature_of_suit
        self.text_reads = 0

    @property
    def text(self) -> str:
        self.text_reads += 1
        return self._text


class Pool:
    def __init__(self, docs: list[Doc], home_region: str | None = None) -> None:
        self.docs = {doc.id: doc for doc in docs}
        self.home_region = home_region


class Model:
    def __init__(self, mode: str = "good") -> None:
        self.mode = mode
        self.calls: list[dict[str, str]] = []
        self.prompts: list[str] = []

    async def complete(self, messages: list[dict[str, str]], *, task_type: str, provider: str) -> str:
        self.calls.append({"task_type": task_type, "provider": provider})
        text = messages[-1]["content"]
        self.prompts.append(text)
        citation = ""
        for line in text.splitlines():
            if line.startswith("Citation: "):
                citation = line[len("Citation: ") :]
                break
        if self.mode == "bad":
            quote = "This sentence is not in the judgment."
            # Keep this case's citation so the failure is the quote, not a foreign cite.
        else:
            found = _QUOTE.search(text)
            quote = found.group(0) if found else ""
        return json.dumps(
            {
                "facts": quote,
                "question": "Was the sale of goods complete?",
                "elements": [{"element_id": "sale", "finding": "met", "quote": quote}],
                "holding": "met",
                "citations": [{"citation": citation, "quote": quote}],
            }
        )


def _case(doc_id: str, jurisdiction: str, *, kind: str = "case") -> Doc:
    quote = f"The court stated fact number {doc_id[-4:]} for this sale of goods."
    prefix = "US" if jurisdiction == "us" else "SA"
    return Doc(
        id=doc_id,
        jurisdiction=jurisdiction,
        citation=f"{doc_id[-4:]} F.2d 1",
        text=f"Opinion. {quote} TAIL-{prefix}-NOT-STORED.",
        kind=kind,
        source_id="cap" if jurisdiction == "us" else "concourt",
    )


def _ids(n: int) -> list[str]:
    return [f"case-{i:04d}" for i in range(n)]


@pytest.mark.asyncio
async def test_fixture_cases_become_worked_matters_with_about_one_in_five_held_out() -> None:
    ids = _ids(100)
    docs = [_case(doc_id, "us") for doc_id in ids]
    pool = Pool(docs, home_region="us-east")
    ladder = LadderMemory()
    model = Model()
    report = await convert_cases(pool, ladder, model, region="us-east")
    assert report["ok"] is True
    assert report["decided"] == 100
    assert report["converted"] == 100
    assert report["failed"] == 0
    expected = sum(is_holdout(doc_id) for doc_id in ids)
    assert report["held_out"] == expected
    assert 0.15 <= expected / len(ids) <= 0.25
    assert all(call["task_type"] == "zenas.case_facts" for call in model.calls)
    assert len(model.calls) == 100
    for row in ladder.worked:
        assert row["holdout"] is is_holdout(row["pool_doc_id"])
        assert row["jurisdiction"] == "us"
        assert row["region"] == "us-east"
        assert row["material_type"] == "public_record"
        pattern = row["fact_pattern"]
        assert pattern["facts"].startswith("The court stated fact")
        assert pattern["question"]
        assert pattern["citation"].endswith("F.2d 1")
        assert "TAIL-US-NOT-STORED" not in json.dumps(row)
        pairs = [pair for pair in ladder.pairs if pair.get("worked_id") == row["id"]]
        if row["holdout"]:
            assert pairs == []
        else:
            assert len(pairs) == 1
            assert pairs[0]["memo"] == pattern["facts"]
            assert "TAIL-US-NOT-STORED" not in pairs[0]["memo"]
    again = Model()
    second = await convert_cases(pool, ladder, again, region="us-east")
    assert second["converted"] == 100
    assert again.calls == []
    assert all(doc.text_reads == 1 for doc in docs)


@pytest.mark.asyncio
async def test_a_failed_cite_check_is_not_stored_and_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENAS_CONVERT_MAX", "200")
    doc = _case("case-0007", "us")
    pool = Pool([doc], home_region="us-east")
    ladder = LadderMemory()
    bad = Model("bad")
    report = await convert_cases(pool, ladder, bad, region="us-east")
    assert report["converted"] == 0
    assert report["failed"] == 1
    assert ladder.worked == []
    assert ladder.failures[0]["reason"] == "quote is not in the judgment"
    assert "TAIL-US-NOT-STORED" not in ladder.failures[0]["reason"]
    assert report["failures"] == [{"reason": "quote is not in the judgment", "count": 1}]
    good = Model()
    retried = await convert_cases(pool, ladder, good, region="us-east")
    assert retried["converted"] == 1
    assert retried["failed"] == 0
    assert len(good.calls) == 1
    assert ladder.worked[0]["pool_doc_id"] == "case-0007"


@pytest.mark.asyncio
async def test_us_conversion_never_reads_an_sa_case_and_the_reverse() -> None:
    us_docs = [_case(f"us-{i:04d}", "us") for i in range(3)]
    sa_docs = [_case(f"za-{i:04d}", "za") for i in range(3)]
    statute = _case("statute-0001", "us", kind="statute")
    us_pool = Pool(us_docs + sa_docs + [statute], home_region="us-east")
    us_ladder = LadderMemory()
    us_model = Model()
    us = await convert_cases(us_pool, us_ladder, us_model, region="us-east")
    assert us["decided"] == 3
    assert us["converted"] == 3
    assert {row["jurisdiction"] for row in us_ladder.worked} == {"us"}
    assert all(doc.text_reads == 0 for doc in sa_docs)
    assert statute.text_reads == 0
    assert us_model.prompts
    assert all("TAIL-SA-NOT-STORED" not in prompt for prompt in us_model.prompts)
    assert all(doc.text_reads == 1 for doc in us_docs)

    fresh_us = [_case(f"us-{i:04d}", "us") for i in range(1)]
    fresh_sa = [_case(f"za-{i:04d}", "za") for i in range(3)]
    za_pool = Pool(fresh_us + fresh_sa, home_region="za")
    za_ladder = LadderMemory()
    za_model = Model()
    za = await convert_cases(za_pool, za_ladder, za_model, region="za")
    assert za["decided"] == 3
    assert za["converted"] == 3
    assert {row["jurisdiction"] for row in za_ladder.worked} == {"za"}
    assert fresh_us[0].text_reads == 0
    assert all(doc.text_reads == 1 for doc in fresh_sa)
    assert all("TAIL-US-NOT-STORED" not in prompt for prompt in za_model.prompts)
    assert all("TAIL-US-NOT-STORED" not in json.dumps(row) for row in za_ladder.worked)

    crossed = Pool(us_docs + sa_docs, home_region="us-east")
    for doc in us_docs + sa_docs:
        doc.text_reads = 0
    refused = await convert_cases(crossed, LadderMemory(), Model(), region="za")
    assert refused["ok"] is False
    assert refused["reason"] == "wrong region"
    assert all(doc.text_reads == 0 for doc in us_docs + sa_docs)


@pytest.mark.asyncio
async def test_sql_list_asks_only_for_this_regions_jurisdiction() -> None:
    class Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, sql: str, *args: object) -> list[dict[str, str]]:
            self.calls.append((sql, args))
            selected = sql.lower().split("from", 1)[0]
            names = re.findall(r"[a-z_]+", selected)
            assert "text" not in names
            assert "title" in names
            assert "kind = 'case'" in sql
            return []

        async def fetchrow(self, sql: str, *args: object) -> None:
            raise AssertionError(sql)

    class SqlPool:
        def __init__(self) -> None:
            self._conn = Conn()
            self.home_region = None

    us = SqlPool()
    await conversion_report(us, LadderMemory(), region="us-east")
    assert us._conn.calls[0][1] == ("us",)
    za = SqlPool()
    await conversion_report(za, LadderMemory(), region="za")
    assert za._conn.calls[0][1] == ("za",)


@pytest.mark.asyncio
async def test_a_cr_docket_is_refused_and_a_cv_docket_converts() -> None:
    criminal = Doc(
        id="crime-0001",
        jurisdiction="us",
        citation="9 F.2d 9",
        title="United States v. Acme Corp",
        docket="1:21-cr-00010",
        text="The accused faced a criminal charge. The court stated fact number 0001 for this sale of goods.",
        source_id="courtlistener",
    )
    civil = Doc(
        id="civil-us-0001",
        jurisdiction="us",
        citation="9 F.2d 8",
        title="United States v. Acme Corp",
        docket="1:21-cv-00010",
        text="The accused faced a criminal charge. The court stated fact number 0001 for this sale of goods.",
        source_id="courtlistener",
    )
    unknown = Doc(
        id="unknown-0001",
        jurisdiction="us",
        citation="9 F.2d 7",
        title="United States v. Acme Corp",
        text="The accused faced a criminal charge. The court stated fact number 0001 for this sale of goods.",
        source_id="courtlistener",
    )
    ladder = LadderMemory()
    report = await convert_cases(
        Pool([criminal, civil, unknown], home_region="us-east"), ladder, Model(), region="us-east"
    )
    assert report["converted"] == 2
    assert report["failed"] == 1
    assert criminal.text_reads == 0
    assert civil.text_reads == 1
    assert unknown.text_reads == 1
    assert ladder.failures[0]["reason"] == "criminal law is refused"
    assert {row["pool_doc_id"] for row in ladder.worked} == {"civil-us-0001", "unknown-0001"}


@pytest.mark.asyncio
async def test_a_criminal_nature_of_suit_is_refused_and_an_sa_caption_is_refused() -> None:
    nature = Doc(
        id="nature-0001",
        jurisdiction="us",
        citation="9 F.2d 6",
        title="United States v. Acme Corp",
        docket="1:21-cv-00099",
        nature_of_suit="Criminal",
        text="The court stated fact number 0001 for this sale of goods.",
        source_id="courtlistener",
    )
    caption = Doc(
        id="caption-0001",
        jurisdiction="za",
        citation="[2024] ZASCA 9",
        title="S v Dlamini",
        text="The court stated fact number 0001 for this sale of goods.",
        source_id="sca",
    )
    dpp = Doc(
        id="dpp-0001",
        jurisdiction="za",
        citation="[2024] ZASCA 10",
        title="Director of Public Prosecutions v Mahlangu",
        text="The court stated fact number 0001 for this sale of goods.",
        source_id="sca",
    )
    us_only = await convert_cases(Pool([nature], home_region="us-east"), LadderMemory(), Model(), region="us-east")
    assert us_only["failed"] == 1
    assert us_only["converted"] == 0
    assert nature.text_reads == 0
    assert us_only["failures"][0]["reason"] == "criminal law is refused"
    za = await convert_cases(Pool([caption, dpp], home_region="za"), LadderMemory(), Model(), region="za")
    assert za["converted"] == 0
    assert za["failed"] == 2
    assert caption.text_reads == 0
    assert dpp.text_reads == 0
    assert {row["reason"] for row in za["failures"]} == {"criminal law is refused"}


def test_convert_limit_defaults_to_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZENAS_CONVERT_MAX", raising=False)
    assert convert_limit() == 200
    monkeypatch.setenv("ZENAS_CONVERT_MAX", "2")
    assert convert_limit() == 2
    monkeypatch.setenv("ZENAS_CONVERT_MAX", "nope")
    assert convert_limit() == 200


@pytest.mark.asyncio
async def test_cap_stops_at_n_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENAS_CONVERT_MAX", "2")
    docs = [_case(doc_id, "za") for doc_id in _ids(5)]
    report = await convert_cases(Pool(docs, home_region="za"), LadderMemory(), Model(), region="za")
    assert report["decided"] == 5
    assert report["converted"] == 2
    assert sum(doc.text_reads for doc in docs) == 2
    assert docs[0].text_reads == 1 and docs[1].text_reads == 1
    assert all(doc.text_reads == 0 for doc in docs[2:])


def test_nightly_converts_after_the_pool_load_and_before_the_self_test() -> None:
    text = (ROOT / "agents" / "zenas" / "pool" / "nightly.py").read_text(encoding="utf-8")
    main = text.split("async def _main", 1)[1]
    assert main.index("await run_nightly") < main.index("await convert_scheduled") < main.index("await run_scheduled")
    job = (ROOT / "agents" / "zenas" / "core" / "convert.py").read_text(encoding="utf-8")
    assert 'task_type="zenas.case_facts"' in job
    assert "os.nice(19)" in job
    assert "ZENAS_CONVERT_MAX" in job


def test_feeds_page_has_a_convert_button_per_region() -> None:
    board = (ROOT / "workspace" / "app" / "zenas" / "feeds" / "board.tsx").read_text(encoding="utf-8")
    route = (ROOT / "workspace" / "app" / "api" / "zenas" / "feeds" / "region" / "convert" / "route.ts").read_text(
        encoding="utf-8"
    )
    public = (ROOT / "workspace" / "app" / "api" / "zenas" / "feeds" / "public.ts").read_text(encoding="utf-8")
    app = (ROOT / "agents" / "zenas" / "app.py").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    timeout = (ROOT / "tank" / "main.py").read_text(encoding="utf-8").split("def zenas_timeout", 1)[1].split("\ndef ", 1)[0]
    post = app.split("async def post_cases_convert", 1)[1].split("@app.", 1)[0]
    get = app.split("async def get_cases_convert", 1)[1].split("@app.", 1)[0]
    assert "Convert cases" in board
    assert "decided cases loaded" in board
    assert "held out" in board
    assert "failed" in board
    assert 'setMessage(body.status)' in board
    assert 'body.status === "running" || body.status === "already running"' in board
    assert 'last_status === "running"' in board
    assert "Load now" in board
    assert "setInterval" in board
    assert "3000" in board
    assert "body.running" in board
    assert "zenasIn" in route
    assert "publicConvert" in route
    assert "matter_id" not in route
    assert "tenant_id" not in route
    assert "JUDGMENT" not in route
    assert 'value === "running" || value === "Started"' in public
    assert 'value === "already running" || value === "Already running"' in public
    assert "await begin_convert" in post
    assert "return await convert_cases" not in post
    assert "return report" in post
    assert 'report["running"] = convert_running(box)' in get
    assert "root.running === true" in public
    assert "ZENAS_CONVERT_MAX=200" in example
    assert "cases/convert" not in timeout
    assert "return ZENAS_TIMEOUT" in timeout


@pytest.mark.asyncio
async def test_convert_starts_in_the_background_and_a_second_press_is_already_running() -> None:
    _convert_running.clear()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def runner() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    try:
        first = await begin_convert("us-east", runner)
        assert first == {"ok": True, "region": "us-east", "running": True, "status": "running"}
        assert started.is_set() is False
        assert convert_running("us-east") is True
        await asyncio.wait_for(started.wait(), timeout=1)
        second = await begin_convert("us-east", runner)
        assert second["status"] == "already running"
        assert second["running"] is True
        assert calls == 1
        other = await begin_convert("za", runner)
        assert other["status"] == "running"
        await asyncio.sleep(0)
        assert calls == 2
        release.set()
        for _ in range(50):
            if not convert_running("us-east") and not convert_running("za"):
                break
            await asyncio.sleep(0.01)
        assert convert_running("us-east") is False
        assert convert_running("za") is False
        again = await begin_convert("us-east", runner)
        assert again["status"] == "running"
    finally:
        release.set()
        for _ in range(50):
            if not _convert_running:
                break
            await asyncio.sleep(0.01)
        _convert_running.clear()


@pytest.mark.asyncio
async def test_a_case_outside_the_court_list_is_not_stored() -> None:
    doc = Doc(
        id="statute-0002",
        jurisdiction="us",
        citation="49 U.S.C. § 14706",
        text="The court stated fact number 0002 for this sale of goods.",
        source_id="govinfo-uscode",
    )
    ladder = LadderMemory()
    report = await convert_cases(Pool([doc], home_region="us-east"), ladder, Model(), region="us-east")
    assert report["converted"] == 0
    assert report["failed"] == 1
    assert doc.text_reads == 0
    assert ladder.worked == []
    assert ladder.failures[0]["reason"] == "material type is unknown"


@pytest.mark.asyncio
async def test_a_civil_judgment_that_says_prosecution_is_still_converted() -> None:
    doc = Doc(
        id="civil-0001",
        jurisdiction="za",
        citation="[2024] ZASCA 8",
        text="The prosecution of the appeal failed. The court stated fact number 0001 for this sale of goods.",
        source_id="sca",
    )
    ladder = LadderMemory()
    report = await convert_cases(Pool([doc], home_region="za"), ladder, Model(), region="za")
    assert report["converted"] == 1
    assert report["failed"] == 0
    assert report["failures"] == []
    assert ladder.worked[0]["material_type"] == "public_record"


@pytest.mark.asyncio
async def test_a_not_yet_area_is_still_converted() -> None:
    doc = Doc(
        id="family-0001",
        jurisdiction="za",
        citation="[2024] ZACC 1",
        text="This divorce judgment. The court stated fact number 0001 for this sale of goods.",
        source_id="concourt",
    )
    ladder = LadderMemory()
    report = await convert_cases(Pool([doc], home_region="za"), ladder, Model(), region="za")
    assert report["converted"] == 1
    assert report["failed"] == 0
    assert ladder.worked[0]["material_type"] == "public_record"


def test_facts_and_convert_share_one_court_list() -> None:
    from agents.zenas.pool.catalogue import CATALOGUE, _za_case_sources
    from agents.zenas.core.release_mask import COURT_SOURCES, material_type_for_pool

    expected = {row.source_id for row in _za_case_sources()}
    expected.update({"saflii", "courtlistener", "cap", "govza-gazette"})
    assert set(COURT_SOURCES) == expected
    assert expected <= {row.source_id for row in CATALOGUE}
    assert material_type_for_pool("concourt") == "public_record"
    assert material_type_for_pool("special-tribunal") == "public_record"
    assert material_type_for_pool("labour-appeal") == "public_record"
    assert material_type_for_pool("govinfo-uscode") is None
    assert material_type_for_pool("uniform-rules") is None
