"""US cart state machine. Store checkout owns money; Rig owns price holds."""
from __future__ import annotations

import copy
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from rigbox.cart_store import CartStore
from rigbox.fulfilment import RigOrders
from rigbox.quotes.senders import DeliveryError
from rigbox.commerce import CommerceError, event_digest, money
from rigbox.locate import locate, stamp
from rigbox.locate_live import Holds, confirm, hold_secret

WINDOW = 300


def session_id(token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,100}', token):
        raise CommerceError('session')
    return 'cs_' + hashlib.sha256(token.encode()).hexdigest()


def public_line(line):
    result = {k: line[k] for k in ('id', 'product_id', 'title', 'quantity', 'price', 'currency', 'kind', 'delivery')}
    if line.get('unavailable'):
        result['unavailable'] = True
    if line.get('previous'):
        result['previous'] = {key: line['previous'][key] for key in ('price', 'delivery')}
    return result


def public_checkout(row):
    result = {k: row[k] for k in ('id', 'state', 'expires_at')}
    if row['state'] == 'pending':
        result['checkout_url'] = row['remote']['url']
    result['payment_confirmed'] = bool(row.get('paid_order_id'))
    if row.get('remote'):
        result.update(total=row.get('paid_total', row['remote']['total']), currency=row['remote']['currency'],
                      number=row.get('paid_order_number', row['remote']['id']))
    if row.get('late_payment'):
        result['late_payment'] = True
    return result


def delivery(value):
    if not isinstance(value, dict) or set(value) - {'ship_days', 'eta', 'min_days', 'max_days'}:
        raise CommerceError('delivery')
    if 'min_days' in value or 'max_days' in value:
        if (not all(type(value.get(key)) is int and 0 <= value[key] <= 60 for key in ('min_days', 'max_days'))
                or value['min_days'] > value['max_days']):
            raise CommerceError('delivery')
        return {key: value[key] for key in ('min_days', 'max_days')}
    if value.get('ship_days') is not None and (type(value['ship_days']) is not int or not 0 <= value['ship_days'] <= 3650):
        raise CommerceError('delivery')
    if value.get('eta') is not None and (not isinstance(value['eta'], str) or len(value['eta']) > 200):
        raise CommerceError('delivery')
    return {k: value[k] for k in ('ship_days', 'eta') if value.get(k) is not None}


class Cart:
    def __init__(self, service, platforms, *, clock=lambda: datetime.now(timezone.utc)):
        self.service, self.store, self.platforms, self.clock = service, service.store, platforms, clock
        self.rows = CartStore(self.store)
        self.fulfilment = RigOrders(self.store)

    async def config(self, tenant):
        config = await self.store.commerce_config(tenant)
        # Explicit US opt-in only. Quote stores, including ZA, are unchanged.
        if config.get('mode', 'quote') != 'cart' or config.get('region') != 'US':
            raise CommerceError('cart_disabled')
        return config

    async def platform(self, tenant, config):
        return await self.platforms(tenant, config)

    async def connect(self, tenant, config):
        allowed = {'mode', 'region', 'delivery', 'platform', 'site', 'vault_name', 'store_hash',
                   'checkout_hosts', 'products'}
        if set(config) - allowed or config.get('mode') not in ('quote', 'cart'):
            raise CommerceError('config')
        if config['mode'] == 'quote':
            previous = await self.store.commerce_config(tenant)
            await self.store.save_commerce_config(tenant, {**previous, 'mode': 'quote'})
            return {'saved': True}
        previous = await self.store.commerce_config(tenant)
        config = {'products': {}, 'delivery': {}, **previous, **config}
        if config.get('region') != 'US' or not config.get('vault_name') or not isinstance(config.get('products'), dict):
            raise CommerceError('config')
        config = {**config, 'delivery': delivery(config['delivery']) if config['delivery'] else {}}
        # Reject supplier fields, card details and arbitrary platform payloads.
        for product in config['products'].values():
            if not isinstance(product, dict) or set(product) - {'product_id', 'variant_id'}:
                raise CommerceError('product_mapping')
        async with self.rows.lock(tenant, 'connect'):
            platform = await self.platform(tenant, config)
            await platform.connect()
            await self.store.save_commerce_config(tenant, config)
        return {'saved': True}

    async def view(self, tenant, token, *, product_id=None, recheck=False):
        config = await self.config(tenant)
        identity = session_id(token)
        async with self.rows.lock(tenant, identity) as rows:
            row = await rows.get(tenant, identity) or {'lines': [], 'checkout': None}
            if recheck:
                old = await self.rows.get(tenant, row['checkout']) if row.get('checkout') else None
                if old and old['state'] != 'expired':
                    raise CommerceError('cart_locked')
                platform = await self.platform(tenant, config)
                updated, state = [], 'unchanged'
                for line in row['lines']:
                    try:
                        fresh = await self._line(tenant, config, platform, line['product_id'], line['quantity'],
                                                 line['kind'] == 'back_order')
                        fresh['id'] = line['id']
                        if any(fresh[key] != line[key] for key in ('price', 'delivery', 'kind')):
                            fresh['previous'] = {key: line[key] for key in ('price', 'delivery')}
                            if state != 'unavailable':
                                state = 'changed'
                    except CommerceError as exc:
                        if str(exc) not in {'stock', 'product'}:
                            raise
                        fresh = {**line, 'unavailable': True}
                        state = 'unavailable'
                    updated.append(fresh)
                await self.rows.release(tenant, row['lines'], self.clock())
                row.update(lines=updated, checkout=None, recheck_state=state)
                await rows.put(tenant, identity, row)
        result = {'lines': [public_line(line) for line in row['lines']], 'checkout_id': row.get('checkout'),
                  'recheck_state': row.get('recheck_state')}
        if product_id is not None:
            preview = await self._line(tenant, config, await self.platform(tenant, config), product_id, 1, True)
            result['product'] = public_line(preview)
            await self.rows.release(tenant, [preview], self.clock())
        return result

    async def _line(self, tenant, config, platform, sku, quantity, back_order=False):
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise CommerceError('quantity')
        deleted = await self.store.deleted_skus(tenant)
        product = next((p for p in await self.store.products(tenant) if p['sku'] == sku and sku not in deleted), None)
        if product is None or (product.get('active') is False and not product.get('supply_eligible')):
            raise CommerceError('product')
        moment = self.clock()
        line = {'id': 'li_' + secrets.token_hex(16), 'product_id': sku, 'title': product['name'],
                'quantity': quantity, 'currency': product['currency']}
        mapping = config['products'].get(sku)
        if mapping is None and hasattr(platform, 'product_mapping'):
            mapping = await platform.product_mapping(sku)
        stock = await platform.stock(mapping) if mapping else None
        if stock and stock['available'] and (stock['quantity'] is None or stock['quantity'] >= quantity):
            if not config['delivery']:
                raise CommerceError('delivery')
            line.update(kind='stock', delivery=config['delivery'], price=stock['price'], platform_product=mapping)
            private = {'kind': 'stock'}
        else:
            retailer = await self.store.retailer(tenant) or {}
            if retailer.get('locate_enabled') is not True:
                raise CommerceError('locate_disabled')
            available = await locate(self.store, tenant, product, now=moment)
            enough = (available['public']['status'] == 'available_to_order'
                      and Decimal(str(available['store'].get('quantity') or 0)) >= quantity)
            if enough:
                # Supplier quantity is checked again at checkout as well.
                checked = await confirm(self.store, tenant, product, self.service.locate_checker,
                                        secret=hold_secret(), now=moment, ttl=WINDOW)
                if 'hold_id' not in checked:
                    raise CommerceError('stock')
                private = checked['store']
                eta = private['offer'].get('eta')
                days = private['offer'].get('ship_days')
                days = int(days) if days is not None else None
                # Never invent a longer promise than the source actually gives.
                own = config['delivery'].get('max_days', config['delivery'].get('ship_days'))
                if own is None or days is None or days <= own:
                    raise CommerceError('delivery')
                line.update(kind='locate', price=money(checked['public']['price']['amount']),
                            delivery=delivery({'ship_days': days, 'eta': eta}), located=private)
                line['hold_id'] = checked['hold_id']
            elif (available['public']['status'] == 'unavailable' or not enough) and back_order is True:
                # Back-order is explicit; price comes from the store's own public product.
                shown = await self.service.product(tenant, sku)
                if shown['price']['currency'] != line['currency']:
                    raise CommerceError('currency')
                line.update(kind='back_order', price=money(shown['price']['amount']), delivery={'status': 'back_order'})
                private = {'kind': 'back_order'}
            else:
                raise CommerceError('stock')
        line['price'] = money(line['price'])
        if 'hold_id' not in line:
            line['hold_id'] = 'h_' + secrets.token_hex(16)
            await Holds(self.store).save(tenant, line['hold_id'], sku,
                {'price': {'amount': line['price'], 'currency': line['currency']}}, private,
                moment + timedelta(seconds=WINDOW))
        return line

    async def add(self, tenant, token, sku, quantity, *, back_order=False, line_id=None):
        config = await self.config(tenant)
        identity = session_id(token)
        async with self.rows.lock(tenant, identity) as rows:
            row = await rows.get(tenant, identity) or {'lines': [], 'checkout': None}
            if row.get('checkout'):
                prior = await self.rows.get(tenant, row['checkout'])
                if prior and prior.get('paid_order_id') and prior['state'] in {'placed', 'needs_person'}:
                    row = {'lines': [], 'checkout': None}
            existing = next((line for line in row['lines'] if line['product_id'] == sku), None)
            if row.get('checkout') or (len(row['lines']) >= 50 and existing is None):
                raise CommerceError('cart_locked')
            if existing and existing['id'] != line_id:
                raise CommerceError('duplicate_product')
            if line_id is not None and (existing is None or existing['id'] != line_id):
                raise CommerceError('not_found')
            line = await self._line(tenant, config, await self.platform(tenant, config), sku, quantity, back_order)
            if existing:
                await self.rows.release(tenant, [existing], self.clock())
                line['id'] = existing['id']
                row['lines'] = [line if value['id'] == line_id else value for value in row['lines']]
            else:
                row['lines'].append(line)
            row['recheck_state'] = 'unavailable' if any(value.get('unavailable') for value in row['lines']) else None
            await rows.put(tenant, identity, row)
        return await self.view(tenant, token)

    async def remove(self, tenant, token, line_id):
        await self.config(tenant)
        identity = session_id(token)
        async with self.rows.lock(tenant, identity) as rows:
            row = await rows.get(tenant, identity) or {'lines': [], 'checkout': None}
            if row.get('checkout'):
                raise CommerceError('cart_locked')
            removed = [line for line in row['lines'] if line['id'] == line_id]
            await self.rows.release(tenant, removed, self.clock())
            row['lines'] = [line for line in row['lines'] if line['id'] != line_id]
            await rows.put(tenant, identity, row)
        return await self.view(tenant, token)

    async def checkout(self, tenant, token):
        config = await self.config(tenant)
        identity = session_id(token)
        async with self.rows.lock(tenant, identity) as rows:
            cart = await rows.get(tenant, identity)
            if not cart or not cart['lines']:
                raise CommerceError('empty_cart')
            if cart.get('checkout'):
                old = await self.rows.get(tenant, cart['checkout'])
                if old and old['state'] != 'expired':
                    return public_checkout(old)
                cart['checkout'] = None
                expired = True
            else:
                expired = False
            platform = await self.platform(tenant, config)
            checked = []
            for line in cart['lines']:
                if not expired and not await Holds(self.store).peek(tenant, line['hold_id'], self.clock()):
                    raise CommerceError('hold_expired')
                fresh = await self._line(tenant, config, platform, line['product_id'], line['quantity'],
                                         line['kind'] == 'back_order')
                if any(fresh[k] != line[k] for k in ('price', 'currency', 'kind', 'delivery')):
                    await self.rows.release(tenant, [fresh], self.clock())
                    raise CommerceError('recheck_changed')
                fresh['id'] = line['id']
                checked.append(fresh)
            if len({line['currency'] for line in checked}) != 1:
                raise CommerceError('currency')
            moment = self.clock()
            checkout = {'id': 'co_' + secrets.token_hex(16), 'session': identity, 'state': 'creating',
                        'started_at': moment.isoformat(), 'expires_at': (moment + timedelta(seconds=WINDOW)).isoformat(),
                        'config': config, 'lines': checked, 'events': []}
            # Reservation is committed before the external create. A crash or
            # lost response leaves creating/create_unknown, never a second order.
            await self.rows.put(tenant, checkout['id'], checkout)
            cart['checkout'] = checkout['id']
            await rows.put(tenant, identity, cart)
        async with self.rows.lock(tenant, checkout['id']) as rows:
            try:
                checkout['remote'] = await platform.create(checkout['id'], checked, checked[0]['currency'])
                checkout['remote']['reference'] = checkout['id']
                checkout['state'] = 'pending'
            except Exception:
                checkout['state'] = 'create_unknown'
            await rows.put(tenant, checkout['id'], checkout)
        return public_checkout(checkout)

    async def order(self, tenant, token, checkout_id):
        await self.config(tenant)
        row = await self.rows.get(tenant, checkout_id)
        if not row or row['session'] != session_id(token):
            raise CommerceError('not_found')
        row = await self._fulfil(tenant, row)
        return {**public_checkout(row), 'lines': [public_line(line) for line in row['lines']]}

    async def _paid(self, tenant, row, order):
        if (not order.get('paid') or order.get('totals_valid') is not True
                or money(order['merchandise']) != row['remote']['merchandise']
                or order['currency'] != row['remote']['currency']):
            raise CommerceError('order_mismatch')
        row.update(state='paid_pending', paid_order_id=order['order_id'],
                   paid_order_number=order.get('number', order['order_id']), paid_total=money(order['total']),
                   late_payment=self.clock() >= stamp(row['expires_at']))

    async def _fulfil(self, tenant, row):
        # One bounded CAS lease covers all lines. External work holds no DB lock.
        now = self.clock()
        if row['state'] == 'placing':
            if stamp(row['attempted_at']) > now - timedelta(minutes=15):
                return row
        elif row['state'] != 'paid_pending':
            return row
        leased = {**row, 'state': 'placing', 'attempted_at': now.isoformat(),
                  'attempt_id': secrets.token_hex(16)}
        if not await self.rows.swap(tenant, row['id'], row, leased):
            return await self.rows.get(tenant, row['id'])
        finished = copy.deepcopy(leased)
        try:
            retailer = await self.store.retailer(tenant) or {}
            for line in finished['lines']:
                if line['kind'] == 'back_order':
                    line['fulfilment_state'] = 'back_order'
                elif line['kind'] == 'locate':
                    reference = 'cart:' + row['id'] + ':' + line['id']
                    order = await self.fulfilment.place_located_order(
                        tenant_id=tenant, quote_id=reference, located=line['located'],
                        price={'amount': line['price'], 'currency': line['currency']},
                        quantity=line['quantity'])
                    if retailer.get('auto_order') is True:
                        sent = await self.fulfilment.send_supplier_order(
                            tenant_id=tenant, quote_id=reference,
                            store_name=retailer.get('name') or '', store_email=retailer.get('email'))
                        if sent['purchase_order'].get('recorded') is False:
                            raise DeliveryError('order_unavailable')
                    line['order_number'] = order['order']['number']
                    line['fulfilment_state'] = 'placed'
            finished['state'] = 'placed'
        except Exception as exc:
            finished['state'] = ('needs_person' if isinstance(exc, DeliveryError)
                                 and str(exc) in {'order_mismatch', 'order_rejected'} else 'paid_pending')
        finished.update(attempt_id=None, attempted_at=None)
        if not await self.rows.swap(tenant, row['id'], leased, finished):
            # A slow worker never overwrites a newer lease or returns a 500.
            return await self.rows.get(tenant, row['id'])
        if finished['state'] == 'placed':
            await self.rows.release(tenant, finished['lines'], self.clock())
        return finished

    async def webhook(self, tenant, raw, headers):
        config = await self.store.commerce_config(tenant)
        if config.get('region') != 'US':
            raise CommerceError('cart_disabled')
        platform = await self.platform(tenant, config)
        event = platform.verify(raw, headers, now=self.clock().timestamp())
        digest = event_digest(raw)
        matched = 0
        for candidate in await self.rows.checkouts(tenant):
            if candidate['config']['platform'] != config['platform'] or not candidate.get('remote'):
                continue
            if candidate['state'] in {'paid_pending', 'placing'}:
                await self._fulfil(tenant, candidate)
                matched += 1
                continue
            async with self.rows.lock(tenant, candidate['id']) as rows:
                row = await rows.get(tenant, candidate['id'])
                if row['state'] in {'placed', 'needs_person'}:
                    continue
                if row['state'] in {'paid_pending', 'placing'}:
                    continue
                if config['platform'] == 'woocommerce' and str(event.get('id')) != row['remote']['id']:
                    continue
                platform = await self.platform(tenant, row['config'])
                order = await platform.read(row['remote'], event)
                if not order.get('paid'):
                    continue
                await self._paid(tenant, row, order)
                row['events'] = (row['events'] + [digest])[-100:]
                # Persist paid evidence before fulfilment. Its shared idempotent
                # step can be retried after a process crash, exactly once per line.
                await rows.put(tenant, row['id'], row)
            await self._fulfil(tenant, row)
            matched += 1
        return {'accepted': True, 'matched': matched}

    async def sweep(self, tenant):
        """Called every second on the US box; only tenant-scoped reads/writes."""
        for candidate in await self.rows.checkouts(tenant):
            if candidate['state'] in {'paid_pending', 'placing'}:
                await self._fulfil(tenant, candidate)
                continue
            async with self.rows.lock(tenant, candidate['id']) as rows:
                row = await rows.get(tenant, candidate['id'])
                if row['state'] in ('pending', 'cancel_unknown') and self.clock() >= stamp(row['expires_at']):
                    try:
                        platform = await self.platform(tenant, row['config'])
                        order = await platform.read(row['remote'])
                        if order.get('paid'):
                            # A read is not a signed webhook. Await signed evidence.
                            continue
                        await platform.cancel(row['remote'])
                        row['state'] = 'expired'
                        await self.rows.release(tenant, row['lines'], self.clock())
                    except Exception:
                        row['state'] = 'cancel_unknown'
                    await rows.put(tenant, row['id'], row)
