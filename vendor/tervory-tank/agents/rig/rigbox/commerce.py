"""Store-owned checkout APIs. Only explicitly projected order lines leave Rig.

No card endpoint, payment provider or model is involved. Credentials are supplied
by the existing sealed vault corridor, never by a shopper request.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlsplit
from rigbox.deadline import bounded


class CommerceError(Exception):
    """Machine code only; platform responses and credentials never escape."""


def money(value) -> str:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.quantize(Decimal('.01')):
            raise ValueError()
        return format(number, '.2f')
    except (ValueError, InvalidOperation, TypeError):
        raise CommerceError('invalid_amount') from None


def origin(value: str) -> str:
    p = urlsplit(value)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment
            or p.path not in ('', '/') or p.port not in (None, 443)):
        raise CommerceError('invalid_origin')
    return 'https://' + p.hostname.lower()


def signature(platform, raw: bytes, headers, secret: str, *, now=None) -> None:
    """Raw-body verification; BC uses its documented Standard Webhooks scheme."""
    if not secret or len(raw) > 256_000:
        raise CommerceError('signature')
    if platform == 'bigcommerce':
        identity, stamp = headers.get('webhook-id', ''), headers.get('webhook-timestamp', '')
        try:
            if not identity or abs((now if now is not None else time.time()) - int(stamp)) > 300:
                raise ValueError()
        except (ValueError, TypeError):
            raise CommerceError('signature') from None
        message = identity.encode() + b'.' + stamp.encode() + b'.' + raw
        expected = base64.b64encode(hmac.digest(secret.encode(), message, 'sha256')).decode()
        valid = any(hmac.compare_digest('v1,' + expected, part)
                    for part in headers.get('webhook-signature', '').split())
    else:
        header = {'shopify': 'x-shopify-hmac-sha256', 'woocommerce': 'x-wc-webhook-signature'}.get(platform)
        if not header:
            raise CommerceError('platform')
        expected = base64.b64encode(hmac.digest(secret.encode(), raw, 'sha256')).decode()
        valid = hmac.compare_digest(expected, headers.get(header, ''))
    if not valid:
        raise CommerceError('signature')


class Platform:
    def __init__(self, http, config: dict, credential: dict):
        self.http, self.config, self.credential = http, config, credential
        self.kind = config['platform']
        self.site = origin(config['site'])
        if self.kind == 'shopify' and not re.fullmatch(r'https://[a-z0-9-]+\.myshopify\.com', self.site):
            raise CommerceError('invalid_origin')
        if self.kind == 'bigcommerce':
            store_hash = config.get('store_hash', '')
            if not re.fullmatch('[a-z0-9]+', store_hash):
                raise CommerceError('invalid_store')
            self.base = 'https://api.bigcommerce.com/stores/' + store_hash
        else:
            self.base = self.site + ('/wp-json/wc/v3' if self.kind == 'woocommerce' else '/admin/api/2026-10')
        if self.kind not in ('shopify', 'woocommerce', 'bigcommerce'):
            raise CommerceError('platform')

    def __repr__(self):
        return f'Platform({self.kind})'

    @bounded(25, lambda: CommerceError('platform_request'))
    async def request(self, method, path, **kwargs):
        headers = {'Accept': 'application/json'}
        if self.kind == 'woocommerce':
            auth = (self.credential['consumer_key'], self.credential['consumer_secret'])
            kwargs['auth'] = auth
        else:
            headers['X-Shopify-Access-Token' if self.kind == 'shopify' else 'X-Auth-Token'] = self.credential['access_token']
        try:
            response = await self.http.request(method, self.base + path, headers=headers,
                                               follow_redirects=False, timeout=25, **kwargs)
            if response.status_code not in (200, 201, 202, 204):
                raise CommerceError('platform_request')
            return response.json() if response.content else {}
        except Exception:
            raise CommerceError('platform_request') from None

    async def graphql(self, query, variables):
        data = await self.request('POST', '/graphql.json', json={'query': query, 'variables': variables})
        if data.get('errors') or not isinstance(data.get('data'), dict):
            raise CommerceError('platform_request')
        return data['data']

    async def connect(self):
        """Woo's hidden carrier is found by SKU; a reconnect never recreates it."""
        if self.kind == 'shopify':
            await self.graphql('query{shop{id}}', {})
            return
        if self.kind == 'bigcommerce':
            await self.request('GET', '/v2/store')
            return
        found = await self.request('GET', '/products', params={'sku': 'tervory-rig-line'})
        if not found:
            await self.request('POST', '/products', json={
                'name': 'tervory-rig-line', 'sku': 'tervory-rig-line', 'type': 'simple',
                'catalog_visibility': 'hidden', 'status': 'private', 'virtual': False,
                'manage_stock': False, 'tax_status': 'taxable', 'regular_price': '0'})

    async def product_mapping(self, sku):
        """Match the store's exact SKU; never substitute a similarly named item."""
        if self.kind == 'shopify':
            data = await self.graphql('query($q:String!){productVariants(first:20,query:$q){nodes{id sku}}}',
                                      {'q': 'sku:' + json.dumps(sku)})
            found = [p for p in data['productVariants']['nodes'] if p.get('sku') == sku]
            return {'variant_id': found[0]['id']} if len(found) == 1 else None
        if self.kind == 'woocommerce':
            found = [p for p in await self.request('GET', '/products', params={'sku': sku}) if p.get('sku') == sku]
        else:
            # BigCommerce product lookup excludes option SKUs. The global
            # variant endpoint returns both base and option variants.
            data = await self.request('GET', '/v3/catalog/variants', params={'sku': sku})
            found = [p for p in data['data'] if p.get('sku') == sku]
            pages = data.get('meta', {}).get('pagination', {})
            if len(found) != 1 or pages.get('total_pages', 1) > 1:
                return None
            return {'product_id': found[0]['product_id'], 'variant_id': found[0]['id']}
        return {'product_id': found[0]['id']} if len(found) == 1 else None

    async def stock(self, product: dict) -> dict:
        """Read the store's own product, including price and available quantity."""
        if self.kind == 'woocommerce':
            path = '/products/' + str(int(product['product_id']))
            if product.get('variant_id'):
                path += '/variations/' + str(int(product['variant_id']))
            p = await self.request('GET', path)
            return {'price': money(p['price']), 'available': p['stock_status'] == 'instock',
                    'quantity': None if p.get('manage_stock') is False else p.get('stock_quantity')}
        if self.kind == 'shopify':
            data = await self.graphql('query($id:ID!){productVariant(id:$id){price inventoryQuantity availableForSale inventoryItem{tracked}}}',
                                      {'id': product['variant_id']})
            p = data['productVariant']
            if not p:
                raise CommerceError('product')
            return {'price': money(p['price']), 'available': p['availableForSale'] is True,
                    'quantity': None if p.get('inventoryItem', {}).get('tracked') is False else p.get('inventoryQuantity')}
        p = (await self.request('GET', '/v3/catalog/products/' + str(int(product['product_id']))))['data']
        available = p.get('availability') == 'available' and p.get('is_visible') is not False
        tracking = p.get('inventory_tracking')
        # A variant mapping is mandatory for a product with variants.
        if product.get('variant_id'):
            variant = (await self.request('GET', '/v3/catalog/products/' + str(int(product['product_id']))
                                    + '/variants/' + str(int(product['variant_id']))))['data']
            if variant.get('product_id') != int(product['product_id']) or variant.get('id') != int(product['variant_id']):
                raise CommerceError('product_mapping')
            quantity = variant.get('inventory_level') if tracking == 'variant' else p.get('inventory_level') if tracking == 'product' else None
            return {'price': money(variant['calculated_price']),
                    'available': available and variant.get('purchasing_disabled') is False,
                    'quantity': quantity}
        if tracking == 'variant':
            raise CommerceError('product_mapping')
        return {'price': money(p['calculated_price']), 'available': available,
                'quantity': p.get('inventory_level') if tracking == 'product' else None}

    def checkout_url(self, url):
        p = urlsplit(url)
        allowed = {urlsplit(self.site).hostname, *self.config.get('checkout_hosts', [])}
        if p.scheme != 'https' or p.username or p.password or p.hostname not in allowed:
            raise CommerceError('checkout_url')
        return url

    async def create(self, reference: str, lines: list[dict], currency: str) -> dict:
        if self.kind == 'woocommerce':
            items = []
            for line in lines:
                item = {'quantity': line['quantity']}
                if line['kind'] == 'stock':
                    item['product_id'] = int(line['platform_product']['product_id'])
                    if line['platform_product'].get('variant_id'):
                        item['variation_id'] = int(line['platform_product']['variant_id'])
                else:
                    item.update(sku='tervory-rig-line', name=line['title'])
                amount = money(Decimal(line['price']) * line['quantity'])
                item.update(subtotal=amount, total=amount, meta_data=[
                    {'key': '_tervory_line_id', 'value': line['id']},
                    {'key': '_tervory_delivery', 'value': json.dumps(line['delivery'], sort_keys=True)}])
                items.append(item)
            data = await self.request('POST', '/orders', json={'status': 'pending', 'currency': currency,
                'line_items': items, 'meta_data': [{'key': '_tervory_checkout', 'value': reference}]})
            result = {'id': str(data['id']), 'url': self.checkout_url(data['payment_url']),
                      'total': money(data['total']), 'currency': data['currency']}
        elif self.kind == 'shopify':
            items = []
            for line in lines:
                item = {'quantity': line['quantity']}
                if line['kind'] == 'stock':
                    item['variantId'] = line['platform_product']['variant_id']
                else:
                    item.update(title=line['title'], originalUnitPrice=line['price'], requiresShipping=True,
                                customAttributes=[{'key': '_tervory_delivery', 'value': json.dumps(line['delivery'])}])
                items.append(item)
            data = await self.graphql('mutation($input:DraftOrderInput!){draftOrderCreate(input:$input){draftOrder{id invoiceUrl totalPriceSet{shopMoney{amount currencyCode}}} userErrors{field message}}}',
                {'input': {'lineItems': items, 'presentmentCurrencyCode': currency,
                           'tags': ['tervory:' + reference]}})
            if data['draftOrderCreate']['userErrors']:
                raise CommerceError('platform_request')
            p = data['draftOrderCreate']['draftOrder']
            amount = p['totalPriceSet']['shopMoney']
            result = {'id': p['id'], 'url': self.checkout_url(p['invoiceUrl']),
                      'total': money(amount['amount']), 'currency': amount['currencyCode']}
        else:
            items, custom = [], []
            for line in lines:
                if line['kind'] == 'stock':
                    items.append({**line['platform_product'], 'quantity': line['quantity']})
                else:
                    custom.append({'sku': line['id'], 'name': line['title'], 'quantity': line['quantity'],
                                   'list_price': float(line['price'])})
            p = (await self.request('POST', '/v3/carts', json={'line_items': items, 'custom_items': custom,
                                        'currency': {'code': currency}}))['data']
            redirect = (await self.request('POST', '/v3/carts/' + p['id'] + '/redirect_urls', json={}))['data']
            result = {'id': p['id'], 'url': self.checkout_url(redirect['checkout_url']),
                      'total': money(p['cart_amount']), 'currency': p['currency']['code']}
        if result['currency'] != currency:
            raise CommerceError('currency')
        result['merchandise'] = money(sum(Decimal(line['price']) * line['quantity'] for line in lines))
        return result

    async def read(self, remote: dict, event: dict | None = None) -> dict:
        """Return a minimal verified order projection, never the raw customer data."""
        if self.kind == 'woocommerce':
            p = await self.request('GET', '/orders/' + quote(remote['id'], safe=''))
            merchandise = sum(Decimal(item['total']) for item in p['line_items'])
            fees = sum(Decimal(item['total']) for item in p.get('fee_lines', []))
            calculated = merchandise + fees + Decimal(p.get('shipping_total', '0')) + Decimal(p.get('total_tax', '0'))
            return {'paid': bool(p.get('date_paid')) and str(p['status']).upper() in {'PROCESSING', 'COMPLETED'},
                    'merchandise': money(merchandise), 'totals_valid': money(calculated) == money(p['total']),
                    'total': money(p['total']), 'currency': p['currency'], 'order_id': str(p['id'])}
        if self.kind == 'shopify':
            fields = 'id tags displayFinancialStatus subtotalPriceSet{shopMoney{amount currencyCode}} totalPriceSet{shopMoney{amount currencyCode}} totalTaxSet{shopMoney{amount}} totalShippingPriceSet{shopMoney{amount}}'
            if event and event.get('admin_graphql_api_id'):
                order = (await self.graphql('query($id:ID!){order(id:$id){' + fields + '}}',
                                             {'id': event['admin_graphql_api_id']}))['order']
                if order and 'tervory:' + remote['reference'] not in order.get('tags', []):
                    return {'paid': False}
            else:
                p = (await self.graphql('query($id:ID!){draftOrder(id:$id){order{' + fields + '}}}',
                                        {'id': remote['id']}))['draftOrder']
                order = p.get('order') if p else None
            if not order:
                return {'paid': False}
            amount = order['totalPriceSet']['shopMoney']
            subtotal = Decimal(order['subtotalPriceSet']['shopMoney']['amount'])
            calculated = subtotal + Decimal(order['totalTaxSet']['shopMoney']['amount']) + Decimal(order['totalShippingPriceSet']['shopMoney']['amount'])
            return {'paid': order['displayFinancialStatus'] == 'PAID', 'total': money(amount['amount']),
                    'merchandise': money(subtotal), 'totals_valid': money(calculated) == money(amount['amount']),
                    'currency': amount['currencyCode'], 'order_id': order['id']}
        # The signed callback supplies an order id, but cart_id from read-back is
        # the authority that binds that order to this checkout and tenant.
        if event is None:
            orders = await self.request('GET', '/v2/orders', params={'cart_id': remote['id']})
            if not orders:
                return {'paid': False}
            p = orders[0]
        else:
            p = await self.request('GET', '/v2/orders/' + str(int(event['data']['id'])))
        if p.get('cart_id') != remote['id']:
            return {'paid': False}
        subtotal = Decimal(p['subtotal_ex_tax'])
        calculated = subtotal + Decimal(p['subtotal_tax']) + sum(Decimal(p.get(k, '0')) for k in
            ('shipping_cost_inc_tax', 'handling_cost_inc_tax', 'wrapping_cost_inc_tax'))
        return {'paid': str(p.get('payment_status', '')).upper() == 'CAPTURED', 'total': money(p['total_inc_tax']),
                'merchandise': money(subtotal), 'totals_valid': money(calculated) == money(p['total_inc_tax']),
                'currency': p['currency_code'], 'order_id': str(p['id'])}

    async def cancel(self, remote: dict):
        if self.kind == 'woocommerce':
            await self.request('PUT', '/orders/' + quote(remote['id'], safe=''), json={'status': 'cancelled'})
        elif self.kind == 'shopify':
            result = await self.graphql('mutation($input:DraftOrderDeleteInput!){draftOrderDelete(input:$input){deletedId userErrors{field message}}}',
                                        {'input': {'id': remote['id']}})
            if result['draftOrderDelete']['userErrors']:
                raise CommerceError('cancel_unknown')
        else:
            await self.request('DELETE', '/v3/carts/' + quote(remote['id'], safe=''))

    def verify(self, raw, headers, *, now=None):
        signature(self.kind, raw, headers, self.credential.get('webhook_secret', ''), now=now)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise CommerceError('signature') from None


def event_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
