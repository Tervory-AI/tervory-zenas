"""Fix 7: Ask Zenas reaches the room's answers path through Tank, and bail/theft are criminal.

No network and no database. Tank's proxy is called directly with a fake room.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
os.environ.setdefault("LITELLM_MASTER_KEY", "test")
os.environ.setdefault("TANK_API_KEY", "test")

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core import scope_rules  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
MATTER = "22222222-2222-2222-2222-222222222222"
DOC_QUESTION = "How do I get bail for theft?"


def _request(method: str, path: str, body: bytes = b"", query: bytes = b"") -> Request:
    sent = {"done": False}

    async def receive() -> dict:
        if sent["done"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    return Request(scope, receive)


class _Room:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode()
        self.calls: list[tuple[str, str, bytes | None]] = []

    async def request(self, method: str, url: str, **kwargs: object) -> object:
        self.calls.append((method, url, kwargs.get("content")))  # type: ignore[arg-type]

        class _Response:
            content = self.payload
            status_code = 200
            headers = {"content-type": "application/json"}

        return _Response()


@pytest.fixture
def us_room(monkeypatch: pytest.MonkeyPatch) -> _Room:
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    room = _Room({"ok": False, "reason": "could not verify"})
    monkeypatch.setattr(main, "_http", room)
    return room


# ---------- Tank forwarding ----------


def test_answers_and_release_are_on_the_allowlist_and_nothing_near_them_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    assert main.zenas_upstream("answers") == "http://agent-zenas:8000/answers"
    assert main.zenas_upstream(f"matters/{MATTER}/release") == f"http://agent-zenas:8000/matters/{MATTER}/release"
    for bad in (
        "answers/x",
        "answer",
        "matters/release",
        "matters/not-a-uuid/release",
        f"matters/{MATTER}/release/x",
        f"matters/{MATTER}/releases",
        f"documents/{MATTER}/release",
    ):
        with pytest.raises(HTTPException) as caught:
            main.zenas_upstream(bad)
        assert caught.value.status_code == 404, bad


@pytest.mark.asyncio
async def test_answers_are_forwarded_with_the_tenant_and_home_unchanged(us_room: _Room) -> None:
    body = json.dumps(
        {"tenant_id": TENANT, "home_region": "us-east", "jurisdiction": "us", "question": DOC_QUESTION, "matter_id": MATTER}
    ).encode()
    response = await main.zenas_proxy("answers", _request("POST", "/v1/zenas/answers", body))
    assert response.status_code == 200
    assert us_room.calls == [("POST", "http://agent-zenas:8000/answers", body)]
    assert json.loads(response.body) == {"ok": False, "reason": "could not verify"}


@pytest.mark.asyncio
async def test_release_is_forwarded_for_this_home(us_room: _Room) -> None:
    body = json.dumps({"tenant_id": TENANT, "home_region": "us-east", "document_id": MATTER}).encode()
    await main.zenas_proxy(f"matters/{MATTER}/release", _request("POST", f"/v1/zenas/matters/{MATTER}/release", body))
    assert us_room.calls == [("POST", f"http://agent-zenas:8000/matters/{MATTER}/release", body)]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["answers", f"matters/{MATTER}/release"])
@pytest.mark.parametrize("home", ["za", "", None, "eu"])
async def test_a_foreign_or_missing_home_is_refused_before_the_room(us_room: _Room, path: str, home: str | None) -> None:
    payload: dict = {"tenant_id": TENANT, "question": DOC_QUESTION}
    if home is not None:
        payload["home_region"] = home
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy(path, _request("POST", f"/v1/zenas/{path}", json.dumps(payload).encode()))
    assert caught.value.status_code == 403
    assert us_room.calls == []


@pytest.mark.parametrize("path", ["/v1/zenas/answers", f"/v1/zenas/matters/{MATTER}/release"])
def test_no_key_a_wrong_key_and_an_agent_key_are_refused(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("TANK_KEY_TESTAGENT", "agent-key-for-tests")
    body = json.dumps({"tenant_id": TENANT, "home_region": "us-east"}).encode()
    for key, status in (("", 401), ("not-the-key", 401), ("agent-key-for-tests", 403)):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(main.require_key(_request("POST", path, body), x_tank_key=key))
        assert caught.value.status_code == status, key
    assert asyncio.run(main.require_key(_request("POST", path, body), x_tank_key=main.TANK_API_KEY)) is None


# ---------- Scope gate ----------

SKILL = ROOT / "tank" / "skills" / "zenas.scope" / "1.1.0"


@pytest.mark.parametrize(
    "text",
    [
        DOC_QUESTION,
        "how do i get BAIL for my cousin?",
        "Is shoplifting theft in Texas?",
        "Two thefts were reported at the warehouse.",
        "Bail, please: they took my client in last night.",
    ],
)
def test_bail_and_theft_get_the_criminal_refusal_and_referral(text: str) -> None:
    assert scope_rules.ruled(text) == "criminal"
    result = scope_rules.classify(text)
    assert result["criminal"] is True
    assert result["in_scope"] is False
    assert result["reason"] == scope_rules.REFUSED
    assert "Ask a criminal lawyer" in result["reason"]


@pytest.mark.parametrize(
    "text",
    [
        "Draft a bailment clause for goods held in our warehouse under the supply agreement.",
        "Who is the bailee when the carrier holds the cargo under a bill of lading?",
        "The bailiff attached our stock. Can we challenge the sale of goods?",
        "Did the shareholder agreement allow a bailout by the parent corporation?",
        "Is a theftproof lock warranty part of the supply agreement?",
    ],
)
def test_words_that_only_contain_bail_or_theft_are_not_caught(text: str) -> None:
    assert scope_rules.ruled(text) != "criminal"
    assert scope_rules.classify(text)["criminal"] is False


def test_the_box_copy_and_the_new_skill_version_agree_and_1_0_0_is_untouched() -> None:
    box = ROOT / "agents" / "zenas" / "core"
    assert (SKILL / "areas.json").read_bytes() == (box / "areas.json").read_bytes()
    assert (SKILL / "rules.py").read_bytes() == (box / "scope_rules.py").read_bytes()
    old = json.loads((ROOT / "tank" / "skills" / "zenas.scope" / "1.0.0" / "areas.json").read_text(encoding="utf-8"))
    assert "bail" not in old["criminal_phrases"]
    new = json.loads((SKILL / "areas.json").read_text(encoding="utf-8"))
    for word in ("bail", "theft", "thefts"):
        assert word in new["criminal_phrases"]
    assert "version: 1.1.0" in (SKILL / "skill.yaml").read_text(encoding="utf-8")


def test_the_new_skill_version_evals_include_the_doc_question() -> None:
    sys.path.insert(0, str(SKILL))
    cases = [json.loads(line) for line in (SKILL / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    texts = {case["input"]["text"]: case for case in cases}
    assert DOC_QUESTION in texts
    for case in cases:
        got = scope_rules.classify(case["input"]["text"], case["input"].get("jurisdiction"))
        if case["group"] == "criminal":
            assert got == case["expected"], case["name"]


@pytest.mark.asyncio
async def test_the_room_refuses_the_doc_question_without_a_model_call() -> None:
    from agents.zenas.core.matters import consider

    class _Tank:
        def __init__(self) -> None:
            self.calls = 0

        async def run_skill(self, *args: object, **kwargs: object) -> dict:
            self.calls += 1
            return {"in_scope": True, "area": "contracts", "jurisdiction": "us", "criminal": False, "needs_lawyer": True, "reason": "x"}

    tank = _Tank()
    gate = await consider(tank, tenant_id=TENANT, text=DOC_QUESTION, jurisdiction="us")
    assert gate["stopped"] is True
    assert gate["scope"]["criminal"] is True
    assert gate["scope"]["reason"] == scope_rules.REFUSED


# ---------- Workspace route ----------


def test_ask_rules_show_plain_english_only() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed on this job")
    subprocess.check_call([node, "--experimental-strip-types", "tests/zenas_ask_rules.mjs"], cwd=ROOT)


def test_answers_route_is_signed_in_tenant_scoped_and_plain() -> None:
    route = (ROOT / "workspace" / "app" / "api" / "zenas" / "answers" / "route.ts").read_text(encoding="utf-8")
    assert "currentTenant()" in route
    assert "tenant_id: tenantId" in route
    assert "body?.tenant_id" not in route and "body.tenant_id" not in route
    assert 'zenas("matters"' in route and 'zenas("answers"' in route
    assert route.index('zenas("matters"') < route.index('zenas("answers"')
    assert "home_region: home" in route
    assert "matter_id: matterId" in route
    assert "presentAnswer(" in route and "askRefusal(" in route
    assert ".message" not in route.replace("message:", "")
    for word in ("luna", "litellm", "model", "provider", "cost_usd", "gpt", "claude", "gemini"):
        assert word not in route.lower(), word
    middleware = (ROOT / "workspace" / "middleware.ts").read_text(encoding="utf-8")
    assert "/api/zenas" not in middleware


def test_ask_box_calls_the_route_with_loading_and_error_states() -> None:
    view = (ROOT / "workspace" / "app" / "zenas" / "view.tsx").read_text(encoding="utf-8")
    ask = view.split("async function onAsk", 1)[1].split("\n  }\n", 1)[0]
    assert 'fetch("/api/zenas/answers"' in ask
    assert "setAsking(true)" in ask and "setAsking(false)" in ask
    assert "catch" in ask
    assert "Zenas cannot answer from this page yet" not in view
    assert "Zenas is checking the law." in view
    assert 'role={reply.kind === "error" ? "alert" : "status"}' in view
    assert "reply.referral" in view
    assert "disabled={asking}" in view
    for word in ("luna", "litellm", "provider", "cost_usd", "model_used"):
        assert word not in view.lower(), word
