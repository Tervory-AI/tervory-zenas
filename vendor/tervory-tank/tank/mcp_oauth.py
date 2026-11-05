"""MCP G1 OAuth server: existing Trigger sign-in identity and hash-only CLI credentials.

Public protocol responses contain no service identity. Only the signed-in
Workspace corridor may approve a grant; client URLs are never browser redirects
until validated against public metadata or an explicit registration.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import access
import command
import device_login as device
import personal_tokens as pat
from tenant_db import tenant_scope

ISSUER = 'https://app.tervory.com'
RESOURCE = ISSUER + '/mcp'
PROTECTED_METADATA = ISSUER + '/.well-known/oauth-protected-resource/mcp'
CODE = re.compile(r'tvo1\.us-east\.([0-9a-f]{32})\.([A-Za-z0-9_-]{43})\Z')
CHALLENGE = re.compile(r'[A-Za-z0-9_-]{43}\Z')
VERIFIER = re.compile(r'[A-Za-z0-9._~-]{43,128}\Z')
router = APIRouter(tags=['MCP OAuth'])


def east_only() -> None:
    from main import box_region
    if box_region() != 'us-east':
        raise HTTPException(404, 'Not Found')


def oauth_error(name: str, status: int = 400) -> JSONResponse:
    return JSONResponse({'error': name}, status_code=status, headers=device.NO_STORE)


def redirect_allowed(value: str) -> bool:
    try:
        p = urlsplit(value)
        return (bool(p.hostname) and not p.username and not p.password and not p.fragment
                and not any(ord(c) < 33 or ord(c) == 127 for c in value)
                and '\\' not in value and p.port != 0
                and (p.scheme == 'https' or (p.scheme == 'http' and p.hostname in {'127.0.0.1', 'localhost'})))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Client:
    client_id: str
    name: str
    redirects: tuple[str, ...]


# Public metadata only, bounded in size and duration. Never OAuth credentials.
_metadata: OrderedDict[str, tuple[float, Client]] = OrderedDict()


def metadata_client(client_id: str, value: dict) -> Client:
    redirects, name = value.get('redirect_uris'), value.get('client_name', client_id)
    if (value.get('client_id') != client_id or not isinstance(name, str) or not 1 <= len(name) <= 100
            or not isinstance(redirects, list) or not 1 <= len(redirects) <= 32
            or any(not isinstance(uri, str) or len(uri) > 2048 or not redirect_allowed(uri) for uri in redirects)
            or value.get('token_endpoint_auth_method', 'none') != 'none'):
        raise ValueError('invalid client metadata')
    return Client(client_id, name, tuple(redirects))


async def client_metadata(client_id: str) -> Client:
    if not isinstance(client_id, str) or not 1 <= len(client_id) <= 2048:
        raise ValueError('invalid client')
    if client_id.startswith('https://'):
        p = urlsplit(client_id)
        if p.fragment or not p.path or p.path == '/':
            raise ValueError('invalid client metadata URL')
        access.origin(client_id)
        cached = _metadata.get(client_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        _metadata.pop(client_id, None)
        data, headers = await access.public_get(client_id, limit=65536)
        if len(data) > 65536:
            raise ValueError('client metadata too large')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError('invalid client metadata')
        client = metadata_client(client_id, value)
        control = next((v for k, v in headers.items() if k.lower() == 'cache-control'), '').lower()
        age = re.search(r'(?:^|,)\s*max-age=(\d+)', control)
        ttl = min(86400, int(age[1])) if age else 3600
        if 'no-store' not in control and 'no-cache' not in control and ttl > 0:
            _metadata[client_id] = (time.monotonic() + ttl, client)
            while len(_metadata) > 256:
                _metadata.popitem(last=False)
        return client
    # Registration ids are generated opaque identifiers, never alternative URLs.
    if not re.fullmatch(r'tvc_[0-9a-f]{32}', client_id):
        raise ValueError('invalid client')
    with device.platform():
        async with command.db().acquire() as conn:
            row = await conn.fetchrow('SELECT client_name,redirect_uris FROM tank.oauth_clients WHERE client_id=$1', client_id)
    if row is None:
        raise ValueError('unknown client')
    redirects = command.unpack(row['redirect_uris'])
    return metadata_client(client_id, {'client_id': client_id, 'client_name': row['client_name'], 'redirect_uris': redirects})


async def bounded_body(request: Request, limit: int = 65536) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError('request too large')
    return bytes(data)


async def form(request: Request) -> dict[str, str]:
    if request.headers.get('content-type', '').split(';')[0].lower() != 'application/x-www-form-urlencoded':
        raise ValueError('form required')
    pairs = parse_qsl((await bounded_body(request, 16384)).decode('utf-8'), keep_blank_values=True,
                      strict_parsing=True, max_num_fields=16)
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('duplicate parameter')
    return dict(pairs)


@router.get('/.well-known/oauth-protected-resource')
@router.get('/.well-known/oauth-protected-resource/mcp')
async def protected_resource():
    east_only()
    return JSONResponse({'resource': RESOURCE, 'authorization_servers': [ISSUER],
                         'bearer_methods_supported': ['header']}, headers=device.NO_STORE)


@router.get('/.well-known/oauth-authorization-server')
async def authorization_server():
    east_only()
    return JSONResponse({'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/oauth/authorize',
        'token_endpoint': ISSUER + '/oauth/token', 'registration_endpoint': ISSUER + '/oauth/register',
        'revocation_endpoint': ISSUER + '/oauth/revoke', 'code_challenge_methods_supported': ['S256'],
        'grant_types_supported': ['authorization_code', 'refresh_token'], 'response_types_supported': ['code'],
        'token_endpoint_auth_methods_supported': ['none'], 'client_id_metadata_document_supported': True},
        headers=device.NO_STORE)


@router.post('/oauth/register')
async def register(request: Request):
    east_only()
    try:
        value = json.loads(await bounded_body(request))
        if not isinstance(value, dict):
            return oauth_error('invalid_client_metadata')
        grants = value.get('grant_types', ['authorization_code', 'refresh_token'])
        responses = value.get('response_types', ['code'])
        if (value.get('token_endpoint_auth_method', 'none') != 'none'
                or not supported_values(grants, {'authorization_code', 'refresh_token'})
                or not supported_values(responses, {'code'})):
            return oauth_error('invalid_client_metadata')
        client_id = 'tvc_' + uuid.uuid4().hex
        client = metadata_client(client_id, {**value, 'client_id': client_id})
    except (ValueError, TypeError, RecursionError):
        return oauth_error('invalid_client_metadata')
    try:
        address = registration_address(request)
    except ValueError:
        return oauth_error('invalid_request')
    source = hashlib.sha256(address.encode()).hexdigest()
    with device.platform():
        async with command.db().acquire() as conn:
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(hashtext($1))', source)
                count = await conn.fetchval("""SELECT count(*) FROM tank.oauth_clients
                    WHERE source_address_hash=$1 AND created_at>now()-interval '1 hour'""", source)
                if count >= 50:
                    return oauth_error('temporarily_unavailable', 429)
                await conn.execute('INSERT INTO tank.oauth_clients(client_id,client_name,redirect_uris,source_address_hash) VALUES($1,$2,$3::jsonb,$4)',
                                   client.client_id, client.name, json.dumps(client.redirects), source)
    return JSONResponse({'client_id': client.client_id, 'client_name': client.name,
        'redirect_uris': list(client.redirects), 'token_endpoint_auth_method': 'none',
        'grant_types': sorted(set(grants)), 'response_types': sorted(set(responses))},
        status_code=201, headers=device.NO_STORE)


def supported_values(values, supported: set[str]) -> bool:
    return (isinstance(values, list) and bool(values)
            and all(isinstance(value, str) for value in values) and set(values) <= supported)


def registration_address(request: Request) -> str:
    # Only Workspace's existing service authentication can assert a proxy-derived
    # source. Public callers cannot select a bucket by spoofing any header.
    from main import TANK_API_KEY
    if command.key_matches(request.headers.get('x-tank-key', ''), TANK_API_KEY):
        return str(ipaddress.ip_address(request.headers.get('x-tervory-oauth-source', '')))
    return str(ipaddress.ip_address(request.client.host)) if request.client else 'unknown'


class Authorization(BaseModel):
    model_config = ConfigDict(extra='ignore')
    response_type: str
    client_id: str = Field(min_length=1, max_length=2048)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=4096)
    code_challenge: str = Field(min_length=43, max_length=43)
    code_challenge_method: str
    resource: str


class Decision(Authorization):
    action: str


async def validate_authorization(value: Authorization) -> Client:
    if value.resource != RESOURCE:
        raise HTTPException(400, 'invalid_target')
    if value.response_type != 'code' or value.code_challenge_method != 'S256' or not CHALLENGE.fullmatch(value.code_challenge):
        raise HTTPException(400, 'invalid_request')
    try:
        client = await client_metadata(value.client_id)
    except (ValueError, OSError, httpx.HTTPError, RecursionError, HTTPException):
        raise HTTPException(400, 'invalid_client') from None
    if value.redirect_uri not in client.redirects:
        raise HTTPException(400, 'invalid_redirect_uri')
    return client


@router.get('/oauth/authorize')
async def authorization(request: Request, actor: command.Actor = Depends(pat.workspace_actor)):
    east_only()
    try:
        pairs = list(request.query_params.multi_items())
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError('duplicate parameter')
        value = Authorization.model_validate(dict(pairs))
    except (ValueError, ValidationError):
        raise HTTPException(400, 'invalid_request') from None
    client = await validate_authorization(value)
    return JSONResponse({'client_id': client.client_id, 'client_name': client.name,
                         'redirect_uri': value.redirect_uri, 'tenant_id': str(actor.tenant_id), 'region': 'us-east'},
                        headers=device.NO_STORE)


@router.post('/oauth/authorize')
async def decide(body: Decision, actor: command.Actor = Depends(pat.workspace_actor)):
    east_only()
    client = await validate_authorization(body)
    if body.action not in {'approve', 'deny'}:
        raise HTTPException(400, 'invalid_request')
    query = {'state': body.state, 'iss': ISSUER}
    if body.action == 'deny':
        query['error'] = 'access_denied'
    else:
        code = f'tvo1.us-east.{actor.tenant_id.hex}.{secrets.token_urlsafe(32)}'
        with tenant_scope(actor.tenant_id):
            async with command.db().acquire() as conn:
                await conn.execute("""INSERT INTO tank.oauth_codes(code_hash,tenant_id,user_id,region,client_id,client_name,redirect_uri,challenge,resource,expires_at,trigger_signin)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,now()+$10::interval,true)""", pat.digest(code), actor.tenant_id,
                    actor.user_id, 'us-east', client.client_id, client.name, body.redirect_uri, body.code_challenge,
                    body.resource, timedelta(minutes=10))
        query['code'] = code
    # Preserve an already registered redirect query, exactly; fragments were refused.
    separator = '&' if '?' in body.redirect_uri else '?'
    return JSONResponse({'redirect_to': body.redirect_uri + separator + urlencode(query)}, headers=device.NO_STORE)


@router.post('/oauth/token')
async def token(request: Request):
    east_only()
    try:
        value = await form(request)
    except (ValueError, UnicodeError):
        return oauth_error('invalid_request')
    if value.get('resource') != RESOURCE:
        return oauth_error('invalid_target')
    if request.headers.get('authorization') or value.get('client_secret'):
        return oauth_error('invalid_client')
    client_id = value.get('client_id', '')
    if not client_id or len(client_id) > 2048:
        return oauth_error('invalid_client')
    if value.get('grant_type') == 'refresh_token':
        return await refresh_grant(value.get('refresh_token', ''), client_id)
    if value.get('grant_type') != 'authorization_code':
        return oauth_error('unsupported_grant_type')
    code, verifier = value.get('code', ''), value.get('code_verifier', '')
    match = CODE.fullmatch(code)
    if match is None or not VERIFIER.fullmatch(verifier):
        return oauth_error('invalid_grant')
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')
    tenant = uuid.UUID(hex=match[1])
    access_token, refresh_token = device.credential_pair('us-east', tenant)
    issued = device.now()
    expiries = (issued + device.ACCESS_TTL, issued + device.REFRESH_TTL)
    with tenant_scope(tenant):
        async with command.db().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""SELECT *,expires_at>now() AS unexpired FROM tank.oauth_codes
                    WHERE tenant_id=$1 AND code_hash=$2 FOR UPDATE""", tenant, pat.digest(code))
                if (row is None or not row['trigger_signin'] or row['client_id'] != client_id or row['redirect_uri'] != value.get('redirect_uri')
                        or row['resource'] != RESOURCE or row['region'] != 'us-east'
                        or not hmac.compare_digest(row['challenge'], challenge)):
                    return oauth_error('invalid_grant')
                if row['consumed_at'] is not None:
                    await conn.execute("""UPDATE tank.personal_tokens SET revoked_at=COALESCE(revoked_at,now())
                        WHERE tenant_id=$1 AND token_id=$2 AND client_id=$3 AND audience='mcp'""",
                        tenant, row['token_id'], client_id)
                    return oauth_error('invalid_grant')
                if not row['unexpired']:
                    return oauth_error('invalid_grant')
                ident = await conn.fetchval("""INSERT INTO tank.personal_tokens(tenant_id,user_id,region,name,device_name,token_hash,expires_at,refresh_hash,refresh_expires_at,audience,client_id,trigger_signin,trigger_signin_issuer)
                    VALUES($1,$2,'us-east',$3,$3,$4,$5,$6,$7,'mcp',$8,true,'https://auth.tervory.com') RETURNING token_id""",
                    tenant, row['user_id'], row['client_name'], pat.digest(access_token), expiries[0],
                    pat.digest(refresh_token), expiries[1], client_id)
                await conn.execute("""UPDATE tank.oauth_codes SET consumed_at=now(),token_id=$3
                    WHERE tenant_id=$1 AND code_hash=$2""", tenant, pat.digest(code), ident)
    return JSONResponse(device.grant(access_token, refresh_token, ident, 'us-east', issued), headers=device.NO_STORE)


async def refresh_grant(presented: str, client_id: str):
    match = device.REFRESH.fullmatch(presented)
    if match is None or match[1] != 'us-east':
        return oauth_error('invalid_grant')
    tenant = uuid.UUID(hex=match[2])
    with tenant_scope(tenant):
        async with command.db().acquire() as conn:
            row = await conn.fetchrow("""SELECT client_id FROM tank.personal_tokens
                WHERE tenant_id=$1 AND region='us-east' AND audience='mcp' AND refresh_hash=$2
                AND revoked_at IS NULL AND refresh_expires_at>now()""", tenant, pat.digest(presented))
    if row is None or row['client_id'] != client_id:
        return oauth_error('invalid_grant')
    return await device.rotate_refresh(presented, audience='mcp', client_id=client_id)


@router.post('/oauth/revoke')
async def revoke(request: Request):
    east_only()
    try:
        value = await form(request)
    except (ValueError, UnicodeError):
        return oauth_error('invalid_request')
    token = value.get('token', '')
    match = pat.TOKEN.fullmatch(token) or device.REFRESH.fullmatch(token)
    if match and match[1] == 'us-east':
        tenant = uuid.UUID(hex=match[2])
        column = 'token_hash' if pat.TOKEN.fullmatch(token) else 'refresh_hash'
        with tenant_scope(tenant):
            async with command.db().acquire() as conn:
                await conn.execute("""UPDATE tank.personal_tokens SET revoked_at=COALESCE(revoked_at,now())
                    WHERE tenant_id=$1 AND region='us-east' AND audience='mcp' AND client_id=$2
                    AND (($4='token_hash' AND token_hash=$3) OR ($4='refresh_hash' AND refresh_hash=$3))""",
                    tenant, value.get('client_id', ''), pat.digest(token), column)
    return Response(status_code=200, headers=device.NO_STORE)
