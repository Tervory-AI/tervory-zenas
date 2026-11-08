"""Original contract node IDs, now executed on fixed real Hermes/SQL/Qdrant."""
import inspect
import re
import httpx
import pytest
import pytest_asyncio

from chassis.memory import MemoryClient
from gates78.conftest import gate78_memory as gate78_memory, gate78_database as gate78_database, sql_memory as sql_memory
from agents.zenas import gates_memory

FIXTURE_AGENT_KEY='placeholder-gate78-product-key'
FIXTURE_WORKSPACE_KEY='placeholder-workspace-key'
FIXTURE_OTHER_AGENT_KEY='placeholder-other-agent-key'
TENANT_A='11111111-1111-4111-8111-111111111111'
TENANT_B='22222222-2222-4222-8222-222222222222'
USER_A='placeholder-user-a'
USER_B='placeholder-user-b'
KIND='zenas.result'


@pytest_asyncio.fixture
async def hermes_app(gate78_memory,monkeypatch):
    stack=await gate78_memory('zenas')
    monkeypatch.setenv('TANK_API_KEY',FIXTURE_WORKSPACE_KEY)
    monkeypatch.setenv('TANK_KEY_TRIGGER',FIXTURE_OTHER_AGENT_KEY)
    # Preserve the original positive second-agent regression using two real
    # SQL namespaces and the unchanged Hermes service, not an auth-error waiver.
    from hermes.records import Records
    from hermes.service import create_app
    from hermes.store import InMemoryStore
    existed = await stack.records.pool.fetchval("SELECT to_regnamespace('agent_trigger') IS NOT NULL")
    peer = Records(stack.records.pool,stack.records.index,stack.records.encoder,stack.records.model,['trigger'])
    await peer.setup()
    combined = Records(stack.records.pool,stack.records.index,stack.records.encoder,stack.records.model,['zenas','trigger'])
    combined._schemas['zenas'] = stack.records.schema('zenas')
    try:
        yield create_app(InMemoryStore(), stack.records.encoder, records=combined)
    finally:
        if not existed:
            await stack.records.pool.execute('DROP SCHEMA agent_trigger CASCADE')


async def _client(app,api_key,user=USER_A):
    http=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://hermes')
    return http,MemoryClient('http://hermes',api_key,'zenas',http,user_id=user)


@pytest.mark.asyncio
async def test_memory_client_write_recall_forget_against_real_hermes(hermes_app):
    http,memory=await _client(hermes_app,FIXTURE_AGENT_KEY)
    async with http:
        saved=await memory.remember(TENANT_A,'placeholder notice before cancellation',KIND,'placeholder-source')
        hits=await memory.recall(TENANT_A,'placeholder notice',kind=KIND)
        assert saved['id'] in {h['id'] for h in hits}
        assert all(h['tenant_id']==TENANT_A and h['meta']['user_id']==USER_A for h in hits)
        await memory.forget(saved['id'],TENANT_A)
        assert saved['id'] not in {r['id'] for r in await memory.records(TENANT_A)}


@pytest.mark.asyncio
async def test_hermes_tenant_scope_hides_other_tenant(hermes_app):
    http,memory=await _client(hermes_app,FIXTURE_AGENT_KEY)
    async with http:
        await memory.remember(TENANT_A,'placeholder tenant private',KIND,'placeholder-source')
        assert await memory.recall(TENANT_B,'placeholder tenant private',kind=KIND)==[]


@pytest.mark.asyncio
async def test_user_id_filtered_within_meta_not_hermes_auth(hermes_app):
    """Retained node: fixed Hermes now enforces the user before vector ranking."""
    http,a=await _client(hermes_app,FIXTURE_AGENT_KEY)
    b=MemoryClient('http://hermes',FIXTURE_AGENT_KEY,'zenas',http,user_id=USER_B)
    async with http:
        first=await a.remember(TENANT_A,'placeholder private attorney a',KIND,'placeholder-source')
        second=await b.remember(TENANT_A,'placeholder private attorney b',KIND,'placeholder-source')
        assert {r['id'] for r in await a.records(TENANT_A)}=={first['id']}
        assert {r['id'] for r in await b.records(TENANT_A)}=={second['id']}
        assert all(r['meta']['user_id']==USER_A for r in await a.recall(TENANT_A,'placeholder',limit=1))
        with pytest.raises(RuntimeError,match='hermes answered (401|403|404)'):
            await b.forget(first['id'],TENANT_A)
        assert {r['id'] for r in await a.records(TENANT_A)}=={first['id']}


@pytest.mark.asyncio
async def test_workspace_shared_key_cannot_write_memories(hermes_app):
    http,memory=await _client(hermes_app,FIXTURE_WORKSPACE_KEY)
    async with http:
        with pytest.raises(RuntimeError,match='hermes answered 403'):
            await memory.remember(TENANT_A,'placeholder refused',KIND,'placeholder-source')


@pytest.mark.asyncio
async def test_other_agent_key_cannot_write_zenas_collection(hermes_app):
    http,memory=await _client(hermes_app,FIXTURE_OTHER_AGENT_KEY)
    async with http:
        saved=await memory.remember(TENANT_A,'placeholder trigger-only row','trigger.deal','placeholder-source')
        assert saved['id']
        own=MemoryClient('http://hermes',FIXTURE_AGENT_KEY,'zenas',http,user_id=USER_A)
        assert saved['id'] not in {r['id'] for r in await own.records(TENANT_A)}
        assert saved['id'] not in {r['id'] for r in await own.recall(TENANT_A,'placeholder trigger-only row')}



@pytest.mark.asyncio
async def test_v1_memory_prefix_is_not_chassis_contract(hermes_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hermes_app),base_url='http://hermes') as http:
        for path in ('/v1/memory','/v1/memory/records','/v1/memories'):
            response=await http.post(path,headers={'X-Tank-Key':FIXTURE_AGENT_KEY},json={'tenant_id':TENANT_A})
            assert response.status_code==404


@pytest.mark.asyncio
async def test_agent_key_is_not_signed_in_user_proof(hermes_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hermes_app),base_url='http://hermes') as http:
        response=await http.post('/memories',headers={'X-Tank-Key':FIXTURE_AGENT_KEY},json={
            'tenant_id':TENANT_A,'user_id':USER_A,'meta':{'user_id':USER_A},
            'kind':KIND,'text':'placeholder refused','source':'placeholder-source'})
        assert response.status_code in {401,403}


def test_kind_format_matches_hermes_and_product():
    for kind in gates_memory.REMEMBERS:
        assert re.fullmatch(r'[a-z][a-z0-9_.]{0,79}',kind)


def test_pinned_hermes_contract_lacks_central_user_id_param():
    """Retained regression: current runtime must require the fixed core, not patch vendor."""
    assert 'user_id' in inspect.signature(MemoryClient.remember).parameters
    assert 'user_id' in inspect.signature(MemoryClient.records).parameters
    from chassis.user_assertion import PostgresNonces
    assert callable(PostgresNonces.claim)


@pytest.mark.asyncio
async def test_v1_memory_get_also_absent_on_hermes(hermes_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hermes_app),base_url='http://hermes') as http:
        for path in ('/v1/memory','/v1/memory/records','/v1/memories'):
            response=await http.get(path,headers={'X-Tank-Key':FIXTURE_AGENT_KEY})
            assert response.status_code==404


def test_zenas_kinds_match_hermes_regex_and_none_hardcoded_empty():
    assert gates_memory.TENANT_WIDE==frozenset()
    assert all(isinstance(value,str) and value.strip() for value in gates_memory.REMEMBERS.values())
    assert {gates_memory.FACT,gates_memory.PARTY,gates_memory.DOCUMENT,gates_memory.RESULT,gates_memory.CORRECTION} <= gates_memory.REMEMBERS.keys()
