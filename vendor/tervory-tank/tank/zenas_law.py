"""Tank's same-region, local-only legal embedding route."""
import os
from uuid import UUID
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from tenant_db import tenant_scope

MODEL='BAAI/bge-small-en-v1.5@aa8f8b060edb00e03bfdd08813a2949946c8ba55'

class Embeddings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    home_region: str | None = None
    tenant_id: UUID
    jurisdiction: str
    model: str
    texts: list[str] = Field(min_length=1,max_length=512)

async def ledger(tenant):
    from trigger_routes import code_receipt
    return await code_receipt(tenant,'zenas.embeddings',agent='zenas')

def build_router(tank):
    router=APIRouter(prefix='/v1/zenas/law')
    @router.post('/embeddings')
    @router.post('/vectors')
    async def embeddings(body: Embeddings, caller=Depends(tank.require_key)):
        if caller!='zenas':raise HTTPException(403,'Zenas room identity required')
        region=tank.box_region()
        if region not in {'us-east','za'} or body.home_region!=region or body.model!=MODEL or body.jurisdiction!=('za' if region=='za' else 'us'):
            raise HTTPException(403,'wrong_region')
        if sum(len(t) for t in body.texts)>1_000_000:raise HTTPException(413,'input_too_large')
        with tenant_scope(body.tenant_id):
            try:
                async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=120,
                                             transport=getattr(tank,'law_transport',None)) as client:
                    response=await client.post('http://hermes:8000/law/embeddings',
                        headers={'X-Tank-Key':os.environ.get('TANK_KEY_ZENAS','')},
                        json=body.model_dump(mode='json',exclude={'tenant_id','home_region'}))
                response.raise_for_status()
                data=response.json()
            except (httpx.HTTPError,ValueError):
                raise HTTPException(503,'meaning search unavailable') from None
            receipt=await ledger(body.tenant_id)
            return {**data,'ledger_id':str(receipt),'cost_usd':0,'rung':2}
    return router


def install(tank):
    """Specific internal law route precedes the existing product catch-all."""
    routes = tank.app.router.routes
    index = next(i for i,r in enumerate(routes) if getattr(r,"path","") == "/v1/zenas/{path:path}")
    routes[index:index] = build_router(tank).routes
