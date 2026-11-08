"""Workspace vault contract. Tenant grants are read on every request.

Tank owns metadata and sealed credentials, never an agent's R2 credentials.
Files travel over the existing authenticated corridor to their owning room.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from access import origin
from vault import seal, open_seal
from tenant_db import activate_tenant

VAULTS = {f'{room}.vault': room for room in ('base', 'tiger', 'rig', 'tisan', 'radix', 'zenas')}
VAULTS['lab.vault'] = 'market_lab'
VAULTS['ghost.vault'] = 'ghost'
LIMIT = 32 * 1024 * 1024


def fail(status: int, message: str):
    raise HTTPException(status, message)


def clean(value, maximum=200, *, empty=False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or any(ord(c) < 32 for c in value):
        fail(422, 'Check the fields and try again.')
    return value.strip()


def public_url(value) -> str:
    value = clean(value, 2000)
    origin(value)  # HTTPS, no credentials, no local/private address.
    parsed = urlsplit(value)
    # Tokens belong in the sealed token field, never a stored/displayed URL.
    if parsed.query or parsed.fragment:
        fail(422, 'Use an HTTPS address without a query or fragment. Save keys separately.')
    return value


def build_router(tank):
    router = APIRouter(prefix='/v1/vaults')

    @router.post('/rig.vault/commerce/resolve')
    async def rig_commerce_resolve(request: Request):
        # The existing private retailer corridor, never a browser response.
        # Reuse the vault's tenant/name AAD; no second encryption or key store.
        expected = os.environ.get('RIG_RETAILER_KEY', '')
        supplied = request.headers.get('x-rig-retailer-key', '')
        if not expected or not supplied or not hmac.compare_digest(expected.encode(), supplied.encode()):
            return JSONResponse({'error': {'code': 'unauthorized'}}, status_code=401)
        data = await body(request)
        try:
            owner = uuid.UUID(str(data['tenant_id']))
            name = clean(data['name'], 100)
        except (ValueError, KeyError, TypeError):
            return JSONResponse({'error': {'code': 'invalid'}}, status_code=422)
        activate_tenant(owner)
        async with tank.pool().acquire() as conn:
            enabled = await conn.fetchval("SELECT 1 FROM tank.tenant_squad WHERE tenant_id=$1 AND agent='rig'", owner)
            encrypted = await conn.fetchval("SELECT sealed FROM tank.vault_items WHERE tenant_id=$1 AND vault='rig.vault' AND kind='secrets' AND name=$2", owner, name) if enabled else None
        if not encrypted:
            return JSONResponse({'error': {'code': 'not_found'}}, status_code=404)
        try:
            value = open_seal(encrypted, f'plugins:v1:{owner}:rig.vault:secret:{name}')['value']
            credential = json.loads(value)
            if not isinstance(credential, dict):
                raise ValueError()
        except Exception:
            return JSONResponse({'error': {'code': 'unavailable'}}, status_code=503)
        return JSONResponse({'credential': credential}, headers={'Cache-Control': 'no-store'})

    async def authorize(request: Request, vault: str, tenant) -> tuple[uuid.UUID, str, str]:
        key = request.headers.get('x-tank-key', '')
        if not key or not tank.TANK_API_KEY or not hmac.compare_digest(key.encode(), tank.TANK_API_KEY.encode()):
            fail(401, 'Workspace authentication required')
        room = VAULTS.get(vault)
        if not room or request.headers.get('x-tervory-squad') != room:
            fail(403, 'This file area belongs to another product.')
        if not request.headers.get('x-tervory-user', '').strip():
            fail(401, 'Sign in to use this product.')
        user = clean(request.headers.get('x-tervory-user', ''), 200)
        try:
            owner = uuid.UUID(str(tenant))
        except ValueError:
            fail(422, 'A workspace is required.')
        asserted = request.headers.get('x-tervory-tenant')
        if asserted is not None and asserted != str(owner):
            fail(403, 'Workspace does not match.')
        if request.headers.get('x-tervory-home-region', tank.box_region()) != tank.box_region():
            fail(403, 'This request must stay on its home server.')
        activate_tenant(owner)
        admins = __import__("trigger_signin_config").founders()
        if room == 'ghost':
            # Autumbra has no tenant_squad grant on main. Connect is an explicit
            # opt-in by this workspace's administrator, never a grant of tools.
            from command import identity
            actor = await identity(request)
            if actor.tenant_id != owner or not (actor.founder or actor.tenant_admin):
                fail(403, 'A workspace administrator must connect this tool.')
        else:
            async with tank.pool().acquire() as conn:
                if user not in admins and not await conn.fetchval('SELECT 1 FROM tank.tenant_squad WHERE tenant_id=$1 AND agent=$2', owner, room):
                    fail(403, 'This product is not enabled for your workspace.')
        return owner, room, user

    async def body(request: Request) -> dict:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 64000:
                fail(413, 'Request is too large.')
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            fail(422, 'Check the fields and try again.')

    async def room_call(room: str, method: str, path: str, owner, *, params=None, content=None):
        key = os.environ.get(f'TANK_KEY_{room.upper()}', '')
        if not key or tank._http is None:
            fail(503, 'File storage is not connected. Nothing was confirmed.')
        try:
            response = await tank._http.request(method, f'http://agent-{room.replace("_", "-")}:8000{path}',
                params={'tenant_id': str(owner), 'region': tank.box_region(), **(params or {})}, content=content,
                headers={'x-tank-key': key, **({'content-type': 'application/json'} if path.startswith('/market/feeds/') and content is not None else {})}, timeout=120)
            if response.status_code >= 400:
                fail(503, 'File storage could not complete the request. Please try again.')
            return response.json() if response.content else {}
        except HTTPException:
            raise
        except Exception:
            fail(503, 'File storage could not complete the request. Please try again.')

    def reply(value):
        return JSONResponse(value, headers={'Cache-Control': 'private, no-store'})

    @router.get('/{vault}/plugins')
    async def plugins(vault: str, tenant_id: str, request: Request):
        owner, room, _ = await authorize(request, vault, tenant_id)
        result = {'vault': vault, 'enabled': True, 'message': None, 'secrets': [], 'files': [], 'links': [], 'connections': []}
        async with tank.pool().acquire() as conn:
            rows = await conn.fetch('SELECT kind, name, metadata, mcp_state FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 ORDER BY created_at,id', owner, vault)
        for row in rows:
            meta = json.loads(row['metadata']) if isinstance(row['metadata'], str) else dict(row['metadata'])
            if row['kind'] == 'connections':
                meta = {**meta, 'mcp_state': row['mcp_state']}
            result[row['kind']].append(meta)
        if room == 'market_lab':
            feeds = await room_call(room, 'GET', '/market/feeds', owner)
            result['links'].extend(feeds['links'])
        return reply(result)

    async def save(owner, vault, kind, name, meta, encrypted=None):
        async with tank.pool().acquire() as conn:
            await conn.execute('''INSERT INTO tank.vault_items(tenant_id,vault,kind,name,metadata,sealed)
                VALUES($1,$2,$3,$4,$5::jsonb,$6) ON CONFLICT(tenant_id,vault,kind,name)
                DO UPDATE SET metadata=excluded.metadata,sealed=excluded.sealed''', owner, vault, kind, name, json.dumps(meta), encrypted)

    @router.post('/{vault}/secrets')
    async def secret(vault: str, request: Request):
        data = await body(request)
        owner, _, _ = await authorize(request, vault, data.get('tenant_id'))
        name = clean(data.get('name'), 100)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*', name):
            fail(422, 'Use a simple key name.')
        purpose = clean(data.get('purpose', ''), 500, empty=True)
        value = clean(data.get('value'), 16000)
        encrypted = seal({'value': value}, f'plugins:v1:{owner}:{vault}:secret:{name}')
        from datetime import datetime, timezone
        await save(owner, vault, 'secrets', name, {'name': name, 'purpose': purpose, 'mask': '••••••••', 'saved_at': datetime.now(timezone.utc).isoformat()}, encrypted)
        return reply({'ok': True})

    @router.post('/{vault}/secrets/resolve')
    async def resolve(vault: str, request: Request):
        data = await body(request)
        owner, _, _ = await authorize(request, vault, data.get('tenant_id'))
        name = clean(data.get('name'), 100)
        async with tank.pool().acquire() as conn:
            encrypted = await conn.fetchval("SELECT sealed FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='secrets' AND name=$3", owner, vault, name)
        return reply({'found': True, 'value': open_seal(encrypted, f'plugins:v1:{owner}:{vault}:secret:{name}')['value']} if encrypted else {'found': False})

    @router.delete('/{vault}/secrets')
    async def remove_secret(vault: str, tenant_id: str, name: str, request: Request):
        owner, _, _ = await authorize(request, vault, tenant_id)
        async with tank.pool().acquire() as conn:
            await conn.execute("DELETE FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='secrets' AND name=$3", owner, vault, name)
        return reply({'ok': True})

    @router.post('/{vault}/links')
    async def link(vault: str, request: Request):
        data = await body(request)
        owner, room, _ = await authorize(request, vault, data.get('tenant_id'))
        ident = str(uuid.uuid4())
        frequency = data.get('frequency')
        if frequency not in {'daily', 'weekly'}:
            fail(422, 'Choose daily or weekly.')
        if room == 'market_lab':
            # The room owns the subscription and the worker queue atomically.
            # Its shared Tiger URL check also resolves DNS on every fetch/redirect.
            payload = {'url': clean(data.get('url'), 2000),
                       'label': clean(data.get('label', ''), empty=True), 'frequency': frequency}
            await room_call(room, 'PUT', f'/market/feeds/{ident}', owner, content=json.dumps(payload).encode())
            return reply({'ok': True})
        meta = {'id': ident, 'url': public_url(data.get('url')), 'label': clean(data.get('label', ''), empty=True), 'frequency': frequency, 'last_checked': None, 'status': 'not_checked'}
        await save(owner, vault, 'links', ident, meta)
        return reply({'ok': True})

    @router.post('/{vault}/connections')
    async def connection(vault: str, request: Request):
        data = await body(request)
        owner, _, _ = await authorize(request, vault, data.get('tenant_id'))
        ident = str(uuid.uuid4())
        auth_type = data.get('auth_type')
        if auth_type not in {'token', 'provider', 'none'}:
            fail(422, 'Choose a connection sign-in method.')
        token = clean(data.get('token', ''), 16000, empty=auth_type != 'token')
        meta = {'id': ident, 'name': clean(data.get('name')), 'server_url': public_url(data.get('server_url')), 'auth_type': auth_type,
                'status': 'needs_auth' if auth_type == 'provider' else 'saved', 'last_used': None, 'has_token': bool(token)}
        encrypted = seal({'token': token}, f'plugins:v1:{owner}:{vault}:connection:{ident}') if token else None
        await save(owner, vault, 'connections', ident, meta, encrypted)
        from command import Actor, current_actor
        from mcp_connections import discover
        actor = current_actor(Actor(owner, request.headers['x-tervory-user'], False, frozenset({VAULTS[vault]})))
        await discover(actor, vault, ident, force=False)
        return reply({'ok': True})

    @router.post('/{vault}/connections/{ident}/discover')
    async def discover_connection(vault: str, ident: str, request: Request):
        data = await body(request)
        owner, room, user = await authorize(request, vault, data.get('tenant_id'))
        from command import Actor, current_actor
        from mcp_connections import discover
        tools = await discover(current_actor(Actor(owner, user, False, frozenset({room}))), vault, ident, force=False)
        return reply({'tools': tools})

    @router.post('/{vault}/files')
    async def files(vault: str, tenant_id: str, request: Request):
        owner, room, _ = await authorize(request, vault, tenant_id)
        # Bound the entire multipart request, including bodies without a length.
        total = 0
        stream = request.stream
        async def bounded():
            nonlocal total
            async for chunk in stream():
                total += len(chunk)
                if total > LIMIT + 256000:
                    raise MultiPartException('Upload exceeds the limit')
                yield chunk
        from starlette.formparsers import MultiPartParser, MultiPartException
        try:
            parser = MultiPartParser(request.headers, bounded(), max_files=10, max_fields=0)
            form = await parser.parse()
        except MultiPartException:
            fail(413 if total > LIMIT + 256000 else 422, 'Upload up to 32 MB at a time.' if total > LIMIT + 256000 else 'Choose up to 10 files to upload.')
        try:
            uploads = form.getlist('file')
            if not uploads or any(not isinstance(f, UploadFile) for f in uploads) or len(uploads) != len(form.multi_items()):
                fail(422, 'Choose files to upload.')
            for upload in uploads:
                name = clean(upload.filename, 240)
                if '/' in name or '\\' in name:
                    fail(422, 'Use a file name without folders.')
                if room == 'market_lab' and not name.lower().endswith(('.pdf', '.csv', '.xlsx', '.docx')):
                    fail(422, 'Only CSV, XLSX, and PDF files can be uploaded.')
            from datetime import datetime, timezone
            for upload in uploads:
                ident = str(uuid.uuid4())
                data = await upload.read(LIMIT + 1)
                if not data or len(data) > LIMIT:
                    fail(413, 'Choose a nonempty file of up to 32 MB.')
                meta = {'id': ident, 'name': upload.filename, 'type': upload.content_type or 'application/octet-stream', 'size': len(data),
                        'rows': None, 'pages': None, 'uploaded_at': datetime.now(timezone.utc).isoformat(), 'status': 'saved'}
                path = f'/market/files/{ident}' if room == 'market_lab' else f'/storage/objects/uploads/{ident}'
                result = await room_call(room, 'PUT', path, owner, params={'name': upload.filename} if room == 'market_lab' else None, content=data)
                if room != 'market_lab' and (result.get('bucket') != 'tervory-' + room.replace('_', '-') or result.get('key') != f'{tank.box_region()}/{owner}/uploads/{ident}'):
                    fail(502, 'File storage did not confirm the correct workspace.')
                if room == 'market_lab':
                    meta.update({key: result[key] for key in ('status', 'rows', 'pages', 'reason')})
                await save(owner, vault, 'files', ident, meta)
        finally:
            await form.close()
        return reply({'ok': True})

    @router.delete('/{vault}/{kind}/{ident}')
    async def remove(vault: str, kind: str, ident: str, tenant_id: str, request: Request):
        owner, room, _ = await authorize(request, vault, tenant_id)
        if kind not in {'files', 'links', 'connections'}:
            fail(404, 'Not found')
        try:
            ident = str(uuid.UUID(ident))
        except ValueError:
            fail(404, 'Not found')
        if room == 'market_lab' and kind == 'links':
            # The room owns links (#421) and its delete is an idempotent no-op for an id this
            # tenant does not have. Links saved before #421 may still sit in tank.vault_items
            # (never migrated; the listing above still shows them). The vault contract (#362)
            # answers 404 only when neither place has the id, as for files and connections.
            links = await room_call(room, 'GET', '/market/feeds', owner)
            in_room = ident in {str(link.get('id')) for link in links.get('links', [])}
            async with tank.pool().acquire() as conn:
                legacy = await conn.fetchval("SELECT count(*) FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='links' AND name=$3", owner, vault, ident)
            if not in_room and not legacy:
                fail(404, 'Not found')
            if in_room:
                await room_call(room, 'DELETE', f'/market/feeds/{ident}', owner)
            async with tank.pool().acquire() as conn:
                await conn.execute("DELETE FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='links' AND name=$3", owner, vault, ident)
            return reply({'ok': True})
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow('SELECT name FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind=$3 AND name=$4 FOR UPDATE', owner, vault, kind, ident)
                if row is None:
                    fail(404, 'Not found')
                if kind == 'files':
                    path = f'/market/files/{ident}' if room == 'market_lab' else f'/storage/objects/uploads/{ident}'
                    await room_call(room, 'DELETE', path, owner)
                await conn.execute('DELETE FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind=$3 AND name=$4', owner, vault, kind, ident)
        return reply({'ok': True})

    from autumbra_connect import register as register_autumbra_connect
    register_autumbra_connect(router, tank, authorize, body, save, reply)
    return router
