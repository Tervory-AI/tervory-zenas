"""One durable passive/infra pair per tenant-domain-week, using the room scheduler."""
from uuid import UUID,uuid4

from chassis.tenant_db import tenant_scope
from passive_store import Store,watch_artifact


async def queue(pool,tenant,domain):
    """Atomic pair + due-time advancement. Caller already holds WATCH_LOCK."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow('''SELECT w.* FROM agent_radix.weekly_domains w
              JOIN agent_radix.passive_domains d ON d.tenant_id=w.tenant_id AND d.domain=w.domain AND d.challenge=w.challenge
              WHERE w.tenant_id=$1 AND w.domain=$2 AND w.enabled AND w.next_run<=now()
              AND (w.own OR EXISTS(SELECT 1 FROM agent_radix.active_consents c WHERE c.tenant_id=w.tenant_id
                AND c.id=w.consent_id AND c.withdrawn_at IS NULL)) FOR UPDATE OF w''',tenant,domain)
            if row is None:return
            if row['own']:
                from active_policy import active_target
                try:active_target('https://'+domain+'/')
                except PermissionError:
                    await conn.execute('UPDATE agent_radix.weekly_domains SET enabled=false WHERE tenant_id=$1 AND domain=$2',tenant,domain)
                    return
            for kind in ('passive','infra'):
                ident=uuid4()
                inserted=await conn.fetchval('''INSERT INTO agent_radix.weekly_runs(tenant_id,domain,period,kind,scan_id)
                  VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant_id,domain,period,kind) DO NOTHING RETURNING scan_id''',
                  tenant,domain,row['next_run'],kind,ident)
                if inserted:
                    await conn.execute('''INSERT INTO agent_radix.passive_scans
                      (tenant_id,id,domain,target,artifact,watched,active,infra,active_consent_id)
                      VALUES($1,$2,$3,'https://' || $3 || '/',$4,true,$5,$5,$6)''',tenant,ident,domain,
                      watch_artifact(domain),kind=='infra',None if row['own'] else row['consent_id'])
            await conn.execute('''UPDATE agent_radix.weekly_domains SET next_run=now()+interval '7 days'
              WHERE tenant_id=$1 AND domain=$2''',tenant,domain)
            # An explicit weekly watch uses this same week's passive run rather
            # than issuing a second passive run. Daily watches retain their schedule.
            await conn.execute('''UPDATE agent_radix.passive_watches SET next_run=now()+interval '7 days'
              WHERE tenant_id=$1 AND domain=$2 AND interval='weekly' AND status='active' ''',tenant,domain)
    return True


async def tick(pool,discovery,*,execute_passive=None,execute_infra=None):
    from passive_routes import execute,Scan
    from active_routes import execute as active_execute
    from passive_infra_tools import collect
    owner=await discovery.fetchrow('''SELECT tenant_id,domain FROM agent_radix.weekly_domains w
      WHERE enabled AND next_run<=now() AND (own OR EXISTS(SELECT 1 FROM agent_radix.active_consents c
        WHERE c.tenant_id=w.tenant_id AND c.id=w.consent_id AND c.withdrawn_at IS NULL))
      ORDER BY next_run,tenant_id,domain LIMIT 1''')
    if owner:
        tenant=UUID(str(owner['tenant_id']))
        with tenant_scope(tenant):await queue(pool,tenant,owner['domain'])
    # Administrative discovery only; all reads/execution below re-enter the owner.
    run=await discovery.fetchrow('''SELECT r.tenant_id,r.scan_id,r.domain,r.kind FROM agent_radix.weekly_runs r
      JOIN agent_radix.passive_scans s ON s.tenant_id=r.tenant_id AND s.id=r.scan_id
      WHERE s.status='pending' ORDER BY r.period,r.tenant_id,r.domain,
        CASE WHEN r.kind='passive' THEN 0 ELSE 1 END LIMIT 1''')
    if run is None:return None
    tenant=UUID(str(run['tenant_id']));ident=UUID(str(run['scan_id']))
    if await discovery.fetchval("SELECT 1 FROM agent_radix.passive_scans WHERE status='running' LIMIT 1"):
        return {'count':0,'queued':True}
    with tenant_scope(tenant):
        store=Store(pool)
        row=await store.pending(tenant,ident)
        if row is None:
            await store.fail(tenant,ident)
            return {'count':0}
        try:
            if run['kind']=='passive':
                result=await (execute_passive or execute)(store,Scan(tenant_id=tenant,
                    target='https://'+run['domain']+'/',artifact=watch_artifact(run['domain'])),watched=True,ident=ident)
            else:
                result=await (execute_infra or active_execute)(store,tenant,'https://'+run['domain']+'/',
                    scanner=collect,infra=True,ident=ident,client=row['active_consent_id'] is not None)
        except Exception as error:
            from fastapi import HTTPException
            if isinstance(error,HTTPException) and error.status_code==409:
                async with pool.acquire() as conn:
                    await conn.execute("""UPDATE agent_radix.passive_scans SET status='pending',lease_until=NULL
                      WHERE tenant_id=$1 AND id=$2 AND status='error'""",tenant,ident)
                return {'count':0,'queued':True}
            # Execution records an incomplete run. The pair is never duplicated;
            # the next scheduled period is independent and rechecks proof.
            await store.fail(tenant,ident)
            return {'count':0}
    return {'count':1,'scan_id':result['scan_id'],'weekly':True,'kind':run['kind']}
