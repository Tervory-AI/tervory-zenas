"""GBP's official OAuth/API endpoints; scoped vault encryption, no token projection.

Only refresh tokens persist. Access tokens live for one API operation in memory.
The existing MCP client is resource-discovery/public-client OAuth, so its client
registration flow cannot substitute for Google's founder-owned confidential app.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import HTTPException
from trigger_marketing_contract import utc_now
from vault import open_seal, seal

SCOPE = 'https://www.googleapis.com/auth/business.manage'
REDIRECT = 'https://app.tervory.com/oauth/trigger-gbp/callback'
AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
REVOKE = 'https://oauth2.googleapis.com/revoke'
V4 = 'https://mybusiness.googleapis.com/v4/'


def context(tenant, purpose):
    return f'trigger:gbp:v1:{tenant}:{purpose}'


def settings():
    values = {name: os.environ.get('TRIGGER_GOOGLE_OAUTH_' + name.upper(), '')
              for name in ('client_id', 'client_secret')}
    if not all(values.values()):
        raise HTTPException(503, {"missing_settings": ["TRIGGER_GOOGLE_OAUTH_" + key.upper() for key, value in values.items() if not value]})
    return values


class GoogleFailure(Exception):
    def __init__(self, status=503, message=None, *, uncertain=False):
        super().__init__()  # Exception/log strings must never contain API bodies.
        self.status, self.message, self.uncertain = status, message, uncertain
        self.too_long = bool(message and re.search(r'too long|length|characters|size limit', message, re.IGNORECASE))
        self.retryable = (status == 429 or 500 <= status < 600) and not self.too_long and not uncertain


class Google:
    def __init__(self, pool):
        self.pool = pool

    async def http(self, method, url, *, may_have_sent=False, **kwargs):
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
                response = await client.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise GoogleFailure(503) from None
        except httpx.HTTPError:
            raise GoogleFailure(503, uncertain=may_have_sent) from None
        if len(response.content) > 2_000_000:
            raise GoogleFailure(502, uncertain=may_have_sent)
        return response

    @staticmethod
    def result(response, redactions=(), *, may_have_sent=False):
        try:
            data = response.json() if response.content else {}
        except ValueError:
            raise GoogleFailure(502, uncertain=may_have_sent) from None
        if not response.is_success:
            error = data.get('error') if isinstance(data, dict) else None
            message = error.get('message') if isinstance(error, dict) else None
            if isinstance(message, str):
                for value in redactions:
                    if value:
                        message = message.replace(value, '')
                message = message[:2000]
            else:
                message = None
            raise GoogleFailure(response.status_code, message, uncertain=may_have_sent and response.status_code >= 500)
        if not isinstance(data, dict):
            raise GoogleFailure(502, uncertain=may_have_sent)
        return data

    async def connect(self, actor):
        config = settings()
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        digest = hashlib.sha256(state.encode()).hexdigest()
        generation = uuid4()
        encrypted = seal({'verifier': verifier}, context(actor.tenant_id, 'state:' + digest))
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute('''INSERT INTO tank.trigger_gbp_connections(tenant_id,generation,status,user_id)
                VALUES($1,$2,'disconnected',$3) ON CONFLICT DO NOTHING''', actor.tenant_id, generation, actor.user_id)
            current = await conn.fetchrow('SELECT * FROM tank.trigger_gbp_connections WHERE tenant_id=$1 FOR UPDATE', actor.tenant_id)
            if current['sealed']:
                # Revoke at confirmed connect, before issuing the consent URL.
                # Revoking after exchange could also invalidate the new grant.
                await self.revoke(actor.tenant_id, current['generation'], current['sealed'])
                await conn.execute("""UPDATE tank.trigger_gbp_connections SET sealed=NULL,status='disconnected',
                    location=NULL,location_title=NULL,reviews_checked_at=NULL,updated_at=now() WHERE tenant_id=$1""", actor.tenant_id)
            await conn.execute('DELETE FROM tank.trigger_gbp_states WHERE tenant_id=$1', actor.tenant_id)
            await conn.execute('''INSERT INTO tank.trigger_gbp_states(tenant_id,state_hash,user_id,generation,sealed,expires_at)
                VALUES($1,$2,$3,$4,$5,$6)''', actor.tenant_id, digest, actor.user_id, generation, encrypted, utc_now()+timedelta(minutes=10))
        params = {'client_id': config['client_id'], 'redirect_uri': REDIRECT, 'response_type': 'code',
                  'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent', 'state': state,
                  'code_challenge_method': 'S256', 'code_challenge': base64.urlsafe_b64encode(
                      hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()}
        return {'url': AUTH + '?' + urlencode(params)}

    async def callback(self, actor, state, code, error=None):
        digest = hashlib.sha256(state.encode()).hexdigest()
        row = await self.pool.fetchrow('''UPDATE tank.trigger_gbp_states SET consumed_at=now()
            WHERE tenant_id=$1 AND state_hash=$2 AND user_id=$3 AND consumed_at IS NULL AND expires_at>now()
            RETURNING sealed,generation''', actor.tenant_id, digest, actor.user_id)
        if row is None or error or not code:
            raise HTTPException(409)
        verifier = open_seal(row['sealed'], context(actor.tenant_id, 'state:' + digest))['verifier']
        config = settings()
        response = await self.http('POST', TOKEN, data={**config, 'grant_type': 'authorization_code',
            'code': code, 'code_verifier': verifier, 'redirect_uri': REDIRECT})
        data = self.result(response, (code, verifier, config['client_secret']))
        refresh = data.get('refresh_token')
        if (not isinstance(refresh, str) or not refresh or len(refresh)>16000
                or SCOPE not in data.get('scope', SCOPE).split()):
            raise HTTPException(409)
        encrypted = seal({'refresh_token': refresh}, context(actor.tenant_id, str(row['generation'])))
        async with self.pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow('SELECT * FROM tank.trigger_gbp_connections WHERE tenant_id=$1 FOR UPDATE', actor.tenant_id)
            valid = await conn.fetchval('SELECT 1 FROM tank.trigger_gbp_states WHERE tenant_id=$1 AND state_hash=$2 AND consumed_at IS NOT NULL', actor.tenant_id, digest)
            if not valid or not current or current['sealed']:
                raise HTTPException(409)
            await conn.execute('''UPDATE tank.trigger_gbp_connections SET generation=$2,sealed=$3,status='connected',
                user_id=$4,location=NULL,location_title=NULL,reviews_checked_at=NULL,updated_at=now() WHERE tenant_id=$1''',
                actor.tenant_id, row['generation'], encrypted, actor.user_id)
            await conn.execute('DELETE FROM tank.trigger_gbp_states WHERE tenant_id=$1', actor.tenant_id)
        return {'status': 'connected'}

    async def request(self, tenant, method, url, *, conn=None, **kwargs):
        db = conn or self.pool
        row = await db.fetchrow("SELECT generation,sealed,status FROM tank.trigger_gbp_connections WHERE tenant_id=$1", tenant)
        if row is None or row['status'] != 'connected' or not row['sealed']:
            raise HTTPException(409)
        refresh = open_seal(row['sealed'], context(tenant, str(row['generation'])))['refresh_token']
        config = settings()
        response = await self.http('POST', TOKEN, data={**config, 'grant_type': 'refresh_token', 'refresh_token': refresh})
        token = self.result(response, (refresh, config['client_secret'])).get('access_token')
        if not isinstance(token, str) or not token or len(token)>16000:
            raise GoogleFailure(502)
        publishing = method.upper() == 'POST' and url.startswith(V4) and url.endswith('/localPosts')
        response = await self.http(method, url, may_have_sent=publishing, headers={'Authorization': 'Bearer ' + token}, **kwargs)
        return self.result(response, (refresh, token, config['client_secret']), may_have_sent=publishing)

    async def location(self, tenant):
        row = await self.pool.fetchrow("SELECT location,generation,reviews_checked_at FROM tank.trigger_gbp_connections WHERE tenant_id=$1 AND status='connected'", tenant)
        if row is None or not row['location']:
            raise HTTPException(409)
        return row

    async def locations(self, tenant):
        accounts = await self.pages(tenant, 'https://mybusinessaccountmanagement.googleapis.com/v1/accounts', 'accounts')
        locations = []
        for account in accounts:
            name = account.get('name', '')
            if not re.fullmatch(r'accounts/[0-9]+', name):
                continue
            rows = await self.pages(tenant, 'https://mybusinessbusinessinformation.googleapis.com/v1/' + name + '/locations',
                                    'locations', params={'readMask': 'name,title'})
            for row in rows:
                if re.fullmatch(r'locations/[0-9]+', row.get('name', '')):
                    locations.append({'location': name + '/' + row['name'], 'title': row.get('title')})
        return locations

    async def pages(self, tenant, url, field, params=None, *, conn=None):
        rows, page = [], None
        for _ in range(100):
            data = await self.request(tenant, 'GET', url, conn=conn, params={**(params or {}), **({'pageToken': page} if page else {})})
            batch = data.get(field, [])
            if not isinstance(batch, list):
                raise GoogleFailure(502)
            rows.extend(batch)
            page = data.get('nextPageToken')
            if not page:
                return rows
        raise GoogleFailure(502)  # Never advance a cursor after an incomplete read.

    async def revoke(self, tenant, generation, sealed):
        refresh = open_seal(sealed, context(tenant, str(generation)))['refresh_token']
        response = await self.http('POST', REVOKE, data={'token': refresh})
        if response.status_code == 200:
            return
        if response.status_code == 400:
            try:
                data = response.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get('error') == 'invalid_token':
                return  # Google already revoked this grant.
        raise GoogleFailure(response.status_code)  # No token-bearing error body.

    async def disconnect(self, tenant, generation):
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow('SELECT * FROM tank.trigger_gbp_connections WHERE tenant_id=$1 FOR UPDATE', tenant)
            if row is None or row['generation'] != generation:
                raise HTTPException(409)
            if row['sealed']:
                await self.revoke(tenant, generation, row['sealed'])
            await conn.execute("UPDATE tank.trigger_gbp_connections SET sealed=NULL,status='disconnected',location=NULL,location_title=NULL,updated_at=now() WHERE tenant_id=$1", tenant)
            await conn.execute('DELETE FROM tank.trigger_gbp_states WHERE tenant_id=$1', tenant)
        return {'status': 'disconnected'}
