"""Observe the running Tank collector after a real restart; never invoke a fake writer."""
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import asyncpg


async def main():
    proof = Path(os.environ.get('RIG_PROOF_DIR','/proof'))
    tenants = json.loads((proof/'fixture.json').read_text())
    result = json.loads((proof/'browser-result.json').read_text())
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        for _ in range(30):
            rows = await conn.fetch("SELECT tenant_id,source,source_ref,input,output,public,client_matter FROM tank.training_pairs WHERE tenant_id=ANY($1::uuid[]) AND source IN ('rig_no_result','rig_quote')",[UUID(v) for v in tenants.values()])
            a=[r for r in rows if str(r['tenant_id'])==tenants['store_a']]
            b=[r for r in rows if str(r['tenant_id'])==tenants['store_b']]
            if len([r for r in a if r['source']=='rig_quote'])==2 and len([r for r in a if r['source']=='rig_no_result'])>=2 and len([r for r in b if r['source']=='rig_no_result'])>=1:
                break
            await asyncio.sleep(1)
        else:
            raise AssertionError('Running Tank did not collect every fixture event')
        assert {r['source_ref'] for r in a if r['source']=='rig_quote'}=={result['quote'],result['retry_quote']}
        assert all(not r['public'] and r['client_matter'] for r in rows)
        assert not any(r['source']=='rig_quote' for r in b)
        assert 'shopper@rig.test' not in json.dumps([dict(r) for r in rows],default=str)
        pending=await conn.fetchval('SELECT count(*) FROM agent_rig.training_events WHERE tenant_id=ANY($1::uuid[])',[UUID(v) for v in tenants.values()])
        assert pending==0
        print('PASS real Tank restart collected per-tenant save_pair rows: A 2 quotes + repeated misses; B only its own misses; no contacts or public export; durable queue drained')
    finally:
        await conn.close()


if __name__=='__main__':
    asyncio.run(main())
