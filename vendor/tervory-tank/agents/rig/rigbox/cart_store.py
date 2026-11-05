"""Durable tenant state and transaction-scoped locks for US carts."""
from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from uuid import UUID

from rigbox.commerce import CommerceError


class CartStore:
    def __init__(self, store, conn=None):
        self.store, self.pool, self.conn = store, getattr(store, 'pool', None), conn
        if self.pool is None and not hasattr(store, '_commerce_rows'):
            store._commerce_rows, store._commerce_locks = {}, {}

    @asynccontextmanager
    async def lock(self, tenant, identity):
        if self.pool is None:
            lock = self.store._commerce_locks.setdefault((str(tenant), identity), asyncio.Lock())
            async with lock:
                yield self
        else:
            async with self.pool.acquire() as conn, conn.transaction():
                # Refuse new cart data if RLS was activated without enrolling
                # the additive table in the platform's opt-in migration.
                enforced = await conn.fetchval("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_rig.retailers'::regclass")
                if enforced and not await conn.fetchval("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_rig.commerce_state'::regclass"):
                    raise CommerceError('rls_rollout_required')
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',
                                   'rig-cart:' + str(tenant) + ':' + identity)
                yield CartStore(self.store, conn)

    async def get(self, tenant, identity):
        if self.pool is None:
            return copy.deepcopy(self.store._commerce_rows.get((str(tenant), identity)))
        if self.conn is None:
            async with self.lock(tenant, identity) as locked:
                return await locked.get(tenant, identity)
        raw = await self.conn.fetchval('SELECT payload FROM agent_rig.commerce_state WHERE tenant_id=$1 AND id=$2',
                                       UUID(str(tenant)), identity)
        return json.loads(raw) if isinstance(raw, str) else raw

    async def put(self, tenant, identity, data):
        if self.pool is None:
            self.store._commerce_rows[(str(tenant), identity)] = copy.deepcopy(data)
        elif self.conn is None:
            async with self.lock(tenant, identity) as locked:
                await locked.put(tenant, identity, data)
        else:
            await self.conn.execute('INSERT INTO agent_rig.commerce_state(tenant_id,id,payload) VALUES($1,$2,$3::jsonb) '
                'ON CONFLICT(tenant_id,id) DO UPDATE SET payload=excluded.payload',
                UUID(str(tenant)), identity, json.dumps(data))

    async def swap(self, tenant, identity, expected, replacement):
        """Compare the complete persisted row under the existing tenant lock."""
        async with self.lock(tenant, identity) as rows:
            if await rows.get(tenant, identity) != expected:
                return False
            await rows.put(tenant, identity, replacement)
            return True

    async def checkouts(self, tenant):
        if self.pool is None:
            return [copy.deepcopy(v) for (t, k), v in self.store._commerce_rows.items()
                    if t == str(tenant) and k.startswith('co_')]
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT payload FROM agent_rig.commerce_state WHERE tenant_id=$1 AND id LIKE 'co\\_%' ESCAPE '\\'",
                                    UUID(str(tenant)))
        return [json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload'] for row in rows]

    async def release(self, tenant, lines, now):
        """Consume price holds only, never a card authorization or payment."""
        from rigbox.locate_live import Holds
        for line in lines:
            await Holds(self.store).take(tenant, line['hold_id'], now)
            if self.pool is None:
                row = self.store.supply_state.get(str(tenant), {}).get('holds', {}).get(line['hold_id'])
                if row:
                    row['used_at'] = now
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                await conn.execute('UPDATE agent_rig.supply_holds SET used_at=$3 WHERE tenant_id=$1 AND id=ANY($2::text[]) AND used_at IS NULL',
                                   UUID(str(tenant)), [line['hold_id'] for line in lines], now)
