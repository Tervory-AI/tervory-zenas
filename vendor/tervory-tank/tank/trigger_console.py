"""Trigger page reads and explicit proposals. Reuses C1/M1/B1 and Command Confirm."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated
from uuid import UUID, NAMESPACE_URL, uuid5
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import command
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from trigger_console_contract import ConversationAction, Settings
from trigger_routes import code_receipt

router = APIRouter(prefix='/v1/command/trigger/console')
MUTATIONS = {'trigger.crm.write', 'trigger.post.create', 'trigger.post.edit', 'trigger.post.approve',
             'trigger.post.schedule', 'trigger.post.cancel', 'trigger.post.retry', 'trigger.gbp.connect',
             'trigger.gbp.disconnect', 'trigger.gbp.location.select', 'trigger.meta.connect',
             'trigger.meta.disconnect', 'trigger.meta.page.select', 'trigger.gbp.reply.draft',
             'trigger.gbp.reply.edit', 'trigger.gbp.reply.send', 'trigger.bot.settings',
             'trigger.bot.handover', 'trigger.bot.reply', 'trigger.bot.lead.confirm',
             'trigger.bot.quote.confirm', 'trigger.bot.source.add', 'trigger.bot.faq.add'}


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def bot():
    from trigger_bot_api import make_bot
    return make_bot()


async def authority(actor, tool):
    actor = await command.authorized_actor(actor)
    if not (actor.founder or actor.tenant_admin):
        raise HTTPException(403)
    command.require_tool(actor, tool)
    return actor


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str = Field(max_length=100)
    arguments: dict = Field(default_factory=dict)


@router.post('/propose')
async def propose(body: Proposal, actor: Annotated[command.Actor, Depends(command.identity)]):
    if body.tool not in MUTATIONS or len(json.dumps(body.arguments)) > 25000:
        raise HTTPException(422)
    actor = await authority(actor, body.tool)
    if body.tool == 'trigger.post.schedule' and 'local_date' in body.arguments:
        body.arguments = local_schedule(body.arguments)
    action = command.validate_action(command.PlannedAction(tool=body.tool, arguments=body.arguments), actor)
    thread = await command.create_thread(command.NewThread(title='Trigger'), actor)
    async with command.editing(UUID(str(thread['thread_id'])), actor) as (conn, row, state):
        card = await command.propose_action(conn, row, state, action, actor)
    return {'thread_id': thread['thread_id'], 'card': command.public_card(card), 'preview': card_preview(card)}


@router.get('/crm/{resource}')
async def crm_list(resource: str, request: Request, actor: Annotated[command.Actor, Depends(command.identity)]):
    from trigger_crm import PATHS, listing
    if resource not in PATHS:
        raise HTTPException(404)
    return await listing(resource, request, actor)


@router.get('/crm/{resource}/{record_id}')
async def crm_detail(resource: str, record_id: UUID, actor: Annotated[command.Actor, Depends(command.identity)]):
    from trigger_crm import PATHS, detail
    if resource not in PATHS:
        raise HTTPException(404)
    return await detail(resource, record_id, actor)


@router.get('/marketing/{area}')
async def marketing(area: str, actor: Annotated[command.Actor, Depends(command.identity)], record_id: UUID | None = None):
    from trigger_marketing import Marketing, public
    from trigger_marketing_contract import Posts
    actor = await authority(actor, 'trigger.posts')
    pool, tenant = bot().pool, actor.tenant_id
    store = Marketing(pool)
    if area in {'posts', 'calendar', 'composer'}:
        result = await store.posts(tenant, Posts())
        if record_id:
            result['post'] = public(await store.post(tenant, record_id))
            result['post']['events'] = [dict(r) for r in await pool.fetch('''SELECT state,revision,created_at FROM tank.trigger_post_events
                WHERE tenant_id=$1 AND post_id=$2 ORDER BY created_at,id''', tenant, record_id)]
        result['voice'] = await bot().voice(tenant)
    elif area == 'reviews':
        await authority(actor, 'trigger.gbp.reviews')
        connected = await pool.fetchval('''SELECT 1 FROM tank.trigger_gbp_connections
            WHERE tenant_id=$1 AND status='connected' AND location IS NOT NULL''', tenant)
        if connected:
            from trigger_google import GoogleFailure
            try:
                await store.reviews(tenant)
            except GoogleFailure as error:
                raise HTTPException(502, error.message) from None
        reviews = await pool.fetch('''SELECT location,review_id,rating,text,updated_at FROM tank.trigger_gbp_reviews
            WHERE tenant_id=$1 ORDER BY updated_at DESC LIMIT 100''', tenant)
        replies = await pool.fetch('''SELECT id,review_id,location,text,state,revision,report,ledger_id,platform_error FROM tank.trigger_gbp_replies
            WHERE tenant_id=$1 ORDER BY id LIMIT 100''', tenant)
        result = {'reviews': [{**dict(r), 'id': str(uuid5(NAMESPACE_URL, r['location']+'/'+r['review_id']))} for r in reviews],
                  'replies': [{**dict(r), 'report': obj(r['report'])} for r in replies], 'voice': await bot().voice(tenant)}
    elif area == 'channels':
        google = await pool.fetchrow('''SELECT status,location,location_title,generation FROM tank.trigger_gbp_connections
            WHERE tenant_id=$1''', tenant)
        meta = None
        if await pool.fetchval("SELECT to_regclass('tank.trigger_meta_connections')"):
            meta = await pool.fetchrow('''SELECT status,page_id,page_name,generation FROM tank.trigger_meta_connections
                WHERE tenant_id=$1''', tenant)
        result = {'gbp': dict(google) if google else None, 'facebook': dict(meta) if meta else None}
        if google and google['status'] == 'connected':
            from trigger_google import GoogleFailure
            try:
                result['gbp']['choices'] = await store.google.locations(tenant)
            except GoogleFailure:
                result['gbp']['choices'] = []
        if meta and meta['status'] == 'connected':
            from trigger_meta import Meta, MetaFailure
            try:
                result['facebook']['choices'] = await Meta(pool).pages(tenant)
            except MetaFailure:
                result['facebook']['choices'] = []
    else:
        raise HTTPException(404)
    result['ledger_id'] = str(await code_receipt(tenant, 'trigger.posts'))
    return result


@router.get('/bot/{area}')
async def bot_data(area: str, actor: Annotated[command.Actor, Depends(command.identity)], record_id: UUID | None = None):
    actor = await authority(actor, 'trigger.bot.sources')
    pool, tenant = bot().pool, actor.tenant_id
    if area == 'settings':
        row = await pool.fetchrow('SELECT enabled,settings FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant)
        sources = await pool.fetch('''SELECT id,kind,title,url,status FROM tank.trigger_bot_sources
            WHERE tenant_id=$1 AND status<>'removed' ORDER BY title,id LIMIT 500''', tenant)
        result = {'settings': {**(obj(row['settings']) if row else {}), 'enabled': bool(row and row['enabled'])},
                  'sources': [dict(r) for r in sources]}
    elif area == 'inbox':
        rows = await pool.fetch('''SELECT c.id,c.visitor_id,c.status,c.updated_at,
            (SELECT question FROM tank.trigger_bot_messages m WHERE m.tenant_id=$1 AND m.conversation_id=c.id
             ORDER BY created_at DESC LIMIT 1) AS last_message
            FROM tank.trigger_bot_conversations c WHERE c.tenant_id=$1 ORDER BY c.updated_at DESC LIMIT 100''', tenant)
        result = {'conversations': [dict(row) for row in rows]}
        if record_id:
            current = await pool.fetchrow('''SELECT id,visitor_id,status,updated_at FROM tank.trigger_bot_conversations
                WHERE tenant_id=$1 AND id=$2''', tenant, record_id)
            if not current:
                raise HTTPException(404)
            transcript = await pool.fetch('''SELECT id,question,answer,cited,outcome,person,created_at
                FROM tank.trigger_bot_messages WHERE tenant_id=$1 AND conversation_id=$2 ORDER BY created_at,id LIMIT 500''', tenant, record_id)
            result.update(conversation=dict(current), messages=[dict(r) for r in transcript])
            drafts = await pool.fetch('''SELECT id,kind,data,state FROM tank.trigger_bot_drafts
                WHERE tenant_id=$1 AND conversation_id=$2 AND kind IN ('lead','quote') ORDER BY created_at''', tenant, record_id)
            result['drafts'] = [dict(r) for r in drafts]
    else:
        raise HTTPException(404)
    result['ledger_id'] = str(await code_receipt(tenant, 'trigger.bot.sources'))
    return result


async def snapshot(pool, tenant, op, arguments):
    if op == 'settings':
        row = await pool.fetchrow('SELECT enabled,settings FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant)
    else:
        row = await pool.fetchrow('''SELECT id,status,updated_at FROM tank.trigger_bot_conversations
            WHERE tenant_id=$1 AND id=$2''', tenant, UUID(str(arguments['conversation_id'])))
        if row is None:
            raise HTTPException(404)
    return dict(row) if row else None


async def prepare(action, actor, conn):
    values = dict(action.arguments)
    current = await snapshot(conn, actor.tenant_id, action.tool.removeprefix('trigger.bot.'), values)
    values['expected_digest'] = digest(current)
    values['brief_markdown'] = json.dumps({k: v for k, v in values.items() if k not in {'brief_markdown', 'expected_digest'}}, default=str)
    return command.validate_action(command.PlannedAction(tool=action.tool, arguments=values), actor)


async def execute(op, args: Settings | ConversationAction, actor, operation_id):
    tenant, pool = actor.tenant_id, bot().pool
    async with pool.acquire() as conn, conn.transaction():
        # One tenant lock protects stale previews and concurrent settings/conversation mutations.
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', 'trigger:console:' + str(tenant))
        current = await snapshot(conn, tenant, op, args.model_dump())
        if digest(current) != args.expected_digest:
            raise HTTPException(409)
        if op == 'settings':
            settings = args.model_dump(exclude={'expected_digest', 'brief_markdown', 'enabled'})
            await conn.execute('''INSERT INTO tank.trigger_bot_config(tenant_id,enabled,settings,operation_id)
                VALUES($1,$2,$3::jsonb,$4) ON CONFLICT(tenant_id) DO UPDATE
                SET enabled=$2,settings=$3::jsonb,operation_id=$4,updated_at=now()''',
                tenant, args.enabled, json.dumps(settings), operation_id)
        else:
            await conn.execute('''UPDATE tank.trigger_bot_conversations SET status='handed_over',updated_at=now()
                WHERE tenant_id=$1 AND id=$2''', tenant, args.conversation_id)
            if op == 'reply':
                if not args.text.strip() or not args.person.strip():
                    raise HTTPException(422)
                # Human replies are stored for the web widget only; never bypass WhatsApp's free-tier gate.
                row = await conn.fetchrow('SELECT * FROM tank.trigger_bot_conversations WHERE tenant_id=$1 AND id=$2', tenant, args.conversation_id)
                if dict(row).get('transport', 'web') != 'web':
                    raise HTTPException(409)
                ledger = await code_receipt(tenant, 'trigger.bot.reply', conn=conn)
                await conn.execute('''INSERT INTO tank.trigger_bot_messages
                    (tenant_id,id,conversation_id,question,answer,outcome,person,ledger_id)
                    VALUES($1,$2,$3,'',$4,'handed_over',$5,$6)''', tenant, operation_id,
                    args.conversation_id, args.text, args.person, ledger)
    if op == 'handover':
        recipient = await pool.fetchval('SELECT handover_email FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant)
        await bot().notify(tenant, args.conversation_id, recipient, args.text, address='console')
    return {'status': 'confirmed', 'conversation_id': str(args.conversation_id) if op != 'settings' else None}


def local_schedule(arguments):
    """Resolve only an unambiguous IANA wall time; never guess a DST offset."""
    values = dict(arguments)
    try:
        date, time = values.pop('local_date'), values.pop('local_time')
        wall = datetime.fromisoformat(date+'T'+time)
        if wall.tzinfo is not None:
            raise ValueError()
        zone = ZoneInfo(values['time_zone'])
        choices = {candidate.isoformat() for fold in (0, 1)
                   if (candidate := wall.replace(tzinfo=zone, fold=fold)).astimezone(ZoneInfo('UTC')).astimezone(zone).replace(tzinfo=None) == wall}
        if len(choices) != 1:
            raise ValueError()
        values['planned_at'] = choices.pop()
    except (ValueError, KeyError, TypeError, ZoneInfoNotFoundError):
        raise HTTPException(422) from None
    return values


def card_preview(card):
    """Render the same server-prepared payload Confirm will execute, not stale page props."""
    tool, args = card['tool'], card['arguments']
    try:
        stored = json.loads(args.get('brief_markdown', '{}'))
    except (ValueError, TypeError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    row = {**stored, **{key: value for key, value in args.items() if key not in {'brief_markdown', 'expected_digest'}}}
    if tool == 'trigger.post.schedule':
        when = datetime.fromisoformat(row['planned_at'])
        return [('Post title', stored.get('title')), ('Approved revision', stored.get('approved_revision')),
                ('Channels', stored.get('channel')), ('Date', when.date().isoformat()),
                ('Time zone', row['time_zone']), ('Time', when.timetz().isoformat())]
    if tool.startswith('trigger.post.'):
        return [('Post title', row.get('title')), ('Text', row.get('text')), ('Channels', row.get('channel')),
                ('Saved voice', row.get('voice_revision')), ('Checker findings', row.get('report'))]
    labels = {'enabled': 'Bot', 'opening_hours': 'Opening hours', 'time_zone': 'Time zone', 'greeting': 'Greeting',
              'handover_rules': 'Hand-over rules', 'no_source_message': 'No-source message',
              'handover_message': 'Hand-over message', 'conversation_id': 'Conversation', 'person': 'Person',
              'text': 'Reply', 'draft_id': 'Lead draft' if '.lead.' in tool else 'Quote draft',
              'first_name': 'First name', 'last_name': 'Last name', 'email': 'Email', 'phone': 'Phone',
              'description': 'Notes', 'title': 'Title', 'request': 'Request', 'quantity': 'Quantity',
              'product_id': 'Product', 'contact': 'Contact', 'source_ref': 'Source conversation',
              'location_title': 'Account', 'page_name': 'Account', 'location': 'Profile', 'page_id': 'Account',
              'review_id': 'Review', 'report': 'Checker findings'}
    return [(label, row[key]) for key, label in labels.items() if key in row]
