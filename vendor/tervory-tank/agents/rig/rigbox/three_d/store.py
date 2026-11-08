"""Durable state. All SQL, including updates, uses the authenticated tenant."""
from __future__ import annotations

import json
from uuid import UUID


def decoded(row):
    if row is None:
        return None
    result = dict(row)
    for key in ("request", "report", "review", "mount_points", "progress"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


class Store:
    def __init__(self, pool):
        self.pool = pool

    async def record_progress(self, tenant, job_id, events):
        # Polling/reconnects append each durable event once, including across workers.
        for event in events:
            await self.pool.execute('''UPDATE agent_rig.rig_3d_jobs SET progress=progress || $3::jsonb
                WHERE tenant_id=$1 AND id=$2 AND NOT EXISTS
                (SELECT 1 FROM jsonb_array_elements(progress) e WHERE e->>'id'=$4)''',
                UUID(str(tenant)), UUID(str(job_id)), json.dumps([event]), event['id'])

    async def terminal_rows(self, tenant):
        return [decoded(row) for row in await self.pool.fetch('''WITH ranked AS (
            SELECT j.*, row_number() OVER (PARTITION BY status ORDER BY COALESCE(approved_at,created_at) DESC,id DESC) AS position
            FROM agent_rig.rig_3d_jobs j WHERE tenant_id=$1)
            SELECT j.*, p.name, p.price, p.currency
            FROM ranked j JOIN agent_rig.products p
            ON p.tenant_id=j.tenant_id AND p.sku=j.product_id
            WHERE j.tenant_id=$1 AND p.active=true AND j.position <= CASE
                WHEN j.status='approved' THEN 20 WHEN j.status='failed' THEN 1 ELSE 10 END
            ORDER BY COALESCE(j.approved_at,j.created_at) DESC, j.id DESC''', UUID(str(tenant)))]

    async def completed(self, tenant, before=None, today=True):
        return [decoded(row) for row in await self.pool.fetch('''SELECT j.*,p.name,p.price,p.currency
            FROM agent_rig.rig_3d_jobs j JOIN agent_rig.products p
            ON p.tenant_id=j.tenant_id AND p.sku=j.product_id
            WHERE j.tenant_id=$1 AND j.status='approved' AND p.active=true
            AND (NOT $3::boolean OR j.approved_at >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
            AND ($2::uuid IS NULL OR (j.approved_at,j.id) <
                (SELECT approved_at,id FROM agent_rig.rig_3d_jobs WHERE tenant_id=$1 AND id=$2 AND status='approved'))
            ORDER BY j.approved_at DESC,j.id DESC LIMIT 20''', UUID(str(tenant)), UUID(str(before)) if before else None, today)]

    async def terminal_counts(self, tenant):
        return dict(await self.pool.fetchrow('''SELECT
            count(*) FILTER (WHERE j.status='approved') AS approved,
            count(*) FILTER (WHERE j.status='approved' AND j.approved_at >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS today,
            count(*) FILTER (WHERE j.status IN ('queued','running','draft')) AS active
            FROM agent_rig.rig_3d_jobs j JOIN agent_rig.products p
            ON p.tenant_id=j.tenant_id AND p.sku=j.product_id WHERE j.tenant_id=$1 AND p.active=true''', UUID(str(tenant))))

    async def entitlement(self, tenant):
        return decoded(await self.pool.fetchrow(
            "SELECT enabled, price_amount, currency FROM agent_rig.rig_3d_entitlements WHERE tenant_id=$1",
            UUID(str(tenant))))

    async def set_entitlement(self, tenant, enabled, price, currency):
        await self.pool.execute("""
            INSERT INTO agent_rig.rig_3d_entitlements (tenant_id, enabled, price_amount, currency)
            VALUES ($1,$2,$3,$4) ON CONFLICT (tenant_id) DO UPDATE
            SET enabled=excluded.enabled, price_amount=excluded.price_amount, currency=excluded.currency
        """, UUID(str(tenant)), enabled, price, currency)

    async def create(self, job):
        await self.pool.execute("""
            INSERT INTO agent_rig.rig_3d_jobs (tenant_id, id, product_id, request)
            VALUES ($1,$2,$3,$4::jsonb) ON CONFLICT (tenant_id,id) DO NOTHING
        """, job.tenant_id, job.id, job.product_id, job.model_dump_json())

    async def job(self, tenant, job_id):
        return decoded(await self.pool.fetchrow(
            "SELECT * FROM agent_rig.rig_3d_jobs WHERE tenant_id=$1 AND id=$2", UUID(str(tenant)), UUID(str(job_id))))

    async def finish(self, tenant, job_id, status, report=None, reason=None):
        await self.pool.execute("""
            UPDATE agent_rig.rig_3d_jobs SET status=$3, report=$4::jsonb, reason=$5
            WHERE tenant_id=$1 AND id=$2 AND status IN ('queued','running')
        """, UUID(str(tenant)), UUID(str(job_id)), status, json.dumps(report) if report else None, reason)

    async def review_queue(self, tenant):
        return [decoded(r) for r in await self.pool.fetch("""
            SELECT id, product_id, report FROM agent_rig.rig_3d_jobs
            WHERE tenant_id=$1 AND status='draft' ORDER BY created_at, id LIMIT 100
        """, UUID(str(tenant)))]

    async def jobs(self, tenant):
        return [decoded(r) for r in await self.pool.fetch("""
            SELECT id, product_id, status, report, reason, progress FROM agent_rig.rig_3d_jobs
            WHERE tenant_id=$1 ORDER BY created_at DESC, id DESC LIMIT 100
        """, UUID(str(tenant)))]

    async def approve(self, tenant, job_id, review, reviewer):
        return await self.pool.fetchval("""
            UPDATE agent_rig.rig_3d_jobs SET status='approved', review=$3::jsonb,
                reviewed_by=$4, approved_at=now()
            WHERE tenant_id=$1 AND id=$2 AND status='draft' RETURNING id
        """, UUID(str(tenant)), UUID(str(job_id)), json.dumps(review), reviewer)

    async def approved(self, tenant, product_id):
        return decoded(await self.pool.fetchrow("""
            SELECT * FROM agent_rig.rig_3d_jobs WHERE tenant_id=$1 AND product_id=$2 AND status='approved'
            ORDER BY approved_at DESC, id DESC LIMIT 1
        """, UUID(str(tenant)), product_id))

    async def mounts(self, tenant, part, centre, family):
        return [decoded(r) for r in await self.pool.fetch("""
            SELECT rule, mount_points FROM agent_rig.fits WHERE tenant_id=$1 AND part_sku=$2 AND approved=true
              AND (fits_sku=$3 OR (fits_sku='' AND fits_family=$4 AND fits_family<>''))
        """, UUID(str(tenant)), part, centre, family)]

    async def set_mounts(self, tenant, part, centre, family, centre_anchor, part_anchor):
        return await self.pool.fetchval("""
            UPDATE agent_rig.fits SET mount_points=$5::jsonb
            WHERE tenant_id=$1 AND part_sku=$2 AND approved=true
              AND (fits_sku=$3 OR (fits_sku='' AND fits_family=$4 AND fits_family<>'')) RETURNING part_sku
        """, UUID(str(tenant)), part, centre, family, json.dumps({"centre": centre_anchor, "part": part_anchor}))

    async def kiosk(self, tenant, kiosk_id, reset=False):
        if reset:
            return await self.pool.fetchval("""
                INSERT INTO agent_rig.rig_3d_kiosks (tenant_id,id,revision) VALUES ($1,$2,1)
                ON CONFLICT (tenant_id,id) DO UPDATE SET revision=agent_rig.rig_3d_kiosks.revision+1
                RETURNING revision
            """, UUID(str(tenant)), UUID(str(kiosk_id)))
        return (await self.pool.fetchval(
            "SELECT revision FROM agent_rig.rig_3d_kiosks WHERE tenant_id=$1 AND id=$2",
            UUID(str(tenant)), UUID(str(kiosk_id)))) or 0
