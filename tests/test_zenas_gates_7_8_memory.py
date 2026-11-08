"""Gates 7+8 offline proof: product → memory → brain → Tank route → receipt → memory.

All cost and token figures here are FAKE FIXTURE values (a fixed price per
Tank route call and fixed input/output tokens). They are not billing, not
provider spend and not a live measurement. No network or paid call is made.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx
import pytest
import yaml

from chassis.memory import MemoryClient
from agents.zenas import gates_memory
from agents.zenas.zenas_brain import ask_brain_tasks, brain_enabled, brain_then_route

ROOT = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
OTHER_USER = "44444444-4444-4444-8444-444444444444"
FIRM = TENANT
ATTORNEY = USER
CLIENT = "55555555-5555-4555-8555-555555555555"
OTHER_CLIENT = "66666666-6666-4666-8666-666666666666"
MATTER = "77777777-7777-4777-8777-777777777777"
OTHER_MATTER = "88888888-8888-4888-8888-888888888888"
TASK = "zenas.apply"
ANSWER = {"finding": "met", "support": "section 5", "missing": ""}
CORRECTED = {"finding": "not_met", "support": "section 5 as corrected", "missing": ""}
# Fixed fake fixture figures per Tank route call; never derived from call counts.
FAKE_COST_USD = 0.0025
FAKE_TOKENS_IN = 1200
FAKE_TOKENS_OUT = 300


class FakeHermes:
    """Tenant-scoped records store behind the real chassis MemoryClient."""

    def __init__(self):
        self.rows = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if request.method == "POST" and url.path == "/memories":
            body = json.loads(request.content)
            row = {
                "id": str(uuid.uuid4()),
                "tenant_id": body["tenant_id"],
                "text": body["text"],
                "claim": body["text"],
                "kind": body["kind"],
                "meta": copy.deepcopy(body.get("meta") or {}),
            }
            self.rows.append(row)
            return httpx.Response(201, json=row)
        if request.method == "GET" and url.path == "/records":
            offset, limit = int(query["offset"]), int(query["limit"])
            mine = [r for r in self.rows if r["tenant_id"] == query["tenant_id"]]
            return httpx.Response(200, json={"memories": copy.deepcopy(mine[offset : offset + limit])})
        if request.method == "DELETE" and url.path.startswith("/memories/"):
            ident = url.path.rsplit("/", 1)[1]
            self.rows = [r for r in self.rows if not (r["id"] == ident and r["tenant_id"] == query["tenant_id"])]
            return httpx.Response(204)
        if url.path == "/recall":
            raise AssertionError("exact repeats must not use embedding recall")
        return httpx.Response(404)


class FakeTank:
    def __init__(self, answer=ANSWER):
        self.calls = []
        self.answer = answer

    async def route(self, tenant, task, messages, **kwargs):
        assert task == TASK
        self.calls.append(tenant)
        return {
            "content": json.dumps(self.answer),
            "ledger_id": str(uuid.uuid4()),
            "cost_usd": FAKE_COST_USD,
            "tokens_in": FAKE_TOKENS_IN,
            "tokens_out": FAKE_TOKENS_OUT,
        }


class FakeHTTP:
    """Standby brain: /health ok, /tasks 404 so routing falls through to Tank."""

    async def get(self, url, params=None, **kwargs):
        class Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "room": "zenas"}

        return Resp()

    async def post(self, url, json=None, **kwargs):
        class Resp:
            status_code = 404

        return Resp()


@pytest.fixture
def hermes(monkeypatch):
    monkeypatch.setenv('ZENAS_MEMORY_ENABLED', 'true')
    return FakeHermes()


def client(hermes, http):
    return MemoryClient(base_url="http://placeholder-hermes", api_key="placeholder-room", agent="zenas", http=http)


def payload(question="Is notice required before cancellation?"):
    return {"question": question, "jurisdiction": "za"}


async def run_once(
    hermes,
    *,
    tenant=TENANT,
    user=USER,
    firm_id=FIRM,
    attorney_id=ATTORNEY,
    client_id=CLIENT,
    matter_id=MATTER,
    question="Is notice required before cancellation?",
):
    """One product run with a fresh Tank client: per-run calls, fake cost and tokens."""
    tank = FakeTank()
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        result = await gates_memory.remembered_task(
            client(hermes, http),
            tenant,
            user,
            TASK,
            payload(question),
            http=FakeHTTP(),
            tank=tank,
            firm_id=firm_id,
            attorney_id=attorney_id,
            client_id=client_id,
            matter_id=matter_id,
        )
    receipt = result["receipt"]
    assert len(tank.calls) == receipt["paid_calls"]
    return result, receipt, len(tank.calls)


def figures(receipt):
    return receipt["paid_calls"], receipt["cost_usd"], receipt["tokens_in"] + receipt["tokens_out"]


@pytest.mark.asyncio
async def test_request_routes_through_tank_records_receipt_and_remembers(hermes):
    result, receipt, calls = await run_once(hermes)
    assert calls == 1
    assert receipt["source"] == "tank" and receipt["memory_hit"] is False and len(receipt["ledger_ids"]) == 1
    assert figures(receipt) == (1, FAKE_COST_USD, FAKE_TOKENS_IN + FAKE_TOKENS_OUT)
    kinds = sorted(r["kind"] for r in hermes.rows)
    assert kinds == ["zenas.result"]
    saved = hermes.rows[0]
    assert saved["id"] == receipt["memory_id"] and saved["meta"]["user"] == USER
    assert saved["tenant_id"] == TENANT
    assert saved["meta"]["firm_id"] == FIRM and saved["meta"]["attorney_id"] == ATTORNEY
    assert saved["meta"]["client_id"] == CLIENT and saved["meta"]["matter_id"] == MATTER
    assert json.loads(saved["meta"]["result"]) == ANSWER
    assert saved["meta"]["receipt"]["ledger_ids"] == receipt["ledger_ids"]


@pytest.mark.asyncio
async def test_first_run_pays_once_and_exact_repeat_pays_nothing(hermes):
    _, first, first_calls = await run_once(hermes)
    _, repeat, repeat_calls = await run_once(hermes)
    assert (first_calls, figures(first)) == (1, (1, FAKE_COST_USD, FAKE_TOKENS_IN + FAKE_TOKENS_OUT))
    assert (repeat_calls, figures(repeat)) == (0, (0, 0.0, 0))
    assert repeat["source"] == "memory" and repeat["memory_hit"] is True and repeat["ledger_ids"] == []
    assert repeat.get("answered_from_memory") is True
    assert isinstance(repeat.get("would_have_cost"), dict)
    assert repeat["would_have_cost"]["cost_usd"] == FAKE_COST_USD
    assert repeat["would_have_cost"]["tokens_in"] == FAKE_TOKENS_IN
    assert repeat["would_have_cost"]["tokens_out"] == FAKE_TOKENS_OUT
    print(
        f"GATES78_FAKE_FIXTURE first paid_calls={first_calls} cost_usd={first['cost_usd']} "
        f"tokens_in={first['tokens_in']} tokens_out={first['tokens_out']}; "
        f"repeat paid_calls={repeat_calls} cost_usd={repeat['cost_usd']} "
        f"tokens_in={repeat['tokens_in']} tokens_out={repeat['tokens_out']} "
        f"(fake fixture, not billing)"
    )


@pytest.mark.asyncio
async def test_changed_input_misses_and_pays(hermes):
    await run_once(hermes)
    _, changed, calls = await run_once(hermes, question="Does the deposit earn interest?")
    assert calls == 1 and changed["memory_hit"] is False and figures(changed)[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other",
    [
        {"user": OTHER_USER, "attorney_id": OTHER_USER},
        {"tenant": OTHER_TENANT, "firm_id": OTHER_TENANT},
        {"client_id": OTHER_CLIENT},
        {"matter_id": OTHER_MATTER},
        {"firm_id": OTHER_TENANT},
        {"attorney_id": OTHER_USER},
    ],
)
async def test_second_user_tenant_firm_attorney_client_matter_never_reuse(hermes, other):
    await run_once(hermes)
    _, isolated, calls = await run_once(hermes, **other)
    assert calls == 1 and isolated["memory_hit"] is False
    _, own_repeat, own_calls = await run_once(hermes, **other)
    assert own_calls == 0 and own_repeat["memory_hit"] is True
    _, original, original_calls = await run_once(hermes)
    assert original_calls == 0 and original["memory_hit"] is True


@pytest.mark.asyncio
async def test_correction_overrides_stale_result_without_paid_call(hermes):
    await run_once(hermes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        gate = gates_memory.GateMemory(
            client(hermes, http),
            TENANT,
            USER,
            firm_id=FIRM,
            attorney_id=ATTORNEY,
            client_id=CLIENT,
            matter_id=MATTER,
        )
        await gate.correct(TASK, payload(), CORRECTED)
    assert not [r for r in hermes.rows if r["kind"] == "zenas.result"]
    tank = FakeTank()
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        outcome = await gates_memory.remembered_task(
            client(hermes, http),
            TENANT,
            USER,
            TASK,
            payload(),
            http=FakeHTTP(),
            tank=tank,
            firm_id=FIRM,
            attorney_id=ATTORNEY,
            client_id=CLIENT,
            matter_id=MATTER,
        )
    assert outcome["result"] == CORRECTED and tank.calls == [] and outcome["receipt"]["source"] == "correction"
    assert figures(outcome["receipt"]) == (0, 0.0, 0)


@pytest.mark.asyncio
async def test_memory_off_edit_and_forget_are_honoured(hermes):
    await run_once(hermes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        gate = gates_memory.GateMemory(
            client(hermes, http),
            TENANT,
            USER,
            firm_id=FIRM,
            attorney_id=ATTORNEY,
            client_id=CLIENT,
            matter_id=MATTER,
        )
        assert {r["kind"] for r in await gate.view()} == {"zenas.result"}
        await gate.set_enabled(False)
    before = len(hermes.rows)
    _, off, off_calls = await run_once(hermes)
    assert off_calls == 1 and off["memory_hit"] is False and off["memory_id"] is None
    assert len(hermes.rows) == before
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        gate = gates_memory.GateMemory(
            client(hermes, http),
            TENANT,
            USER,
            firm_id=FIRM,
            attorney_id=ATTORNEY,
            client_id=CLIENT,
            matter_id=MATTER,
        )
        await gate.set_enabled(True)
        rows = await gate.view()
        result_row = next(r for r in rows if r["kind"] == "zenas.result")
        await gate.edit(result_row["id"], CORRECTED)
        assert await gate.forget() >= 1
        assert all(r["kind"] == "zenas.memory_setting" for r in await gate.view())
    _, forgotten, calls = await run_once(hermes)
    assert calls == 1 and forgotten["memory_hit"] is False


@pytest.mark.asyncio
async def test_approved_worked_law_recalled_before_paid_path(hermes):
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        gate = gates_memory.GateMemory(
            client(hermes, http),
            TENANT,
            USER,
            firm_id=FIRM,
            attorney_id=ATTORNEY,
            client_id=CLIENT,
            matter_id=MATTER,
        )
        await gate.remember_worked_law("notice", {"rule": "notice required", "approved": True})
    result, receipt, calls = await run_once(hermes)
    assert calls == 1 and result["memory"]["worked_law"] == 1 and receipt["memory_hit"] is False


@pytest.mark.asyncio
async def test_switch_off_keeps_the_previous_path(hermes, monkeypatch):
    monkeypatch.setenv("ZENAS_BRAIN_ENABLED", "false")
    tank = FakeTank()
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        outcome = await gates_memory.remembered_task(
            client(hermes, http),
            TENANT,
            USER,
            TASK,
            payload(),
            http=FakeHTTP(),
            tank=tank,
            firm_id=FIRM,
            attorney_id=ATTORNEY,
            client_id=CLIENT,
            matter_id=MATTER,
        )
    # Brain off (main default): memory still brackets; first run pays and remembers.
    assert outcome["brain"]["enabled"] is False
    assert outcome["receipt"]["paid_calls"] == 1 and len(tank.calls) == 1
    assert outcome["answered_from_memory"] is False
    assert hermes.rows  # remembered after paid run


def test_remembered_kinds_defined_and_brain_defaults_off_like_main(monkeypatch):
    required = {
        "zenas.worked_law", "zenas.result", "zenas.correction", "zenas.outcome",
        "zenas.receipt", "zenas.memory_setting", "zenas.fact", "zenas.party",
        "zenas.document", "zenas.position", "zenas.draft", "zenas.clause",
        "zenas.house_style", "zenas.jurisdiction", "zenas.deadline",
        "zenas.student_context", "zenas.visitor_context",
    }
    assert set(gates_memory.REMEMBERS) == required
    assert gates_memory.TENANT_WIDE == frozenset()
    monkeypatch.delenv("ZENAS_BRAIN_ENABLED", raising=False)
    monkeypatch.delenv("ZENAS_MEMORY_ENABLED", raising=False)
    assert gates_memory.memory_enabled_env() is False
    assert gates_memory.brain_enabled() is False
    assert brain_enabled() is False
    for name in ("docker-compose.yml", "docker-compose.za.yml"):
        box = yaml.safe_load((ROOT / "agents/zenas" / name).read_text())
        room = box["services"]["agent-zenas"]["environment"]
        assert room["ZENAS_BRAIN_ENABLED"] == "${ZENAS_BRAIN_ENABLED:-false}"
        assert "ZENAS_BRAIN_URL" not in room
        assert room["TANK_URL"] == "http://tank:8000"
        assert room["ZENAS_MEMORY_ENABLED"] == "${ZENAS_MEMORY_ENABLED:-false}"
        assert box["services"]["zenas-brain"]["profiles"] == ["brain"]
        assert box["services"]["zenas-sandbox"]["profiles"] == ["brain"]
        assert box["services"]["zenas-brain"]["environment"]["TANK_URL"] == "http://tank:8000"
    docker = (ROOT / "agents/zenas/Dockerfile").read_text()
    assert "COPY zenas/gates_memory.py /app/agents/zenas/gates_memory.py" in docker
    assert "COPY zenas/zenas_brain.py /app/agents/zenas/zenas_brain.py" in docker
    assert "COPY zenas/memory_api.py /app/agents/zenas/memory_api.py" in docker
    assert "COPY zenas/request_signing.py /app/agents/zenas/request_signing.py" in docker


def test_gate_memory_uses_the_chassis_client_not_a_second_store():
    source = (ROOT / "agents/zenas/gates_memory.py").read_text()
    for banned in ("qdrant", "sqlite", "asyncpg", "redis", "open(", "httpx"):
        assert banned not in source


@pytest.mark.asyncio
async def test_brain_then_route_free_first_then_tasks_then_tank(monkeypatch):
    from chassis.tank import TankClient
    from chassis.user_assertion import SignedUser, verify_assertion
    monkeypatch.setenv("ZENAS_BRAIN_ENABLED", "true")
    monkeypatch.setenv("TANK_REGION", "us-east")
    actor = SignedUser(TENANT, USER, "member")
    events, nonces = [], set()

    async def claim(tenant, nonce, expires):
        assert tenant == TENANT
        if nonce in nonces:
            return False
        nonces.add(nonce)
        return True

    async def standby(request):
        assert request.method == "POST" and request.url.path == "/v1/agents/zenas/delegate"
        assert request.url.host == "tank"
        verified = await verify_assertion(request.headers['X-Tervory-User-Assertion'],
            key='placeholder-zenas-room-key', product='zenas', tenant_id=TENANT,
            method=request.method, target=request.url.raw_path.decode(), body=request.content,
            consume_nonce=claim)
        assert verified == actor
        payload = json.loads(request.content)
        assert payload['task_key'] == TASK and payload['input']['messages'] == [{'role':'user','content':'hi'}]
        events.append('delegate')
        return httpx.Response(404)

    class Tank(TankClient):
        async def route(self, tenant_id, task_type, messages):
            assert tenant_id == TENANT and task_type == TASK
            events.append('route')
            return {"content":"draft-from-tank", "ledger_id":"led-1", "cost_usd":FAKE_COST_USD,
                    "tokens_in":FAKE_TOKENS_IN, "tokens_out":FAKE_TOKENS_OUT, "model":"fake"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(standby)) as http:
        tank = Tank('http://tank:8000', 'placeholder-zenas-room-key', 'zenas', http)
        routed = await brain_then_route(http, tank, tenant_id=TENANT, task_type=TASK,
            messages=[{"role":"user","content":"hi"}], actor=actor, run_id=str(uuid.uuid4()))
    assert events == ['delegate', 'route']
    assert routed["content"] == "draft-from-tank"
    assert routed["brain"]["free_first"] is True and routed["brain"]["code_first"] is True
    assert routed["brain"]["tasks"]["status"] == "standby"
    assert routed["brain"]["tasks"]["gap_filled"] is False


@pytest.mark.asyncio
async def test_ask_brain_tasks_standby_404():
    from chassis.tank import TankClient
    from chassis.user_assertion import SignedUser
    calls = []
    def standby(request):
        calls.append(request)
        assert request.method == 'POST' and request.url.path == '/v1/agents/zenas/delegate'
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(standby)) as http:
        tank = TankClient('http://tank:8000', 'placeholder-zenas-room-key', 'zenas', http)
        gap = await ask_brain_tasks(tank, actor=SignedUser(TENANT, USER, 'member'),
            run_id=str(uuid.uuid4()), tenant_id=TENANT, task_type=TASK, messages=[])
    assert len(calls) == 1
    assert gap["status"] == "standby" and gap["gap_filled"] is False


def test_no_secrets_in_memory_kinds():
    assert "secret" not in json.dumps(gates_memory.REMEMBERS).lower()
    assert "token" not in json.dumps(list(gates_memory.REMEMBERS)).lower()


# --- Real HTTP memory controls: VIEW / EDIT / FORGET / SWITCH OFF ---
# Controls now execute the real product routes, shared verifier and SQL nonce store.
import pytest_asyncio
from gates78.conftest import gate78_memory as gate78_memory, gate78_database as gate78_database, sql_memory as sql_memory


def _fixture_key():
    return 'placeholder-gate78-product-key'


async def _snapshot(stack):
    schema = stack.records.schema('zenas')
    return await stack.records.pool.fetch(f'SELECT to_jsonb(r) AS row FROM {schema}.memory_records r ORDER BY id')


def _sign_headers(key, *, method, path, tenant, user, body=b''):
    from chassis.user_assertion import HEADER, SignedUser, sign_assertion
    return {'X-Tank-Key': key, HEADER: sign_assertion('zenas', SignedUser(str(tenant), str(user), 'member'),
              method, path, body, key=key)}


async def _signed_request(client, key, *, method, path, tenant, user, json_body=None, params=None):
    body = json.dumps(json_body, separators=(',', ':'), sort_keys=True).encode() if json_body is not None else b''
    target = httpx.Request(method, 'http://room'+path, params=params).url.raw_path.decode()
    headers = _sign_headers(key, method=method, path=target, tenant=tenant, user=user, body=body)
    return await client.request(method, path, params=params, content=body,
                                headers={**headers, 'Content-Type':'application/json'})


@pytest_asyncio.fixture
async def room_app(gate78_memory, monkeypatch):
    stack = await gate78_memory('zenas')
    monkeypatch.setenv('TANK_KEY', stack.key)
    monkeypatch.setenv('TANK_REGION', 'us-east')
    monkeypatch.setenv('ZENAS_MEMORY_ENABLED', 'true')
    from agents.zenas import app as room
    from agents.zenas.core.store import PgStore
    await PgStore(stack.records.pool).apply_schema()
    room.app.state.configuration = {'ok': True}
    room.app.state.pool = stack.records.pool
    room.app.state.memory = MemoryClient('http://hermes',stack.key,'zenas',stack.http)
    yield room, stack
    await stack.records.pool.execute('DROP SCHEMA IF EXISTS agent_zenas CASCADE')


async def _seed(stack, tenant, user):
    return await stack.client(user).remember(str(tenant), 'placeholder private worked law', 'zenas.result',
        'placeholder-regression', meta={'user':str(user),'user_id':str(user),'task_key':'/answers',
         'key':'placeholder-input','result':{'ok':True,'memo':'placeholder owner answer'},'receipt':{'cost_usd':.0025}})


@pytest.mark.asyncio
@pytest.mark.parametrize('control,other_kind', [(c,k) for c in ('view','edit','forget','switch') for k in ('user','tenant')])
async def test_memory_controls_refuse_foreign_scope_unchanged(control, other_kind, room_app):
    room, stack = room_app
    saved = await _seed(stack, stack.tenant, USER)
    before = await _snapshot(stack)
    tenant, user = (stack.tenant, OTHER_USER) if other_kind == 'user' else (stack.other_tenant, USER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app),base_url='http://room') as client:
        if control == 'view':
            response = await _signed_request(client,stack.key,method='GET',path='/memory/records',tenant=tenant,user=user,
                                               params={'tenant_id':stack.tenant,'user_id':USER})
        else:
            values = {'tenant_id':stack.tenant,'user_id':USER}
            if control == 'switch': values['enabled'] = False
            else: values['memory_id'] = saved['id']
            if control == 'edit': values['value'] = 'placeholder denied'
            response = await _signed_request(client,stack.key,method='POST',path='/memory/'+control,
                                               tenant=tenant,user=user,json_body=values)
        assert response.status_code in {401,403}
    assert await _snapshot(stack) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('flaw',['unsigned','wrong_sig','other_user'])
async def test_memory_controls_refuse_bad_signatures_unchanged(flaw, room_app):
    room, stack = room_app
    saved = await _seed(stack,stack.tenant,USER)
    before = await _snapshot(stack)
    body = json.dumps({'tenant_id':stack.tenant,'user_id':USER,'memory_id':saved['id'],'value':'placeholder denied'}).encode()
    headers = _sign_headers(stack.key if flaw != 'wrong_sig' else 'placeholder-wrong-key', method='POST',
        path='/memory/edit',tenant=stack.tenant,user=OTHER_USER if flaw=='other_user' else USER,body=body)
    headers['X-Tank-Key'] = stack.key
    if flaw == 'unsigned': headers.pop('X-Tervory-User-Assertion')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app),base_url='http://room') as client:
        response=await client.post('/memory/edit',content=body,headers={**headers,'Content-Type':'application/json'})
    assert response.status_code in {401,403}
    assert await _snapshot(stack)==before


@pytest.mark.asyncio
async def test_memory_controls_own_positive_view_edit_forget_switch(room_app):
    room,stack=room_app
    saved=await _seed(stack,stack.tenant,USER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app),base_url='http://room') as client:
        async def ask(method,path,body=None):
            return await _signed_request(client,stack.key,method=method,path=path,tenant=stack.tenant,user=USER,
                json_body=body,params={'tenant_id':stack.tenant} if method=='GET' else None)
        viewed=await ask('GET','/memory/records')
        assert viewed.status_code==200 and saved['id'] in viewed.text
        got=await ask('GET','/memory/records/'+saved['id']);assert got.status_code==200
        edited=await ask('POST','/memory/edit',{'tenant_id':stack.tenant,'memory_id':saved['id'],'value':CORRECTED})
        assert edited.status_code==200,edited.text
        correction=edited.json()['memory']['id']
        got=await ask('GET','/memory/records/'+correction);assert got.json()['meta']['result']==CORRECTED
        forgot=await ask('POST','/memory/forget',{'tenant_id':stack.tenant,'memory_id':correction});assert forgot.status_code==200
        off=await ask('POST','/memory/switch',{'tenant_id':stack.tenant,'enabled':False});assert off.status_code==200
        viewed=await ask('GET','/memory/records');assert viewed.json()['memory_enabled'] is False and not viewed.json()['memories']


@pytest.mark.asyncio
@pytest.mark.parametrize('headers',[{}, {'x-tank-key':'placeholder-gate78-product-key'},
 {'x-tank-key':'placeholder-forged-key','X-Tervory-User':USER,'X-Tervory-Tenant':TENANT},
 {'x-tank-key':'placeholder-gate78-product-key','X-Tervory-User':USER,'X-Tervory-Tenant':TENANT}])
async def test_memory_controls_refuse_missing_or_forged_actor(headers, room_app):
    room,stack=room_app
    await _seed(stack,stack.tenant,USER);before=await _snapshot(stack)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=room.app),base_url='http://room') as client:
        response=await client.get('/memory/records',params={'tenant_id':stack.tenant},headers=headers)
    assert response.status_code==401
    assert await _snapshot(stack)==before


def test_request_signing_binds_tenant_user_not_room_key_alone():
    from chassis.user_assertion import HEADER
    good=_sign_headers('placeholder-key',method='GET',path='/memory/records',tenant=TENANT,user=USER)
    assert HEADER in good and 'X-Lifecycle-Signature' not in good
    assert good[HEADER] != _sign_headers('placeholder-key',method='GET',path='/memory/records',tenant=TENANT,user=OTHER_USER)[HEADER]


@pytest.mark.asyncio
async def test_memory_service_outage_answers_without_memory(hermes):
    """Hermes outage: answer without memory; receipt marks memory unavailable; no stored repeat."""
    class BrokenRecords:
        async def records(self, *a, **k):
            raise RuntimeError("hermes unreachable")
        async def remember(self, *a, **k):
            raise RuntimeError("hermes unreachable")
        async def forget(self, *a, **k):
            raise RuntimeError("hermes unreachable")
        async def recall(self, *a, **k):
            raise RuntimeError("hermes unreachable")

    tank = FakeTank()
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)):
        # Replace memory client with one whose records always fail.
        broken = BrokenRecords()
        result = await gates_memory.remembered_task(
            broken, TENANT, USER, TASK, payload(),
            http=FakeHTTP(), tank=tank,
            firm_id=FIRM, attorney_id=ATTORNEY, client_id=CLIENT, matter_id=MATTER,
        )
    assert result["answered_from_memory"] is False
    assert result["receipt"]["memory_status"] == "memory unavailable"
    assert result["receipt"]["paid_calls"] == 1
    assert hermes.rows == []  # never stored a repeat on outage


@pytest.mark.asyncio
async def test_invalid_tenant_or_user_refused():
    hermes = FakeHermes()
    async with httpx.AsyncClient(transport=httpx.MockTransport(hermes.handler)) as http:
        with pytest.raises(ValueError):
            await gates_memory.remembered_task(
                client(hermes, http), "not-a-uuid", USER, TASK, payload(),
                http=FakeHTTP(), tank=FakeTank(),
            )
        with pytest.raises(ValueError):
            await gates_memory.remembered_task(
                client(hermes, http), TENANT, "", TASK, payload(),
                http=FakeHTTP(), tank=FakeTank(),
            )


@pytest.mark.asyncio
async def test_brain_off_still_serves_exact_repeat_from_memory(hermes, monkeypatch):
    """Brain switch defaults off (main behaviour); memory still brackets paid work."""
    monkeypatch.setenv("ZENAS_BRAIN_ENABLED", "false")
    _, first, first_calls = await run_once(hermes)
    _, repeat, repeat_calls = await run_once(hermes)
    assert first_calls == 1 and figures(first) == (1, FAKE_COST_USD, FAKE_TOKENS_IN + FAKE_TOKENS_OUT)
    assert repeat_calls == 0 and figures(repeat) == (0, 0.0, 0)
    assert repeat.get("answered_from_memory") is True
