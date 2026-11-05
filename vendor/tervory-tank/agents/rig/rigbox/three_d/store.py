"""Durable state. All SQL, including updates, uses the authenticated tenant."""
from __future__ import annotations

import json
from uuid import UUID


def decoded(row):
    if row is None:
        return None
    result = dict(row)
    for key in ("request", "report", "review", "mount_points"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


class Store:
    def __init__(self, pool):
        self.pool = pool

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
            SELECT id, product_id, status, report, reason FROM agent_rig.rig_3d_jobs
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
