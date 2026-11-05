"""Reconcile local law dependencies without network calls or model work."""
import json


async def lock_sources(conn, sources):
    if not sources or not await conn.fetchval("SELECT to_regclass('zenas_pool.documents') IS NOT NULL"):
        return
    await conn.fetch("""SELECT d.version_id FROM zenas_pool.documents d
        JOIN jsonb_to_recordset($1::jsonb) AS ref(doc_id text, version_id text)
          ON ref.doc_id=d.document_id AND (NULLIF(ref.version_id,'') IS NULL OR ref.version_id=d.version_id)
        ORDER BY d.version_id FOR SHARE OF d""", json.dumps(sources))


async def reconcile(conn, region, card_id=None, *, tenant_id=None):
    if not await conn.fetchval("SELECT to_regclass('zenas_pool.documents') IS NOT NULL"):
        return 0
    result = await conn.execute("""UPDATE agent_zenas.rule_cards c SET status='stale', active=false
        WHERE c.region=$1 AND c.status<>'stale' AND ($2::uuid IS NULL OR c.id=$2)
          AND ($3::uuid IS NULL OR c.tenant_id IS NULL OR c.tenant_id=$3)
          AND EXISTS (SELECT 1 FROM jsonb_array_elements(c.sources) ref
            JOIN zenas_pool.documents d ON d.document_id=ref->>'doc_id'
              AND (NULLIF(ref->>'version_id','') IS NULL OR d.version_id=ref->>'version_id')
            JOIN zenas_pool.sources s ON s.source_id=d.source_id
            WHERE s.region=$1 AND (d.effective_to IS NOT NULL OR d.status='superseded' OR NOT s.enabled))""", region, card_id, tenant_id)
    return int(result.split()[-1])
