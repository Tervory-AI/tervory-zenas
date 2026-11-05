"""Trigger sign-in OIDC verification shared by both regional device flows."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from urllib.parse import urlsplit

import httpx
import jwt
import trigger_signin_config as registry

ISSUERS = registry.ISSUERS
ISSUER = registry.ISSUER
ORG_CLAIM = 'urn:zitadel:iam:user:resourceowner:id'
# Zitadel defines this reserved claim as roles for the requesting application's
# own project. Never combine it with project-specific claims from other projects.
ROLES_CLAIM = 'urn:zitadel:iam:org:project:roles'
SCOPE = 'openid urn:zitadel:iam:user:resourceowner urn:zitadel:iam:org:project:role:org_admin urn:zitadel:iam:org:project:role:member'
_cache: dict = {}
_lock = asyncio.Lock()


class IdentityError(ValueError):
    """Fixed refusal; never echo token claims, provider bodies or HTTP exceptions."""


def configured() -> bool:
    return 'us-east' in registry._records


def settings(*, workspace: bool = False) -> tuple[str, str]:
    from main import box_region
    record = registry.config('us-east' if workspace else box_region())
    issuer = record['issuer']
    client = record['workspace_client_id' if workspace else 'client_id']
    expected = ISSUERS['us-east'] if workspace else ISSUERS.get(box_region())
    if issuer != expected or not re.fullmatch(r'[A-Za-z0-9@._-]{1,200}', client):
        raise IdentityError('Trigger sign-in configuration refused')
    return issuer, client


def endpoint(value: str, *, workspace: bool = False) -> str:
    if not isinstance(value, str):
        raise IdentityError('Trigger sign-in endpoint refused')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.netloc != urlsplit(settings(workspace=workspace)[0]).netloc
            or parsed.username or parsed.password or parsed.fragment):
        raise IdentityError('Trigger sign-in endpoint refused')
    return value


def http_client():
    return httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False)


async def fetch(method: str, url: str, *, workspace: bool = False, **kwargs) -> dict:
    try:
        async with http_client() as client:
            response = await client.request(method, endpoint(url, workspace=workspace), **kwargs)
        if len(response.content) > 262144:
            raise IdentityError('Trigger sign-in response refused')
        body = response.json()
        if not isinstance(body, dict) or response.is_redirect:
            raise IdentityError('Trigger sign-in response refused')
        if not response.is_success and not (response.status_code == 400 and isinstance(body.get('error'), str)):
            raise IdentityError('Trigger sign-in request refused')
        return body
    except (httpx.HTTPError, ValueError, TypeError):
        raise IdentityError('Trigger sign-in request refused') from None


async def discovery(*, workspace: bool = False) -> dict:
    issuer, _ = settings(workspace=workspace)
    async with _lock:
        cache = _cache.setdefault(issuer, {})
        if cache.get('issuer') == issuer and cache.get('expires', 0) > time.monotonic():
            return dict(cache['discovery'])
    data = await fetch('GET', issuer + '/.well-known/openid-configuration', workspace=workspace)
    if data.get('issuer') != issuer:
        raise IdentityError('Trigger sign-in discovery refused')
    for name in ('device_authorization_endpoint', 'token_endpoint', 'jwks_uri'):
        endpoint(data.get(name), workspace=workspace)
    async with _lock:
        if cache.get('issuer') != issuer or cache.get('expires', 0) <= time.monotonic():
            cache.clear()
            cache.update(issuer=issuer, discovery=data, expires=time.monotonic() + 300)
        return dict(cache['discovery'])


async def verify(token: str, *, workspace: bool = False) -> dict:
    issuer, client = settings(workspace=workspace)
    data = await discovery(workspace=workspace)
    cache = _cache[issuer]
    try:
        if not isinstance(token, str) or len(token) > 16384:
            raise IdentityError('Trigger sign-in token refused')
        header = jwt.get_unverified_header(token)
        kid = header.get('kid')
        if header.get('alg') != 'RS256' or not isinstance(kid, str) or not kid or len(kid) > 200:
            raise IdentityError('Trigger sign-in token refused')
        async with _lock:
            matches = [key for key in cache.get('keys', []) if key.get('kid') == kid]
            refresh = not matches and time.monotonic() >= cache.get('keys_refresh_after', 0)
            if refresh:
                # Reserve before releasing the lock; failed fetches are bounded too.
                cache['keys_refresh_after'] = time.monotonic() + 60
        if refresh:
            jwks = await fetch('GET', data['jwks_uri'], workspace=workspace)
            keys = jwks.get('keys')
            if not isinstance(keys, list) or len(keys) > 100 or not all(isinstance(k, dict) for k in keys):
                raise IdentityError('Trigger sign-in keys refused')
            async with _lock:
                cache['keys'] = keys
            matches = [key for key in keys if key.get('kid') == kid]
        if len(matches) != 1:
            raise IdentityError('Trigger sign-in key refused')
        key = matches[0]
        if key.get('kty') != 'RSA' or key.get('use', 'sig') != 'sig' or key.get('alg', 'RS256') != 'RS256':
            raise IdentityError('Trigger sign-in key refused')
        public = jwt.PyJWK.from_dict(key, algorithm='RS256').key
        claims = jwt.decode(token, public, algorithms=['RS256'], audience=client, issuer=issuer,
                            options={'require': ['iss', 'aud', 'sub', 'exp', 'iat']})
        aud = claims['aud']
        if isinstance(aud, list) and len(aud) > 1 and claims.get('azp') != client:
            raise IdentityError('Trigger sign-in audience refused')
        if claims.get('azp', client) != client:
            raise IdentityError('Trigger sign-in audience refused')
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise IdentityError('Trigger sign-in token refused') from None


def principal(claims: dict, *, org_id: str | None = None, workspace: bool = False) -> tuple[uuid.UUID, str, bool]:
    try:
        sub = claims['sub']
        if not isinstance(sub, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', sub):
            raise ValueError
        from main import box_region
        bindings = registry.config('us-east' if workspace else box_region())['organisations']
        roles = claims.get(ROLES_CLAIM, {})
        if not isinstance(bindings, dict) or not isinstance(roles, dict):
            raise ValueError
        granted = sorted({org for grants in roles.values() if isinstance(grants, dict)
                          for org in grants if org in bindings})
        home = claims.get(ORG_CLAIM)
        org = org_id if org_id is not None else home if home in granted else next(iter(granted), None)
        if not isinstance(org, str) or not re.fullmatch(r'[0-9]{1,30}', org) or org not in granted:
            raise ValueError
        tenant = uuid.UUID(bindings[org])
        admins = roles.get('org_admin', {}) if isinstance(roles, dict) else {}
        tenant_admin = isinstance(admins, dict) and org in admins
        return tenant, sub, tenant_admin
    except (ValueError, TypeError, KeyError):
        raise IdentityError('Trigger sign-in organisation refused') from None


async def start() -> dict:
    await registry.refresh()
    _, client = settings()
    data = await discovery()
    response = await fetch('POST', data['device_authorization_endpoint'],
                           data={'client_id': client, 'scope': SCOPE})
    endpoint(response.get('verification_uri'))
    if response.get('verification_uri_complete'):
        endpoint(response['verification_uri_complete'])
    if (not isinstance(response.get('device_code'), str) or not 1 <= len(response['device_code']) <= 128
            or not re.fullmatch(r'[A-Z0-9-]{4,40}', str(response.get('user_code', '')))
            or not isinstance(response.get('expires_in'), int) or not 1 <= response['expires_in'] <= 600):
        raise IdentityError('Trigger sign-in device response refused')
    return response


async def poll(device_code: str) -> dict:
    _, client = settings()
    data = await discovery()
    return await fetch('POST', data['token_endpoint'], data={
        'client_id': client, 'device_code': device_code,
        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
    })
