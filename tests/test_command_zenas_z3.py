"""Existing Confirm cards -> real Tank proxy -> offline Zenas watch routes."""
from contextlib import asynccontextmanager
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
os.environ.setdefault("LITELLM_MASTER_KEY", "test")
os.environ.setdefault("TANK_API_KEY", "test")

import command
import main as tank_main
from agents.zenas.core import regulation_api
from agents.zenas.core.regulation_store import MemoryWatches
from agents.zenas.core.scope_rules import REFUSED
from agents.zenas.core.store import MemoryStore
from test_command import TENANT, OTHER, runtime as runtime, command_pool as command_pool, api as api

WATCH = "11111111-1111-4111-8111-111111111111"
CASES = [
    ("zenas.watch.add", {"source": "federal-register", "topic": "customs", "options": {}}),
    ("zenas.watch.list", {}),
    ("zenas.watch.enable", {"watch_id": WATCH}),
    ("zenas.watch.disable", {"watch_id": WATCH}),
]


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    import pgserver
    server = pgserver.get_server(tmp_path / "command-db")
    monkeypatch.setenv("POOL_SCHEMA_DATABASE_URL", server.get_uri())
    yield
    server.cleanup()


@pytest.mark.parametrize("tool,args", CASES)
def test_scope_cannot_come_from_arguments(runtime, tool, args):
    actor = command.Actor(TENANT, "founder", True)
    for field in ("tenant_id", "home_region", "enabled"):
        with pytest.raises(Exception):
            command.adapter_request(command.PlannedAction(tool=tool, arguments={**args, field: str(OTHER)}), actor)
    method, path, query, body = command.adapter_request(command.PlannedAction(tool=tool, arguments=args), actor)
    assert (body or query)["tenant_id"] == str(TENANT)
    assert (body or query)["home_region"] == "us-east"
    assert command.ADAPTERS[tool].label == tool
    assert tool in command.TOOLS["zenas"]
    if tool.endswith(("enable", "disable")):
        assert path.endswith(WATCH)
        assert body["enabled"] is tool.endswith("enable")
        assert "watch_id" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("region,source", [("us-east", "federal-register"), ("za", "laws-africa")])
async def test_confirm_watch_lifecycle_receipt_refusals_and_isolation(api, runtime, monkeypatch, region, source):
    client, pool = api
    runtime.box_region = lambda: region
    runtime.pool = lambda: pool
    runtime.app = tank_main.app
    monkeypatch.setattr(tank_main, "TANK_API_KEY", runtime.TANK_API_KEY)
    monkeypatch.setenv("TANK_REGION", region)
    monkeypatch.setenv("TANK_KEY_ZENAS", "offline-room")
    monkeypatch.setenv("TANK_KEY", "offline-room")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    from contextvars import ContextVar
    from trigger_signin_fakes import native_provider, ORG
    import trigger_signin_config as registry
    provider = native_provider(monkeypatch, {ORG: str(TENANT)})
    signed = provider.workspace_headers('founder', TENANT)
    monkeypatch.setattr(registry, 'proof', ContextVar('fixture_verified_proof', default={
        name: value for name, value in signed.items() if name.startswith('x-trigger-signin-')}))
    core, watches = MemoryStore(), MemoryWatches()
    await core.register_tenant(str(TENANT), region)
    await core.register_tenant(str(OTHER), region)

    @asynccontextmanager
    async def pool_store():
        yield SimpleNamespace(_conn=None)

    async def apply_schema():
        return None

    watches.apply_schema = apply_schema
    monkeypatch.setattr(regulation_api, "PgWatches", lambda _: watches)
    room = FastAPI()
    regulation_api.register_routes(room, pool_store, lambda: core, lambda: region)
    calls = []

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room), base_url="http://room") as room_client:
        async def forward(request):
            assert request.url.host == "agent-zenas"
            assert request.headers["x-tank-key"] == "offline-room"
            payload = json.loads(request.content) if request.method == "POST" else dict(request.url.params)
            assert payload["home_region"] == region
            calls.append((request.url.path, payload))
            return await room_client.request(request.method, request.url.path, params=request.url.params,
                                             content=request.content, headers=request.headers)

        async with httpx.AsyncClient(transport=httpx.MockTransport(forward)) as transport:
            monkeypatch.setattr(tank_main, "_http", transport)
            thread = (await client.post("/v1/command/threads", json={})).json()["thread_id"]

            async def confirm(tool, arguments):
                before = len(calls)
                proposed = await client.post(f"/v1/command/threads/{thread}/actions", json={"tool": tool, "arguments": arguments})
                assert proposed.status_code == 200, proposed.text
                card = proposed.json()
                assert card["status"] == "pending" and card["summary"] == tool
                assert len(calls) == before
                confirmed = await client.post(f"/v1/command/cards/{card['card_id']}", json={"action": "confirm"})
                assert confirmed.status_code == 200, confirmed.text
                assert len(calls) == before + 1
                return confirmed.json()

            added = await confirm("zenas.watch.add", {"source": source, "topic": "customs"})
            assert added["status"] == "confirmed", added
            watch_id = added["result"]["watch"]["id"]
            for tool, enabled in [("zenas.watch.list", True), ("zenas.watch.disable", False),
                                  ("zenas.watch.list", False), ("zenas.watch.enable", True),
                                  ("zenas.watch.list", True)]:
                card = await confirm(tool, {} if tool.endswith("list") else {"watch_id": watch_id})
                assert card["status"] == "confirmed", card
                if tool.endswith("list"):
                    assert card["result"]["watches"][0]["enabled"] is enabled
                    receipt = await pool.fetchrow("SELECT * FROM tank.ledger WHERE id=$1", __import__('uuid').UUID(card["result"]["ledger_id"]))
                    assert receipt["tenant_id"] == TENANT and receipt["agent"] == "zenas"
                    assert receipt["cost_usd"] == 0 and receipt["model"] == "code"
            founder = command.Actor(OTHER, "founder", True)
            status, result = await command.dispatch_action(command.PlannedAction(tool="zenas.watch.list", arguments={}), founder)
            assert status == 200 and result["watches"] == []
            for tool in ("zenas.watch.enable", "zenas.watch.disable"):
                status, result = await command.dispatch_action(command.PlannedAction(tool=tool, arguments={"watch_id": watch_id}), founder)
                assert status == 404 and result == {"detail": "Not in scope."}
            criminal = await confirm("zenas.watch.add", {"source": source, "topic": "criminal sentencing"})
            assert criminal["status"] == "failed"
            assert criminal["error"] == REFUSED
            if region == "za":
                refused = await confirm("zenas.watch.add", {"source": "federal-register", "topic": "customs"})
                assert refused["status"] == "failed"
                assert refused["error"] == "Not in scope."
            assert all(payload["tenant_id"] in {str(TENANT), str(OTHER)} for _, payload in calls)


@pytest.mark.asyncio
async def test_review_proxy_and_removed_paths(monkeypatch):
    from agents.zenas import app as room_module
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    monkeypatch.setattr(tank_main, "TANK_API_KEY", "offline-workspace")
    calls = []

    def review(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(review)) as room:
        monkeypatch.setattr(tank_main, "_http", room)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tank_main.app), base_url="http://tank",
                                     headers={"X-Tank-Key": "offline-workspace"}) as tank:
            response = await tank.post("/v1/zenas/contract-reviews", json={"home_region": "us-east"})
            assert response.status_code == 200 and calls == ["/contract-reviews"]
            removed = [("GET", "moot/fixtures"), ("GET", "moot/problems/offline/counsel/appellant"),
                       ("POST", "moot/problems/offline/edit"), ("POST", "moot/problems/offline/lock"),
                       ("POST", "moot/matches/ai-ai"), ("POST", "moot/matches/student"),
                       ("POST", f"moot/matches/{WATCH}/publish"), ("GET", "unlisted")]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room_module.app), base_url="http://room") as direct:
                for method, path in removed:
                    response = await tank.request(method, "/v1/zenas/" + path, params={"home_region": "us-east"})
                    assert response.status_code == 404, response.text
                    assert (await direct.request(method, "/" + path)).status_code == 404
            assert calls == ["/contract-reviews"]
    assert tank_main._ZENAS_PATH.fullmatch("matches")
    assert tank_main._ZENAS_PATH.fullmatch("moot/published")
    for action in ("", "/edit", "/lock", "/start", "/handover", "/issue", "/turn", "/ai", "/abandon", "/publish", "/pair", "/fairness"):
        assert tank_main._ZENAS_PATH.fullmatch("moot/hearings/" + WATCH + action)
