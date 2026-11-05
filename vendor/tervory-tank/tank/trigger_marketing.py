"""Tenant-private M1 posts, approvals and calendar. W1 owns drafting and checks."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from trigger_google import V4, Google, GoogleFailure
from trigger_marketing_contract import FORMATS, schedulable, utc_now
from trigger_meta import Meta


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


def public(row):
    keys = ('id', 'write_id', 'channel', 'format', 'text', 'report', 'voice_revision', 'revision',
            'approved_revision', 'state', 'planned_at', 'time_zone', 'location', 'platform_error',
            'platform_id', 'platform_url', 'published_at', 'ledger_id')
    result = {k: obj(row[k]) if k in {'report', 'voice_revision'} and row[k] is not None else row[k]
            for k in keys if k in row}
    if row.get('channel') == 'facebook':
        result.update(status=row['state'], url=row.get('platform_url'))
    return result


class Marketing:
    def __init__(self, pool, google=None, meta=None):
        self.pool, self.google = pool, google or Google(pool)
        self.meta = meta or Meta(pool)

    async def write(self, actor, brief, format=None, customer_review=None):
        from trigger_learning import guidance
        from trigger_routes import draft_and_record
        return await draft_and_record(actor, {'text': brief, 'format': format,
            **({'customer_review': customer_review} if customer_review is not None else {}),
            **await guidance(actor, brief, format)})

    async def check(self, tenant, text, format):
        from trigger_routes import room
        result = await room('check', {'tenant_id': str(tenant), 'text': text, 'format': format})
        return result['report']

    async def post(self, tenant, ident, *, conn=None, lock=False):
        # Literal SQL branches: Semgrep and tenant scoping remain inspectable.
        if lock:
            row = await conn.fetchrow('SELECT * FROM tank.trigger_posts WHERE tenant_id=$1 AND id=$2 FOR UPDATE', tenant, ident)
        else:
            row = await (conn or self.pool).fetchrow('SELECT * FROM tank.trigger_posts WHERE tenant_id=$1 AND id=$2', tenant, ident)
        if row is None:
            raise HTTPException(404)
        return row

    async def event(self, conn, row, operation, ledger=None):
        await conn.execute('''INSERT INTO tank.trigger_post_events(tenant_id,post_id,revision,state,operation_id,ledger_id)
            VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(tenant_id,operation_id) DO NOTHING''',
            row['tenant_id'], row['id'], row['revision'], row['state'], operation, ledger)

    async def create(self, actor, args, operation):
        prior = await self.pool.fetchrow('SELECT * FROM tank.trigger_posts WHERE tenant_id=$1 AND operation_id=$2', actor.tenant_id, operation)
        if prior:
            return public(prior)
        result = {}
        if args.write_id:
            ident = args.write_id
        else:
            result = await self.write(actor, args.text, FORMATS[args.channel])
            if not isinstance(result, dict) or not result.get('write_id'):
                raise HTTPException(409)
            ident = UUID(result['write_id'])
        source = await self.pool.fetchrow('SELECT * FROM tank.trigger_writes WHERE tenant_id=$1 AND id=$2', actor.tenant_id, ident)
        if source is None or source['format'] != FORMATS[args.channel]:
            raise HTTPException(409)
        voice = obj(source['write_context']).get('voice_revision')
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow('''INSERT INTO tank.trigger_posts
                (tenant_id,write_id,user_id,operation_id,channel,format,text,report,voice_revision)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)
                ON CONFLICT(tenant_id,operation_id) DO UPDATE SET operation_id=EXCLUDED.operation_id RETURNING *''',
                actor.tenant_id, ident, actor.user_id, operation, args.channel, source['format'], source['draft'],
                json.dumps(obj(source['checker_results'])), json.dumps(voice))
            await self.event(conn, row, operation)
        return {**public(row), **{k: result[k] for k in ('ledger_id', 'cost_usd') if k in result}}

    async def transition(self, tenant, operation, args, operation_id):
        # Run free checkers outside locks; the confirmed expected revision is
        # rechecked under the lock before any change is committed.
        report = None
        if operation == 'post.edit':
            source = await self.post(tenant, args.post_id)
            report = await self.check(tenant, args.text, source['format'])
        async with self.pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchval('SELECT 1 FROM tank.trigger_post_events WHERE tenant_id=$1 AND operation_id=$2', tenant, operation_id)
            row = await self.post(tenant, args.post_id, conn=conn, lock=True)
            if existing:
                return public(row)
            if (row['revision'] != args.expected_revision or row['state'] in {'published', 'cancelled'}
                    or (row['delivery'] == 'uncertain' and operation not in {'post.cancel', 'post.retry'})
                    or (row['delivery'] == 'inflight' and operation != 'post.cancel')):
                raise HTTPException(409)
            state, target, generation = row['state'], row['location'], row['connection_generation']
            if operation == 'post.edit':
                if row['delivery'] == 'uncertain':
                    raise HTTPException(409)  # Unresolved external side effect cannot be replayed as an edit.
                row = await conn.fetchrow('''UPDATE tank.trigger_posts SET text=$3,report=$4::jsonb,revision=revision+1,
                    state='draft',approved_revision=NULL,planned_at=NULL,time_zone=NULL,attempts=0,
                    retryable=false,retry_at=NULL,platform_error=NULL,delivery='pending',updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 RETURNING *''', tenant, args.post_id, args.text, json.dumps(report))
            elif operation == 'post.approve':
                if state != 'draft' or not schedulable(obj(row['report']), row['channel']):
                    raise HTTPException(409)
                if row['channel'] in {'gbp', 'facebook'}:
                    provider = self.google if row['channel'] == 'gbp' else self.meta
                    current = await provider.location(tenant)
                    target, generation = current['location'], current['generation']
                    if (args.location, args.connection_generation) != (target, generation):
                        raise HTTPException(409)
                row = await conn.fetchrow('''UPDATE tank.trigger_posts SET state='approved',approved_revision=revision,
                    location=$3,connection_generation=$4,updated_at=now() WHERE tenant_id=$1 AND id=$2 RETURNING *''',
                    tenant, args.post_id, target, generation)
            elif operation == 'post.schedule':
                if state != 'approved' or row['approved_revision'] != row['revision'] or not schedulable(obj(row['report']), row['channel']) or args.planned_at <= utc_now():
                    raise HTTPException(409)
                if row['channel'] in {'gbp', 'facebook'}:
                    provider = self.google if row['channel'] == 'gbp' else self.meta
                    current = await provider.location(tenant)
                    if (target, generation) != (current['location'], current['generation']):
                        raise HTTPException(409)
                row = await conn.fetchrow('''UPDATE tank.trigger_posts SET state='scheduled',planned_at=$3,time_zone=$4,
                    delivery='pending',retryable=false,retry_at=NULL,updated_at=now() WHERE tenant_id=$1 AND id=$2 RETURNING *''',
                    tenant, args.post_id, args.planned_at, args.time_zone)
            elif operation == 'post.cancel':
                row = await conn.fetchrow("UPDATE tank.trigger_posts SET state='cancelled',delivery='done',retryable=false,retry_at=NULL,updated_at=now() WHERE tenant_id=$1 AND id=$2 RETURNING *", tenant, args.post_id)
            elif operation == 'post.retry':
                if state != 'failed' or row['approved_revision'] != row['revision']:
                    raise HTTPException(409)
                found = await self.reconcile(conn, row) if row['delivery'] == 'uncertain' else None
                if found:
                    row = await conn.fetchrow('''UPDATE tank.trigger_posts SET state='published',delivery='done',
                        retryable=false,retry_at=NULL,platform_error=NULL,platform_id=$3,platform_url=$4,published_at=$5,
                        updated_at=now() WHERE tenant_id=$1 AND id=$2 RETURNING *''', tenant, args.post_id,
                        found['name'], found.get('searchUrl'), found['created'])
                else:
                    row = await conn.fetchrow("UPDATE tank.trigger_posts SET state='scheduled',delivery='pending',attempts=0,retry_at=NULL,updated_at=now() WHERE tenant_id=$1 AND id=$2 RETURNING *", tenant, args.post_id)
            else:
                raise HTTPException(404)
            await self.event(conn, row, operation_id)
            return public(row)

    async def reconcile(self, conn, row):
        tenant = row['tenant_id']
        if row['channel'] == 'facebook':
            binding = await conn.fetchrow("SELECT generation,page_id AS location FROM tank.trigger_meta_connections WHERE tenant_id=$1 AND status='connected'", tenant)
        else:
            binding = await conn.fetchrow("SELECT generation,location FROM tank.trigger_gbp_connections WHERE tenant_id=$1 AND status='connected'", tenant)
        if not binding or (binding['generation'], binding['location']) != (row['connection_generation'], row['location']):
            raise HTTPException(409)
        # Pre-review claims persisted the same timestamp as a five-minute lease.
        claimed = row['claimed_at'] or (row['lease_until'] - timedelta(minutes=5) if row['lease_until'] else None)
        if claimed is None:
            raise HTTPException(409)
        if row['channel'] == 'facebook':
            posts = await self.meta.published(tenant, row['location'], claimed, conn=conn)
            for post in posts:
                if post['summary'] == row['text'] and post['created'] >= claimed.replace(microsecond=0):
                    try:
                        url = await self.meta.permalink(tenant, post['name'], conn=conn)
                    except (GoogleFailure, HTTPException):
                        url = None
                    return {**post, 'searchUrl': url}
            return None
        posts = await self.google.pages(tenant, V4+row['location']+'/localPosts', 'localPosts', conn=conn)
        for post in posts:
            if not isinstance(post, dict):
                raise GoogleFailure(502)
            if post.get('summary') != row['text']:
                continue
            try:
                created = datetime.fromisoformat(post['createTime'].replace('Z', '+00:00'))
                name = post['name']
                if created.tzinfo is None or not isinstance(name, str) or not name.startswith(row['location']+'/localPosts/'):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise GoogleFailure(502) from None  # An incomplete read cannot authorize another POST.
            if created > claimed:
                return {**post, 'created': created}
        return None

    async def posts(self, tenant, args):
        # Dates are filtered in each post's own zone, not the server's date.
        rows = await self.pool.fetch('''SELECT * FROM tank.trigger_posts WHERE tenant_id=$1
            AND ($2::text IS NULL OR state=$2) AND ($3::text IS NULL OR channel=$3)
            AND ($4::date IS NULL OR (planned_at AT TIME ZONE time_zone)::date >= $4)
            AND ($5::date IS NULL OR (planned_at AT TIME ZONE time_zone)::date < $5)
            ORDER BY planned_at NULLS LAST,created_at,id LIMIT 500''', tenant, args.state, args.channel, args.start, args.end)
        items, link_reads = [], 0
        for row in rows:
            if link_reads < 3 and row['channel'] == 'facebook' and row['state'] == 'published' and row['platform_id'] and not row['platform_url']:
                link_reads += 1
                try:
                    binding = await self.meta.location(tenant)
                    if (binding['location'], binding['generation']) == (row['location'], row['connection_generation']):
                        url = await self.meta.permalink(tenant, row['platform_id'])
                        await self.pool.execute('''UPDATE tank.trigger_posts SET platform_url=$3,updated_at=now()
                            WHERE tenant_id=$1 AND id=$2 AND platform_id=$4''', tenant, row['id'], url, row['platform_id'])
                        row = {**dict(row), 'platform_url': url}
                except (GoogleFailure, HTTPException):
                    pass  # Recover only the link, never re-publish a known ID.
            items.append(public(row))
        return {'posts': items, 'items': items}

    async def calendar(self, tenant, args):
        start = args.start
        if args.period == 'month':
            start = start.replace(day=1)
            end = (start.replace(day=28)+timedelta(days=4)).replace(day=1)
        else:
            end = start + timedelta(days=7)
        rows = await self.pool.fetch('''SELECT *, (planned_at AT TIME ZONE time_zone)::date AS local_day,
            (planned_at AT TIME ZONE time_zone)::time AS local_time,
            channel NOT IN ('gbp','facebook') AND state='scheduled' AND planned_at<=now() AS due
            FROM tank.trigger_posts WHERE tenant_id=$1 AND planned_at IS NOT NULL
            AND (planned_at AT TIME ZONE time_zone)::date >= $2
            AND (planned_at AT TIME ZONE time_zone)::date < $3 ORDER BY planned_at,id''', tenant, start, end)
        days = {}
        for row in rows:
            days.setdefault(str(row['local_day']), []).append({**public(row), 'time': str(row['local_time']), 'due': row['due']})
        return {'start': start, 'end': end, 'days': days}

    async def reviews(self, tenant):
        current = await self.google.location(tenant)
        checked = current['reviews_checked_at']
        rows = await self.google.pages(tenant, 'https://mybusiness.googleapis.com/v4/' + current['location'] + '/reviews', 'reviews',
                                       params={'orderBy': 'updateTime desc', 'pageSize': 50})
        found = []
        async with self.pool.acquire() as conn, conn.transaction():
            binding = await conn.fetchrow('SELECT generation,location FROM tank.trigger_gbp_connections WHERE tenant_id=$1 FOR UPDATE', tenant)
            if binding is None or (binding['generation'], binding['location']) != (current['generation'], current['location']):
                raise HTTPException(409)
            for review in rows:
                ident = review.get('reviewId', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', ident):
                    continue
                updated = datetime.fromisoformat(review['updateTime'])
                if current['reviews_checked_at'] and updated <= current['reviews_checked_at']:
                    continue
                checked = max(checked, updated) if checked else updated
                record = {'review_id': ident, 'rating': review.get('starRating', ''), 'text': review.get('comment', ''), 'updated_at': updated}
                await conn.execute('''INSERT INTO tank.trigger_gbp_reviews(tenant_id,location,review_id,rating,text,updated_at)
                    VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(tenant_id,location,review_id)
                    DO UPDATE SET rating=EXCLUDED.rating,text=EXCLUDED.text,updated_at=EXCLUDED.updated_at''',
                    tenant, current['location'], ident, record['rating'], record['text'], updated)
                found.append(record)
            await conn.execute('UPDATE tank.trigger_gbp_connections SET reviews_checked_at=$2 WHERE tenant_id=$1', tenant, checked)
        return {'reviews': found}

    async def reply_draft(self, actor, review_id):
        current = await self.google.location(actor.tenant_id)
        review = await self.pool.fetchrow('SELECT * FROM tank.trigger_gbp_reviews WHERE tenant_id=$1 AND location=$2 AND review_id=$3', actor.tenant_id, current['location'], review_id)
        if review is None:
            raise HTTPException(404)
        brief = 'Draft a reply in the saved voice to the separately supplied customer review. Do not add facts.'
        result = await self.write(actor, brief, customer_review={'rating': review['rating'], 'text': review['text']})
        source = await self.pool.fetchrow('SELECT draft,checker_results FROM tank.trigger_writes WHERE tenant_id=$1 AND id=$2', actor.tenant_id, UUID(result['write_id']))
        row = await self.pool.fetchrow('''INSERT INTO tank.trigger_gbp_replies(tenant_id,location,review_id,generation,write_id,text,report)
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(tenant_id,location,review_id)
            DO UPDATE SET write_id=EXCLUDED.write_id,text=EXCLUDED.text,report=EXCLUDED.report,generation=EXCLUDED.generation,
            revision=tank.trigger_gbp_replies.revision+1,state='draft' WHERE tank.trigger_gbp_replies.state<>'sending' RETURNING id,text,report,revision,state''',
            actor.tenant_id, current['location'], review_id, current['generation'], UUID(result['write_id']), source['draft'], json.dumps(obj(source['checker_results'])))
        if row is None:
            raise HTTPException(409)
        return {**dict(row), 'report': obj(row['report']), **{k: result[k] for k in ('ledger_id', 'cost_usd') if k in result}}
