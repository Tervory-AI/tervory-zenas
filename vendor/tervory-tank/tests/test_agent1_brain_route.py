"""AGENT-1 protocol and accounting tests; all gateway traffic is mocked."""
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import agent_skills as route


class Pool:
    def __init__(self):
        self.rows = []
        self.paused = False
        self.unavailable = False

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchval(self, sql, *args):
        if "tank.standard_model_for" in sql:
            return "baseline"
        assert "tank.controls" in sql
        if self.unavailable:
            raise RuntimeError("fixture control unavailable")
        return self.paused

    async def execute(self, sql, *args):
        self.rows.append((sql, args))


@pytest.fixture
def setup(monkeypatch):
    import paid_steps
    async def checkpoint(key, invoke):
        return await invoke()
    async def configured_admission(*args, **kwargs):
        await route.controls.refuse_paid(task=args[1], path="paid_steps")
        return None
    monkeypatch.setattr(paid_steps, "checkpoint", checkpoint)
    monkeypatch.setattr(paid_steps, "model_admission", configured_admission)
    key = uuid.uuid4().hex
    monkeypatch.setenv("TANK_KEY_BASE", key)
    pool = Pool()
    monkeypatch.setattr(route.controls, "db", lambda: pool)

    async def policy(task, tenant):
        assert task == "agent.base.think"
        return {"model": "serving", "baseline_model": "baseline", "rung": 1, "priority": "standard", "max_tokens_out": 100}

    async def roster():
        return [{"model_name": name, "litellm_params": {"model": "openai/gpt-fixture"}, "model_info": {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}} for name in ("baseline", "serving")]

    tank = SimpleNamespace(pool=lambda: pool, load_policy=policy, _roster_models=roster,
                           box_region=lambda: "us-east", model_call_refusal=lambda *a, **kw: None, graduate=SimpleNamespace(router_cannot_serve=lambda m: False),
                           LITELLM_URL="http://gateway.invalid", LITELLM_MASTER_KEY=uuid.uuid4().hex)
    app = FastAPI()
    app.include_router(route.build_router(tank))
    return app, tank, pool, key


@pytest.mark.asyncio
async def test_tool_calls_policy_room_key_and_durable_receipt(setup, monkeypatch):
    app, tank, pool, key = setup
    original = httpx.AsyncClient
    sent = []

    def gateway(request):
        sent.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer " + tank.LITELLM_MASTER_KEY
        return httpx.Response(200, headers={"x-litellm-response-cost": "0.01", "x-litellm-model-group": "serving"}, json={
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "pipeline", "arguments": "{}"}}]},
                "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    monkeypatch.setattr(route.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(gateway)))
    tenant = str(uuid.uuid4())
    async with original(transport=httpx.ASGITransport(app=app), base_url="http://tank") as client:
        response = await client.post("/v1/agents/base/chat/completions", headers={"Authorization": "Bearer " + key, "X-Tenant-ID": tenant},
            json={"model": "cannot-override-policy", "messages": [{"role": "user", "content": "Plan"}], "max_tokens": 500,
                  "tools": [{"type": "function", "function": {"name": "pipeline", "parameters": {"type": "object"}}}]})
    assert response.status_code == 200
    assert sent[0]["model"] == "serving" and sent[0]["max_tokens"] == 100
    assert sent[0]["tools"][0]["function"]["name"] == "pipeline"
    data = response.json()
    assert data["choices"][0]["message"]["tool_calls"][0]["id"] == "call1"
    assert data["receipt"]["baseline_cost_usd"] == 0.02
    assert data["receipt"]["tenant_id"] == tenant
    assert len(pool.rows) == 3 and pool.rows[0][1][1] == uuid.UUID(tenant)
    assert "tank.transcripts" in pool.rows[1][0]
    assert json.loads(pool.rows[1][1][2])[0]["content"] == "Plan"


@pytest.mark.asyncio
@pytest.mark.parametrize("room,authorized,region,expected", [
    ("radix", True, "us-east", 401), ("base", False, "us-east", 401), ("base", True, "za", 403),
])
async def test_rejects_other_room_key_and_region_before_work(setup, room, authorized, region, expected):
    app, _, pool, key = setup
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tank") as client:
        result = await client.post(f"/v1/agents/{room}/chat/completions", json={"messages": [{"role": "user", "content": "Plan"}]},
            headers={"Authorization": "Bearer " + (key if authorized else uuid.uuid4().hex),
                     "X-Tenant-ID": str(uuid.uuid4()), "X-Home-Region": region})
    assert result.status_code == expected
    assert pool.rows == []


def test_stream_keeps_tools_and_receipt():
    result = {"id": "chatcmpl-one", "created": 1, "model": "policy", "usage": {}, "receipt": {"ledger_id": "one"},
              "choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "call", "function": {"name": "pipeline", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
    chunks = list(route.stream_completion(result))
    assert '"index": 0' in chunks[0]
    assert '"receipt"' in chunks[-2]
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_founder_pause_blocks_thinking_before_gateway(setup, unavailable):
    app, _, pool, key = setup
    pool.paused = True
    pool.unavailable = unavailable
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tank") as client:
        result = await client.post("/v1/agents/base/chat/completions",
            headers={"X-Tank-Key": key, "X-Tenant-ID": str(uuid.uuid4())},
            json={"messages": [{"role": "user", "content": "Plan"}]})
    assert result.status_code == 503
    assert len(pool.rows) == 1 and "tank.control_log" in pool.rows[0][0]
    assert pool.rows[0][1][-2:] == ("agent.base.think", "paid_steps")


@pytest.mark.asyncio
async def test_provider_allowlist_refuses_before_gateway_or_ledger(setup, monkeypatch):
    app, tank, pool, key = setup
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("TANK_API_KEY", uuid.uuid4().hex)
    monkeypatch.setenv("LITELLM_MASTER_KEY", uuid.uuid4().hex)
    import main
    tenant = str(uuid.uuid4())
    monkeypatch.setenv("TANK_PROVIDER_ALLOW", json.dumps({tenant: ["another-model"]}))
    tank.model_call_refusal = main.model_call_refusal
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tank") as client:
        response = await client.post("/v1/agents/base/chat/completions", headers={"X-Tank-Key": key, "X-Tenant-ID": tenant},
            json={"messages": [{"role": "user", "content": "Public task"}]})
    assert response.status_code == 403
    assert pool.rows == []


@pytest.mark.asyncio
async def test_missing_cost_estimated_and_sdk_fields_removed(setup, monkeypatch):
    app, tank, pool, key = setup
    original = httpx.AsyncClient
    sent = []
    def gateway(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    monkeypatch.setattr(route.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(gateway)))
    async with original(transport=httpx.ASGITransport(app=app), base_url="http://tank") as client:
        response = await client.post("/v1/agents/base/chat/completions", headers={"X-Tank-Key": key, "X-Tenant-ID": str(uuid.uuid4())},
            json={"reasoning_effort": "high", "messages": [{"role": "user", "content": "Plan", "metadata": {"route": "elsewhere"}, "name": "untrusted"}]})
    assert response.status_code == 200
    assert sent[0]["messages"] == [{"role": "user", "content": "Plan"}]
    assert "reasoning_effort" not in sent[0]
    assert response.json()["receipt"]["cost_usd"] == pytest.approx(0.02)
    assert response.json()["receipt"]["cost_status"] == "estimated"
    assert pool.rows[0][1][8] == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_input_budget_prevents_billed_call(setup):
    app, _, pool, key = setup
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tank") as client:
        response = await client.post("/v1/agents/base/chat/completions", headers={"X-Tank-Key": key, "X-Tenant-ID": str(uuid.uuid4())},
            json={"messages": [{"role": "user", "content": "x!y?z@ " * 40000}]})
    assert response.status_code == 413 and pool.rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reported",[None,"0.03"])
async def test_malformed_billed_reply_retains_reported_or_bounded_cost(setup,monkeypatch,reported):
    app,_,pool,key=setup
    original=httpx.AsyncClient
    def gateway(request):
        return httpx.Response(200,headers={} if reported is None else {'x-litellm-response-cost':reported},
            json={'choices':[{'message':{'role':'assistant','content':'incomplete'}}]})
    monkeypatch.setattr(route.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(gateway)))
    async with original(transport=httpx.ASGITransport(app=app),base_url='http://tank') as client:
        result=await client.post('/v1/agents/base/chat/completions',headers={'X-Tank-Key':key,'X-Tenant-ID':str(uuid.uuid4())},
            json={'messages':[{'role':'user','content':'Public task'}]})
    assert result.status_code==502 and pool.rows[0][1][8]>0
    receipt=result.json()['detail']['receipt']
    assert receipt['cost_status']==('reported' if reported else 'estimated_upper_bound')
    if reported: assert receipt['cost_usd']==pytest.approx(.03)


@pytest.mark.asyncio
async def test_read_timeout_after_send_records_unknown_upper_bound(setup, monkeypatch):
    app, _, pool, key = setup
    original = httpx.AsyncClient
    def gateway(request):
        raise httpx.ReadTimeout("fixture after send", request=request)
    monkeypatch.setattr(route.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(gateway)))
    async with original(transport=httpx.ASGITransport(app=app), base_url="http://tank") as client:
        response = await client.post('/v1/agents/base/chat/completions', headers={'X-Tank-Key':key, 'X-Tenant-ID':str(uuid.uuid4())},
            json={'messages':[{'role':'user','content':'Plan a public batch'}]})
    assert response.status_code == 502 and len(pool.rows) == 3
    receipt = response.json()['detail']['receipt']
    assert receipt['outcome'] == 'unknown' and receipt['cost_status'] == 'unknown_upper_bound'
    assert receipt['cost_usd'] > 0 and receipt['tokens_out'] == 100


def test_token_budget_counts_bpe_instead_of_utf8_bytes():
    payload = {'messages':[{'role':'user','content':'Public business evidence. ' * 3000}]}
    assert len(json.dumps(payload).encode()) > 48000
    assert route.input_tokens(payload) < 20000
