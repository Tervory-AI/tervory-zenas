"""Durable tenant-scoped Street Law sessions; session locks serialize transitions."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from uuid import UUID

from agents.zenas.core.store import PgStore
from agents.zenas.core.street_law import Refused


class ConnectionPool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class StreetMatters(PgStore):
    async def lock_document(self, tenant_id, document_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchval('''SELECT id FROM agent_zenas.documents
                WHERE tenant_id=$1 AND id=$2 FOR UPDATE''', UUID(tenant_id), UUID(document_id)) is not None


    async def set_document_state(self, tenant_id, document_id, state):
        if state != 'released':
            return await super().set_document_state(tenant_id, document_id, state)
        # This store is used only inside the locked human-release transaction.
        async with self.pool.acquire() as conn:
            await conn.execute('''UPDATE agent_zenas.documents SET state='released',reviewed=true
                WHERE tenant_id=$1 AND id=$2''', UUID(tenant_id), UUID(document_id))
        return await self.get_document(tenant_id, document_id)


class PgSessions:
    def __init__(self, pool):
        self.pool = pool

    async def create(self, tenant_id, row):
        async with self.pool.acquire() as conn:
            await conn.execute('''INSERT INTO agent_zenas.street_law_sessions(tenant_id,id,data)
                VALUES($1,$2,$3::jsonb)''', UUID(tenant_id), UUID(row['id']), json.dumps(row))

    @asynccontextmanager
    async def edit(self, tenant_id, session_id):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                raw = await conn.fetchval('''SELECT data FROM agent_zenas.street_law_sessions
                    WHERE tenant_id=$1 AND id=$2 FOR UPDATE''', UUID(tenant_id), UUID(session_id))
                if raw is None:
                    raise Refused('not found')
                data = json.loads(raw) if isinstance(raw, str) else dict(raw)
                before = json.dumps(data, sort_keys=True)
                yield data, StreetMatters(ConnectionPool(conn))
                if json.dumps(data, sort_keys=True) == before:
                    return
                await conn.execute('''UPDATE agent_zenas.street_law_sessions SET data=$3::jsonb
                    WHERE tenant_id=$1 AND id=$2''', UUID(tenant_id), UUID(session_id), json.dumps(data))
