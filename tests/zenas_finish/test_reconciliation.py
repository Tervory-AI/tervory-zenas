import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from test_source_freshness import NEXT, TENANT, card, read, room, save


@pytest.mark.asyncio
async def test_late_old_source_card_is_never_reusable(database):
    ladder = await room(database)
    old, _ = await save(database)
    await save(database, text="placeholder amended civil rule", start=NEXT)
    late = await card(ladder, old)
    assert late["status"] == "stale" and not late["active"]


@pytest.mark.asyncio
async def test_approval_cannot_revive_retired_source_dependency(database):
    ladder = await room(database)
    old, _ = await save(database)
    saved = await card(ladder, old)
    await save(database, text="placeholder amended civil rule", start=NEXT)
    returned = await ladder.set_status(saved['id'], region='us-east', status='lawyer_approved')
    assert returned['status'] == 'stale' and not returned['active']
    async with database.acquire() as conn:
        assert await conn.fetchval('SELECT status FROM agent_zenas.rule_cards WHERE id=$1', UUID(saved['id'])) == 'stale'


@pytest.mark.asyncio
async def test_read_reconciles_preexisting_stale_cache(database):
    ladder = await room(database)
    old, _ = await save(database)
    saved = await card(ladder, old)
    await save(database, text="placeholder amended civil rule", start=NEXT)
    # Simulate a row left active by an older deployment before this fix.
    async with database.acquire() as conn:
        await conn.execute("UPDATE agent_zenas.rule_cards SET status='lawyer_approved',active=true WHERE id=$1", UUID(saved['id']))
    assert (await read(ladder, saved))["status"] == "stale"


@pytest.mark.asyncio
async def test_source_lock_covers_insert_and_update_race(database):
    ladder = await room(database)
    old, _ = await save(database)
    from agents.zenas.core.freshness import lock_sources
    async with database.acquire() as conn:
        async with conn.transaction():
            await lock_sources(conn, [{"doc_id":old.id,"version_id":old.version_id}])
            update = asyncio.create_task(save(database, text="placeholder amended civil rule", start=NEXT))
            await asyncio.sleep(.05)
            assert not update.done()
            from agents.zenas.core.ladder_store import PgLadder
            class HeldPool:
                @asynccontextmanager
                async def acquire(self):
                    yield conn
            saved = await card(PgLadder(HeldPool()), old)
        await asyncio.wait_for(update, timeout=5)
    assert (await read(ladder, saved))["status"] == "stale"


@pytest.mark.asyncio
async def test_reconciliation_does_not_touch_another_tenants_private_card(database):
    ladder = await room(database)
    old, _ = await save(database)
    other = "22222222-2222-4222-8222-222222222222"
    saved = await card(ladder, old, owner=other)
    await save(database, text="placeholder amended civil rule", start=NEXT)
    async with database.acquire() as conn:
        await conn.execute("UPDATE agent_zenas.rule_cards SET status='lawyer_approved',active=true WHERE id=$1", UUID(saved['id']))
    assert await ladder.get_rule_card(saved['id'],region='us-east',tenant_id=TENANT) is None
    async with database.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM agent_zenas.rule_cards WHERE id=$1",UUID(saved['id'])) == 'lawyer_approved'
