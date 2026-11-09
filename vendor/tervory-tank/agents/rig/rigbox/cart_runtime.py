"""US-only wiring. Credentials stay in the existing Tank vault at rest."""
import asyncio
import logging
import os
import socket
import httpx

from chassis.tenant_db import tenant_scope
from rigbox.cart import Cart
from rigbox.commerce import CommerceError, Platform
from rigbox.deadline import bounded


class PublicCommerceHTTP:
    """Pin each credential-bearing store request to its checked public address."""
    def __init__(self, http):
        self.http = http

    async def request(self, method, url, **kwargs):
        from chassis.public_urls import _blocked_address
        target = httpx.URL(url)
        hostname = target.raw_host.decode('ascii')
        async with asyncio.timeout(kwargs.get('timeout', 25)):
            answers = await asyncio.get_running_loop().getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
            if target.scheme != 'https' or target.port not in (None, 443) or not addresses or any(
                    _blocked_address(address) for address in addresses):
                raise CommerceError('connection_unavailable')
            # Pool keys use the pinned IP. Do not reuse a TLS connection for
            # a different shop on that IP with a different certificate name.
            headers = {**kwargs.pop('headers', {}), 'Host': target.netloc.decode('ascii'), 'Connection': 'close'}
            return await self.http.request(method, target.copy_with(host=addresses[0]),
                headers=headers, extensions={'sni_hostname': hostname}, **kwargs)


class Platforms:
    def __init__(self, http, tank):
        self.http, self.tank = http, tank

    @bounded(60, lambda: CommerceError('connection_unavailable'))
    async def __call__(self, tenant, config):
        from chassis.public_urls import check_url
        # Reuse the rooms' existing public-address check. Never follow redirects
        # with store credentials. Tests replace this boundary, not live DNS.
        try:
            response = await self.http.post(self.tank._base + '/v1/vaults/rig.vault/commerce/resolve',
                headers={'x-rig-retailer-key': os.environ.get('RIG_RETAILER_KEY', '')},
                json={'tenant_id': str(tenant), 'name': config['vault_name']},
                timeout=20, follow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            credential = response.json()['credential']
            if config.get('platform') == 'bigcommerce' and not config.get('site'):
                import re
                store_hash = config.get('store_hash', '')
                if not re.fullmatch('[a-z0-9]+', store_hash):
                    raise ValueError()
                store = await self.http.get('https://api.bigcommerce.com/stores/' + store_hash + '/v2/store',
                    headers={'X-Auth-Token': credential['access_token']}, timeout=20, follow_redirects=False)
                if store.status_code != 200:
                    raise ValueError()
                config['site'] = store.json()['secure_url']
            await asyncio.to_thread(check_url, config['site'])
            return Platform(PublicCommerceHTTP(self.http), config, credential)
        except Exception:
            raise CommerceError('connection_unavailable') from None


def install(service, http, tank):
    if os.environ.get('TANK_REGION', 'us-east') != 'us-east':
        return None
    service.cart = Cart(service, Platforms(http, tank))

    async def sweep_tenant(tenant):
        with tenant_scope(tenant):
            try:
                await service.cart.sweep(tenant)
            except Exception:
                # No remote body/exception/credential in logs. Durable rows keep
                # uncertain states for the next pass and private order view.
                return

    async def loop():
        while True:
            try:
                with tenant_scope(None, administrative=True):
                    tenants = await service.store.tenant_ids()
                await asyncio.gather(*(sweep_tenant(tenant) for tenant in tenants))
            except Exception:
                logging.getLogger(__name__).warning('Cart tenant discovery will retry')
            await asyncio.sleep(1)

    return asyncio.create_task(loop())
