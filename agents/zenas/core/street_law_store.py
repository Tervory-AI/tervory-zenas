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
                yield data, PgStore(ConnectionPool(conn))
                await conn.execute('''UPDATE agent_zenas.street_law_sessions SET data=$3::jsonb
                    WHERE tenant_id=$1 AND id=$2''', UUID(tenant_id), UUID(session_id), json.dumps(data))
