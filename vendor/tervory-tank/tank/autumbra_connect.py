"""Autumbra connect backend: tenant-authorized vault writes; no credential to Watch."""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException, Request
from vault import seal

VAULT = 'ghost.vault'
PERMISSIONS = ['Zone Read', 'Zone WAF Edit']


async def verify_cloudflare(http, token: str, zone_name: str, zone_id: str) -> None:
    """Read-only checks. Self-verification cannot reveal a scoped token's policy."""
    headers = {'Authorization': 'Bearer ' + token}
    try:
        response = await http.get('https://api.cloudflare.com/client/v4/user/tokens/verify',
                                  headers=headers, follow_redirects=False, timeout=10)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        if response.status_code != 200 or data.get('success') is not True or data['result']['status'] != 'active':
            raise ValueError()
        response = await http.get('https://api.cloudflare.com/client/v4/zones',
                                  headers=headers, params={'per_page': 50}, follow_redirects=False, timeout=10)
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        rows = data.get('result')
        if (response.status_code != 200 or data.get('success') is not True or not isinstance(rows, list)
                or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('id') != zone_id or rows[0].get('name') != zone_name
                or data.get('result_info', {}).get('total_count', len(rows)) != 1):
            raise ValueError()
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(422, 'Cloudflare token or single-zone scope could not be verified.') from None


def register(router, tank, authorize, body, save, reply):
    async def client(request, data):
        owner, _, _ = await authorize(request, VAULT, data.get('tenant_id'))
        # Vault authorization alone permits reads; connection changes need owner role.
        from command import identity
        actor = await identity(request)
        if actor.tenant_id != owner or not (actor.founder or actor.tenant_admin):
            raise HTTPException(403, 'A workspace administrator must connect this tool.')
        return owner

    @router.get('/ghost.vault/autumbra/alerts')
    async def alerts(request: Request, tenant_id: uuid.UUID):
        owner, _, _ = await authorize(request, VAULT, str(tenant_id))
        from ghost_routes import call_room
        response = await call_room('GET', 'ghost', '/ghost/autumbra/workspace-alerts', params={'tenant_id': str(owner)})
        if response.status_code != 200:
            raise HTTPException(503, 'Workspace alerts are unavailable.')
        return reply(response.json())

    @router.post('/ghost.vault/autumbra/connect/cloudflare')
    async def cloudflare(request: Request):
        data = await body(request)
        owner = await client(request, data)
        try:
            name, token = data['name'], data['token']
            zone_id, zone_name = data['zone_id'], data['zone_name']
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', name)
                    or not isinstance(token, str) or not 1 <= len(token) <= 16000
                    or any(ord(c) < 33 for c in token)
                    or not re.fullmatch(r'[a-f0-9]{32}', zone_id)
                    or not re.fullmatch(r'[a-z0-9.-]{1,253}', zone_name)
                    or data.get('permissions') != PERMISSIONS
                    or data.get('specific_zone_only') is not True):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, 'Use a named token scoped only to this zone with Zone Read and Zone WAF Edit.') from None
        if tank._http is None:
            raise HTTPException(503, 'Cloudflare verification is unavailable.')
        await verify_cloudflare(tank._http, token, zone_name, zone_id)
        encrypted = seal({'value': token}, f'plugins:v1:{owner}:{VAULT}:secret:{name}')
        await save(owner, VAULT, 'secrets', name, {
            'name': name, 'purpose': 'Autumbra Cloudflare edge', 'mask': '••••••••',
            'zone_name': zone_name, 'zone_id': zone_id, 'permissions': PERMISSIONS,
            'scope_confirmed_by_client': True, 'saved_at': datetime.now(UTC).isoformat(),
        }, encrypted)
        return reply({'saved': True, 'name': name, 'status': 'awaiting_enforce_binding',
                      'runtime_setting': 'CLOUDFLARE_API_TOKEN_ENFORCE'})

    @router.post('/ghost.vault/autumbra/connect/google_workspace')
    async def google(request: Request):
        data = await body(request)
        owner = await client(request, data)
        # Watch validates provider field shapes; only these non-secret fields cross.
        fields = ('audience', 'service_account', 'subscription', 'customer_id')
        if any(not isinstance(data.get(k), str) or not 1 <= len(data[k]) <= 1000 for k in fields):
            raise HTTPException(422, 'Complete the Google push subscription fields.')
        binding = {k: data[k] for k in fields} | {'tenant_id': str(owner)}
        # Stable tenant/subscription identity makes repeated connect requests idempotent.
        ident = str(uuid.uuid5(owner, binding['subscription']))
        binding['connection_id'] = ident
        from ghost_routes import call_room
        response = await call_room('POST', 'ghost', '/ghost/autumbra/connections/google_workspace', body=binding)
        if response.status_code != 200:
            raise HTTPException(422, 'Google push binding was refused.')
        name = 'GOOGLE_WORKSPACE_ALERTS_' + ident.replace('-', '')
        encrypted = seal({'value': json.dumps(binding)}, f'plugins:v1:{owner}:{VAULT}:secret:{name}')
        await save(owner, VAULT, 'secrets', name, {'name': name, 'purpose': 'Google Workspace Alert Center push binding',
                   'mask': '••••••••', 'saved_at': datetime.now(UTC).isoformat()}, encrypted)
        return reply({'saved': True, 'name': name, 'connection_id': ident, 'status': 'awaiting_verified_push',
                      'push_path': '/ghost/autumbra/webhooks/google_workspace/' + ident})
