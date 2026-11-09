"""Tenant-owned DNS challenges and completed passive assessments, never raw traffic."""
from __future__ import annotations

import json
import hashlib
import re
from html.parser import HTMLParser
import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid4

DDL = """
CREATE TABLE IF NOT EXISTS agent_radix.passive_domains (
 tenant_id uuid NOT NULL, domain text NOT NULL, challenge text NOT NULL,
 expires_at timestamptz NOT NULL DEFAULT now() + interval '1 day',
 PRIMARY KEY (tenant_id, domain)
);
CREATE TABLE IF NOT EXISTS agent_radix.passive_scans (
 tenant_id uuid NOT NULL, id uuid NOT NULL, domain text NOT NULL,
 target text NOT NULL, artifact text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','complete','error')),
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 lease_until timestamptz,
 high integer NOT NULL DEFAULT 0, critical integer NOT NULL DEFAULT 0,
 PRIMARY KEY (tenant_id,id),
 FOREIGN KEY (tenant_id,domain) REFERENCES agent_radix.passive_domains(tenant_id,domain)
);
ALTER TABLE agent_radix.passive_scans ADD COLUMN IF NOT EXISTS infra boolean NOT NULL DEFAULT false;
ALTER TABLE agent_radix.passive_scans ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT false;
ALTER TABLE agent_radix.passive_scans ADD COLUMN IF NOT EXISTS checks jsonb;
ALTER TABLE agent_radix.passive_scans ADD COLUMN IF NOT EXISTS watched boolean NOT NULL DEFAULT false;
ALTER TABLE agent_radix.passive_scans ADD COLUMN IF NOT EXISTS changes jsonb;
ALTER TABLE agent_radix.passive_scans ADD COLUMN IF NOT EXISTS previous_scan_id uuid;
CREATE TABLE IF NOT EXISTS agent_radix.passive_watches (
 tenant_id uuid NOT NULL, domain text NOT NULL, challenge text NOT NULL,
 interval text NOT NULL CHECK (interval IN ('daily','weekly')),
 status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','stopped')),
 stop_reason text CHECK (stop_reason IN ('txt_missing','txt_changed')),
 next_run timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,domain),
 FOREIGN KEY (tenant_id,domain) REFERENCES agent_radix.passive_domains(tenant_id,domain)
);
-- Follow personal_tokens: join an existing platform rollout, never start one.
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='tank' AND c.relname='command_threads' AND c.relrowsecurity) THEN
  ALTER TABLE agent_radix.passive_watches ENABLE ROW LEVEL SECURITY;
  ALTER TABLE agent_radix.passive_watches FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.passive_watches'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON agent_radix.passive_watches FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.passive_watches'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON agent_radix.passive_watches AS RESTRICTIVE FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS agent_radix.regulation_runs (
 tenant_id uuid NOT NULL, domain text NOT NULL, event_id uuid NOT NULL,
 scan_id uuid NOT NULL, facts jsonb NOT NULL,
 settled boolean NOT NULL DEFAULT false,
 next_attempt timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,domain,event_id),
 UNIQUE (tenant_id,scan_id)
);
CREATE TABLE IF NOT EXISTS agent_radix.page_hashes (
 tenant_id uuid NOT NULL, scan_id uuid NOT NULL, domain text NOT NULL,
 url text NOT NULL, sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
 PRIMARY KEY (tenant_id,scan_id,url)
);

CREATE TABLE IF NOT EXISTS agent_radix.active_consents (
 tenant_id uuid NOT NULL, id uuid NOT NULL, domain text NOT NULL,
 consent_text text NOT NULL CHECK(length(consent_text) BETWEEN 1 AND 16000),
 granted_by text NOT NULL, granted_at timestamptz NOT NULL DEFAULT now(),
 withdrawn_by text, withdrawn_at timestamptz,
 PRIMARY KEY(tenant_id,id)
);
ALTER TABLE IF EXISTS agent_radix.passive_scans ADD COLUMN IF NOT EXISTS active_consent_id uuid;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='tank' AND c.relname='command_threads' AND c.relrowsecurity) THEN
  ALTER TABLE agent_radix.active_consents ENABLE ROW LEVEL SECURITY;
  ALTER TABLE agent_radix.active_consents FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.active_consents'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON agent_radix.active_consents FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.active_consents'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON agent_radix.active_consents AS RESTRICTIVE FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;

CREATE TABLE IF NOT EXISTS agent_radix.weekly_domains (
 tenant_id uuid NOT NULL, domain text NOT NULL, challenge text NOT NULL,
 own boolean NOT NULL DEFAULT false, consent_id uuid, enabled boolean NOT NULL DEFAULT true,
 next_run timestamptz NOT NULL DEFAULT now()+interval '7 days',
 PRIMARY KEY(tenant_id,domain)
);
CREATE TABLE IF NOT EXISTS agent_radix.weekly_runs (
 tenant_id uuid NOT NULL, domain text NOT NULL, period timestamptz NOT NULL,
 kind text NOT NULL CHECK(kind IN ('passive','infra')), scan_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,domain,period,kind), UNIQUE(tenant_id,scan_id)
);
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='tank' AND c.relname='command_threads' AND c.relrowsecurity) THEN
  ALTER TABLE agent_radix.weekly_domains ENABLE ROW LEVEL SECURITY;
  ALTER TABLE agent_radix.weekly_domains FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.weekly_domains'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON agent_radix.weekly_domains FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.weekly_domains'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON agent_radix.weekly_domains AS RESTRICTIVE FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='tank' AND c.relname='command_threads' AND c.relrowsecurity) THEN
  ALTER TABLE agent_radix.weekly_runs ENABLE ROW LEVEL SECURITY;
  ALTER TABLE agent_radix.weekly_runs FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.weekly_runs'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON agent_radix.weekly_runs FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='agent_radix.weekly_runs'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON agent_radix.weekly_runs AS RESTRICTIVE FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS passive_one_running
 ON agent_radix.passive_scans ((true)) WHERE status='running';
"""


def domain_name(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 253:
        raise ValueError('invalid domain')
    value = value.lower().removesuffix('.')
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value):
        raise ValueError('invalid domain')
    if value.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('invalid domain')
    return value


def target_url(value: str) -> tuple[str, str]:
    p = urlsplit(value)
    host = domain_name(p.hostname or '')
    # Deliberately one HTTPS origin root. No credentials, ports, query, path or fragments.
    if value != f'https://{host}/' or p.port is not None:
        raise ValueError('invalid target')
    return value, host


def artifact_id(value: str) -> str:
    if not re.fullmatch(r'(?:commit:[0-9a-f]{40}|sha256:[0-9a-f]{64})', value):
        raise ValueError('invalid artifact')
    return value


def watch_artifact(domain: str) -> str:
    """Hash the canonical HTTPS scan target, not a nonexistent source commit.

    Identifies the assessed origin, not a content snapshot (the spider observes
    multiple responses). The run UUID and timestamps distinguish observations.
    """
    target = f'https://{domain_name(domain)}/'
    return 'sha256:' + hashlib.sha256(target.encode('ascii')).hexdigest()


def compare_findings(current: list[dict], previous: list[dict]) -> dict:
    # A check is namespaced by its scanner. An item is its URL/file and line.
    # A changed severity retires the old finding and introduces the new one.
    def identity(item):
        return (item.get('tool'), item.get('rule'), item.get('item') or item.get('file'),
                0 if item.get('item') else item.get('line') or 0, item.get('severity'))
    old = {identity(item): item for item in previous}
    new = {identity(item): item for item in current}
    return {'new': [item for key, item in new.items() if key not in old],
            'fixed': [item for key, item in old.items() if key not in new],
            'unchanged': [item for key, item in new.items() if key in old]}


class RuleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def report_finding(row) -> dict:
    item = dict(row)
    encoded = item.pop('fix', None)
    data = json.loads(encoded) if encoded else {}
    texts = data.get(item['rule'], {})
    if '_measured' in data:
        item.update(data['_measured'])
    for field in ('explanation', 'remediation'):
        text = texts.get(field)
        if text:
            parser = RuleText()
            parser.feed(text)
            item[field] = ' '.join(' '.join(parser.parts).split())
        elif '_measured' not in data:
            item[field] = f"No rule text for {item['rule']}."
    return item


class Store:
    def __init__(self, pool):
        self.pool = pool

    async def issue(self, tenant: UUID, domain: str, *, own=False) -> dict:
        domain = domain_name(domain)
        # Public DNS proof, not an authentication key. No cross-tenant default.
        challenge = secrets.token_hex(32)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""INSERT INTO agent_radix.passive_domains(tenant_id,domain,challenge)
              VALUES($1,$2,'tervory-' || $3) ON CONFLICT(tenant_id,domain) DO UPDATE
              SET challenge=CASE WHEN EXISTS (SELECT 1 FROM agent_radix.passive_watches w
                WHERE w.tenant_id=$1 AND w.domain=$2 AND w.status='active') OR EXISTS(
                SELECT 1 FROM agent_radix.weekly_domains v WHERE v.tenant_id=$1 AND v.domain=$2 AND v.enabled)
                THEN passive_domains.challenge ELSE 'tervory-' || $3 END,
              expires_at=CASE WHEN EXISTS (SELECT 1 FROM agent_radix.passive_watches w
                WHERE w.tenant_id=$1 AND w.domain=$2 AND w.status='active')
                THEN passive_domains.expires_at ELSE now()+interval '1 day' END
              RETURNING expires_at,challenge""", tenant, domain, challenge)
        await self.enroll_weekly(tenant,domain,own=own)
        return {'tenant_id': str(tenant), 'domain': domain, 'name': '_tervory-verify.' + domain,
                'value': row['challenge'], 'expires_at': row['expires_at'].isoformat()}

    async def enroll_weekly(self,tenant,domain,*,own=False):
        if own:
            from active_policy import active_target
            try:active_target('https://'+domain+'/')
            except PermissionError:return
        consent=await self.consent_id(tenant,domain)
        if not own and consent is None:return
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO agent_radix.weekly_domains(tenant_id,domain,challenge,own,consent_id)
              SELECT tenant_id,domain,challenge,$3,$4 FROM agent_radix.passive_domains WHERE tenant_id=$1 AND domain=$2
              ON CONFLICT(tenant_id,domain) DO UPDATE SET challenge=excluded.challenge,
              own=weekly_domains.own OR excluded.own,consent_id=excluded.consent_id,enabled=true""",tenant,domain,own,consent)
            await conn.execute("""UPDATE agent_radix.passive_watches SET next_run=(SELECT next_run
              FROM agent_radix.weekly_domains WHERE tenant_id=$1 AND domain=$2)
              WHERE tenant_id=$1 AND domain=$2 AND interval='weekly' AND status='active' AND next_run>now()""",tenant,domain)

    async def consent(self, tenant, domain, user, text):
        domain=domain_name(domain)
        if not user or not isinstance(text,str) or not text.strip() or len(text)>16000:
            raise ValueError('scan not authorized')
        async with self.pool.acquire() as conn:
            row=await conn.fetchrow("""INSERT INTO agent_radix.active_consents(tenant_id,id,domain,granted_by,consent_text)
              VALUES($1,$2,$3,$4,$5) RETURNING id,domain,granted_by,granted_at""",tenant,uuid4(),domain,user,text)
        await self.enroll_weekly(tenant,domain)
        return {**dict(row),'id':str(row['id']),'granted_at':row['granted_at'].isoformat()}

    async def withdraw_consent(self, tenant, domain, user):
        domain=domain_name(domain)
        async with self.pool.acquire() as conn:
            await conn.execute("""UPDATE agent_radix.active_consents SET withdrawn_by=$3,withdrawn_at=now()
              WHERE tenant_id=$1 AND domain=$2 AND withdrawn_at IS NULL""",tenant,domain,user)
            await conn.execute('''UPDATE agent_radix.weekly_domains SET enabled=false
              WHERE tenant_id=$1 AND domain=$2 AND NOT own''',tenant,domain)
        return {'domain':domain,'withdrawn':True}

    async def consent_id(self, tenant, domain):
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""SELECT id FROM agent_radix.active_consents
              WHERE tenant_id=$1 AND domain=$2 AND withdrawn_at IS NULL
              ORDER BY granted_at DESC,id DESC LIMIT 1""",tenant,domain)

    async def consent_valid(self, tenant, domain, ident):
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval("""SELECT 1 FROM agent_radix.active_consents
              WHERE tenant_id=$1 AND domain=$2 AND id=$3 AND withdrawn_at IS NULL""",tenant,domain,ident))

    async def create(self, tenant: UUID, target: str, artifact: str, *, watched: bool = False, active: bool = False, consent_id: UUID | None = None, infra: bool = False) -> UUID:
        target, domain = target_url(target)
        artifact = artifact_id(artifact)
        if active and consent_id is None:
            from active_policy import active_target
            active_target(target)
        ident = uuid4()
        async with self.pool.acquire() as conn:
            if consent_id is not None and not await conn.fetchval("""SELECT 1 FROM agent_radix.active_consents
              WHERE tenant_id=$1 AND domain=$2 AND id=$3 AND withdrawn_at IS NULL""",tenant,domain,consent_id):
                raise PermissionError('scan not authorized')
            row = await conn.fetchrow("""INSERT INTO agent_radix.passive_scans(tenant_id,id,domain,target,artifact,watched,active,active_consent_id,infra)
              SELECT d.tenant_id,$2,d.domain,$3,$4,$6,$7,$8,$9 FROM agent_radix.passive_domains d
              WHERE d.tenant_id=$1 AND d.domain=$5 AND
              (($6=false AND d.expires_at>now()) OR (($6=true OR $7=true) AND EXISTS (
                SELECT 1 FROM agent_radix.passive_watches w WHERE w.tenant_id=d.tenant_id
                AND w.domain=d.domain AND w.status='active' AND w.challenge=d.challenge)) OR ($7 AND EXISTS (
                SELECT 1 FROM agent_radix.weekly_domains v WHERE v.tenant_id=d.tenant_id AND v.domain=d.domain
                AND v.enabled AND v.challenge=d.challenge AND (v.own OR EXISTS(
                  SELECT 1 FROM agent_radix.active_consents c WHERE c.tenant_id=v.tenant_id
                    AND c.id=v.consent_id AND c.withdrawn_at IS NULL))))) RETURNING id""",
              tenant, ident, target, artifact, domain, watched, active, consent_id, infra)
        if row is None:
            raise PermissionError('domain proof required')
        return ident

    async def pending(self, tenant: UUID, ident: UUID):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""SELECT s.domain,d.challenge,s.active,s.active_consent_id,s.infra,
              EXISTS(SELECT 1 FROM agent_radix.weekly_runs r WHERE r.tenant_id=s.tenant_id AND r.scan_id=s.id) AS weekly FROM agent_radix.passive_scans s
              JOIN agent_radix.passive_domains d ON d.tenant_id=s.tenant_id AND d.domain=s.domain
              WHERE s.tenant_id=$1 AND s.id=$2 AND s.status='pending' AND
              ((s.watched=false AND d.expires_at>now()) OR ((s.watched=true OR s.active=true) AND EXISTS (
                SELECT 1 FROM agent_radix.passive_watches w WHERE w.tenant_id=s.tenant_id
                AND w.domain=s.domain AND w.status='active' AND w.challenge=d.challenge)) OR (s.active AND EXISTS (
                SELECT 1 FROM agent_radix.weekly_domains v WHERE v.tenant_id=d.tenant_id AND v.domain=d.domain
                AND v.enabled AND v.challenge=d.challenge AND (v.own OR EXISTS(
                  SELECT 1 FROM agent_radix.active_consents c WHERE c.tenant_id=v.tenant_id
                    AND c.id=v.consent_id AND c.withdrawn_at IS NULL)))) OR EXISTS (
                SELECT 1 FROM agent_radix.weekly_runs r JOIN agent_radix.weekly_domains v
                 ON v.tenant_id=r.tenant_id AND v.domain=r.domain
                WHERE r.tenant_id=s.tenant_id AND r.scan_id=s.id AND v.enabled AND v.challenge=d.challenge
                 AND (v.own OR EXISTS(SELECT 1 FROM agent_radix.active_consents c WHERE c.tenant_id=v.tenant_id
                   AND c.id=v.consent_id AND c.withdrawn_at IS NULL))))
              AND (s.created_at>now()-interval '5 minutes' OR EXISTS (
                SELECT 1 FROM agent_radix.regulation_runs r WHERE r.tenant_id=s.tenant_id
                AND r.scan_id=s.id AND NOT r.settled) OR EXISTS (
                SELECT 1 FROM agent_radix.weekly_runs v WHERE v.tenant_id=s.tenant_id AND v.scan_id=s.id))""", tenant, ident)

    async def running(self, tenant: UUID, ident: UUID) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""UPDATE agent_radix.passive_scans
              SET status='running',lease_until=now()+CASE WHEN active THEN interval '1800 seconds' ELSE interval '420 seconds' END
              WHERE tenant_id=$1 AND id=$2 AND status='pending' RETURNING id""", tenant, ident)
        return row is not None

    async def reap_expired(self, discovery=None) -> None:
        """Gateway/scheduler maintenance, never a tenant HTTP request.

        Discover expired lease owners, then update each row with its persisted
        tenant/id and recheck expiry. An active lease is never touched.
        """
        from chassis.tenant_db import tenant_scope
        query = """SELECT tenant_id,id FROM agent_radix.passive_scans
          WHERE status='running' AND lease_until<now()"""
        if discovery is None:
            maintenance = getattr(self.pool, 'admin_pool', None) or getattr(self.pool, 'raw', self.pool)
            async with maintenance.acquire() as conn:
                owners = await conn.fetch(query)
        else:
            owners = await discovery.fetch(query)
        for owner in owners:
            with tenant_scope(owner['tenant_id']):
                async with self.pool.acquire() as conn:
                    # Durable queues keep their scan IDs across a worker crash.
                    # Execution still rechecks current DNS proof and consent.
                    await conn.execute("""UPDATE agent_radix.passive_scans s SET
                      status=CASE WHEN EXISTS(SELECT 1 FROM agent_radix.weekly_runs w
                        WHERE w.tenant_id=s.tenant_id AND w.scan_id=s.id)
                        OR EXISTS(SELECT 1 FROM agent_radix.regulation_runs r
                        WHERE r.tenant_id=s.tenant_id AND r.scan_id=s.id AND NOT r.settled)
                        THEN 'pending' ELSE 'error' END,lease_until=NULL
                      WHERE s.tenant_id=$1 AND s.id=$2 AND s.status='running' AND s.lease_until<now()""",
                      owner['tenant_id'], owner['id'])

    async def fail(self, tenant: UUID, ident: UUID) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""UPDATE agent_radix.passive_scans SET status='error'
              WHERE tenant_id=$1 AND id=$2 AND status IN ('pending','running')""", tenant, ident)

    async def complete(self, tenant: UUID, ident: UUID, findings: list[dict], *, measurements=None) -> None:
        from scanlib.store import ensure_findings, insert_findings
        high = sum(f['severity'] == 'high' for f in findings)
        critical = sum(f['severity'] == 'critical' for f in findings)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Serialize baselines for the same tenant/domain, including manual scans.
                domain = await conn.fetchval('SELECT domain FROM agent_radix.passive_scans WHERE tenant_id=$1 AND id=$2', tenant, ident)
                await conn.fetchrow('SELECT domain FROM agent_radix.passive_domains WHERE tenant_id=$1 AND domain=$2 FOR UPDATE', tenant, domain)
                await ensure_findings(conn, 'agent_radix')
                prior = await conn.fetchval('''SELECT id FROM agent_radix.passive_scans
                  WHERE tenant_id=$1 AND domain=$2 AND status='complete' AND id<>$3
                  AND infra=(SELECT infra FROM agent_radix.passive_scans WHERE tenant_id=$1 AND id=$3)
                  AND active=(SELECT active FROM agent_radix.passive_scans WHERE tenant_id=$1 AND id=$3)
                  AND NOT EXISTS(SELECT 1 FROM jsonb_each(coalesce(checks,'{}'::jsonb)) AS check_row
                    WHERE check_row.value->>'status' IN ('partial','skipped'))
                  AND EXISTS(SELECT 1 FROM agent_radix.regulation_runs r WHERE r.tenant_id=$1 AND r.scan_id=$3)
                    = EXISTS(SELECT 1 FROM agent_radix.regulation_runs r WHERE r.tenant_id=$1 AND r.scan_id=passive_scans.id)
                  ORDER BY completed_at DESC,id DESC LIMIT 1''', tenant, domain, ident)
                from scanlib.store import list_findings
                previous = [report_finding(r) for r in await list_findings(conn, 'agent_radix', str(tenant), str(prior))] if prior else []
                if measurements is not None:
                    covered = {'zap','nuclei'} if not any(f.get('tool') == 'http' for f in findings) else set()
                    if measurements.get('checks', {}).get('active', {}).get('status') in {'partial','skipped'}:
                        covered = set()
                    namespaces = {'pages':{'sha256'},'certificate':{'scan.tls'},'tls':{'testssl.sh'},
                        'malware':{'clamav'},'browser':{'axe-core','chromium'},'notices':{'comply.gdpr'}}
                    for check, status in measurements.get('checks', {}).items():
                        if status.get('status') == 'complete':
                            covered.update(namespaces.get(check,set()))
                    previous = [f for f in previous if f['tool'] in covered]
                changes = compare_findings([report_finding(f) for f in findings], previous)
                await insert_findings(conn, 'agent_radix', str(tenant), str(ident), findings)
                row = await conn.fetchrow("""UPDATE agent_radix.passive_scans
                  SET status=CASE WHEN $8 THEN 'error' ELSE 'complete' END,high=$3,critical=$4,completed_at=now(),changes=$5::jsonb,previous_scan_id=$6,checks=$7::jsonb
                  WHERE tenant_id=$1 AND id=$2 AND status='running'
                  AND lease_until>now() RETURNING id""", tenant, ident, high, critical, json.dumps(changes, default=str), prior, json.dumps((measurements or {}).get('checks', {})),
                  (measurements or {}).get('checks', {}).get('active', {}).get('status') in {'partial','skipped'})
                if row is None:
                    raise RuntimeError('scan lease expired')
                for url, digest in ((measurements or {}).get('page_hashes') or {}).items():
                    await conn.execute('''INSERT INTO agent_radix.page_hashes(tenant_id,scan_id,domain,url,sha256)
                      VALUES($1,$2,$3,$4,$5)''', tenant, ident, domain, url, digest)
        from chassis.events import publish
        for ordinal, finding in enumerate(findings):
            # The bus uses its fixed severity vocabulary; keep the tool's own
            # severity unchanged in findings (axe also reports serious).
            event_severity = finding['severity'] if finding['severity'] in {
                'critical','high','medium','low','info','unknown'} else 'unknown'
            await publish(str(tenant), 'radix.finding', f'{ident}:{ordinal}',
                          {'scan_id': str(ident), 'ordinal': ordinal, 'severity': event_severity})

    async def watch(self, tenant: UUID, domain: str, interval: str, *, run_now: bool = True) -> dict:
        domain = domain_name(domain)
        if interval not in {'daily', 'weekly'}:
            raise ValueError('invalid interval')
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""INSERT INTO agent_radix.passive_watches
              (tenant_id,domain,challenge,interval,next_run)
              SELECT d.tenant_id,d.domain,d.challenge,$3,
                now()+CASE WHEN $4 THEN interval '0 seconds' WHEN $3='daily' THEN interval '1 day' ELSE interval '7 days' END
              FROM agent_radix.passive_domains d WHERE d.tenant_id=$1 AND d.domain=$2
              AND (d.expires_at>now() OR EXISTS (SELECT 1 FROM agent_radix.passive_watches w
                WHERE w.tenant_id=d.tenant_id AND w.domain=d.domain AND w.status='active' AND w.challenge=d.challenge))
              ON CONFLICT (tenant_id,domain) DO UPDATE SET interval=EXCLUDED.interval,
                challenge=EXCLUDED.challenge,status='active',stop_reason=NULL,next_run=EXCLUDED.next_run
              RETURNING domain AS name,interval,status,stop_reason,next_run""", tenant, domain, interval, run_now)
        if row is None:
            raise PermissionError('domain proof required')
        return dict(row)

    async def unwatch(self, tenant: UUID, domain: str) -> dict:
        domain = domain_name(domain)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""UPDATE agent_radix.passive_watches SET status='stopped',stop_reason=NULL
              WHERE tenant_id=$1 AND domain=$2 RETURNING domain AS name,interval,status,stop_reason,next_run""", tenant, domain)
        return dict(row) if row else {'name': domain, 'status': 'stopped'}

    async def watches(self, tenant: UUID) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT domain AS name,interval,status,stop_reason,next_run
              FROM agent_radix.passive_watches WHERE tenant_id=$1 ORDER BY domain""", tenant)
        return [dict(row) for row in rows]

    async def stop_for_scan(self, tenant: UUID, ident: UUID, reason: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""UPDATE agent_radix.passive_watches w SET status='stopped',stop_reason=$3
              FROM agent_radix.passive_scans s WHERE s.tenant_id=$1 AND s.id=$2 AND s.watched
              AND w.tenant_id=s.tenant_id AND w.domain=s.domain AND w.status='active'""", tenant, ident, reason)
            await conn.execute('''UPDATE agent_radix.weekly_domains w SET enabled=false
              FROM agent_radix.passive_scans s WHERE s.tenant_id=$1 AND s.id=$2
              AND w.tenant_id=s.tenant_id AND w.domain=s.domain''',tenant,ident)

    async def changes(self, tenant: UUID, domain: str) -> dict:
        domain = domain_name(domain)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT id,status,completed_at,changes,previous_scan_id,checks,active,
              (SELECT jsonb_build_object('event_id',r.event_id,'facts',r.facts,
                'checks',jsonb_build_array('comply.gdpr','cookie-missing-flags'),
                'browser_cookies_checked',coalesce(checks->'browser'->>'status'='complete',false))
               FROM agent_radix.regulation_runs r WHERE r.tenant_id=$1 AND r.scan_id=passive_scans.id) AS regulation_event
              FROM agent_radix.passive_scans WHERE tenant_id=$1 AND domain=$2
              ORDER BY created_at DESC,id DESC LIMIT 1""", tenant, domain)
        if not row:
            return {'status': 'pending'}
        result = {'scan_id': str(row['id']), 'status': 'failed' if row['status'] == 'error' else row['status'],
                  'completed_at': row['completed_at'], 'previous_scan_id': str(row['previous_scan_id']) if row['previous_scan_id'] else None}
        if row.get('checks') is not None:
            result['checks'] = json.loads(row['checks']) if isinstance(row['checks'], str) else row['checks']
        if row.get('regulation_event') is not None:
            event = row['regulation_event']
            result['regulation_event'] = json.loads(event) if isinstance(event, str) else event
        if row.get('active'):
            result['active'] = True
        if row['status'] == 'complete' or row.get('active') and row['status'] == 'error':
            delta = json.loads(row['changes']) if isinstance(row['changes'], str) else row['changes']
            # Old complete manual scans predate comparison persistence.
            result.update(delta or {'new': [], 'fixed': [], 'unchanged': []})
            result['findings'] = await self.findings(tenant, row['id'])
        return result

    async def previous_hashes(self, tenant: UUID, domain: str) -> dict:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT url,sha256 FROM agent_radix.page_hashes
              WHERE tenant_id=$1 AND domain=$2 AND scan_id=(
                SELECT id FROM agent_radix.passive_scans WHERE tenant_id=$1 AND domain=$2
                AND status='complete' AND checks->'pages'->>'status'='complete'
                ORDER BY completed_at DESC,id DESC LIMIT 1)""", tenant, domain)
        return {r['url']:r['sha256'] for r in rows}

    async def gate(self, tenant: UUID, target: str, artifact: str) -> dict:
        target_url(target)
        artifact_id(artifact)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT id,status,high,critical,completed_at
              FROM agent_radix.passive_scans WHERE tenant_id=$1 AND target=$2 AND artifact=$3 AND NOT active
              ORDER BY created_at DESC,id DESC LIMIT 1""", tenant, target, artifact)
        return gate_result(row, tenant, target, artifact)

    async def report(self,tenant: UUID,ident: UUID) -> dict:
        async with self.pool.acquire() as conn:
            row=await conn.fetchrow("""SELECT target,status,checks,active FROM agent_radix.passive_scans
              WHERE tenant_id=$1 AND id=$2""",tenant,ident)
        if row is None:
            raise LookupError('scan not found')
        return {'tenant_id':str(tenant),'scan_id':str(ident),'target':row['target'],
            'status':'failed' if row['status']=='error' else row['status'], 'active':row['active'],
            'checks':json.loads(row['checks']) if isinstance(row['checks'],str) else row['checks'],
            'findings':await self.findings(tenant,ident)}

    async def active_state(self, tenant: UUID, ident: UUID) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT status,checks FROM agent_radix.passive_scans
              WHERE tenant_id=$1 AND id=$2 AND active""", tenant, ident)
        if not row:
            return {}
        return {'active': True, 'status': 'failed' if row['status']=='error' else row['status'],
                'checks': json.loads(row['checks']) if isinstance(row['checks'],str) else row['checks']}

    async def findings(self, tenant: UUID, ident: UUID) -> list:
        from scanlib.store import list_findings
        async with self.pool.acquire() as conn:
            return [report_finding(row) for row in await list_findings(conn, 'agent_radix', str(tenant), str(ident))]


def gate_result(row, tenant: UUID, target: str, artifact: str, *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    complete = row and row['status'] == 'complete' and row['completed_at'] is not None
    fresh = complete and 0 <= (now - row['completed_at']).total_seconds() <= 3600
    return {'allowed': bool(fresh and row['high'] == 0 and row['critical'] == 0),
            'tenant_id': str(tenant), 'target': target, 'artifact': artifact,
            'scan_id': str(row['id']) if row else None, 'status': row['status'] if row else 'missing',
            'high': row['high'] if row else None, 'critical': row['critical'] if row else None,
            'completed_at': row['completed_at'].isoformat() if complete else None}
