"""Trigger sign-in device grants issue the existing regional Tank credential pair."""
from __future__ import annotations

import base64
from datetime import timedelta

from fastapi.responses import JSONResponse

import command
import device_login as device
import identity_oidc as oidc
import personal_tokens as pat

PREFIX = 'tzd1.'


async def create(name: str) -> JSONResponse:
    box = device.region()
    try:
        upstream = await oidc.start()
        # Caller carries the provider code; only its envelope's hash is stored.
        wrapped = PREFIX + base64.urlsafe_b64encode(upstream['device_code'].encode('ascii')).decode().rstrip('=')
        interval = min(60, max(1, int(upstream.get('interval', 5))))
    except (oidc.IdentityError, UnicodeError, ValueError, TypeError):
        return device.error('temporarily_unavailable', 503)
    with device.platform():
        async with command.db().acquire() as conn:
            await conn.execute("DELETE FROM tank.device_authorizations WHERE expires_at < now() - interval '1 day'")
            count = await conn.fetchval("SELECT count(*) FROM tank.device_authorizations WHERE status='pending' AND expires_at > now()")
            if count >= device.MAX_PENDING:
                return device.error('slow_down', 429)
            row = await conn.fetchrow(
                "INSERT INTO tank.device_authorizations(device_hash,user_code,region,device_name,expires_at,trigger_signin) "
                "VALUES($1,$2,$3,$4,now()+$5::interval,true) ON CONFLICT DO NOTHING RETURNING expires_at",
                pat.digest(wrapped), device.user_code(), box, name, timedelta(seconds=upstream['expires_in']))
            if row is None:
                return device.error('temporarily_unavailable', 503)
    return JSONResponse({
        'identity_provider': 'trigger-signin', 'region': box, 'device_code': wrapped,
        'user_code': upstream['user_code'], 'verification_uri': upstream['verification_uri'],
        'verification_uri_complete': upstream.get('verification_uri_complete', upstream['verification_uri']),
        'expires_in': upstream['expires_in'], 'interval': interval,
    }, headers=device.NO_STORE)


async def poll(wrapped: str) -> JSONResponse:
    box = device.region()
    try:
        encoded = wrapped.removeprefix(PREFIX)
        code = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True).decode('ascii')
        if not wrapped.startswith(PREFIX) or not 1 <= len(code) <= 128:
            raise ValueError
    except (ValueError, UnicodeError):
        return device.error('invalid_grant')
    with device.platform():
        async with command.db().acquire() as conn:
            # Reserve the poll interval, then release the admin connection before HTTP.
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM tank.device_authorizations WHERE device_hash=$1 AND region=$2 "
                    "AND trigger_signin FOR UPDATE", pat.digest(wrapped), box)
                if row is None or row['status'] != 'pending':
                    return device.error('invalid_grant')
                if row['expires_at'] <= device.now():
                    return device.error('expired_token')
                if row['last_polled_at'] and device.now() - row['last_polled_at'] < timedelta(seconds=4):
                    return device.error('slow_down')
                await conn.execute("UPDATE tank.device_authorizations SET last_polled_at=now() WHERE device_hash=$1", pat.digest(wrapped))
    try:
        response = await oidc.poll(code)
        if response.get('error'):
            allowed = {'authorization_pending', 'slow_down', 'access_denied', 'expired_token'}
            return device.error(response['error'] if response['error'] in allowed else 'invalid_grant')
        claims = await oidc.verify(response.get('id_token'))
        tenant, user, tenant_admin = oidc.principal(claims)
    except oidc.IdentityError:
        return device.error('invalid_grant')
    with device.platform():
        async with command.db().acquire() as conn:
            async with conn.transaction():
                # The status transition wins exactly once, including polls that
                # overlapped while the provider was slow. Roll back on insert failure.
                row = await conn.fetchrow(
                    "UPDATE tank.device_authorizations SET status='consumed',tenant_id=$2,user_id=$3 "
                    "WHERE device_hash=$1 AND status='pending' AND region=$4 "
                    "AND trigger_signin AND expires_at > now() RETURNING *", pat.digest(wrapped), tenant, user, box)
                if row is None:
                    return device.error('invalid_grant')
                access, refresh = device.credential_pair(box, tenant)
                issued = device.now()
                # Administrative pre-tenant device lookup; the inserted row has
                # the verified mapped tenant, never a request body/header tenant.
                token_id = await conn.fetchval(
                    "INSERT INTO tank.personal_tokens(tenant_id,user_id,region,name,token_hash,device_name,"
                    "expires_at,refresh_hash,refresh_expires_at,identity_tenant_admin,trigger_signin,trigger_signin_issuer) "
                    "VALUES($1,$2,$3,$4,$5,$4,$6,$7,$8,$9,true,$10) RETURNING token_id",
                    tenant, user, box, row['device_name'], pat.digest(access), issued + device.ACCESS_TTL,
                    pat.digest(refresh), issued + device.REFRESH_TTL, tenant_admin, claims['iss'])
                await conn.execute(
                    "UPDATE tank.device_authorizations SET status='consumed',tenant_id=$2,user_id=$3,"
                    "token_id=$4,decided_at=now() WHERE device_hash=$1", pat.digest(wrapped), tenant, user, token_id)
    return JSONResponse(device.grant(access, refresh, token_id, box, issued), headers=device.NO_STORE)
