"""Execute founder-confirmed active work in the ordinary scan lock/table."""
import asyncio
import tempfile
from pathlib import Path

import httpx
from fastapi import HTTPException

from active_policy import SCAN_SECONDS, active_target
from active_tools import collect
from passive_routes import GATEWAY
from passive_store import watch_artifact, target_url


async def execute(store, tenant, url, *, scanner=collect, http=None, client=False, infra=False, ident=None):
    target, domain = target_url(url) if client else active_target(url)  # Before DB, gateway, DNS or scanner requests.
    consent = await store.consent_id(tenant,domain) if client else None
    if client and consent is None:
        raise PermissionError('scan not authorized')
    ident = ident or await store.create(tenant, target, watch_artifact(domain), active=True, **({'consent_id':consent} if client else {}), **({'infra':True} if infra else {}))
    lease = {'tenant_id': str(tenant), 'scan_id': str(ident)}
    async with httpx.AsyncClient(trust_env=False, timeout=15) if http is None else http as transport:
        try:
            try:
                async with asyncio.timeout(SCAN_SECONDS):
                    response = await transport.post(GATEWAY + ('/infra/lease' if infra else '/active/lease'), json=lease)
                    if response.status_code != 200 or response.json().get('ready') is not True:
                        raise HTTPException(response.status_code if response.status_code in {403,409} else 503, 'scan not authorized')
                    if not client:
                        await store.enroll_weekly(tenant,domain,own=True)
                    with tempfile.TemporaryDirectory(prefix='radix-active-') as directory:
                        result = await scanner(target, Path(directory))
            except TimeoutError:
                result = {'findings': [], 'checks': {'active': {'status': 'partial', 'reason': 'scan_timeout'}}}
            await store.complete(tenant, ident, result['findings'], measurements=result)
        except BaseException:
            await asyncio.shield(store.fail(tenant, ident))
            raise
        finally:
            try:
                await asyncio.shield(transport.post(GATEWAY + '/release', json=lease))
            except Exception:
                pass  # Captured proxy deadline independently revokes old tunnels.
    return {'scan_id': str(ident), 'active': True, 'status': 'complete' if
            result['checks']['active']['status'] == 'complete' else 'failed',
            'checks': result['checks'], 'findings': await store.findings(tenant, ident)}
