"""Same-box Trigger adapter; identity and squad grants come from Command."""
from __future__ import annotations

import json
import hmac
import os
import re
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

import httpx
from command import Actor, authorized_actor, identity, require_tool
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1/trigger", tags=["trigger"])


async def code_receipt(tenant_id: UUID, task: str, *, task_id: UUID | None = None, agent: str = "trigger", conn=None) -> UUID:
    from main import box_region, pool
    return await (conn if conn is not None else pool()).fetchval(
        "INSERT INTO tank.ledger(tenant_id,region,agent,task_type,rung,model,cost_usd,"
        "tokens_in,tokens_out,task_id) "
        "VALUES($1,$2,$5,$3,3,'code',0,0,0,$4) RETURNING id",
        tenant_id, box_region(), task, task_id, agent)


async def code_plan(actor: Actor, messages: list[dict], task_id: UUID | None):
    """Explicit Trigger check commands need no model to select their one tool.

    Other conversation turns keep the existing planner. Confirmation semantics
    are unchanged: this only proposes a card, and never runs the checker.
    """
    last = messages[-1] if messages else {}
    if last.get("role") != "user":
        return None
    from trigger_commands import plan
    source_text = last.get('content', '')
    if task_id and re.match(r'\s*trigger\.write\.batch\b', source_text, re.I):
        # Attachment contents are data, never part of the command syntax.
        from command import db, unpack
        async with db().acquire() as conn:
            raw = await conn.fetchval("SELECT state FROM tank.command_threads WHERE tenant_id=$1 AND user_id=$2 AND state->>'active_turn'=$3",
                                      actor.tenant_id, actor.user_id, str(task_id))
        messages = unpack(raw or {}).get('messages', [])
        source_text = next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'), source_text)
    from trigger_marketing_api import plan as marketing_plan
    proposed = await marketing_plan(actor, source_text)
    if proposed is None:
        proposed = await plan(actor, source_text, task_id)
    if proposed is not None:
        return SimpleNamespace(content=json.dumps(proposed, ensure_ascii=False), ledger_id=await code_receipt(actor.tenant_id, "command.turn", task_id=task_id))
    prefix = r"\s*trigger(?:\.check|[, ]+check)(?:\s+(?:this\s+)?paragraph)?\s*:\s*"
    match = re.fullmatch(prefix + r"(.+)", last.get("content", ""), re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    actor = await authorized_actor(actor)
    require_tool(actor, "trigger.check")
    text = match[1]
    if task_id and text.startswith("Attachment "):
        # The existing composer accepts text files longer than its message box.
        # Read the owned turn to remove only its real attachment transport label.
        from command import db, unpack
        async with db().acquire() as conn:
            raw = await conn.fetchval("SELECT state FROM tank.command_threads WHERE tenant_id=$1 AND user_id=$2 AND state->>'active_turn'=$3",
                                      actor.tenant_id, actor.user_id, str(task_id))
        source = unpack(raw or {}).get("messages", [])
        source = source[-1] if source else {}
        attached = source.get("attachments", [])
        if (re.fullmatch(prefix, source.get("content", ""), re.IGNORECASE | re.DOTALL)
                and len(attached) == 1 and isinstance(attached[0].get("text"), str)):
            text = attached[0]["text"]
    if len(text) > 32000:
        raise HTTPException(422, "Text exceeds the checker limit")
    content = json.dumps({"text": "trigger.check", "actions": [
        {"tool": "trigger.check", "arguments": {"text": text}}]}, ensure_ascii=False)
    return SimpleNamespace(content=content, ledger_id=await code_receipt(actor.tenant_id, "command.turn", task_id=task_id))


async def room(operation: str, body: dict, *, actor=None):
    key = os.environ.get("TANK_KEY_TRIGGER", "")
    if not key:
        raise HTTPException(503, "Trigger is not configured")
    try:
        from product_identity import actor_scope, request_hook
        with actor_scope(actor):
            async with httpx.AsyncClient(timeout=120, follow_redirects=False, trust_env=False, event_hooks={"request": [request_hook]}) as client:
                response = await client.post("http://agent-trigger:8000/" + operation,
                                             json=body, headers={"X-Tank-Key": key})
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Trigger is unavailable") from None
    if response.status_code >= 400:
        # Do not expose room exceptions, prompts or credentials.
        raise HTTPException(response.status_code if response.status_code in {409,422,503} else 502,
                            "Trigger could not complete this request")
    if not isinstance(data, dict):
        raise HTTPException(502, "Invalid Trigger response")
    return data


async def zenas_draft(body: dict, actor: Actor):
    from main import TANK_API_KEY, app, box_region
    from zenas_commands import draft_payload
    require_tool(actor, "zenas.ask")
    if not body.get("template") or not body.get("jurisdiction"):
        raise HTTPException(422, "Zenas drafting requires a template and jurisdiction")
    payload = draft_payload(tenant_id=str(actor.tenant_id), home_region=box_region(),
                            template=body["template"], jurisdiction=body["jurisdiction"],
                            terms=body.get("terms", {}), text=body["text"],
                            matter_id=body.get("matter_id"), include_empty_matter=True)
    # Zenas's own scope and tenant checks remain authoritative, including criminal refusal.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tank", trust_env=False) as client:
        response = await client.post("/v1/zenas/drafts", json=payload, headers={"X-Tank-Key": TANK_API_KEY})
    data = response.json()
    if isinstance(data, dict):
        data.setdefault("notice", "legal information, not legal advice")
        # Existing chat result projection displays text/summary without a new page.
        data.setdefault("summary", data["notice"])
    return JSONResponse(data, status_code=response.status_code)


@router.get("/copy/{write_id}")
async def accepted_copy(write_id: UUID, tenant_id: UUID, request: Request):
    """Tisan can read only confirmed, currently accepted copy in its job tenant."""
    from main import box_region, pool
    expected = os.environ.get("TANK_KEY_TISAN", "")
    supplied = request.headers.get("x-tank-key", "")
    if not expected or not supplied or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(401, "Room authentication required")
    if request.headers.get("x-tervory-tenant") != str(tenant_id):
        raise HTTPException(403, "Tenant mismatch")
    if request.headers.get("x-tervory-home-region") != box_region():
        raise HTTPException(403, "This conversation must stay on its home box")
    async with pool().acquire() as conn:
        row = await conn.fetchrow("""SELECT w.draft FROM tank.trigger_writes w
            WHERE w.id=$1 AND w.tenant_id=$2 AND w.feedback_status='accepted'
            AND EXISTS (SELECT 1 FROM tank.command_threads t
                CROSS JOIN LATERAL jsonb_each(t.state->'cards') c
                CROSS JOIN LATERAL jsonb_array_elements(CASE
                    WHEN c.value->>'tool'='trigger.write.batch' THEN c.value->'result'->'items'
                    ELSE jsonb_build_array(c.value->'result') END) item
                WHERE t.tenant_id=w.tenant_id AND c.value->>'tool' IN ('trigger.write','trigger.revise','trigger.write.batch')
                AND c.value->>'status'='confirmed'
                AND item->>'write_id'=w.id::text)""", write_id, tenant_id)
    if row is None:
        raise HTTPException(404, "Writing record unavailable")
    return JSONResponse({"write_id": str(write_id), "tenant_id": str(tenant_id), "text": row["draft"]},
                        headers={"Cache-Control": "private, no-store"})


@router.get('/scorecard')
async def get_scorecard(tenant_id: UUID, actor: Annotated[Actor, Depends(identity)]):
    require_tool(actor, 'trigger.scorecard')
    if tenant_id != actor.tenant_id:
        raise HTTPException(403, 'Tenant mismatch')
    from trigger_learning import scorecard
    data = await scorecard(actor)
    return {**data, 'text': json.dumps(data, ensure_ascii=False, indent=2), 'model_calls': 0,
            'ledger_id': str(await code_receipt(actor.tenant_id, 'trigger.scorecard')), 'cost_usd': 0}


async def draft_and_record(actor, body, parent=None):
    from trigger_learning import record
    data = await room('write', {**body, 'tenant_id': str(actor.tenant_id)}, actor=actor)
    if data.get('delegate') == 'zenas.draft':
        response = await zenas_draft(body, actor)
        if response.status_code >= 400:
            return response
        data = json.loads(response.body)
    return await record(actor, body['text'], data, context=body, parent_write_id=parent)


@router.post("/{operation}")
async def invoke(operation: str, request: Request, actor: Annotated[Actor, Depends(identity)]):
    from command import TriggerCheckArgs, TriggerWriteArgs, TriggerVoiceShowArgs, TriggerVoiceSetArgs, TriggerVoiceRollbackArgs, TriggerFeedbackArgs, TriggerReviseArgs, TriggerVoiceLearnArgs, TriggerBatchArgs
    schemas = {"revise": TriggerReviseArgs, "voice.learn": TriggerVoiceLearnArgs, "write.batch": TriggerBatchArgs, "write": TriggerWriteArgs, "check": TriggerCheckArgs, "voice.show": TriggerVoiceShowArgs,
               "voice.set": TriggerVoiceSetArgs, "voice.rollback": TriggerVoiceRollbackArgs, "feedback": TriggerFeedbackArgs}
    if operation not in schemas:
        raise HTTPException(404, "No such Trigger action")
    require_tool(actor, "trigger." + operation)
    raw = await request.body()
    if len(raw) > 60000:
        raise HTTPException(413, "Request too large")
    try:
        body = json.loads(raw)
        tenant = body.pop("tenant_id", str(actor.tenant_id))
        if tenant != str(actor.tenant_id):
            raise HTTPException(403, "Tenant mismatch")
        body = schemas[operation].model_validate(body).model_dump(mode="json", exclude_none=True)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid Trigger request") from None
    if operation.startswith("voice.") or operation == "feedback":
        from trigger_commands import confirmation
        if operation == "voice.show":
            data = await room("voice/show", {**body, "tenant_id": str(actor.tenant_id)}, actor=actor)
        else:
            operation_id = confirmation(actor, "trigger." + operation, body)
            if operation == 'voice.learn':
                data = await room('voice/commit', {'tenant_id': str(actor.tenant_id),
                    'rules': body['rules'], 'expected_revision': body['expected_revision'],
                    'preview_hash': body['preview_hash'], 'operation_id': str(operation_id)}, actor=actor)
            elif operation == "feedback":
                from trigger_learning import feedback
                data = await feedback(actor, UUID(body["write_id"]), body["status"], body["changes"], operation_id)
            else:
                data = await room("voice/commit", {k: v for k, v in {**body, "tenant_id": str(actor.tenant_id), "operation_id": str(operation_id)}.items() if k != "brief_markdown"}, actor=actor)
        return {**data, "text": json.dumps(data, ensure_ascii=False, indent=2), "model_calls": 0,
                "ledger_id": str(await code_receipt(actor.tenant_id, "trigger." + operation)), "cost_usd": 0}
    from trigger_commands import confirmation
    confirmation(actor, "trigger." + operation, body)
    from trigger_learning import guidance, revision
    if operation == 'write':
        context = {**body, **await guidance(actor, body['text'], body.get('format'))}
        return await draft_and_record(actor, context)
    if operation == 'revise':
        context, parent = await revision(actor, UUID(body['write_id']))
        return await draft_and_record(actor, context, parent)
    if operation == 'write.batch':
        from trigger_batch import item_body
        if not 1 <= len(body['products']) <= 25:
            raise HTTPException(422, 'Invalid Trigger request')
        items = []
        for product in body['products']:
            context = item_body(product)
            context.update(await guidance(actor, json.dumps(product, ensure_ascii=False), context['format']))
            result = await draft_and_record(actor, context)
            if not isinstance(result, dict):
                return result
            items.append(result)
        return {'items': items, 'text': '\n\n'.join(i['text'] for i in items),
                'item_count': len(items), 'cost_usd': (None if any(i.get('cost_usd') is None for i in items)
                    else sum(float(i['cost_usd']) for i in items)),
                'ledger_ids': [i.get('ledger_id') for i in items]}
    data = await room(operation, {**body, 'tenant_id': str(actor.tenant_id)}, actor=actor)
    if operation == "check":
        data["ledger_id"] = str(await code_receipt(actor.tenant_id, "trigger.check"))
        data["cost_usd"] = 0
        data["model_calls"] = 0
    return data
