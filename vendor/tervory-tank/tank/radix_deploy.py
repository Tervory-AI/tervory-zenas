"""Materialize confirmed own-main scan findings, and read per-app deploy decisions."""
import hmac
import json
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import APIRouter, Depends, HTTPException, Request
from tenant_db import tenant_path
from deploy_scope import finding_apps, valid_app

REPOSITORY='Tervory-AI/tervory-tank'
DDL='''
CREATE TABLE IF NOT EXISTS tank.radix_deploy_scans (
 tenant_id uuid NOT NULL, scan_id uuid NOT NULL, commit_sha text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), processed boolean NOT NULL DEFAULT false,
 complete boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,scan_id), CHECK(commit_sha ~ '^[a-f0-9]{40}$')
);
CREATE TABLE IF NOT EXISTS tank.radix_deploy_findings (
 tenant_id uuid NOT NULL, id uuid NOT NULL, scan_id uuid NOT NULL, app text NOT NULL,
 commit_sha text NOT NULL, finding jsonb NOT NULL, resolved_at timestamptz,
 override_user text, override_at timestamptz, ledger_id uuid,
 PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,scan_id) REFERENCES tank.radix_deploy_scans(tenant_id,scan_id)
);
'''


async def verify_main(commit):
    from command_repo import github, _get
    async with github() as client:
        ref,_=await _get(client,'git/ref/heads/main')
        if ref.get('object',{}).get('sha')!=commit:
            raise HTTPException(422,'The action arguments are invalid')
        obj,_=await _get(client,'git/commits/'+commit)
        if obj.get('sha')!=commit:
            raise HTTPException(422,'The action arguments are invalid')


async def ancestor(base,head):
    if base==head:
        return True
    from command_repo import github,_get
    async with github() as client:
        data,_=await _get(client,'compare/'+base+'...'+head)
    return data.get('status') in {'ahead','identical'} and data.get('merge_base_commit',{}).get('sha')==base


@tenant_path('actor')
async def register(actor,scan_id,commit):
    from command import db,require_founder
    require_founder(actor)
    async with db().acquire() as conn:
        await conn.execute('''INSERT INTO tank.radix_deploy_scans(tenant_id,scan_id,commit_sha)
          VALUES($1,$2,$3) ON CONFLICT DO NOTHING''',actor.tenant_id,UUID(str(scan_id)),commit)


@tenant_path('tenant')
async def refresh(tenant):
    from command import db
    from ghost_routes import call_room
    async with db().acquire() as conn:
        scans=await conn.fetch('''SELECT scan_id,commit_sha FROM tank.radix_deploy_scans
          WHERE tenant_id=$1 AND NOT processed ORDER BY created_at LIMIT 20''',tenant)
    for scan in scans:
        response=await call_room('GET','radix',f"/jobs/{scan['scan_id']}",params={'tenant_id':str(tenant)})
        if response.status_code!=200:
            continue
        job=response.json();result=job.get('result')
        if (job.get('tenant_id')==str(tenant) and job.get('id')==str(scan['scan_id'])
            and job.get('kind')=='scan' and job.get('status')=='failed'):
            async with db().acquire() as conn:
                await conn.execute('UPDATE tank.radix_deploy_scans SET processed=true WHERE tenant_id=$1 AND scan_id=$2',tenant,scan['scan_id'])
            continue
        if job.get('status')!='done':
            continue
        if (job.get('tenant_id')!=str(tenant) or job.get('id')!=str(scan['scan_id']) or job.get('kind')!='scan'
            or not isinstance(result,dict) or result.get('tenant_id')!=str(tenant)
            or result.get('scan_id')!=str(scan['scan_id']) or result.get('repository')!=REPOSITORY
            or result.get('commit')!=scan['commit_sha'] or not isinstance(result.get('findings'),list)):
            raise HTTPException(503,'Radix results are unavailable.')
        async with db().acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1 || ':radix-deploy',0))",str(tenant))
                owner=await conn.fetchrow('''SELECT processed,created_at FROM tank.radix_deploy_scans
                  WHERE tenant_id=$1 AND scan_id=$2 FOR UPDATE''',tenant,scan['scan_id'])
                if owner['processed']:
                    continue
                completed=await conn.fetch("""SELECT commit_sha,created_at FROM tank.radix_deploy_scans
                  WHERE tenant_id=$1 AND processed AND complete AND scan_id<>$2""",tenant,scan['scan_id'])
                newer=False
                for prior in completed:
                    if ((prior['commit_sha']==scan['commit_sha'] and prior['created_at']>owner['created_at'])
                        or prior['commit_sha']!=scan['commit_sha'] and await ancestor(scan['commit_sha'],prior['commit_sha'])):
                        newer=True
                        break
                if result.get('scan_complete') is True and not newer:
                    observed=await conn.fetch("""SELECT DISTINCT commit_sha FROM tank.radix_deploy_findings
                      WHERE tenant_id=$1 AND resolved_at IS NULL AND scan_id<>$2""",tenant,scan['scan_id'])
                    covered=[row['commit_sha'] for row in observed if await ancestor(row['commit_sha'],scan['commit_sha'])]
                    if covered:
                        await conn.execute("""UPDATE tank.radix_deploy_findings f SET resolved_at=now()
                          FROM tank.radix_deploy_scans s WHERE f.tenant_id=$1 AND s.tenant_id=$1
                          AND f.scan_id=s.scan_id AND s.created_at<$2 AND f.commit_sha=ANY($3::text[])
                          AND f.resolved_at IS NULL""",tenant,owner['created_at'],covered)
                for ordinal,finding in enumerate(result['findings'] if not newer else []):
                    if not isinstance(finding,dict) or finding.get('severity')!='critical':
                        continue
                    safe={k:finding[k] for k in ('tool','rule','file','line','fix','explanation') if k in finding}
                    for app in finding_apps(finding.get('file')):
                        ident=uuid5(NAMESPACE_URL,f"{tenant}/{scan['scan_id']}/{app}/{ordinal}")
                        await conn.execute('''INSERT INTO tank.radix_deploy_findings
                          (tenant_id,id,scan_id,app,commit_sha,finding) VALUES($1,$2,$3,$4,$5,$6::jsonb)
                          ON CONFLICT DO NOTHING''',tenant,ident,scan['scan_id'],app,scan['commit_sha'],json.dumps(safe))
                await conn.execute('UPDATE tank.radix_deploy_scans SET processed=true,complete=$3 WHERE tenant_id=$1 AND scan_id=$2',tenant,scan['scan_id'],result.get('scan_complete') is True)


@tenant_path('tenant')
async def decision(tenant,app):
    from command import db
    if not valid_app(app):
        raise HTTPException(422,'The action arguments are invalid')
    await refresh(tenant)
    async with db().acquire() as conn:
        rows=await conn.fetch('''SELECT id,commit_sha,finding FROM tank.radix_deploy_findings
          WHERE tenant_id=$1 AND app=$2 AND resolved_at IS NULL AND override_at IS NULL ORDER BY id''',tenant,app)
    findings=[{'id':str(row['id']),'commit':row['commit_sha'],'severity':'critical',
        **(json.loads(row['finding']) if isinstance(row['finding'],str) else row['finding'])} for row in rows]
    return {'app':app,'blocked':bool(findings),'findings':findings,'cost_usd':0}


@tenant_path('actor')
async def override(actor,body):
    from command import db,require_founder
    from trigger_commands import confirmation
    from trigger_routes import code_receipt
    require_founder(actor)
    confirmation(actor,'radix.deploy.override',body.model_dump(mode='json'))
    async with db().acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow('''SELECT id,ledger_id FROM tank.radix_deploy_findings
              WHERE tenant_id=$1 AND id=$2 AND app=$3 AND resolved_at IS NULL FOR UPDATE''',actor.tenant_id,body.finding_id,body.app)
            if row is None:
                raise HTTPException(404,'Radix results are unavailable.')
            ledger=row['ledger_id'] or await code_receipt(actor.tenant_id,'radix.deploy.override',agent='radix',conn=conn)
            await conn.execute('''UPDATE tank.radix_deploy_findings SET override_user=$4,override_at=now(),ledger_id=$5
              WHERE tenant_id=$1 AND id=$2 AND app=$3''',actor.tenant_id,body.finding_id,body.app,actor.user_id,ledger)
    return {'app':body.app,'finding_id':str(body.finding_id),'ledger_id':str(ledger),'cost_usd':0}


def build_router():
    from command import Actor,identity,require_tool,RadixDeployOverrideArgs
    router=APIRouter(prefix='/radix/deploy')

    @router.post('/override')
    async def confirmed(body:RadixDeployOverrideArgs,actor:Actor=Depends(identity)):
        require_tool(actor,'radix.deploy.override')
        return await override(actor,body)

    @router.get('/gate')
    @tenant_path('tenant_id')
    async def read(tenant_id:UUID,app:str,request:Request):
        from main import TANK_API_KEY
        supplied=request.headers.get('x-tank-key','')
        if not supplied or not TANK_API_KEY or not hmac.compare_digest(supplied,TANK_API_KEY):
            raise HTTPException(401,'authentication required')
        if request.headers.get('x-tervory-tenant')!=str(tenant_id):
            raise HTTPException(403,'tenant mismatch')
        return await decision(tenant_id,app)
    return router
