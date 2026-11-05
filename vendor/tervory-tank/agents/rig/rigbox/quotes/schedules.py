"""Discover new retailers on every scheduler tick, not only process startup."""

from chassis.db import ensure_schedules, schema_for
from .store import PgQuotes


async def refresh(pool, store, agent: str) -> int:
    tenants = await store.tenant_ids()
    quotes = PgQuotes(pool)
    for tenant in await quotes.expired_tenants():
        await quotes.purge(tenant)
    async with pool.acquire() as conn:
        for tenant in tenants:
            # Only operational counters/receipts expire; aggregate totals remain.
            await conn.execute("""DELETE FROM agent_rig.rate_windows WHERE tenant_id=$1::uuid
                AND window_id < floor(extract(epoch FROM now()) / 60)::bigint - 1""", tenant)
            await conn.execute("""DELETE FROM agent_rig.widget_search_events WHERE tenant_id=$1::uuid
                AND created_at < now() - interval '1 day'""", tenant)
        await ensure_schedules(conn, schema_for(agent), [
            {"tenant_id": tenant, "cron": "*/5 * * * *", "kind": "retry_quotes", "input": {}}
            for tenant in tenants
        ])
    return len(tenants)
