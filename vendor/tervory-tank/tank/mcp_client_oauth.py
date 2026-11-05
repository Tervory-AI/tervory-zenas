"""MCP OAuth client. All credentials and pending state stay in their sealed vault row.

ACCESS browser RPCs are product-specific cookie sessions, not OAuth redirects;
reuse the vault's scoped connection identity instead of sending OAuth tokens to rooms.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from mcp_client import Client, Refused, SignInRequired, document, request
from mcp_connections import approved, connection, credentials, db, state, store_credentials, unpack

CLIENT_ID = 'https://app.tervory.com/oauth/client/mcp.json'
REDIRECT_URI = 'https://app.tervory.com/oauth/callback'
METADATA = {'client_id': CLIENT_ID, 'client_name': 'Tervory', 'redirect_uris': [REDIRECT_URI],
            'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
            'token_endpoint_auth_method': 'none'}


def endpoint(value) -> str:
    from access import origin
    if not isinstance(value, str) or len(value) > 2000:
        raise Refused('MCP OAuth endpoint refused')
    origin(value)
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment:
        raise Refused('MCP OAuth endpoint refused')
    return value


async def get_json(url: str) -> dict:
    status, raw, _ = await request(endpoint(url))
    if status != 200:
        raise Refused('MCP OAuth metadata refused')
    return document(raw)


async def metadata(resource: str, challenge: str) -> tuple[dict, dict, str]:
    base = urlsplit(resource)
    site = f'{base.scheme}://{base.netloc}'
    # Parse only bounded quoted Bearer parameters, never an error description.
    params = dict(re.findall(r'\b(resource_metadata|scope)="([^"\\\r\n]*)"', challenge))
    candidates = [params['resource_metadata']] if params.get('resource_metadata') else [
        site + '/.well-known/oauth-protected-resource' + base.path,
        site + '/.well-known/oauth-protected-resource']
    protected = None
    for url in dict.fromkeys(candidates):
        try:
            protected = await get_json(url)
            break
        except Refused:
            continue
    if not protected or protected.get('resource') != resource:
        raise Refused('MCP OAuth resource refused')
    issuers = protected.get('authorization_servers')
    if not isinstance(issuers, list) or not issuers:
        raise Refused('MCP OAuth issuer refused')
    # Deterministically choose the first advertised issuer; never select an arbitrary input.
    issuer = endpoint(issuers[0])
    issuer_parts = urlsplit(issuer)
    root = f'{issuer_parts.scheme}://{issuer_parts.netloc}'
    path = issuer_parts.path.rstrip('/')
    urls = [root + '/.well-known/oauth-authorization-server' + path,
            root + '/.well-known/openid-configuration' + path]
    if path:
        urls.append(issuer.rstrip('/') + '/.well-known/openid-configuration')
    authorization = None
    for url in urls:
        try:
            candidate = await get_json(url)
            if candidate.get('issuer') == issuer:
                authorization = candidate
                break
        except Refused:
            continue
    if not authorization or 'S256' not in authorization.get('code_challenge_methods_supported', []):
        raise Refused('MCP OAuth PKCE refused')
    for key in ('authorization_endpoint', 'token_endpoint'):
        endpoint(authorization.get(key))
    scope = params.get('scope')
    if scope is None:
        scopes = protected.get('scopes_supported', [])
        if not isinstance(scopes, list) or any(not isinstance(item, str) for item in scopes):
            raise Refused('MCP OAuth scopes refused')
        scope = ' '.join(scopes)
    if len(scope) > 4000 or any(ord(c) < 32 for c in scope):
        raise Refused('MCP OAuth scopes refused')
    return protected, authorization, scope


async def post(url: str, payload: dict, *, registration=False) -> dict:
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json' if registration else 'application/x-www-form-urlencoded'}
    content = json.dumps(payload).encode() if registration else urlencode(payload).encode()
    status, raw, _ = await request(endpoint(url), method='POST', headers=headers, content=content)
    if status not in ({200, 201} if registration else {200}):
        raise Refused('MCP OAuth request refused')
    return document(raw)


async def start(actor, vault: str, ident: str) -> str:
    row = await connection(actor, vault, ident)
    resource = row['metadata']['server_url']
    await approved(resource)
    if row['metadata']['auth_type'] != 'provider':
        raise Refused('MCP OAuth connection refused')
    async def gate():
        await approved(resource)
    challenge = ''
    try:
        await Client(resource, gate).initialize()
    except SignInRequired as exc:
        challenge = exc.challenge
    protected, authorization, scope = await metadata(resource, challenge)
    client_id = CLIENT_ID
    if authorization.get('client_id_metadata_document_supported') is not True:
        registration = await post(authorization.get('registration_endpoint'),
                                  {key: value for key, value in METADATA.items() if key != 'client_id'}, registration=True)
        client_id = registration.get('client_id')
        if not isinstance(client_id, str) or not client_id or len(client_id) > 2000 or registration.get('token_endpoint_auth_method', 'none') != 'none':
            raise Refused('MCP OAuth registration refused')
    verifier = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(32)
    opaque_state = f'{vault}:{ident}:{nonce}'
    pending = {'state_hash': hashlib.sha256(opaque_state.encode()).hexdigest(), 'verifier': verifier,
               'user_id': actor.user_id, 'expires_at': time.time() + 600, 'resource': resource,
               'issuer': authorization['issuer'], 'token_endpoint': authorization['token_endpoint'], 'client_id': client_id}
    await store_credentials(row, {**credentials(row), 'pending': pending}, mcp_state='needs_sign_in')
    params = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': REDIRECT_URI,
              'code_challenge_method': 'S256', 'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),
              'state': opaque_state, 'resource': resource}
    if scope:
        params['scope'] = scope
    return endpoint(authorization['authorization_endpoint']) + '?' + urlencode(params)


def token_data(response: dict, previous: dict) -> dict:
    access = response.get('access_token')
    expiry = response.get('expires_in')
    if not isinstance(access, str) or not access or len(access) > 16000 or response.get('token_type', '').lower() != 'bearer':
        raise Refused('MCP OAuth token refused')
    if expiry is not None and (isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not 0 < expiry <= 366 * 86400):
        raise Refused('MCP OAuth expiry refused')
    refresh = response.get('refresh_token', previous.get('refresh_token'))
    if refresh is not None and (not isinstance(refresh, str) or len(refresh) > 16000):
        raise Refused('MCP OAuth token refused')
    return {key: value for key, value in {**previous, 'access_token': access,
            'refresh_token': refresh, 'expires_at': time.time() + expiry if expiry is not None else None}.items() if key != 'pending'}


async def callback(actor, opaque_state: str, code: str | None, error: str | None, issuer: str | None = None) -> bool:
    try:
        vault, ident, nonce = opaque_state.split(':')
        if len(nonce) > 100 or len(opaque_state) > 500:
            raise ValueError()
    except ValueError:
        raise Refused('MCP OAuth state refused') from None
    row = await connection(actor, vault, ident)
    await approved(row['metadata']['server_url'])
    data = credentials(row)
    pending = data.get('pending', {})
    if (not pending or pending.get('user_id') != actor.user_id or pending.get('expires_at', 0) < time.time()
            or not hmac.compare_digest(pending.get('state_hash', ''), hashlib.sha256(opaque_state.encode()).hexdigest())
            or (issuer is not None and issuer != pending.get('issuer'))):
        raise Refused('MCP OAuth state refused')
    # Claim/consume before the token exchange. Replays fail even after a timeout.
    await store_credentials(row, {key: value for key, value in data.items() if key != 'pending'}, mcp_state='needs_sign_in')
    if error or not code or len(code) > 8000:
        await state(row, 'sign_in_failed')
        return False
    try:
        await approved(row['metadata']['server_url'])
        response = await post(pending['token_endpoint'], {'grant_type': 'authorization_code', 'code': code,
            'client_id': pending['client_id'], 'redirect_uri': REDIRECT_URI,
            'code_verifier': pending['verifier'], 'resource': pending['resource']})
        value = token_data(response, {key: pending[key] for key in ('resource', 'issuer', 'token_endpoint', 'client_id')})
        await store_credentials(row, value, mcp_state='connected')
        from mcp_connections import discover
        await discover(actor, vault, ident, force=False)
        return True
    except Refused:
        await state(row, 'sign_in_failed')
        return False


async def refresh(row, data: dict, *, force=False) -> dict:
    # A row lock works across Tank processes. Re-read inside the lock, including
    # when the caller's snapshot was taken before a concurrent token rotation.
    failed = False
    async with db().acquire() as conn, conn.transaction():
        locked = await conn.fetchrow("SELECT * FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='connections' AND name=$3 FOR UPDATE", row['tenant_id'], row['vault'], row['name'])
        if not locked:
            raise SignInRequired()
        current = {**dict(locked), 'metadata': unpack(locked['metadata'])}
        resource = current['metadata']['server_url']
        await approved(resource)
        latest = credentials(current)
        expiry = latest.get('expires_at')
        fresh = expiry is None or expiry > time.time() + 60
        if latest.get('resource') != resource:
            raise SignInRequired()
        rotated = latest.get('access_token') != data.get('access_token')
        if fresh and (not force or rotated):
            row.update(current)
            return latest
        if not latest.get('refresh_token'):
            raise SignInRequired()
        try:
            response = await post(latest['token_endpoint'], {'grant_type': 'refresh_token', 'refresh_token': latest['refresh_token'],
                                                           'client_id': latest['client_id'], 'resource': resource})
            updated = token_data(response, latest)
            await store_credentials(current, updated, mcp_state='connected', conn=conn)
            row.update(current)
            return updated
        except Refused:
            # Compare sealed bytes as well: a stale failure never overwrites a
            # successful renewal or a new authorization handoff.
            await state(current, 'sign_in_failed', conn=conn)
            row.update(current)
            failed = True
    if failed:
        raise Refused('MCP OAuth refresh refused') from None


# Workspace server corridor; no browser-supplied tenant/user assertions are accepted.
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from command import Actor, identity, authorized_actor

router = APIRouter(prefix='/v1/command/mcp')


class StartBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    vault: str = Field(max_length=40)
    connection_id: str = Field(max_length=36)


class CallbackBody(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    state: str = Field(max_length=500)
    code: str | None = Field(default=None, max_length=8000)
    error: str | None = Field(default=None, max_length=200)
    iss: str | None = Field(default=None, max_length=2000)


@router.post('/oauth/start')
async def start_route(request: Request, actor: Actor = Depends(identity)):
    from access import parse
    body = await parse(request, StartBody, limit=2000)
    try:
        return {'url': await start(await authorized_actor(actor), body.vault, body.connection_id)}
    except Refused:
        raise HTTPException(409, 'Secure connection refused.') from None


@router.post('/oauth/callback')
async def callback_route(request: Request, actor: Actor = Depends(identity)):
    from access import parse
    body = await parse(request, CallbackBody, limit=12000)
    try:
        return {'ok': await callback(await authorized_actor(actor), body.state, body.code, body.error, body.iss)}
    except Refused:
        raise HTTPException(409, 'Secure connection refused.') from None
