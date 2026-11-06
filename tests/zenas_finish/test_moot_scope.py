"""The real non-bypass database role must see only the authenticated Moot tenant."""
from uuid import UUID,uuid4
import httpx
import pytest
from fastapi import FastAPI

from chassis.tenant_db import TenantPool,TenantContextMiddleware,tenant_scope,bind_tenant_endpoints
from agents.zenas.core.moot.hearing_store import HearingStore
from agents.zenas.core.moot.hearing_api import register_routes
from agents.zenas.core.store import PgStore
from moot_hearing.test_hearing import OWNER,HUMANS,problem,hearing
from moot_hearing.test_api import headers


@pytest.mark.asyncio
async def test_moot_authentication_pins_the_non_bypass_tenant_before_any_query(database,monkeypatch):
    await PgStore(database).apply_schema()
    async with database.acquire() as conn:
        assert not await conn.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
        for table in ['tenants','moot_hearings','moot_hearing_requests','moot_hearing_events','moot_hearing_learning','moot_hearing_pairs']:
            await conn.execute(f'''ALTER TABLE agent_zenas.{table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE agent_zenas.{table} FORCE ROW LEVEL SECURITY;
                CREATE POLICY tenant_test ON agent_zenas.{table}
                USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)''')
    scoped=TenantPool(database);store=HearingStore(scoped)
    with tenant_scope(OWNER.tenant_id):
        async with scoped.acquire() as conn:
            await conn.execute("INSERT INTO agent_zenas.tenants VALUES($1,'us-east')",UUID(OWNER.tenant_id))
        state=await store.create(OWNER,str(uuid4()),problem(),hearing()['seats'])
    monkeypatch.setenv('TANK_KEY','disposable-room-test-key')
    app=FastAPI();app.add_middleware(TenantContextMiddleware)
    register_routes(app,lambda:scoped,lambda:PgStore(scoped),lambda:'us-east',lambda:None)
    bind_tenant_endpoints(app)
    with tenant_scope(None):
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
        path=f'/moot/hearings/{state["id"]}?home_region=us-east'
        response=await client.get(path,headers=headers(HUMANS['applicant']))
        assert response.status_code==200,response.text
        assert response.json()['hearing']['id']==state['id']
        foreign={**headers(OWNER),'X-Moot-Tenant':str(uuid4())}
        assert (await client.get(path,headers=foreign)).status_code==403
        assert (await client.get(path)).status_code==401
