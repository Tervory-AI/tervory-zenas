"""Rig's only bus subscription. Tenant identity comes from its own catalogue."""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
import hashlib
import logging
from uuid import UUID, uuid4

from chassis.events import request

EVENT = "tiger.price.changed"
log = logging.getLogger(__name__)


async def save_price(service, tenant: str, message: dict) -> None:
    facts = message['facts']
    if set(facts) != {'watch_id', 'observation_id', 'sku', 'page_id', 'previous_price', 'price', 'currency', 'observed_at'}:
        raise ValueError('Invalid price event')
    sku = facts['sku']
    if sku is None:  # An ordinary Tiger watch need not belong to a Rig product.
        return
    async with service.store.atomic(tenant) as store:
        products = await store.watch_products(tenant)
        product = next((p for p in products if p['sku'] == sku and
                        hashlib.sha256(p['url'].encode()).hexdigest() == facts['page_id']), None)
        if product is None:  # Deleted, rebound or feed-owned products are not eligible.
            return
        price = Decimal(facts['price'])
        stamp = datetime.fromisoformat(facts['observed_at'].replace('Z', '+00:00'))
        if not price.is_finite() or price < 0 or stamp.tzinfo is None:
            raise ValueError('Invalid price event')
        async with store.pool.acquire() as conn:
            # Tenant is never taken from message fields. The catalogue resolves its
            # real product id; consumers cannot fabricate another tenant's product.
            await conn.execute("""INSERT INTO agent_rig.price_history
                (tenant_id,product_id,price,currency,observed_at,event_id)
                SELECT tenant_id,id,$3,$4,$5,$6 FROM agent_rig.products
                WHERE tenant_id=$1 AND sku=$2 AND btrim(url)=$7
                ON CONFLICT(event_id) DO NOTHING""", UUID(tenant), sku, price, facts['currency'],
                stamp, UUID(message['event_id']), product['url'])


async def consume_tenant(service, http, tenant: str, consumer: str) -> int:
    body = {'event': EVENT, 'consumer': consumer}
    result = await request(http, tenant, 'receive', body)
    handled = 0
    for message in result['messages']:
        action = 'ack'
        try:
            await save_price(service, tenant, message)
            handled += 1
        except Exception:
            action = 'fail'
            log.warning('Price event processing will retry')
        # A crash between INSERT and ACK is harmless: event_id is unique.
        await request(http, tenant, 'settle', {**body, 'id': message['id'], 'action': action})
    return handled


async def consume_loop(service, http) -> None:
    consumer = str(uuid4())
    while True:
        try:
            tenants = await service.store.tenant_ids()
            for tenant in tenants:
                try:
                    await consume_tenant(service, http, str(UUID(tenant)), consumer)
                except Exception:
                    log.warning('Price event bus unavailable; catalogue work continues')
        except Exception:
            log.warning('Price event tenant discovery will retry')
        await asyncio.sleep(5)
