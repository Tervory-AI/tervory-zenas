"""Facebook Page OAuth and Graph v26.0; tokens persist only in the scoped vault.

Official references and checked date: docs/ops/TRIGGER-META.md. The documented
manual web flow has no advertised PKCE; confidential exchange and one-use state
are required. Google.http supplies the existing bounded, private transport.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from fastapi import HTTPException
from trigger_google import Google, GoogleFailure
from trigger_marketing_contract import utc_now
from vault import open_seal, seal

VERSION = 'v26.0'
GRAPH = 'https://graph.facebook.com/' + VERSION + '/'
AUTH = 'https://www.facebook.com/' + VERSION + '/dialog/oauth'
TOKEN = GRAPH + 'oauth/access_token'
REDIRECT = 'https://app.tervory.com/oauth/trigger-meta/callback'
PERMISSIONS = ('pages_show_list', 'pages_read_engagement', 'pages_manage_posts')


def context(tenant, generation):
    return f'trigger:meta:v1:{tenant}:{generation}'


def settings():
    values = {'client_id': os.environ.get('TRIGGER_META_APP_ID', ''),
              'client_secret': os.environ.get('TRIGGER_META_APP_SECRET', '')}
    if not all(values.values()):
        raise HTTPException(503, {"missing_settings": [env for key, env in (("client_id", "TRIGGER_META_APP_ID"), ("client_secret", "TRIGGER_META_APP_SECRET")) if not values[key]]})
    return values


def credential(value):
    if not isinstance(value, str) or not value or len(value) > 16000 or any(c.isspace() for c in value):
        raise GoogleFailure(502)
    return value


def scrub(message, redactions):
    if not isinstance(message, str):
        return None
    for value in redactions:
        if isinstance(value, str) and value:
            message = message.replace(value, '')
    # Provider messages are data. Remove URLs and credential-like assignments,
    # including values not present verbatim in our current connection bundle.
    message = re.sub(r'https?://\S+|(?:access_token|client_secret|code)\s*[=:]\s*\S+', '', message, flags=re.IGNORECASE)
    return ''.join(c for c in message if c.isprintable())[:2000]


class MetaFailure(GoogleFailure):
    def __init__(self, status=503, message=None, *, uncertain=False, code=None):
        super().__init__(status, message, uncertain=uncertain)
        transient_code = type(code) is int and code in {1, 2, 4, 17, 32, 613}
        self.retryable = (status == 429 or 500 <= status < 600 or transient_code) and not uncertain and not self.too_long


class Meta(Google):
    @staticmethod
    def result(response, redactions=(), *, may_have_sent=False):
        try:
            data = response.json()
        except ValueError:
            raise MetaFailure(502, uncertain=may_have_sent) from None
        error = data.get('error') if isinstance(data, dict) else None
        if isinstance(error, dict):
            # A structured Graph refusal is known rejected, unlike a timeout,
            # unreadable response or unstructured server error after a POST.
            raise MetaFailure(response.status_code, scrub(error.get('message'), redactions), code=error.get('code'))
        if not response.is_success or not isinstance(data, dict):
            raise MetaFailure(response.status_code if not response.is_success else 502, uncertain=may_have_sent)
        return data

    async def connect(self, actor):
        config = settings()
        state = secrets.token_urlsafe(32)
        digest = hashlib.sha256(state.encode()).hexdigest()
        generation = uuid4()
        encrypted = seal({'generation': str(generation)}, context(actor.tenant_id, 'state:' + digest))
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute('''INSERT INTO tank.trigger_meta_connections(tenant_id,generation,status,user_id)
                VALUES($1,$2,'disconnected',$3) ON CONFLICT DO NOTHING''', actor.tenant_id, generation, actor.user_id)
            current = await conn.fetchrow('SELECT * FROM tank.trigger_meta_connections WHERE tenant_id=$1 FOR UPDATE', actor.tenant_id)
            if current['sealed']:
                await self.revoke(actor.tenant_id, current['generation'], current['sealed'])
            await conn.execute("""UPDATE tank.trigger_meta_connections SET generation=$2,sealed=NULL,status='disconnected',
                page_id=NULL,page_name=NULL,user_id=$3,updated_at=now() WHERE tenant_id=$1""", actor.tenant_id, generation, actor.user_id)
            await conn.execute('DELETE FROM tank.trigger_meta_states WHERE tenant_id=$1', actor.tenant_id)
            await conn.execute('''INSERT INTO tank.trigger_meta_states(tenant_id,state_hash,user_id,generation,sealed,expires_at)
                VALUES($1,$2,$3,$4,$5,$6)''', actor.tenant_id, digest, actor.user_id, generation, encrypted, utc_now()+timedelta(minutes=10))
        params = {'client_id': config['client_id'], 'redirect_uri': REDIRECT, 'response_type': 'code',
                  'scope': ','.join(PERMISSIONS), 'state': state, 'auth_type': 'rerequest'}
        return {'url': AUTH + '?' + urlencode(params)}

    async def callback(self, actor, state, code, error=None):
        digest = hashlib.sha256(state.encode()).hexdigest()
        row = await self.pool.fetchrow('''UPDATE tank.trigger_meta_states SET consumed_at=now()
            WHERE tenant_id=$1 AND state_hash=$2 AND user_id=$3 AND consumed_at IS NULL AND expires_at>now()
            RETURNING sealed,generation''', actor.tenant_id, digest, actor.user_id)
        if row is None or error or not code:
            raise HTTPException(409)
        saved = open_seal(row['sealed'], context(actor.tenant_id, 'state:' + digest))
        if saved.get('generation') != str(row['generation']):
            raise HTTPException(409)
        config = settings()
        # Form bodies keep the confidential app secret and exchange code out of URLs.
        response = await self.http('POST', TOKEN, data={**config, 'redirect_uri': REDIRECT, 'code': code})
        short = credential(self.result(response, (code, config['client_secret'])).get('access_token'))
        response = await self.http('POST', TOKEN, data={**config, 'grant_type': 'fb_exchange_token', 'fb_exchange_token': short})
        token = credential(self.result(response, (code, short, config['client_secret'])).get('access_token'))
        permissions = await self._call(token, 'GET', 'me/permissions')
        grants = permissions.get('data')
        if not isinstance(grants, list):
            raise MetaFailure(502)
        granted = {p.get('permission') for p in grants if isinstance(p, dict) and isinstance(p.get('permission'), str) and p.get('status') == 'granted'}
        if not set(PERMISSIONS) <= granted:
            raise HTTPException(409)
        encrypted = seal({'user_token': token}, context(actor.tenant_id, str(row['generation'])))
        async with self.pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow('SELECT * FROM tank.trigger_meta_connections WHERE tenant_id=$1 FOR UPDATE', actor.tenant_id)
            valid = await conn.fetchval('SELECT 1 FROM tank.trigger_meta_states WHERE tenant_id=$1 AND state_hash=$2 AND consumed_at IS NOT NULL', actor.tenant_id, digest)
            if not valid or not current or current['sealed'] or current['generation'] != row['generation']:
                raise HTTPException(409)
            await conn.execute("""UPDATE tank.trigger_meta_connections SET sealed=$3,status='connected',user_id=$4,
                page_id=NULL,page_name=NULL,updated_at=now() WHERE tenant_id=$1 AND generation=$2""",
                actor.tenant_id, row['generation'], encrypted, actor.user_id)
            await conn.execute('DELETE FROM tank.trigger_meta_states WHERE tenant_id=$1', actor.tenant_id)
        return {'status': 'connected'}

    async def _call(self, token, method, path, *, may_have_sent=False, redactions=(), **kwargs):
        response = await self.http(method, GRAPH + path, may_have_sent=may_have_sent,
                                   headers={'Authorization': 'Bearer ' + token}, **kwargs)
        return self.result(response, (token, settings()['client_secret'], *redactions), may_have_sent=may_have_sent)

    async def bundle(self, tenant, *, conn=None):
        row = await (conn or self.pool).fetchrow('SELECT * FROM tank.trigger_meta_connections WHERE tenant_id=$1', tenant)
        if row is None or row['status'] != 'connected' or not row['sealed']:
            raise HTTPException(409)
        return row, open_seal(row['sealed'], context(tenant, str(row['generation'])))

    async def _accounts(self, tenant, *, conn=None, include_tokens=False):
        _, bundle = await self.bundle(tenant, conn=conn)
        token = credential(bundle.get('user_token'))
        rows, cursor, seen = [], None, set()
        for _ in range(100):
            data = await self._call(token, 'GET', 'me/accounts', params={
                'fields': 'id,name,tasks' + (',access_token' if include_tokens else ''), 'limit': 100,
                **({'after': cursor} if cursor else {})})
            batch = data.get('data')
            if not isinstance(batch, list) or len(rows) + len(batch) > 10000:
                raise MetaFailure(502)
            for item in batch:
                if not isinstance(item, dict) or not re.fullmatch(r'[0-9]{1,50}', str(item.get('id', ''))):
                    raise MetaFailure(502)
                tasks = item.get('tasks')
                if not isinstance(tasks, list):
                    raise MetaFailure(502)
                if 'CREATE_CONTENT' not in tasks:
                    continue
                name = scrub(item.get('name'), (token, settings()['client_secret'], item.get('access_token')))
                rows.append({'page_id': item['id'], 'name': name,
                             **({'access_token': credential(item.get('access_token'))} if include_tokens else {})})
            paging = data.get('paging', {})
            if not isinstance(paging, dict):
                raise MetaFailure(502)
            if not paging.get('next'):
                return rows
            cursors = paging.get('cursors')
            cursor = cursors.get('after') if isinstance(cursors, dict) else None
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
                raise MetaFailure(502)
            seen.add(cursor)  # Never follow Meta's token-bearing pagination URLs.
        raise MetaFailure(502)

    async def pages(self, tenant):
        return await self._accounts(tenant)

    async def select(self, tenant, page_id, generation):
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow('SELECT * FROM tank.trigger_meta_connections WHERE tenant_id=$1 FOR UPDATE', tenant)
            if row is None or row['generation'] != generation or row['status'] != 'connected':
                raise HTTPException(409)
            pages = await self._accounts(tenant, conn=conn, include_tokens=True)
            chosen = next((page for page in pages if page['page_id'] == page_id), None)
            if chosen is None:
                raise HTTPException(404)
            bundle = open_seal(row['sealed'], context(tenant, str(generation)))
            encrypted = seal({'user_token': bundle['user_token'], 'page_token': chosen['access_token']}, context(tenant, str(generation)))
            await conn.execute('''UPDATE tank.trigger_meta_connections SET page_id=$3,page_name=$4,sealed=$5,
                updated_at=now() WHERE tenant_id=$1 AND generation=$2''', tenant, generation, page_id, chosen['name'], encrypted)
        return {'page_id': page_id, 'name': chosen['name'], 'status': 'connected'}

    async def location(self, tenant):
        row = await self.pool.fetchrow("SELECT page_id,generation FROM tank.trigger_meta_connections WHERE tenant_id=$1 AND status='connected'", tenant)
        if row is None or not row['page_id']:
            raise HTTPException(409)
        return {'location': row['page_id'], 'generation': row['generation']}

    async def request(self, tenant, method, path, *, conn=None, **kwargs):
        row, bundle = await self.bundle(tenant, conn=conn)
        if not row['page_id'] or not (path == row['page_id'] + '/feed' or path == row['page_id'] + '/published_posts'
                                     or re.fullmatch(re.escape(row['page_id']) + r'_[0-9]+', path)):
            raise HTTPException(409)
        return await self._call(credential(bundle.get('page_token')), method, path,
                                may_have_sent=method == 'POST', redactions=tuple(bundle.values()), **kwargs)

    async def permalink(self, tenant, post_id, *, conn=None):
        data = await self.request(tenant, 'GET', post_id, conn=conn, params={'fields': 'permalink_url'})
        url = data.get('permalink_url')
        if not isinstance(url, str) or len(url) > 2048:
            raise MetaFailure(502)
        _, bundle = await self.bundle(tenant, conn=conn)
        if any(value in url for value in (*bundle.values(), settings()['client_secret']) if isinstance(value, str) and value):
            raise MetaFailure(502)
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise MetaFailure(502) from None
        if (parsed.scheme != 'https' or parsed.netloc not in {'www.facebook.com', 'facebook.com', 'm.facebook.com'}
                or parsed.username or parsed.password or 'access_token' in parsed.query.lower()):
            raise MetaFailure(502)
        return url

    async def publish(self, tenant, page_id, text, *, conn):
        data = await self.request(tenant, 'POST', page_id + '/feed', conn=conn, data={'message': text})
        ident = data.get('id')
        if not isinstance(ident, str) or not re.fullmatch(re.escape(page_id) + r'_[0-9]+', ident):
            raise MetaFailure(502, uncertain=True)
        try:
            url = await self.permalink(tenant, ident, conn=conn)
        except (GoogleFailure, HTTPException):
            url = None  # Known post ID must commit even when the link read fails.
        return {'name': ident, 'searchUrl': url}

    async def published(self, tenant, page_id, claimed, *, conn):
        rows, cursor, seen = [], None, set()
        for _ in range(100):
            data = await self.request(tenant, 'GET', page_id + '/published_posts', conn=conn, params={
                'fields': 'id,message,created_time,permalink_url', 'since': int(claimed.timestamp()), 'limit': 100,
                **({'after': cursor} if cursor else {})})
            batch = data.get('data')
            if not isinstance(batch, list) or len(rows) + len(batch) > 10000:
                raise MetaFailure(502)
            for item in batch:
                try:
                    created = datetime.fromisoformat(item['created_time'])
                    if created.utcoffset() is None or not re.fullmatch(re.escape(page_id) + r'_[0-9]+', item['id']):
                        raise ValueError()
                    message = item.get('message')
                    if message is not None and not isinstance(message, str):
                        raise ValueError()
                    rows.append({'name': item['id'], 'summary': message, 'created': created})
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise MetaFailure(502) from None
            paging = data.get('paging', {})
            if not isinstance(paging, dict):
                raise MetaFailure(502)
            if not paging.get('next'):
                return rows
            cursors = paging.get('cursors')
            cursor = cursors.get('after') if isinstance(cursors, dict) else None
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
                raise MetaFailure(502)
            seen.add(cursor)
        raise MetaFailure(502)

    async def revoke(self, tenant, generation, sealed):
        bundle = open_seal(sealed, context(tenant, str(generation)))
        token = credential(bundle.get('user_token'))
        response = await self.http('DELETE', GRAPH + 'me/permissions', headers={'Authorization': 'Bearer ' + token})
        # Meta documents a successful boolean response; Graph also wraps it in success.
        if response.is_success and response.content.strip() == b'true':
            return
        result = self.result(response, (*bundle.values(), settings()['client_secret']))
        if result.get('success') is not True:
            raise MetaFailure(502)

    async def disconnect(self, tenant, generation):
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow('SELECT * FROM tank.trigger_meta_connections WHERE tenant_id=$1 FOR UPDATE', tenant)
            if row is None or row['generation'] != generation:
                raise HTTPException(409)
            if row['sealed']:
                await self.revoke(tenant, generation, row['sealed'])
            await conn.execute("""UPDATE tank.trigger_meta_connections SET sealed=NULL,status='disconnected',
                page_id=NULL,page_name=NULL,updated_at=now() WHERE tenant_id=$1""", tenant)
            await conn.execute('DELETE FROM tank.trigger_meta_states WHERE tenant_id=$1', tenant)
        return {'status': 'disconnected'}
