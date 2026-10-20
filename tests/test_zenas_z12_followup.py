"""Z12 follow-up: nightly made-up cases through answer_question, the Skala gate, the convert prompt."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
os.environ.setdefault("LITELLM_MASTER_KEY", "test")
os.environ.setdefault("TANK_API_KEY", "test")

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents" / "_chassis"))

from agents.zenas.core.convert import _PROMPT_HEAD  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.selftest import (  # noqa: E402
    load_case_library,
    run_made_up_test,
    run_scheduled,
    run_selftest,
)
from tank.skala import SkillPackage, zenas_approval_reason, zenas_approval_refusal  # noqa: E402

_PLATFORM = "00000000-0000-0000-0000-000000000001"
_QUOTE = "The court refuses leave to appeal."
_TEXT = "The court refuses leave to appeal."
_NOT_RUN = (
    "This Zenas skill stays unapproved. The made-up-case test has not run "
    "on this box through answer_question."
)
_NO_PATH = (
    "This Zenas skill stays unapproved. The latest made-up-case test on this box "
    "did not run through answer_question."
)
_SHORT = (
    "This Zenas skill stays unapproved. The latest made-up-case test on this box is "
    "49 of 50, not 50 of 50 through answer_question."
)


def _card_for(prompt: str) -> dict:
    citation = "[2024] ZASCA 10"
    quote = _QUOTE
    marker = "Passages:\n"
    at = prompt.rfind(marker)
    if at >= 0:
        raw = prompt[at + len(marker) :].split("\n", 1)[0]
        try:
            passages = json.loads(raw)
        except json.JSONDecodeError:
            passages = []
        if passages:
            citation = str(passages[0].get("citation") or citation)
            text = str(passages[0].get("text") or "")
            if quote not in text and text:
                quote = text[:80]
    return {
        "elements": [{"id": "holding", "test": quote, "citation": citation, "quote": quote}],
        "decision_table": [{"element_id": "holding", "when": {"asked": "yes"}, "finding": "met"}],
    }


class _Response:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _TankHttp:
    def __init__(self, timeout: int = 120) -> None:
        self.timeout = timeout
        self.posts: list[dict] = []
        self.closed = False
        _TankHttp.latest = self

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None) -> _Response:
        self.posts.append({"url": url, "json": json or {}, "headers": headers or {}})
        content = ""
        messages = (json or {}).get("messages") or []
        if messages:
            content = str(messages[0].get("content") or "")
        return _Response(
            {
                "content": json_dumps(_card_for(content)),
                "cost_usd": 0,
                "model": "script",
                "finish_reason": "stop",
                "tokens_in": 1,
                "tokens_out": 1,
            }
        )

    async def aclose(self) -> None:
        self.closed = True


def json_dumps(payload: dict) -> str:
    return json.dumps(payload)


class _Rows:
    def __init__(self, rows: list[dict], jurisdiction: str) -> None:
        self.rows = rows
        self.jurisdiction = jurisdiction
        self.calls: list[tuple] = []

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self.calls.append((sql, args))
        assert args == (self.jurisdiction,)
        assert "zenas_pool.documents" in sql
        assert "superseded" in sql
        return list(self.rows)


class _RecordingLadder(LadderMemory):
    def __init__(self, pool: object) -> None:
        super().__init__()
        _RecordingLadder.made.append(self)


_RecordingLadder.made = []


def test_load_case_library_keeps_this_box_current_cases() -> None:
    text = "Note.\nThe court refuses leave to appeal.\nA later sentence follows here."
    conn = _Rows(
        [
            {"citation": "[2024] ZASCA 10", "title": "Sample v Example", "text": text},
            {"citation": "[2020] ZASCA 1", "title": "", "text": text},
            {"citation": "", "title": "Empty v Cite", "text": text},
        ],
        "za",
    )
    documents = asyncio.run(load_case_library(conn, region="za"))
    assert documents == [
        {
            "name": "Sample v Example",
            "citation": "[2024] ZASCA 10",
            "quote": "The court refuses leave to appeal.",
            "text": text,
        }
    ]
    empty = asyncio.run(load_case_library(_Rows([], "us"), region="us-east"))
    assert empty == []
    assert asyncio.run(load_case_library(None, region="za")) == []


def test_nightly_made_up_row_uses_answer_question_through_tank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each box scores fifty questions. The model call is Tank /v1/route on that box."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _TankHttp)
    monkeypatch.setenv("TANK_KEY", "box-key")
    monkeypatch.setenv("TANK_URL", "http://tank.this-box")
    _RecordingLadder.made = []
    monkeypatch.setattr("agents.zenas.core.ladder_store.PgLadder", _RecordingLadder)

    async def _schema(self) -> None:
        return None

    monkeypatch.setattr("agents.zenas.core.store.PgStore.apply_schema", _schema)
    conn = _Rows(
        [{"citation": "[2024] ZASCA 10", "title": "Sample v Example", "text": _TEXT}],
        "za",
    )
    asyncio.run(run_scheduled(conn, region="za"))
    ladder = _RecordingLadder.made[-1]
    made = next(row for row in ladder.selftests if str(row["area"]).startswith("Made-up"))
    assert made["area"].startswith("Made-up cases: 50 of 50.")
    assert "path: answer_question" in made["area"]
    assert made["cases"] == 50
    assert made["detail"]["path"] == "answer_question"
    assert made["detail"]["skills_may_be_approved"] is True
    client = _TankHttp.latest
    assert client.closed is True
    assert len(client.posts) >= 50
    first = client.posts[0]
    assert first["url"] == "http://tank.this-box/v1/route"
    assert first["json"]["tenant_id"] == _PLATFORM
    assert first["json"]["agent"] == "zenas"
    assert first["json"]["task_type"] == "zenas.rule_card"
    assert first["headers"]["X-Tank-Key"] == "box-key"
    assert all(post["url"].endswith("/v1/route") for post in client.posts)
    assert "box-key" not in made["area"]


def test_nightly_without_a_tank_key_stays_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TANK_KEY", raising=False)
    monkeypatch.delenv("TANK_URL", raising=False)
    _RecordingLadder.made = []
    monkeypatch.setattr("agents.zenas.core.ladder_store.PgLadder", _RecordingLadder)

    async def _schema(self) -> None:
        return None

    monkeypatch.setattr("agents.zenas.core.store.PgStore.apply_schema", _schema)
    conn = _Rows(
        [{"citation": "[2024] ZASCA 10", "title": "Sample v Example", "text": _TEXT}],
        "us",
    )
    asyncio.run(run_scheduled(conn, region="us-east"))
    made = next(row for row in _RecordingLadder.made[-1].selftests if str(row["area"]).startswith("Made-up"))
    assert made["area"] == "Made-up cases: not run."
    assert "50 of 50" not in made["area"]


def test_the_us_box_asks_answer_question_about_its_own_library() -> None:
    document = {
        "name": "Sample v Example",
        "citation": "999 F. Supp. 3d 10",
        "quote": _QUOTE,
        "text": _TEXT,
    }

    class Model:
        async def complete(self, messages, *, task_type: str, provider: str) -> dict:
            assert provider == "luna"
            assert task_type == "zenas.rule_card"
            content = messages[0]["content"]
            assert "United States" in content
            return {"content": json.dumps(_card_for(content)), "cost_usd": 0, "model": "luna"}

    ladder = LadderMemory()
    result = asyncio.run(run_made_up_test(ladder, ladder, Model(), library=[document], region="us-east"))
    assert result["path"] == "answer_question"
    assert result["area"].startswith("Made-up cases: 50 of 50.")
    assert "path: answer_question" in result["area"]
    assert all("United States" in item["question"] for item in result["questions"])
    assert all("commercial dispute" in item["question"] for item in result["questions"])
    assert all(card["region"] == "us-east" and card["jurisdiction"] == "us" for card in ladder.rule_cards)
    nightly = asyncio.run(run_selftest(LadderMemory(), region="us-east"))
    bare = next(run for run in nightly["runs"] if str(run["area"]).startswith("Made-up"))
    assert bare["area"] == "Made-up cases: not run."


def test_a_home_on_the_other_box_does_not_invent_a_score() -> None:
    document = {
        "name": "Sample v Example",
        "citation": "[2024] ZASCA 10",
        "quote": _QUOTE,
        "text": _TEXT,
    }
    ladder = LadderMemory()
    asyncio.run(ladder.register_tenant(_PLATFORM, "us-east"))

    class Model:
        async def complete(self, messages, *, task_type: str, provider: str) -> dict:
            raise AssertionError("the model must not be called")

    result = asyncio.run(run_made_up_test(ladder, ladder, Model(), library=[document], region="za"))
    assert result["path"] == "not run"
    assert result["area"] == "Made-up cases: not run."


def test_nightly_calls_the_scheduled_selftest() -> None:
    text = (ROOT / "agents" / "zenas" / "pool" / "nightly.py").read_text(encoding="utf-8")
    assert "await run_scheduled(conn, region=current_region())" in text


def test_convert_prompt_extracts_each_issue_from_the_court_words() -> None:
    from tank import main

    head = _PROMPT_HEAD
    assert main._CONVERT_HEAD == head
    for phrase in (
        "For each issue the court decided",
        "give the question",
        "the order",
        "reasoning_pointer",
        "likely_ratio_pointer",
        "always label that marking likely",
        "Mark obiter only where the court's own words show it",
        "it is unnecessary to decide",
        "I express no view",
        "minority",
        "dissent",
        "do not merge it into the majority",
        "do not put a length cap on it",
    ):
        assert phrase in head, phrase
    assert '"issues":[{"question":""' in head
    assert '"obiter_pointer":{"paragraphs":[]}' in head
    assert '"minority":[{"judge":""' in head


def test_zenas_approval_reason_requires_fifty_through_answer_question() -> None:
    perfect = "Made-up cases: 50 of 50. path: answer_question. Zenas skills may be approved."
    assert zenas_approval_reason("zenas.cite_check", perfect) is None
    assert zenas_approval_reason("base.find_site", None) is None
    assert zenas_approval_reason("zenas.cite_check", None) == _NOT_RUN
    assert zenas_approval_reason("zenas.cite_check", "Made-up cases: not run.") == _NOT_RUN
    assert zenas_approval_reason("zenas.cite_check", "Made-up cases: 50 of 50.") == _NO_PATH
    short = "Made-up cases: 49 of 50. path: answer_question. Zenas skills may not be approved."
    assert zenas_approval_reason("zenas.cite_check", short) == _SHORT


class _Conn:
    def __init__(self, area: str | None, state: str = "staging") -> None:
        self.area = area
        self.state = state
        self.updates: list[str] = []
        self.regions: list[object] = []

    async def fetchval(self, sql: str, *args: object) -> str:
        return self.state

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        self.regions.append(args[0] if args else None)
        if self.area is None:
            return None
        return {"area": self.area}

    def transaction(self) -> "_Conn":
        return self

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, sql: str, *args: object) -> str:
        self.updates.append(sql)
        return "UPDATE 1"


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> "_Pool":
        return self

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _package(skill_id: str) -> SkillPackage:
    return SkillPackage(
        id=skill_id,
        version="1.0.0",
        agent=skill_id.split(".", 1)[0],
        rung=1,
        task_type=skill_id,
        description="",
        input_schema={},
        output_schema={},
        eval_threshold=1.0,
        path=ROOT / "tank" / "skills" / skill_id / "1.0.0",
    )


def test_approving_a_zenas_skill_is_refused_until_this_box_scores_fifty(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    import tank.main as main

    perfect = "Made-up cases: 50 of 50. path: answer_question. Zenas skills may be approved."
    monkeypatch.setenv("TANK_REGION", "za")

    async def refuse() -> None:
        conn = _Conn("Made-up cases: not run.")
        monkeypatch.setattr(main, "pool", lambda: _Pool(conn))
        main._packages[("zenas.cite_check", "1.0.0")] = _package("zenas.cite_check")
        with pytest.raises(HTTPException) as caught:
            await main.approve_skill("zenas.cite_check", "1.0.0", main.Approval(by="founder"))
        assert caught.value.status_code == 409
        assert caught.value.detail == _NOT_RUN
        assert conn.updates == []
        assert conn.regions == ["za"]

    async def short() -> None:
        conn = _Conn("Made-up cases: 49 of 50. path: answer_question. Zenas skills may not be approved.")
        monkeypatch.setattr(main, "pool", lambda: _Pool(conn))
        with pytest.raises(HTTPException) as caught:
            await main.approve_skill("zenas.cite_check", "1.0.0", main.Approval(by="founder"))
        assert caught.value.status_code == 409
        assert caught.value.detail == _SHORT
        assert conn.updates == []

    async def allow_zenas() -> None:
        conn = _Conn(perfect)
        monkeypatch.setattr(main, "pool", lambda: _Pool(conn))
        body = await main.approve_skill("zenas.cite_check", "1.0.0", main.Approval(by="founder"))
        assert body["state"] == "approved"
        assert conn.updates

    async def allow_other() -> None:
        conn = _Conn(None)
        monkeypatch.setattr(main, "pool", lambda: _Pool(conn))
        main._packages[("base.find_site", "1.0.0")] = _package("base.find_site")
        body = await main.approve_skill("base.find_site", "1.0.0", main.Approval(by="founder"))
        assert body["state"] == "approved"
        assert conn.regions == []

    asyncio.run(refuse())
    asyncio.run(short())
    asyncio.run(allow_zenas())
    asyncio.run(allow_other())


def test_the_gate_reads_the_latest_made_up_row_for_this_box() -> None:
    perfect = "Made-up cases: 50 of 50. path: answer_question. Zenas skills may be approved."

    async def check() -> None:
        missed = await zenas_approval_refusal(_Conn(None), "zenas.rule_card", region="ZA")
        assert missed == _NOT_RUN
        passed = await zenas_approval_refusal(_Conn(perfect), "zenas.rule_card", region="za")
        assert passed is None
        other = await zenas_approval_refusal(_Conn("Made-up cases: not run."), "base.find_site", region="us-east")
        assert other is None
        us = _Conn("Made-up cases: not run.")
        assert await zenas_approval_refusal(us, "zenas.scope", region="us") == _NOT_RUN
        assert us.regions == ["us-east"]

    asyncio.run(check())
