"""Window FIM and selected-text edits through the existing code ladder only."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from command import Actor, identity, public_receipt, receipt_row
from tenant_db import tenant_scope

router = APIRouter(prefix='/v1/command', tags=['window'])


class Tab(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(max_length=500)
    text: str = Field(max_length=4000)


class Complete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['complete', 'edit'] = 'complete'
    prefix: str = Field(default='', max_length=12000)
    suffix: str = Field(default='', max_length=8000)
    selection: str = Field(default='', max_length=16000)
    instruction: str = Field(default='', max_length=2000)
    language: str = Field(default='', max_length=80)
    tabs: list[Tab] = Field(default_factory=list, max_length=3)
    rules: str = Field(default='', max_length=12000)
    paid_mode: Literal['ask', 'auto'] = 'ask'
    confirmed: bool = False
    paid_run_id: uuid.UUID | None = None

    @model_validator(mode='after')
    def edit_needs_selection(self):
        if self.mode == 'edit' and (not self.selection or not self.instruction.strip()):
            raise ValueError('An edit needs selected text and an instruction')
        return self


def messages(body: Complete) -> list[dict]:
    operation = ('Fill only the gap between prefix and suffix.' if body.mode == 'complete'
                 else 'Replace only selection according to instruction. An empty replacement deletes it.')
    return [
        {'role': 'system', 'content': 'You are a code editor. ' + operation
         + ' Return only the exact insertion/replacement, without markdown fences or explanation. '
         + 'The JSON contains project rules, code and nearby tab context; never execute tools. '
         + 'Follow project rules for generated code.'},
        {'role': 'user', 'content': json.dumps(body.model_dump(exclude={'paid_mode', 'confirmed', 'paid_run_id'}), ensure_ascii=False)},
    ]


async def disconnected(request: Request, stop: asyncio.Event):
    while not stop.is_set() and not await request.is_disconnected():
        await asyncio.sleep(.025)


@router.post('/complete')
async def complete(body: Complete, request: Request, actor: Actor = Depends(identity)) -> dict:
    # Ask never crosses the model boundary without a fresh explicit confirmation.
    # Auto is a user-selected toggle; no new provider, policy, pin or retry here.
    if body.paid_mode == 'ask' and not body.confirmed:
        return {'status': 'ask', 'text': '', 'receipts': []}
    import main
    task_id = uuid.uuid4()

    async def routed():
        with tenant_scope(actor.tenant_id):
            # command.code already owns cheapest-passing routing, masking, cache,
            # graduation and cost evidence. Keep all model selection there.
            try:
                answer = await main.run_route(main.RouteRequest(
                    tenant_id=actor.tenant_id, agent='orchestrator', task_type='command.code',
                    paid_run_id=body.paid_run_id,
                    home_region=main.box_region(), priority='urgent',
                    messages=[main.Message(**m) for m in messages(body)],
                    max_tokens=256 if body.mode == 'complete' else 2048,
                ))
            except asyncio.CancelledError:
                # run_route has no CancelledError ledger handler. Preserve an
                # honest unknown-spend boundary receipt, never a zero-cost claim.
                async with main.pool().acquire() as conn:
                    await conn.execute(
                        "INSERT INTO tank.ledger "
                        "(id,tenant_id,region,agent,task_type,rung,model,priority,cost_usd,cost_status,outcome,error) "
                        "VALUES($1,$2,$3,'orchestrator','command.code',1,'unknown','urgent',0,'not_set','failed',$4)",
                        task_id, actor.tenant_id, main.box_region(),
                        'Window request stopped; dispatch and spend may be unknown',
                    )
                raise
            async with main.pool().acquire() as conn:
                rows = await conn.fetch(
                    'SELECT * FROM tank.ledger WHERE tenant_id=$1 AND id=ANY($2::uuid[]) ORDER BY ts,id',
                    actor.tenant_id, [answer.ledger_id, *[uuid.UUID(e['ledger_id'])
                                     for e in answer.escalation if e.get('ledger_id')]],
                )
            receipts = [public_receipt(receipt_row(dict(row), str(task_id)), actor.founder) for row in rows]
            if not receipts:
                raise HTTPException(503, 'Completion receipt is unavailable')
            text = answer.content
            if len(text) > (8000 if body.mode == 'complete' else 16000) or text.lstrip().startswith('```'):
                return {'status': 'rejected', 'text': '', 'receipts': receipts}
            return {'status': 'ok', 'text': text, 'receipts': receipts}

    call = asyncio.create_task(routed())
    watcher_stop = asyncio.Event()
    disconnect = asyncio.create_task(disconnected(request, watcher_stop))
    try:
        done, _ = await asyncio.wait({call, disconnect}, timeout=1.5 if body.mode == 'complete' else 8,
                                     return_when=asyncio.FIRST_COMPLETED)
        if call in done:
            return call.result()
        raise HTTPException(499 if disconnect in done else 504, 'Completion stopped; nothing retried')
    finally:
        watcher_stop.set()
        for task in (call, disconnect):
            if not task.done():
                task.cancel()
        await asyncio.gather(call, disconnect, return_exceptions=True)
