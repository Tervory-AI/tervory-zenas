"""Durable publication claims; no model, paid fallback or ambiguous POST replay."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from tenant_db import tenant_scope
from trigger_google import V4, GoogleFailure
from trigger_marketing import obj, public
from trigger_marketing_contract import schedulable, utc_now

MAX_ATTEMPTS = 3


async def receipt(tenant, task):
    from trigger_routes import code_receipt
    return await code_receipt(tenant, task)


class Publisher:
    def __init__(self, marketing):
        self.service, self.pool, self.google = marketing, marketing.pool, marketing.google
        self.meta = marketing.meta

    async def claim(self, tenant):
        async with self.pool.acquire() as conn, conn.transaction():
            # A process may have died after the platform accepted a POST. Never return
            # that claim to the send queue, including on another Tank instance.
            await conn.execute("""UPDATE tank.trigger_posts SET state='failed',delivery='uncertain',retryable=false,
                retry_at=NULL,updated_at=now() WHERE tenant_id=$1 AND delivery='inflight' AND lease_until<now()""", tenant)
            row = await conn.fetchrow('''SELECT p.* FROM tank.trigger_posts p WHERE tenant_id=$1 AND channel IN ('gbp','facebook')
                AND (channel='gbp' OR EXISTS(SELECT 1 FROM tank.trigger_meta_connections m
                    WHERE m.tenant_id=p.tenant_id AND m.status='connected' AND m.sealed IS NOT NULL
                    AND m.page_id=p.location AND m.generation=p.connection_generation))
                AND delivery='pending' AND planned_at<=now() AND attempts<$2 AND approved_revision=revision
                AND (state='scheduled' OR (state='failed' AND retryable AND retry_at<=now()))
                ORDER BY planned_at,id FOR UPDATE SKIP LOCKED LIMIT 1''', tenant, MAX_ATTEMPTS)
            if row is None:
                return None
            if not schedulable(obj(row['report']), row['channel']):
                await conn.execute("UPDATE tank.trigger_posts SET state='failed',retryable=false WHERE tenant_id=$1 AND id=$2", tenant, row['id'])
                return None
            return await conn.fetchrow("""UPDATE tank.trigger_posts SET delivery='inflight',attempt_id=$3,
                attempts=attempts+1,claimed_at=now(),lease_until=now()+interval '5 minutes',updated_at=now()
                WHERE tenant_id=$1 AND id=$2 RETURNING *""", tenant, row['id'], uuid4())

    async def once(self, tenant):
        claimed = await self.claim(tenant)
        if claimed is None:
            return None
        # The claim is committed BEFORE this transaction/network call. Row locks
        # serialize cancellation/disconnect with the actual platform request.
        async with self.pool.acquire() as conn, conn.transaction():
            if claimed['channel'] == 'facebook':
                connection = await conn.fetchrow('SELECT generation,status,page_id AS location FROM tank.trigger_meta_connections WHERE tenant_id=$1 FOR UPDATE', tenant)
            else:
                connection = await conn.fetchrow('SELECT * FROM tank.trigger_gbp_connections WHERE tenant_id=$1 FOR UPDATE', tenant)
            row = await self.service.post(tenant, claimed['id'], conn=conn, lock=True)
            if row['attempt_id'] != claimed['attempt_id'] or row['delivery'] != 'inflight':
                return public(row)
            if row['state'] == 'cancelled':
                return public(row)
            try:
                if (not connection or connection['status'] != 'connected'
                        or connection['generation'] != row['connection_generation'] or connection['location'] != row['location']):
                    raise HTTPException(409)
                if row['channel'] == 'facebook':
                    data = await self.meta.publish(tenant, row['location'], row['text'], conn=conn)
                else:
                    data = await self.google.request(tenant, 'POST', V4 + row['location'] + '/localPosts',
                                                     conn=conn, json={'topicType': 'STANDARD', 'summary': row['text']})
                name, url = data.get('name'), data.get('searchUrl')
                if row['channel'] == 'gbp' and (not isinstance(name, str) or not name.startswith(row['location']+'/localPosts/')):
                    raise GoogleFailure(502, uncertain=True)
                row = await conn.fetchrow("""UPDATE tank.trigger_posts SET state='published',delivery='done',
                    retryable=false,retry_at=NULL,platform_error=NULL,platform_id=$3,platform_url=$4,published_at=now(),
                    updated_at=now() WHERE tenant_id=$1 AND id=$2 RETURNING *""", tenant, row['id'], name, url)
            except (GoogleFailure, HTTPException) as error:
                retryable = isinstance(error, GoogleFailure) and error.retryable
                uncertain = isinstance(error, GoogleFailure) and error.uncertain
                retry_at = utc_now()+timedelta(seconds=30*2**row['attempts']) if retryable and row['attempts']<MAX_ATTEMPTS else None
                row = await conn.fetchrow("""UPDATE tank.trigger_posts SET state='failed',delivery=$3,
                    platform_error=$4,retryable=$5,retry_at=$6,updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 RETURNING *""", tenant, row['id'], 'uncertain' if uncertain else 'pending',
                    error.message if isinstance(error, GoogleFailure) else None, retryable, retry_at)
            ledger = await receipt(tenant, 'trigger.post.publish')
            await conn.execute('UPDATE tank.trigger_posts SET ledger_id=$3 WHERE tenant_id=$1 AND id=$2', tenant, row['id'], ledger)
            await self.service.event(conn, row, row['attempt_id'], ledger)
            return {**public(row), 'ledger_id': str(ledger)}

    async def reply(self, tenant, args):
        async with self.pool.acquire() as conn, conn.transaction():
            connection = await conn.fetchrow('SELECT * FROM tank.trigger_gbp_connections WHERE tenant_id=$1 FOR UPDATE', tenant)
            row = await conn.fetchrow('SELECT * FROM tank.trigger_gbp_replies WHERE tenant_id=$1 AND id=$2 FOR UPDATE', tenant, args.reply_id)
            if (not row or row['revision'] != args.expected_revision or not connection or connection['status'] != 'connected'
                    or connection['generation'] != row['generation'] or connection['location'] != row['location']
                    or obj(row['report']).get('complete') is not True):
                raise HTTPException(409)
            digest = hashlib.sha256(row['text'].encode()).hexdigest()
            if digest == row['sent_hash']:
                return {'reply_id': str(row['id']), 'state': 'sent'}
            if row['state'] not in {'draft', 'failed'}:
                raise HTTPException(409)
            await conn.execute("UPDATE tank.trigger_gbp_replies SET state='sending' WHERE tenant_id=$1 AND id=$2", tenant, row['id'])
            try:
                # Google's updateReply is a PUT to this one review/reply resource:
                # editing replaces it, and an identical retry cannot create a second reply.
                await self.google.request(tenant, 'PUT', V4 + row['location'] + '/reviews/' + row['review_id'] + '/reply',
                                          conn=conn, json={'comment': row['text']})
                ledger = await receipt(tenant, 'trigger.gbp.reply.send')
                await conn.execute("""UPDATE tank.trigger_gbp_replies SET state='sent',sent_hash=$3,
                    platform_error=NULL,sent_at=COALESCE(sent_at,now()),ledger_id=$4 WHERE tenant_id=$1 AND id=$2""", tenant, row['id'], digest, ledger)
                return {'reply_id': str(row['id']), 'state': 'sent', 'ledger_id': str(ledger)}
            except GoogleFailure as error:
                await conn.execute("UPDATE tank.trigger_gbp_replies SET state='failed',platform_error=$3 WHERE tenant_id=$1 AND id=$2", tenant, row['id'], error.message)
                return {'reply_id': str(row['id']), 'state': 'failed', 'platform_error': error.message}


async def publish_due(runtime):
    from trigger_marketing import Marketing
    admin = runtime.admin_pool()
    with tenant_scope(None, administrative=True):
        tenants = await admin.fetch('''SELECT DISTINCT tenant_id FROM tank.trigger_posts p
            WHERE channel IN ('gbp','facebook') AND planned_at<=now() AND (
                (delivery='inflight' AND lease_until<now()) OR
                (delivery='pending' AND attempts<3 AND (state='scheduled' OR
                    (state='failed' AND retryable AND retry_at<=now()))
                AND (channel='gbp' OR EXISTS(SELECT 1 FROM tank.trigger_meta_connections m
                    WHERE m.tenant_id=p.tenant_id AND m.status='connected' AND m.sealed IS NOT NULL
                    AND m.page_id=p.location AND m.generation=p.connection_generation))))
            ORDER BY tenant_id LIMIT 100''')
    for row in tenants:
        with tenant_scope(row['tenant_id']):
            await Publisher(Marketing(runtime.pool())).once(row['tenant_id'])


async def publish_loop(runtime):
    while True:
        try:
            await publish_due(runtime)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never log Google bodies, credentials or prompts
            logging.getLogger(__name__).warning('Trigger publication unavailable')
        await asyncio.sleep(15)
