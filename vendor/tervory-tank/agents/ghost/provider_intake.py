"""Tenant-bound Google Alert Center intake. No alert bodies or mailbox content persist."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request

from .client_settings import load_settings, save_settings, set_hand
from .native_webhooks import verify_google_push
from .webhooks import WebhookRejected

INDEX_TENANT = '00000000-0000-0000-0000-000000000000'


def google_binding(data: dict) -> dict:
    """Only explicit provider identifiers. No token, tenant override or arbitrary URL."""
    tenant = str(uuid.UUID(str(data['tenant_id'])))
    from urllib.parse import urlsplit
    audience = data['audience']
    parsed = urlsplit(audience)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in {None, 443}
            or len(audience) > 1000):
        raise ValueError('Invalid Google push audience.')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+\.iam\.gserviceaccount\.com', data['service_account']):
        raise ValueError('Invalid Google push service account.')
    if not re.fullmatch(r'projects/[a-zA-Z0-9_.:-]+/subscriptions/[a-zA-Z0-9_.~-]+', data['subscription']):
        raise ValueError('Invalid Google subscription.')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', data['customer_id']):
        raise ValueError('Invalid Workspace customer.')
    return {key: data[key] for key in ('audience', 'service_account', 'subscription', 'customer_id')} | {'tenant_id': tenant}


def alert_metadata(raw: bytes, binding: dict) -> dict:
    envelope = json.loads(raw)
    message = envelope['message']
    attrs = message['attributes']
    if (envelope['subscription'] != binding['subscription']
            or attrs.get('alertcenter_resource_type') != 'ALERT'
            or attrs.get('alertcenter_payload_format') != 'JSON'
            or attrs.get('alertcenter_resource_status') not in {'CREATED', 'MODIFIED', 'DELETED', 'UNDELETED'}):
        raise ValueError('Invalid Alert Center notification.')
    alert = json.loads(base64.b64decode(message['data'], validate=True))
    if alert.get('customerId') != binding['customer_id']:
        raise ValueError('Workspace customer does not match.')
    # Opaque IDs and classifications only, never names, email, raw data or links.
    for key in ('alertId', 'type', 'source'):
        if not isinstance(alert.get(key), str) or not 1 <= len(alert[key]) <= 200:
            raise ValueError('Invalid Alert Center notification.')
    return {'id': hashlib.sha256((binding['subscription'] + ':' + message['messageId']).encode()).hexdigest(),
            'provider': 'google_workspace', 'action': 'alert_received', 'applied': False,
            'tenant_id': binding['tenant_id'], 'alert_id_sha256': hashlib.sha256(alert['alertId'].encode()).hexdigest(),
            'alert_type_sha256': hashlib.sha256(alert['type'].encode()).hexdigest(),
            'resource_status': attrs['alertcenter_resource_status'],
            'timestamp': datetime.now(UTC).isoformat(), 'raw_log_seen': False}


def register(app, require_tank, bounded_json, tank_factory):
    @app.get('/ghost/autumbra/workspace-alerts', dependencies=[Depends(require_tank)])
    async def alerts(tenant_id: uuid.UUID):
        return {'items': app.state.store.get_state(str(tenant_id), 'autumbra_workspace_alerts', [])}

    @app.post('/ghost/autumbra/connections/google_workspace', dependencies=[Depends(require_tank)])
    async def configure(request: Request):
        try:
            data = json.loads(await bounded_json(request, 4000))
            binding = google_binding(data)
            ident = str(uuid.UUID(str(data['connection_id'])))
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, 'Google connection was not understood.') from None
        index = app.state.store.get_state(INDEX_TENANT, 'autumbra_google_bindings', {})
        old = index.get(ident)
        if old and old['tenant_id'] != binding['tenant_id']:
            raise HTTPException(409, 'Connection belongs to another workspace.')
        # A subscription cannot be claimed by two tenants, even with different IDs.
        if any(row['subscription'] == binding['subscription'] and row['tenant_id'] != binding['tenant_id'] for row in index.values()):
            raise HTTPException(409, 'Subscription belongs to another workspace.')
        index[ident] = binding
        app.state.store.set_state(INDEX_TENANT, 'autumbra_google_bindings', index)
        return {'connection_id': ident, 'status': 'awaiting_verified_push'}

    @app.post('/ghost/autumbra/webhooks/google_workspace/{connection_id}')
    async def intake(connection_id: uuid.UUID, request: Request):
        index = app.state.store.get_state(INDEX_TENANT, 'autumbra_google_bindings', {})
        binding = index.get(str(connection_id))
        if binding is None:
            raise HTTPException(401, 'Google push authentication required.')
        raw = await bounded_json(request, 64000)
        try:
            await verify_google_push(raw, request.headers.get('authorization'), tank_factory(getattr(app.state, 'http', None)), config=binding, allow_duplicate=True)
            receipt = alert_metadata(raw, binding)
        except (WebhookRejected, ValueError, KeyError, TypeError):
            raise HTTPException(401, 'Google push authentication required.') from None
        tenant = binding['tenant_id']
        rows = app.state.store.get_state(tenant, 'autumbra_workspace_alerts', [])
        previous = next((row for row in rows if row['id'] == receipt['id']), None)
        if previous is not None:
            return {'accepted': True, 'stored': False, 'queued': False, 'duplicate': True, 'receipt': previous}
        rows = rows[-99:] + [receipt]
        app.state.store.set_state(tenant, 'autumbra_workspace_alerts', rows)
        settings = set_hand(load_settings(app.state.store, tenant), 'google_workspace', connected=True)
        save_settings(app.state.store, settings)
        return {'accepted': True, 'stored': False, 'queued': False, 'receipt': receipt}
