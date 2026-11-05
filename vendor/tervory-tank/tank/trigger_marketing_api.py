"""M1 through existing Command cards. No page, widget or new visible wording."""
from __future__ import annotations

import json
import re
from typing import Annotated
from uuid import UUID

import command
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from trigger_google import GoogleFailure
from trigger_marketing import Marketing, obj, public
from trigger_marketing_contract import TOOLS, Args
from trigger_publisher import Publisher

router = APIRouter()


def service():
    from main import pool
    return Marketing(pool())


async def authority(actor, tool, conn=None):
    actor = await command.authorized_actor(actor, conn)
    if not (actor.founder or actor.tenant_admin):
        raise HTTPException(403, 'This tool is not available to this user')
    command.require_tool(actor, tool)
    return actor


async def plan(actor, text):
    match = re.fullmatch(r'\s*trigger\.((?:post|gbp|meta)\.[a-z.]+|posts|calendar)\s*(?::\s*(.*))?\s*', text, re.DOTALL)
    if not match or match[1] not in TOOLS:
        return None
    await authority(actor, 'trigger.' + match[1])
    try:
        args = json.loads(match[2] or '{}')
        action = command.validate_action(command.PlannedAction(tool='trigger.'+match[1], arguments=args), actor)
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid Trigger request') from None
    return {'text': '', 'actions': [action.model_dump(mode='json')]}


def preview(row):
    data = public(row)
    report = data.get('report', {})
    data['report'] = {k: report[k] for k in ('complete', 'length', 'issue_count') if k in report}
    return json.dumps(data, ensure_ascii=False, default=str, indent=2)


async def prepare(action, actor, conn):
    await authority(actor, action.tool, conn)
    op = action.tool.removeprefix('trigger.')
    values = dict(action.arguments)
    store = service()
    # Reuse the Command editing transaction. A second pool acquisition for a
    # preview can deadlock a busy pool while all cards hold their thread locks.
    if op.startswith('post.') and op != 'post.create':
        row = await store.post(actor.tenant_id, UUID(values['post_id']), conn=conn)
        values.update(expected_revision=row['revision'], brief_markdown=preview(row))
        if op == 'post.approve' and row['channel'] in {'gbp', 'facebook'}:
            if row['channel'] == 'facebook':
                binding = await conn.fetchrow("SELECT generation,page_id AS location FROM tank.trigger_meta_connections WHERE tenant_id=$1 AND status='connected'", actor.tenant_id)
            else:
                binding = await conn.fetchrow("SELECT generation,location FROM tank.trigger_gbp_connections WHERE tenant_id=$1 AND status='connected'", actor.tenant_id)
            if not binding or not binding['location']:
                raise HTTPException(409)
            values.update(connection_generation=str(binding['generation']), location=binding['location'])
            values['brief_markdown'] = preview({**dict(row), 'location': binding['location'], 'connection_generation': binding['generation']})
    elif op in {'gbp.reply.send', 'gbp.reply.edit'}:
        row = await conn.fetchrow('SELECT * FROM tank.trigger_gbp_replies WHERE tenant_id=$1 AND id=$2', actor.tenant_id, UUID(values['reply_id']))
        if not row:
            raise HTTPException(404)
        values.update(expected_revision=row['revision'], brief_markdown=json.dumps({
            'location': row['location'], 'review_id': row['review_id'], 'text': row['text'],
            'report': obj(row['report'])}, ensure_ascii=False, default=str))
    elif op in {'gbp.disconnect', 'gbp.location.select'}:
        row = await conn.fetchrow('SELECT generation,location,location_title FROM tank.trigger_gbp_connections WHERE tenant_id=$1', actor.tenant_id)
        if not row:
            raise HTTPException(409)
        values.update(generation=str(row['generation']), brief_markdown=json.dumps({**dict(row), **({'location': values['location']} if op == 'gbp.location.select' else {})}, default=str))
    elif op in {'meta.disconnect', 'meta.page.select'}:
        row = await conn.fetchrow('SELECT generation,page_id,page_name FROM tank.trigger_meta_connections WHERE tenant_id=$1', actor.tenant_id)
        if not row:
            raise HTTPException(409)
        selection = dict(row)
        if op == 'meta.page.select':
            choices = await store.meta._accounts(actor.tenant_id, conn=conn)
            selected = next((page for page in choices if page['page_id'] == values['page_id']), None)
            if selected is None:
                raise HTTPException(404)
            selection.update(selected)
        values.update(generation=str(row['generation']), brief_markdown=json.dumps(selection, default=str))
    return command.validate_action(command.PlannedAction(tool=action.tool, arguments=values), actor)


async def execute(op, args, actor, operation):
    store = service()
    tenant = actor.tenant_id
    if op == 'post.create':
        return await store.create(actor, args, operation)
    if op.startswith('post.'):
        return await store.transition(tenant, op, args, operation)
    if op == 'posts':
        return await store.posts(tenant, args)
    if op == 'calendar':
        return await store.calendar(tenant, args)
    if op == 'meta.connect':
        return await store.meta.connect(actor)
    if op == 'meta.pages':
        return {'items': await store.meta.pages(tenant), 'cost_usd': 0, 'model_calls': 0}
    if op == 'meta.page.select':
        return await store.meta.select(tenant, args.page_id, args.generation)
    if op == 'meta.disconnect':
        return await store.meta.disconnect(tenant, args.generation)
    if op == 'gbp.connect':
        return await store.google.connect(actor)
    if op == 'gbp.disconnect':
        return await store.google.disconnect(tenant, args.generation)
    if op == 'gbp.locations':
        return {'locations': await store.google.locations(tenant)}
    if op == 'gbp.location.select':
        choices = await store.google.locations(tenant)
        selected = next((row for row in choices if row['location'] == args.location), None)
        if selected is None:
            raise HTTPException(404)
        changed = await store.pool.fetchval("""UPDATE tank.trigger_gbp_connections SET location=$3,location_title=$4,
            reviews_checked_at=NULL,updated_at=now() WHERE tenant_id=$1 AND generation=$2 AND status='connected' RETURNING tenant_id""",
            tenant, args.generation, args.location, selected['title'])
        if not changed:
            raise HTTPException(409)
        return selected
    if op == 'gbp.reviews':
        return await store.reviews(tenant)
    if op == 'gbp.reply.draft':
        return await store.reply_draft(actor, args.review_id)
    if op == 'gbp.reply.send':
        return await Publisher(store).reply(tenant, args)
    if op == 'gbp.reply.edit':
        report = await store.check(tenant, args.text, None)
        row = await store.pool.fetchrow("""UPDATE tank.trigger_gbp_replies SET text=$3,report=$4::jsonb,
            revision=revision+1,state='draft' WHERE tenant_id=$1 AND id=$2 AND revision=$5 AND state<>'sending'
            RETURNING id,text,report,revision,state""", tenant, args.reply_id, args.text, json.dumps(report), args.expected_revision)
        if not row:
            raise HTTPException(409)
        return {**dict(row), 'report': obj(row['report'])}
    raise HTTPException(404)


async def body(request, cap=32000):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>cap:
            raise HTTPException(413)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid Trigger request') from None


@router.post('/v1/trigger/marketing/{op}')
async def invoke(op: str, request: Request, actor: Annotated[command.Actor, Depends(command.identity)]):
    if op not in TOOLS:
        raise HTTPException(404)
    actor = await authority(actor, 'trigger.'+op)
    data = await body(request)
    if data.pop('tenant_id', str(actor.tenant_id)) != str(actor.tenant_id):
        raise HTTPException(403, 'Tenant mismatch')
    try:
        args = TOOLS[op].model_validate(data)
    except ValueError:
        raise HTTPException(422, 'Invalid Trigger request') from None
    from trigger_commands import confirmation
    operation = confirmation(actor, 'trigger.'+op, args.model_dump(mode='json', exclude_none=True))
    try:
        result = await execute(op, args, actor, operation)
    except GoogleFailure as error:
        result = {'state': 'failed', 'platform_error': error.message}
    if not result.get('ledger_id'):
        from trigger_routes import code_receipt
        result['ledger_id'] = str(await code_receipt(actor.tenant_id, 'trigger.'+op))
    # Existing cards render their existing text/report/receipt fields. No model
    # or provider name, sealed connection or OAuth token is projected here.
    result.setdefault('text', json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return result


class Callback(Args):
    state: str = Field(min_length=1, max_length=500)
    code: str | None = Field(default=None, max_length=8000)
    error: str | None = Field(default=None, max_length=200)


@router.post('/v1/command/trigger-gbp/oauth/callback')
async def callback(request: Request, actor: Annotated[command.Actor, Depends(command.identity)]):
    actor = await authority(actor, 'trigger.gbp.connect')
    try:
        data = Callback.model_validate(await body(request, 10000))
        result = await service().google.callback(actor, data.state, data.code, data.error)
    except GoogleFailure:
        raise HTTPException(409) from None
    except ValueError:
        raise HTTPException(422) from None
    from trigger_routes import code_receipt
    await code_receipt(actor.tenant_id, 'trigger.gbp.connect')
    return result


@router.post('/v1/command/trigger-meta/oauth/callback')
async def meta_callback(request: Request, actor: Annotated[command.Actor, Depends(command.identity)]):
    actor = await authority(actor, 'trigger.meta.connect')
    try:
        data = Callback.model_validate(await body(request, 10000))
        result = await service().meta.callback(actor, data.state, data.code, data.error)
    except GoogleFailure:
        raise HTTPException(409) from None
    except ValueError:
        raise HTTPException(422) from None
    from trigger_routes import code_receipt
    await code_receipt(actor.tenant_id, 'trigger.meta.connect')
    return result
