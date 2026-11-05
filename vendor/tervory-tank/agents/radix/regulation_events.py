"""Zenas events queue tenant-owned notice/cookie runs; the watch tick executes them."""
from __future__ import annotations

import asyncio
from datetime import date
import json
import logging
import re
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

from chassis.events import request
from chassis.tenant_db import tenant_scope
from passive_store import watch_artifact

EVENT = 'zenas.regulation.changed'
log = logging.getLogger(__name__)


def facts(value):
    """Accept precisely the two bus fact shapes, including official-source links."""
    if not isinstance(value, dict):
        raise ValueError('Invalid regulation event')
    if set(value) == {'document_id', 'version_id'}:
        if all(isinstance(v, str) and re.fullmatch('[a-f0-9]{64}', v) for v in value.values()):
            return value
    elif set(value) == {'source', 'document_id', 'date', 'link'}:
        hosts = {
            'federal-register': {'www.federalregister.gov', 'federalregister.gov'},
            'ecfr': {'www.ecfr.gov', 'ecfr.gov'}, 'congress': {'www.congress.gov', 'congress.gov'},
            'legiscan': {'legiscan.com'}, 'eur-lex': {'eur-lex.europa.eu'},
            'laws-africa': {'api.laws.africa', 'edit.laws.africa', 'lawlibrary.org.za', 'laws.africa'},
        }
        if not all(isinstance(v, str) for v in value.values()):
            raise ValueError('Invalid regulation event')
        stamp = date.fromisoformat(value['date'])
        link = value['link']
        p = urlsplit(link)
        if (stamp.isoformat() == value['date'] and 1 <= len(value['document_id']) <= 512
                and len(link) <= 4096 and p.scheme == 'https'
                and p.hostname in hosts.get(value['source'], set()) and not p.username and not p.password
                and p.port in (None, 443) and not any(c.isspace() for c in link)
                and not any(k.lower() in {'key', 'api_key', 'token', 'password', 'secret'} for k, _ in parse_qsl(p.query))):
            return value
    raise ValueError('Invalid regulation event')


class Queue:
    def __init__(self, pool):
        self.pool = pool

    async def tenant_ids(self):
        discovery = getattr(self.pool, 'admin_pool', None) or getattr(self.pool, 'raw', self.pool)
        async with discovery.acquire() as conn:
            return [str(r['tenant_id']) for r in await conn.fetch(
                "SELECT DISTINCT tenant_id FROM agent_radix.passive_watches WHERE status='active'")]

    async def enqueue(self, tenant, message):
        tenant, event_id = UUID(str(tenant)), UUID(message['event_id'])
        safe = facts(message['facts'])
        with tenant_scope(tenant):
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    watches = await conn.fetch("""SELECT w.domain FROM agent_radix.passive_watches w
                      JOIN agent_radix.passive_domains d USING(tenant_id,domain)
                      WHERE w.tenant_id=$1 AND w.status='active' AND w.challenge=d.challenge
                      ORDER BY w.domain FOR UPDATE OF w""", tenant)
                    for watch in watches:
                        domain, ident = watch['domain'], uuid4()
                        queued = await conn.fetchval("""INSERT INTO agent_radix.regulation_runs
                          (tenant_id,domain,event_id,scan_id,facts) VALUES($1,$2,$3,$4,$5::jsonb)
                          ON CONFLICT(tenant_id,domain,event_id) DO NOTHING RETURNING scan_id""",
                          tenant, domain, event_id, ident, json.dumps(safe))
                        if queued:
                            await conn.execute("""INSERT INTO agent_radix.passive_scans
                              (tenant_id,id,domain,target,artifact,watched) VALUES($1,$2,$3,$4,$5,true)""",
                              tenant, ident, domain, 'https://' + domain + '/', watch_artifact(domain))


async def consume_tenant(queue, http, tenant, consumer):
    body = {'event': EVENT, 'consumer': consumer}
    # Recheck own watch rows, even when a previously discovered tenant was unwatched.
    if str(tenant) not in await queue.tenant_ids():
        return 0
    result = await request(http, str(tenant), 'receive', body)
    handled = 0
    for message in result['messages']:
        action = 'ack'
        try:
            await queue.enqueue(tenant, message)
            handled += 1
        except Exception:
            action = 'fail'
            log.warning('Regulation event processing will retry')
        await request(http, str(tenant), 'settle', {**body, 'id': message['id'], 'action': action})
    return handled


async def consume_loop(queue, http):
    consumer = str(uuid4())
    while True:
        try:
            for tenant in await queue.tenant_ids():
                try:
                    await consume_tenant(queue, http, tenant, consumer)
                except Exception:
                    log.warning('Regulation event bus unavailable; watch work continues')
        except Exception:
            log.warning('Regulation event tenant discovery will retry')
        await asyncio.sleep(5)


async def run_queued(pool, conn, *, execute_scan=None):
    """Called only under R4's WATCH_LOCK, before the ordinary due watch."""
    from fastapi import HTTPException
    from passive_routes import Scan, execute
    from passive_store import Store
    from passive_r6 import collect_notices as collect
    execute_scan = execute_scan or execute
    owner = await conn.fetchrow("""SELECT r.tenant_id,r.domain,r.scan_id FROM agent_radix.regulation_runs r
      JOIN agent_radix.passive_scans s ON s.tenant_id=r.tenant_id AND s.id=r.scan_id
      WHERE NOT r.settled AND r.next_attempt<=now() AND s.status IN ('pending','error','complete')
      ORDER BY r.next_attempt,r.tenant_id,r.domain LIMIT 1""")
    if owner is None:
        return None
    tenant, ident, domain = owner['tenant_id'], owner['scan_id'], owner['domain']
    with tenant_scope(tenant):
        async with pool.acquire() as scoped:
            active = await scoped.fetchval("""SELECT 1 FROM agent_radix.passive_watches
              WHERE tenant_id=$1 AND domain=$2 AND status='active'""", tenant, domain)
            if not active:
                await scoped.execute("""UPDATE agent_radix.regulation_runs SET settled=true
                  WHERE tenant_id=$1 AND scan_id=$2""", tenant, ident)
                await Store(pool).fail(tenant, ident)
                return {'count': 0}
            # A process may die after completion but before settling this queue row.
            complete = await scoped.fetchval("""SELECT 1 FROM agent_radix.passive_scans
              WHERE tenant_id=$1 AND id=$2 AND status='complete'""", tenant, ident)
            if not complete:
                await scoped.execute("""UPDATE agent_radix.passive_scans SET status='pending'
                  WHERE tenant_id=$1 AND id=$2 AND status='error'""", tenant, ident)
        terminal = bool(complete)
        if not complete:
            try:
                await execute_scan(Store(pool), Scan(tenant_id=tenant, target='https://' + domain + '/',
                    artifact=watch_artifact(domain)), watched=True, scanner=collect, ident=ident)
                terminal = True
            except HTTPException as exc:
                terminal = exc.status_code == 403  # Gateway stops watch on TXT mismatch.
            except Exception:
                pass  # Durable retry; no event or response data in logs.
        async with pool.acquire() as scoped:
            await scoped.execute("""UPDATE agent_radix.regulation_runs SET settled=$3,
              next_attempt=now()+interval '1 minute' WHERE tenant_id=$1 AND scan_id=$2""",
              tenant, ident, terminal)
        return {'count': int(terminal), 'scan_id': str(ident)}
