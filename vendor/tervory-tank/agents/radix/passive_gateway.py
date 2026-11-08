"""DNS proof and one short-lived scan tunnel on the sealed room's egress peer.

The API accepts only persisted pending run IDs. Domains and proof values come
from the tenant-scoped database join, never the HTTP caller. No scan CLI or
browser runs on this internet-connected peer.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from contextlib import suppress
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg
import dns.asyncresolver
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from egress import Proxy, Server, env_revoked, private_bind_address, system_resolve
from passive_store import Store
from chassis.tenant_db import TenantPool, tenant_path, tenant_scope


async def proof_status(domain: str, challenge: str) -> str | None:
    """The scan's one DNS proof check, also used before enabling a watch."""
    try:
        answer = await dns.asyncresolver.resolve('_tervory-verify.' + domain, 'TXT', lifetime=5)
        values = [b''.join(row.strings).decode('ascii') for row in answer]
        if any(hmac.compare_digest(value, challenge) for value in values):
            return None
        return 'txt_changed' if values else 'txt_missing'
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return 'txt_missing'
    except UnicodeError:
        return 'txt_changed'
    except (dns.exception.DNSException, OSError):
        return 'dns_unavailable'


async def proof(domain: str, challenge: str) -> bool:
    return await proof_status(domain, challenge) is None


class Lease:
    def __init__(self, store, *, check=None, server=Server, now=time.monotonic):
        self.store, self.check, self.server, self.now = store, check, server, now
        self.lock = asyncio.Lock()
        self.run = None
        self.consent = None
        self.until = 0.0
        self.proxy_server = None

    def close(self):
        self.until = 0
        self.run = None
        self.consent = None
        if self.proxy_server:
            self.proxy_server.close()
            self.proxy_server = None

    @tenant_path("tenant")
    async def start(self, tenant: UUID, ident: UUID, *, active=False, infra=False):
        async with self.lock:
            if self.run is not None and self.now() < self.until:
                raise HTTPException(409, 'scan busy')
            self.close()
            row = await self.store.pending(tenant, ident)
            if row is None:
                raise HTTPException(403, 'domain proof required')
            if bool(row.get('infra',False)) != infra or bool(row.get('active', False)) != active:
                raise HTTPException(403, 'scan not authorized')
            if row.get('active_consent_id') is not None:
                if not await self.store.consent_valid(tenant,row['domain'],row['active_consent_id']):
                    raise HTTPException(403,'scan not authorized')
            elif active or row.get('weekly'):
                from active_policy import active_target
                try:
                    active_target('https://' + row['domain'] + '/')
                except (PermissionError, ValueError):
                    raise HTTPException(403, 'scan not authorized') from None
            reason = (await proof_status(row['domain'], row['challenge']) if self.check is None
                      else None if await self.check(row['domain'], row['challenge']) else 'txt_missing')
            if reason == 'dns_unavailable':
                raise HTTPException(503, 'scan not authorized')
            if reason:
                await self.store.stop_for_scan(tenant, ident, reason)
                raise HTTPException(403, 'domain proof required')
            if env_revoked():
                raise HTTPException(403, 'egress revoked')
            try:
                started = await self.store.running(tenant, ident)
            except asyncpg.UniqueViolationError:
                raise HTTPException(409, 'scan busy') from None
            if not started:
                raise HTTPException(409, 'scan unavailable')
            self.consent = (row['domain'],row.get('active_consent_id')) if row.get('active_consent_id') else None
            self.run, self.until = (tenant, ident), self.now() + (1800 if active else 420)
            domain = row['domain']
            # Capture this lease's deadline, so a later tenant cannot revive an old tunnel.
            until = self.until
            from active_policy import BOUNDS as ACTIVE_BOUNDS
            proxy_class=Proxy
            if infra:
                from passive_infra_proxy import InfraProxy
                proxy_class=InfraProxy
            proxy = proxy_class(**(ACTIVE_BOUNDS if active else {}), verified_host=domain, allow_storage=False,
                require_sni=active and not infra,
                **({} if infra else {'resolve':lambda host: system_resolve(host, verified_host=domain)}),
                active=lambda: self.run == (tenant, ident) and self.now() < until and not env_revoked())
            try:
                self.proxy_server = self.server(proxy, host=private_bind_address(), port=8082).start()
            except Exception:
                self.close()
                await self.store.fail(tenant, ident)
                raise HTTPException(503, 'scan egress unavailable') from None
            return {'ready': True}

    @tenant_path("tenant")
    async def revoke_withdrawn(self,tenant):
        async with self.lock:
            if self.run and self.run[0]==tenant and self.consent:
                if not await self.store.consent_valid(tenant,*self.consent):
                    prior=self.run
                    self.close()
                    await self.store.fail(*prior)

    @tenant_path("tenant")
    async def release(self, tenant: UUID, ident: UUID):
        async with self.lock:
            if self.run == (tenant, ident):
                self.close()
                await self.store.fail(tenant, ident)


class Run(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    scan_id: UUID


@asynccontextmanager
async def lifespan(app):
    raw = await asyncpg.create_pool(os.environ['DATABASE_URL'], min_size=1, max_size=2)
    admin_url = os.environ.get('RADIX_GATEWAY_ADMIN_DATABASE_URL', '')
    admin = await asyncpg.create_pool(admin_url, min_size=1, max_size=1) if admin_url else None
    try:
        if admin is not None:
            role = await admin.fetchrow('SELECT rolname,rolbypassrls FROM pg_roles WHERE rolname=current_user')
            if not os.environ.get('RADIX_GATEWAY_RLS_ADMIN_ROLE') or role['rolname'] != os.environ['RADIX_GATEWAY_RLS_ADMIN_ROLE'] or not role['rolbypassrls']:
                raise RuntimeError('Gateway admin connection must use the configured BYPASSRLS role')
        enforced = await raw.fetchval("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='agent_radix' AND c.relrowsecurity)")
        if enforced:
            role = await raw.fetchrow('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
            if role['rolsuper'] or role['rolbypassrls'] or admin is None:
                raise RuntimeError('Gateway RLS requires a non-bypass tenant role and separate admin connection')
    except BaseException:
        await raw.close()
        if admin is not None:
            await admin.close()
        raise
    app.state.pool = TenantPool(raw, admin)
    app.state.lease = Lease(Store(app.state.pool))
    async def maintenance():
        while True:
            # Explicit gateway administration, separate from any caller's tenant.
            try:
                await app.state.lease.store.reap_expired()
                if app.state.lease.run:
                    await app.state.lease.revoke_withdrawn(app.state.lease.run[0])
            except (asyncpg.PostgresError, OSError):
                logging.getLogger(__name__).warning('Passive lease maintenance unavailable; will retry')
            await asyncio.sleep(15)
    await app.state.lease.store.reap_expired()
    task = asyncio.create_task(maintenance())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        prior = app.state.lease.run
        app.state.lease.close()
        if prior:
            with tenant_scope(prior[0]):
                await app.state.lease.store.fail(*prior)
        await app.state.pool.close()
        if admin is not None:
            await admin.close()


app = FastAPI(lifespan=lifespan)

from radix_readiness import install as install_readiness
install_readiness(app, service='gateway')


@app.post('/lease')
async def lease(body: Run):
    return await app.state.lease.start(body.tenant_id, body.scan_id)


@app.post('/active/lease')
async def active_lease(body: Run):
    return await app.state.lease.start(body.tenant_id, body.scan_id, active=True)


@app.post('/infra/lease')
async def infra_lease(body: Run):
    return await app.state.lease.start(body.tenant_id,body.scan_id,active=True,infra=True)


@app.post('/active/revoke')
async def revoke(body: Run):
    # Caller cannot grant authority here; only persisted withdrawal can close a lease.
    await app.state.lease.revoke_withdrawn(body.tenant_id)
    return {'released':True}


@app.post('/release')
async def release(body: Run):
    await app.state.lease.release(body.tenant_id, body.scan_id)
    return {'released': True}


@app.get('/health')
async def health():
    return {'ok': True}


class ProofDomain(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    domain: str


@app.post('/proof')
@tenant_path('body')
async def verify_domain(body: ProofDomain):
    from passive_store import domain_name
    domain = domain_name(body.domain)
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow("""SELECT d.challenge FROM agent_radix.passive_domains d
          WHERE d.tenant_id=$1 AND d.domain=$2 AND (d.expires_at>now() OR EXISTS (
            SELECT 1 FROM agent_radix.passive_watches w WHERE w.tenant_id=d.tenant_id
            AND w.domain=d.domain AND w.status='active' AND w.challenge=d.challenge))""", body.tenant_id, domain)
    if row is None:
        raise HTTPException(403, 'domain proof required')
    reason = await proof_status(domain, row['challenge'])
    if reason:
        raise HTTPException(503 if reason == 'dns_unavailable' else 403, 'domain proof required')
    return {'ready': True}
