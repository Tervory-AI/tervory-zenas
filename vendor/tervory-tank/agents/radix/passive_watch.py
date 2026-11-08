"""Durable due times, driven by the room's existing scheduler; no new egress."""
from __future__ import annotations

from uuid import UUID

from chassis.tenant_db import tenant_scope
from passive_store import Store, watch_artifact

# Transaction-scoped global worker lock. The scan lease/index also arbitrates
# against manual scans and gateways on other processes. No session lock can leak.
WATCH_LOCK = 582605004


async def tick(pool, *, execute_scan=None) -> dict:
    from passive_routes import Scan, execute
    execute_scan = execute_scan or execute
    discovery = getattr(pool, 'admin_pool', None) or getattr(pool, 'raw', pool)
    async with discovery.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval('SELECT pg_try_advisory_xact_lock($1)', WATCH_LOCK):
                return {'queued': True}
            await Store(pool).reap_expired(discovery=conn)
            from regulation_events import run_queued
            queued = await run_queued(pool, conn)
            if queued is not None:
                return queued
            from passive_weekly import tick as weekly_tick
            weekly=await weekly_tick(pool,conn)
            if weekly is not None:
                return weekly
            # Administrative discovery only. Every operation below re-enters the
            # persisted owner's tenant scope and rechecks status/due time.
            owner = await conn.fetchrow("""SELECT tenant_id,domain FROM agent_radix.passive_watches
              WHERE status='active' AND next_run<=now() ORDER BY next_run,tenant_id,domain LIMIT 1""")
            if owner is None:
                return {'count': 0}
            tenant, domain = UUID(str(owner['tenant_id'])), owner['domain']
            with tenant_scope(tenant):
                store = Store(pool)
                async with pool.acquire() as scoped:
                    due = await scoped.fetchval("""SELECT 1 FROM agent_radix.passive_watches
                      WHERE tenant_id=$1 AND domain=$2 AND status='active' AND next_run<=now()""", tenant, domain)
                if not due:
                    return {'count': 0}
                try:
                    result = await execute_scan(store, Scan(tenant_id=tenant,
                        target=f'https://{domain}/', artifact=watch_artifact(domain)), watched=True)
                except Exception:
                    # Busy manual scans, DNS outages and interrupted scanners are
                    # retried. A definitive TXT mismatch has already stopped the
                    # watch at the gateway before any scanner starts.
                    async with pool.acquire() as scoped:
                        await scoped.execute("""UPDATE agent_radix.passive_watches SET next_run=now()+interval '1 minute'
                          WHERE tenant_id=$1 AND domain=$2 AND status='active'""", tenant, domain)
                    return {'count': 0}
                async with pool.acquire() as scoped:
                    await scoped.execute("""UPDATE agent_radix.passive_watches
                      SET next_run=now()+CASE WHEN interval='daily' THEN interval '1 day' ELSE interval '7 days' END
                      WHERE tenant_id=$1 AND domain=$2 AND status='active'""", tenant, domain)
                return {'count': 1, 'scan_id': result['scan_id']}
