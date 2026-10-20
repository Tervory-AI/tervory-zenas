"""Z8b: South African case conversion can be seen, and PDF quotes still match.

A saved public judgment (Government of the Republic of South Africa and Others
v Grootboom and Others [2000] ZACC 19, paragraphs 24 and 35) is the text. The
model's answer is what gets stored when conversion fails. Client text is not.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
os.environ.setdefault("LITELLM_MASTER_KEY", "test")
os.environ.setdefault("TANK_API_KEY", "test")

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tank"))

from agents.zenas.core.convert import (  # noqa: E402
    answer_cut_off,
    convert_cases,
    cutoff_label,
    fact_passages,
)
from agents.zenas.core.engine import fold_for_quote, quote_in_source  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402

import main  # noqa: E402

JUDGMENT = (ROOT / "tests" / "fixtures" / "za" / "grootboom-p24.txt").read_text(encoding="utf-8")
CITE = "[2000] ZACC 19"
PASSAGE_A = (
    "The right of access to adequate housing cannot be seen in isolation. "
    "There is a close relationship between it and the other socio-economic rights."
)
PASSAGE_B = (
    "This difference is significant. It recognises that housing entails more than bricks and mortar."
)
TAIL = "TAIL-ZA-NOT-IN-ANSWER"
CLIENT = "Our client asked us to hide this matter."


class Doc:
    def __init__(self, text: str) -> None:
        self.id = "grootboom"
        self.jurisdiction = "za"
        self.citation = CITE
        self._text = text
        self.kind = "case"
        self.status = "in_force"
        self.effective_to = None
        self.source_id = "concourt"
        self.version_id = "v-grootboom"
        self.title = "Government of the Republic of South Africa and Others v Grootboom and Others"
        self.docket_number = ""
        self.nature_of_suit = ""

    @property
    def text(self) -> str:
        return self._text


class Pool:
    def __init__(self, doc: Doc) -> None:
        self.docs = {doc.id: doc}
        self.home_region = "za"


class Answer:
    def __init__(self, content: str, *, finish_reason: str = "STOP", tokens_in: int = 1200, tokens_out: int = 400) -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.calls: list[str] = []

    async def complete(self, messages: list[dict[str, str]], *, task_type: str, provider: str) -> dict[str, object]:
        del messages, provider
        self.calls.append(task_type)
        return {
            "content": self.content,
            "finish_reason": self.finish_reason,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": 0.02,
        }


def _body(facts: str, *, element: str | None = None, citation_quote: str | None = None) -> str:
    import json

    element_quote = PASSAGE_B if element is None else element
    cited = PASSAGE_A if citation_quote is None else citation_quote
    return json.dumps(
        {
            "facts": facts,
            "question": "Has the state met its duty to take reasonable measures toward access to adequate housing?",
            "elements": [{"element_id": "housing", "finding": "met", "quote": element_quote}],
            "holding": "housing entails more than bricks and mortar",
            "citations": [{"citation": CITE, "quote": cited}],
        }
    )


def _gemini_facts() -> str:
    quoted = PASSAGE_A.replace("relationship", "rela-\ntionship")
    joined = PASSAGE_B.replace("recognises", "recog-\nnises")
    return f"\u201c{quoted}\u201d \u2026 {joined}"


def _case_text() -> str:
    return JUDGMENT + "\n" + TAIL + "\n" + CLIENT


def _dump(ladder: LadderMemory) -> str:
    import json

    return json.dumps(ladder.failures)


@pytest.mark.asyncio
async def test_a_gemini_answer_with_curly_quotes_and_line_break_hyphens_passes() -> None:
    assert "\u00ad" in JUDGMENT
    assert "hous-\n" in JUDGMENT
    model = Answer(_body(_gemini_facts(), element=f"\u201c{PASSAGE_B}\u201d"))
    report = await convert_cases(Pool(Doc(_case_text())), LadderMemory(), model, region="za")
    assert report["converted"] == 1
    assert report["failed"] == 0
    assert report["cases"] == []
    assert model.calls == ["zenas.case_facts"]


@pytest.mark.asyncio
async def test_a_paraphrased_quote_fails_and_the_answer_is_stored() -> None:
    paraphrase = "The court held that the housing programme was enough because money had already been spent."
    raw = _body(paraphrase, element=paraphrase, citation_quote=paraphrase)
    assert len(raw) < 1500
    model = Answer(raw, finish_reason="STOP", tokens_in=800, tokens_out=90)
    ladder = LadderMemory()
    report = await convert_cases(Pool(Doc(_case_text())), ladder, model, region="za")
    assert report["converted"] == 0
    assert report["failures"] == [{"reason": "quote is not in the judgment", "count": 1}]
    assert ladder.worked == []
    saved = ladder.failures[0]
    assert saved["reason"] == "quote is not in the judgment"
    assert saved["answer_excerpt"] == raw
    assert saved["finish_reason"] == "STOP"
    assert saved["tokens_in"] == 800
    assert saved["tokens_out"] == 90
    assert TAIL not in saved["answer_excerpt"]
    assert CLIENT not in _dump(ladder)
    assert TAIL not in _dump(ladder)
    case = report["cases"][0]
    assert case["citation"] == CITE
    assert case["answer"] == raw
    assert case["finish_reason"] == "STOP"
    assert "text" not in case
    assert "judgment" not in case


@pytest.mark.asyncio
async def test_a_truncated_answer_reports_answer_cut_off_with_its_finish_reason() -> None:
    raw = '{"facts": "The rights to life'
    model = Answer(raw, finish_reason="MAX_TOKENS", tokens_in=18000, tokens_out=4000)
    ladder = LadderMemory()
    report = await convert_cases(Pool(Doc(_case_text())), ladder, model, region="za")
    assert report["converted"] == 0
    assert ladder.failures[0]["reason"] == "answer cut off (MAX_TOKENS)"
    assert ladder.failures[0]["reason"] != "the model did not return case facts"
    assert ladder.failures[0]["answer_excerpt"] == raw
    assert ladder.failures[0]["finish_reason"] == "MAX_TOKENS"
    assert report["failures"] == [{"reason": "answer cut off (MAX_TOKENS)", "count": 1}]
    assert report["cases"][0]["tokens_in"] == 18000
    assert report["cases"][0]["tokens_out"] == 4000
    assert cutoff_label("length") == "answer cut off (length)"
    assert answer_cut_off("length") is True
    assert answer_cut_off("STOP") is False


@pytest.mark.asyncio
async def test_a_truncated_but_complete_json_is_still_answer_cut_off() -> None:
    model = Answer(_body(PASSAGE_A), finish_reason="length", tokens_in=10, tokens_out=4000)
    report = await convert_cases(Pool(Doc(_case_text())), LadderMemory(), model, region="za")
    assert report["converted"] == 0
    assert report["failures"][0]["reason"] == "answer cut off (length)"


@pytest.mark.asyncio
async def test_joined_passages_pass_only_when_each_is_verbatim() -> None:
    good = f"{PASSAGE_A} \u2026 {PASSAGE_B}"
    passed = await convert_cases(
        Pool(Doc(_case_text())),
        LadderMemory(),
        Answer(_body(good)),
        region="za",
    )
    assert passed["converted"] == 1

    mixed = f"{PASSAGE_A} \u2026 The court took a different view of the appeal and set the order aside."
    failed = await convert_cases(
        Pool(Doc(_case_text())),
        LadderMemory(),
        Answer(_body(mixed, element=PASSAGE_A, citation_quote=PASSAGE_A)),
        region="za",
    )
    assert failed["converted"] == 0
    assert failed["failures"][0]["reason"] == "quote is not in the judgment"

    stitched = "The \u2026 rights \u2026 dignity \u2026 human"
    assert fact_passages(stitched) == [stitched]
    words = await convert_cases(
        Pool(Doc(_case_text())),
        LadderMemory(),
        Answer(_body(stitched, element=PASSAGE_A, citation_quote=PASSAGE_A)),
        region="za",
    )
    assert words["converted"] == 0
    assert words["failures"][0]["reason"] == "facts too short"


@pytest.mark.asyncio
async def test_only_the_first_1500_characters_of_the_answer_are_stored() -> None:
    raw = "x" * 2000
    model = Answer(raw, finish_reason="STOP")
    ladder = LadderMemory()
    await convert_cases(Pool(Doc(_case_text())), ladder, model, region="za")
    assert len(ladder.failures[0]["answer_excerpt"]) == 1500
    assert ladder.failures[0]["answer_excerpt"] == "x" * 1500
    assert TAIL not in ladder.failures[0]["answer_excerpt"]


@pytest.mark.asyncio
async def test_sql_debug_stores_the_answer_and_not_the_judgment() -> None:
    class Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute(self, sql: str, *args: object) -> str:
            self.calls.append((sql, args))
            return "UPDATE 1"

        async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
            self.calls.append(("fetch", args))
            return []

    class Hold:
        def __init__(self, conn: Conn) -> None:
            self.conn = conn

        async def __aenter__(self) -> Conn:
            return self.conn

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    class Ladder:
        def __init__(self) -> None:
            self.conn = Conn()
            self.pool = self

        def acquire(self) -> Hold:
            return Hold(self.conn)

        async def worked_for_doc(self, *, region: str, pool_doc_id: str) -> None:
            del region, pool_doc_id
            return None

        async def record_convert_failure(self, *, region: str, pool_doc_id: str, reason: str) -> None:
            del region, pool_doc_id, reason

        async def case_tally(self, *, region: str, jurisdiction: str) -> dict[str, int]:
            del region, jurisdiction
            return {"converted": 0, "held_out": 0}

        async def convert_failure_count(self, *, region: str) -> int:
            del region
            return 1

        async def convert_failure_reasons(self, *, region: str) -> list[dict[str, object]]:
            del region
            return [{"reason": "quote is not in the judgment", "count": 1}]

    ladder = Ladder()
    raw = _body("This sentence is not in the judgment.", element="This sentence is not in the judgment.", citation_quote="This sentence is not in the judgment.")
    await convert_cases(Pool(Doc(_case_text())), ladder, Answer(raw), region="za")
    updates = [call for call in ladder.conn.calls if "answer_excerpt" in call[0]]
    assert len(updates) == 1
    _sql, args = updates[0]
    assert args[2] == raw
    assert TAIL not in str(args)
    assert CLIENT not in str(args)
    assert JUDGMENT not in str(args)


def test_pdf_folding_keeps_words_and_drops_extraction_noise() -> None:
    assert quote_in_source(PASSAGE_A, JUDGMENT)
    assert quote_in_source(PASSAGE_B, JUDGMENT)
    assert quote_in_source("\u201chousing\u201d", "housing")
    assert quote_in_source("relationship", "rela-\ntionship")
    assert quote_in_source("recognises", "recog\u00adnises")
    assert quote_in_source("bricks-mortar", "bricks\u2014mortar")
    assert quote_in_source("socio-economic rights, Socio-economic", "socio-economic rights,[15] Socio-economic")
    assert quote_in_source("The right of access", "24. The right of access")
    assert not quote_in_source(
        "The court held that the housing programme was enough because money had already been spent.",
        JUDGMENT,
    )
    assert "housing" in fold_for_quote("hous-\n ing")
    assert fold_for_quote("court\u2019s") == "court's"


def test_case_facts_asks_gemini_for_json_and_low_thinking() -> None:
    flash = main.case_facts_generation("flash")
    assert flash["response_format"] == {"type": "json_object"}
    assert flash["reasoning_effort"] == "low"
    assert "thinking" not in flash
    luna = main.case_facts_generation("luna")
    assert luna == {"response_format": {"type": "json_object"}}
    assert main.case_facts_generation("haiku") == {}
    assert main.case_facts_generation("gemini/gemini-3.8-flash")["reasoning_effort"] == "low"


class _Conn:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    async def fetchrow(self, sql: str, *_args: object) -> dict[str, object] | None:
        if "FROM tank.policy" in sql:
            return self.row
        return {"rejected": 0, "judged": 0}

    async def execute(self, *_args: object) -> str:
        return "INSERT 0 1"

    def transaction(self) -> "_Conn":
        return self

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> _Conn:
        return self.conn


class _Gateway:
    sent: list[dict[str, object]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_Gateway":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(self, _url: str, **kwargs: object) -> "_Reply":
        body = kwargs.get("json")
        if isinstance(body, dict):
            _Gateway.sent.append(body)
        return _Reply()


class _Reply:
    status_code = 200
    headers = {"content-type": "application/json", "x-litellm-response-cost": "0.01"}
    text = ""

    def json(self) -> dict[str, object]:
        return {
            "choices": [{"message": {"content": '{"facts":"ok"}'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4000},
        }


def _policy(model: str) -> dict[str, object]:
    return {
        "model": model,
        "priority": "batch",
        "rung": 1,
        "max_tokens_out": 4000,
        "system_prompt": None,
        "cache_scope": "none",
        "candidate_model": None,
        "shadow_rate": 0,
    }


@pytest.mark.asyncio
async def test_run_route_sends_json_and_low_thinking_only_for_case_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.delenv("TANK_PROVIDER_ALLOW", raising=False)
    monkeypatch.setattr(main, "_redis", None)
    _Gateway.sent = []
    monkeypatch.setattr(main.httpx, "AsyncClient", _Gateway)

    async def once(model: str, task_type: str) -> main.RouteResponse:
        monkeypatch.setattr(main, "pool", lambda: _Pool(_Conn(_policy(model))))
        return await main.run_route(
            main.RouteRequest(
                tenant_id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
                home_region="us-east",
                task_type=task_type,
                messages=[main.Message(role="user", content="Read this judgment.")],
            )
        )

    flash = await once("flash", "zenas.case_facts")
    assert flash.finish_reason == "length"
    assert flash.tokens_in == 12
    assert flash.tokens_out == 4000
    assert _Gateway.sent[-1]["response_format"] == {"type": "json_object"}
    assert _Gateway.sent[-1]["reasoning_effort"] == "low"
    assert "thinking" not in _Gateway.sent[-1]

    await once("luna", "zenas.case_facts")
    assert _Gateway.sent[-1]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in _Gateway.sent[-1]

    await once("luna", "tier0.answer")
    assert "response_format" not in _Gateway.sent[-1]
    assert "reasoning_effort" not in _Gateway.sent[-1]


def test_the_feeds_page_shows_why_for_the_founder_only() -> None:
    board = (ROOT / "workspace" / "app" / "zenas" / "feeds" / "board.tsx").read_text(encoding="utf-8")
    page = (ROOT / "workspace" / "app" / "zenas" / "feeds" / "page.tsx").read_text(encoding="utf-8")
    route = (ROOT / "workspace" / "app" / "api" / "zenas" / "feeds" / "region" / "convert" / "route.ts").read_text(
        encoding="utf-8"
    )
    public = (ROOT / "workspace" / "app" / "api" / "zenas" / "feeds" / "public.ts").read_text(encoding="utf-8")
    assert "Why" in board
    assert "finish_reason" in board
    assert "isFounder" in page
    assert "isFounder" in route
    assert "caseFailures" in public
    assert "row.answer" in public
    assert "row.judgment" not in public
    assert "row.text" not in public
    assert "answer cut off" in public
