"""Tenant-only Tiger alert reads through the existing page relay. Code, $0."""

import json
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

KINDS = ('price', 'stock', 'new_product', 'below_cost', 'market_gap')


class AlertsArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str | None = Field(default=None, min_length=1, max_length=2000, pattern=r'^https?://[^\s]+$')
    kind: Literal['price', 'stock', 'new_product', 'below_cost', 'market_gap'] | None = None

    @model_validator(mode='after')
    def public_url(self):
        if self.url is not None:
            from command import UrlArgs
            UrlArgs(url=self.url)
        return self


def query_args(url: str | None = Query(default=None), kind: str | None = Query(default=None)) -> AlertsArgs:
    try:
        return AlertsArgs(url=url, kind=kind)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None


def build_router() -> APIRouter:
    from command import Actor, identity, require_tool
    router = APIRouter(prefix='/v1/command/tiger', tags=['tiger'])

    @router.get('/alerts')
    async def alerts(request: Request, args: Annotated[AlertsArgs, Depends(query_args)], actor: Annotated[Actor, Depends(identity)]):
        from main import tiger_alerts
        from trigger_routes import code_receipt
        require_tool(actor, 'tiger.alerts')
        # Reuse the page's relay, tenant check and response filtering. Caller
        # identity supplies the tenant; an arbitrary query cannot override it.
        query = [(key, value) for key, value in request.query_params.multi_items() if key == 'tenant_id']
        query.extend([('tenant_id', str(actor.tenant_id)), *args.model_dump(exclude_none=True).items()])
        scoped = Request({**request.scope, 'query_string': urlencode(query).encode()})
        response = await tiger_alerts(scoped)
        if response.status_code != 200:
            return response
        rows = json.loads(response.body)['alerts']
        rows = [{key: row[key] for key in ('id', 'url', 'ts', 'kind', 'before', 'after')} for row in rows]
        return {'alerts': rows, 'counts': {kind: sum(row['kind'] == kind for row in rows) for kind in KINDS},
                'ledger_id': str(await code_receipt(actor.tenant_id, 'tiger.alerts', agent='tiger')), 'cost_usd': 0}

    return router
