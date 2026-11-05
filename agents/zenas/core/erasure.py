"""Regional operator erasure. Public law and the immutable home anchor survive."""
from contextlib import asynccontextmanager
from uuid import UUID

from agents.zenas.pool.region import RegionError, assert_box_region


class ConnectionPool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def erase(conn, *, tenant_id, region, matter_id=None):
    """Erase an owning tenant's rows atomically; leave durable vector tombstones."""
    assert_box_region(region)
    tenant = UUID(str(tenant_id))
    matter = UUID(str(matter_id)) if matter_id else None
    # The platform identity owns public records, never a client's account.
    if tenant.int == 1:
        raise ValueError("platform erasure refused")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(tenant))
        home = await conn.fetchval(
            "SELECT home_region FROM agent_zenas.tenants WHERE tenant_id=$1 FOR UPDATE", tenant
        )
        if home != region:
            raise RegionError("tenant is not registered on this region")
        matters = await conn.fetch(
            """SELECT id FROM agent_zenas.matters WHERE tenant_id=$1
            AND ($2::uuid IS NULL OR id=$2) ORDER BY id FOR UPDATE""", tenant, matter
        )
        ids = [row["id"] for row in matters]
        if matter and not ids:
            return {"deleted": False}
        # Reuse Hermes's locking and deletion outbox, including every old encoder.
        from hermes.records import Records

        records = Records(ConnectionPool(conn), None, None, "", ["zenas"])
        if matter:
            memories = await conn.fetch(
                """SELECT id FROM agent_zenas.memory_records WHERE tenant_id=$1
                AND tier='client' AND meta->>'matter_id'=$2""", tenant, str(matter)
            )
            for row in memories:
                await records.forget("zenas", str(tenant), str(row["id"]))
        else:
            await records.forget("zenas", str(tenant))
        worked = await conn.fetch(
            """SELECT id FROM agent_zenas.worked_matters WHERE region=$1 AND source='released'
            AND (tenant_id=$2 OR tenant_id IS NULL) AND released_matter_id=ANY($3::uuid[])""",
            region, tenant, ids,
        )
        worked_ids = [row["id"] for row in worked]
        await conn.execute(
            """DELETE FROM agent_zenas.training_pairs WHERE region=$1 AND source='released'
            AND (tenant_id=$2 OR tenant_id IS NULL) AND worked_id=ANY($3::uuid[])""",
            region, tenant, worked_ids,
        )
        await conn.execute(
            """DELETE FROM agent_zenas.worked_matters WHERE region=$1 AND source='released'
            AND (tenant_id=$2 OR tenant_id IS NULL) AND id=ANY($3::uuid[])""",
            region, tenant, worked_ids,
        )
        await conn.execute(
            """DELETE FROM agent_zenas.reviews WHERE tenant_id=$1 AND document_id IN
            (SELECT id FROM agent_zenas.documents WHERE tenant_id=$1 AND matter_id=ANY($2::uuid[]))""",
            tenant, ids,
        )
        for table in ("pii", "documents"):
            await conn.execute(
                f"DELETE FROM agent_zenas.{table} WHERE tenant_id=$1 AND matter_id=ANY($2::uuid[])", tenant, ids
            )
        await conn.execute("DELETE FROM agent_zenas.matters WHERE tenant_id=$1 AND id=ANY($2::uuid[])", tenant, ids)
        if matter is None:
            # Catalogue discovery covers optional alert tables and future tenant tables.
            # FK children precede parents; identifiers come solely from pg_catalog.
            tables = await conn.fetch("""SELECT c.relname AS name FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='tenant_id' AND NOT a.attisdropped
                WHERE n.nspname='agent_zenas' AND c.relkind='r'
                AND c.relname NOT IN ('tenants','memory_index_jobs')
                ORDER BY EXISTS(SELECT 1 FROM pg_constraint f WHERE f.contype='f' AND f.conrelid=c.oid) DESC,
                c.relname"""
            )
            for row in tables:
                table = row["name"].replace('"', '""')
                await conn.execute(f'DELETE FROM agent_zenas."{table}" WHERE tenant_id=$1', tenant)
        return {"deleted": True}
