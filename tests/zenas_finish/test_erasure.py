from uuid import UUID, uuid4

import pytest
from asyncpg.exceptions import RaiseError
from test_source_freshness import TENANT, room, save

from agents.zenas.core.erasure import erase
from agents.zenas.pool.region import RegionError

OTHER = uuid4()


async def seed(conn, tenant, matter):
    await conn.execute("INSERT INTO agent_zenas.tenants VALUES($1,'us-east') ON CONFLICT DO NOTHING", tenant)
    await conn.execute("""INSERT INTO agent_zenas.command_receipts(tenant_id,request_id,fingerprint,status,receipt)
        VALUES($1,$2,'placeholder-hmac',201,'placeholder-encrypted-receipt')""", tenant, uuid4())
    await conn.execute("""INSERT INTO agent_zenas.matters
        (id,tenant_id,jurisdiction,area,title,created_by) VALUES($1,$2,'us','civil','offline erasure','placeholder')""", matter, tenant)
    document = await conn.fetchval("""INSERT INTO agent_zenas.documents
        (matter_id,tenant_id,kind,version,body) VALUES($1,$2,'memo',1,'masked text') RETURNING id""", matter, tenant)
    await conn.execute("""INSERT INTO agent_zenas.pii(matter_id,tenant_id,kind,ciphertext)
        VALUES($1,$2,'name','placeholder encrypted bytes')""", matter, tenant)
    await conn.execute("""INSERT INTO agent_zenas.reviews
        (document_id,tenant_id,reviewer,jurisdiction,admission_no,decision)
        VALUES($1,$2,'placeholder','us','placeholder','approve')""", document, tenant)
    worked = await conn.fetchval("""INSERT INTO agent_zenas.worked_matters
        (region,tenant_id,source,jurisdiction,released_matter_id) VALUES('us-east',NULL,'released','us',$1) RETURNING id""", matter)
    await conn.execute("""INSERT INTO agent_zenas.training_pairs(region,source,worked_id)
        VALUES('us-east','released',$1)""", worked)
    await conn.execute("""INSERT INTO agent_zenas.memory_records(id,tenant_id,claim,embed_model,meta)
        VALUES($1,$2,'masked memory','offline-encoder',jsonb_build_object('matter_id',$3::text))""", uuid4(), tenant, str(matter))
    return worked


@pytest.mark.asyncio
async def test_matter_erasure_is_scoped_idempotent_and_keeps_vector_deletion_receipt(database):
    await room(database)
    public, _ = await save(database)
    tenant, first, second, foreign = UUID(TENANT), uuid4(), uuid4(), uuid4()
    async with database.acquire() as conn:
        await seed(conn, tenant, first)
        await seed(conn, tenant, second)
        await seed(conn, OTHER, foreign)
        assert await erase(conn, tenant_id=TENANT, matter_id=foreign, region='us-east') == {"deleted": False}
        assert await erase(conn, tenant_id=TENANT, matter_id=first, region='us-east') == {"deleted": True}
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.command_receipts WHERE tenant_id=$1 AND receipt IS NOT NULL',tenant)==0
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.command_receipts WHERE tenant_id=$1 AND status=410',tenant)==2
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.command_receipts WHERE tenant_id=$1 AND receipt IS NOT NULL',OTHER)==1
        for table in ('matters', 'pii', 'documents', 'reviews', 'worked_matters', 'training_pairs', 'memory_records'):
            assert await conn.fetchval(f'SELECT count(*) FROM agent_zenas.{table}') == 2
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.memory_index_jobs WHERE tenant_id=$1 AND operation='delete'", tenant) == 1
        assert await conn.fetchval("SELECT count(*) FROM zenas_pool.documents WHERE document_id=$1", public.id) == 1
        assert await erase(conn, tenant_id=TENANT, matter_id=first, region='us-east') == {"deleted": False}
        with pytest.raises(RegionError):
            await erase(conn, tenant_id=TENANT, matter_id=second, region='za')
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.pii') == 2


@pytest.mark.asyncio
async def test_tenant_erasure_keeps_anchor_foreign_rows_and_public_law(database):
    await room(database)
    tenant = UUID(TENANT)
    async with database.acquire() as conn:
        await seed(conn, tenant, uuid4())
        await seed(conn, OTHER, uuid4())
        hearing = uuid4()
        await conn.execute("INSERT INTO agent_zenas.moot_hearings(id,tenant_id,region,revision,state) VALUES($1,$2,'us-east',0,'{}')", hearing, tenant)
        await conn.execute("INSERT INTO agent_zenas.moot_hearing_events(tenant_id,hearing_id,revision,actor_id,request_id,kind) VALUES($1,$2,0,'placeholder',$3,'turn')", tenant, hearing, uuid4())
        assert await erase(conn, tenant_id=tenant, region='us-east') == {"deleted": True}
        assert await conn.fetchval('SELECT receipt FROM agent_zenas.command_receipts WHERE tenant_id=$1',tenant) is None
        assert await conn.fetchval('SELECT status FROM agent_zenas.command_receipts WHERE tenant_id=$1',tenant)==410
        for table in ('matters', 'pii', 'documents', 'reviews', 'worked_matters', 'training_pairs', 'memory_records'):
            assert await conn.fetchval(f'SELECT count(*) FROM agent_zenas.{table}') == 1
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.moot_hearings') == 0
        assert await conn.fetchval('SELECT home_region FROM agent_zenas.tenants WHERE tenant_id=$1', tenant) == 'us-east'
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.memory_index_jobs WHERE operation='delete'") == 1


@pytest.mark.asyncio
async def test_erasure_rolls_back_every_row_and_outbox_on_failure(database):
    await room(database)
    tenant, matter = UUID(TENANT), uuid4()
    async with database.acquire() as conn:
        await seed(conn, tenant, matter)
        await conn.execute("""CREATE FUNCTION fail_erasure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'offline rollback probe'; END $$"""
        )
        await conn.execute("CREATE TRIGGER erasure_probe BEFORE DELETE ON agent_zenas.pii FOR EACH ROW EXECUTE FUNCTION fail_erasure()")
        with pytest.raises(RaiseError, match='offline rollback probe'):
            await erase(conn, tenant_id=tenant, matter_id=matter, region='us-east')
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.reviews') == 1
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.training_pairs') == 1
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.memory_records') == 1
        assert await conn.fetchval('SELECT count(*) FROM agent_zenas.memory_index_jobs') == 0


@pytest.mark.asyncio
async def test_platform_identity_cannot_be_erased(database):
    async with database.acquire() as conn:
        with pytest.raises(ValueError, match='platform erasure refused'):
            await erase(conn, tenant_id=UUID(int=1), region='us-east')
