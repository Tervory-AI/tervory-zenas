"""Standing address evidence only. No blocking decision, inference or raw-log retention."""
import asyncio
import json
import math
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .client_settings import load_settings
from .never_block import classify_never_block
from .threat_queue import read_undo_used, _advisory_url_is_public

HOLD_SECONDS = 24 * 3600
SHARE_SECONDS = 7 * 24 * 3600


def epoch(value):
    if type(value) in (int, float):
        return float(value) if math.isfinite(value) else None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def block_fact(row):
    if row.get('kind') != 'blocked_address':
        return None
    try:
        fact = json.loads(row['claim'])
    except (ValueError, TypeError, KeyError):
        return None
    if not isinstance(fact, dict) or fact.get('kind') != 'blocked_address':
        return None
    return fact


def published_id(tenant, source):
    return str(uuid.uuid5(uuid.UUID(source), 'platform-memory:' + tenant))


class StandingBlocks:
    def __init__(self, memory, tank, store, queue_for, *, clock=time.time):
        self.memory, self.tank, self.store, self.queue_for = memory, tank, store, queue_for
        self.clock = clock
        self.lock = asyncio.Lock()

    async def reconcile(self, tenant):
        async with self.lock:
            return await self._reconcile(str(uuid.UUID(tenant)))

    async def _reconcile(self, tenant):
        queue = self.queue_for(tenant)
        receipts = [r for r in queue.list_receipts() if r.get('tenant_id') == tenant]
        settings = load_settings(self.store, tenant)
        rows = await self.memory.records(tenant)
        existing = {str(row['id']): row for row in rows if block_fact(row)}
        pending = self.store.get_state(tenant, 'block_withdrawals', {})
        now = self.clock()
        undone = {str(r.get('prior_receipt_id')) for r in receipts if r.get('outcome') in {'undone', 'already_undone'}}
        by_id = {str(r.get('id')): r for r in receipts}
        def was_undone(rid):
            identities = {rid, str((by_id.get(rid) or {}).get('prior_receipt_id') or '')} - {''}
            return any(identity in undone or read_undo_used(queue, identity) for identity in identities)
        for identity, row in existing.items():
            rid = str(block_fact(row).get('receipt_id') or '')
            if was_undone(rid):
                pending[identity] = {'published_row': published_id(tenant, identity)}
        self.store.set_state(tenant, 'block_withdrawals', pending)
        # Delete the client source first. A concurrent publish either commits before
        # this deletion or sees no source; the subsequent platform read catches both.
        for identity in list(pending):
            if identity in existing:
                await self.memory.forget(identity, tenant)
                existing.pop(identity)
            platform = {str(r['id']): r for r in await self.memory.records(tenant, tier='platform')}
            target = pending[identity]['published_row']
            if target in platform:
                result = await self.tank.call('POST', '/v1/ghost/autumbra/blocks/withdraw', {
                    'tenant_id': tenant, 'source_row': identity, 'published_row': target})
                if result.get('result') != 'withdrawn':
                    raise RuntimeError('Memory withdrawal unavailable')
            pending.pop(identity)
            self.store.set_state(tenant, 'block_withdrawals', pending)
        for receipt in receipts:
            applied = receipt.get('applied_record') or {}
            kind = receipt.get('kind') or applied.get('kind')
            rid = str(receipt.get('id') or '')
            address = receipt.get('address') or applied.get('address')
            when = applied.get('applied_at') or applied.get('up_at') or receipt.get('timestamp')
            started = epoch(when)
            if (not receipt.get('applied') or kind != 'block_ip' or not address
                    or started is None or now - started < HOLD_SECONDS
                    or was_undone(rid)
                    or classify_never_block(address, client_own_cidrs=settings.own_cidrs)):
                continue
            try:
                identity = str(uuid.uuid5(uuid.UUID(tenant), 'standing-block:' + str(uuid.UUID(rid))))
            except ValueError:
                continue
            if identity in existing:
                continue
            urls = {str(receipt.get('advisory_url') or ''), str(receipt.get('source_url') or '')}
            for source in receipt.get('sources') or []:
                if isinstance(source, dict):
                    urls.add(str(source.get('url') or source.get('source_url') or ''))
            # Only named public sources, never query/credential fragments.
            def public_url(url):
                try:
                    parsed = urlsplit(url)
                    return _advisory_url_is_public(url) and not (parsed.query or parsed.fragment or parsed.username or parsed.password)
                except ValueError:
                    return False
            public = sorted(url for url in urls if url and public_url(url))
            fact = {'kind': 'blocked_address', 'address': address, 'receipt_id': rid,
                    'applied_at': datetime.fromtimestamp(started, timezone.utc).isoformat(), 'public_sources': public}
            row = await self.memory.remember(tenant, json.dumps(fact, sort_keys=True), 'blocked_address',
                'urn:autumbra:receipt:' + rid, evidence=public, row_id=identity,
                last_verified=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                meta={'public_addresses': [address]}, index=False)
            existing[identity] = row
        return list(existing.values())

    async def items(self, tenant):
        rows = await self.reconcile(tenant)
        shared = {str(r['id']): r for r in await self.memory.records(tenant, tier='platform')}
        items = []
        for row in rows:
            fact = block_fact(row)
            target = shared.get(published_id(tenant, str(row['id'])))
            verified = epoch(target.get('last_verified')) if target else None
            state = 'private' if target is None else ('published' if verified is not None and 0 <= self.clock() - verified < SHARE_SECONDS else 'expired')
            items.append({'id': str(row['id']), 'name': fact['address'], 'status': state,
                          'summary': str(row.get('last_verified') or '') + ' · ' + str(row['id']),
                          'address': fact['address'],
                          'last_verified': row.get('last_verified'), 'publish_state': state})
        return {'items': items}

    async def count(self, tenant, address):
        now = self.clock()
        total = 0
        for row in await self.memory.records(tenant, tier='platform'):
            fact = block_fact(row)
            verified = epoch(row.get('last_verified'))
            if fact and fact.get('address') == address and verified is not None and 0 <= now - verified < SHARE_SECONDS:
                total += 1
        return total
