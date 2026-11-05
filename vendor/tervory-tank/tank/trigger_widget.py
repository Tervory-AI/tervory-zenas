"""B2 visitor transport: origin-bound B1 authentication, evidence and hand-over."""
from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from tenant_db import tenant_scope
from trigger_bot_api import authenticate, bounded_body, make_bot, obj, ask, quote_draft
from trigger_bot_contract import PublicMessage
from trigger_bot_shared import PgRateLimits, proxy_configured

router = APIRouter()
PRESENTATION = {'name', 'greeting', 'no_source_message', 'handover_message', 'privacy_notice',
                'notice_version', 'accent', 'ink', 'paper', 'line'}


class Conversation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: UUID | None = None
    visitor_id: UUID | None = None


def within_hours(settings, now=None):
    """Explicit local weekly intervals only. Unknown hours/zone never imply availability."""
    try:
        zone = ZoneInfo(settings['time_zone'])
        local = (now or datetime.now(zone)).astimezone(zone)
        schedule = json.loads(settings['opening_hours'])
        intervals = schedule[str(local.weekday())]
        if not isinstance(intervals, list):
            return False
        clock = local.strftime('%H:%M')
        return any(isinstance(pair, list) and len(pair) == 2 and
                   all(isinstance(t, str) and len(t) == 5 and 0 <= int(t[:2]) <= 23 and
                       t[2] == ':' and 0 <= int(t[3:]) <= 59 for t in pair) and
                   pair[0] <= clock < pair[1] for pair in intervals)
    except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
        return False


async def handover(bot, tenant, args, peer, text=''):
    conversation, visitor = args.conversation_id or uuid4(), args.visitor_id or uuid4()
    if args.conversation_id and not args.visitor_id:
        raise HTTPException(422)
    async with bot.pool.acquire() as conn, conn.transaction():
        if not args.conversation_id:
            await conn.execute('''INSERT INTO tank.trigger_bot_conversations(tenant_id,id,visitor_id,status)
                VALUES($1,$2,$3,'handed_over')''', tenant, conversation, visitor)
        row = await conn.fetchrow('''UPDATE tank.trigger_bot_conversations SET status='handed_over',updated_at=now()
            WHERE tenant_id=$1 AND id=$2 AND visitor_id=$3 AND status<>'closed' RETURNING id''', tenant, conversation, visitor)
        if row is None:
            raise HTTPException(404)
        from trigger_routes import code_receipt
        ledger = await code_receipt(tenant, 'trigger.bot.answer', conn=conn)
        await conn.execute('''INSERT INTO tank.trigger_bot_messages
            (tenant_id,id,conversation_id,question,outcome,ledger_id)
            VALUES($1,$2,$3,$4,'handed_over',$5)''', tenant, uuid4(), conversation, text, UUID(str(ledger)))
    recipient = await bot.pool.fetchval('SELECT handover_email FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant)
    await bot.notify(tenant, conversation, recipient, text, address=peer)
    return {'state': 'handed_over', 'conversation_id': str(conversation), 'visitor_id': str(visitor), 'sources': [], 'answer': None}


@router.post('/v1/bot/widget/{operation}')
async def public_widget(operation: str, request: Request):
    if operation not in {'config', 'message', 'handover', 'poll'}:
        raise HTTPException(404)
    if not proxy_configured():
        raise HTTPException(503)
    tenant, origin, peer = await authenticate(request)
    with tenant_scope(tenant):
        bot = make_bot()
        limited = await PgRateLimits(bot.pool).consume(str(tenant), peer, '/v1/rig/ask')
        if limited.retry_after:
            raise HTTPException(429, headers={'Retry-After': str(limited.retry_after)})
        try:
            body = await bounded_body(request, 16000)
            args = PublicMessage.model_validate(body) if operation == 'message' else Conversation.model_validate(body)
        except (ValueError, TypeError):
            raise HTTPException(422) from None
        config = await bot.pool.fetchrow('SELECT enabled,settings FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant)
        settings = obj(config['settings']) if config else {}
        enabled = bool(config and config['enabled'])
        available = enabled and within_hours(settings)
        if operation == 'config':
            result = {k: settings[k] for k in PRESENTATION if k in settings}
            result.update(enabled=enabled, available=available)
            result['capture'] = bool(settings.get('privacy_notice') and settings.get('notice_version'))
            result['quote'] = result['capture'] and bool(await bot.pool.fetchval('''SELECT 1 FROM tank.trigger_bot_sources
                WHERE tenant_id=$1 AND kind='rig' AND status='ready' LIMIT 1''', tenant))
        elif operation == 'poll':
            if not args.conversation_id or not args.visitor_id:
                raise HTTPException(422)
            own = await bot.pool.fetchval('''SELECT id FROM tank.trigger_bot_conversations
                WHERE tenant_id=$1 AND id=$2 AND visitor_id=$3''', tenant, args.conversation_id, args.visitor_id)
            if not own:
                raise HTTPException(404)
            messages = await bot.pool.fetch('''SELECT id,answer,person,created_at FROM tank.trigger_bot_messages
                WHERE tenant_id=$1 AND conversation_id=$2 AND person IS NOT NULL ORDER BY created_at,id LIMIT 100''', tenant, args.conversation_id)
            result = {'messages': [{**dict(row), 'id': str(row['id']), 'created_at': row['created_at'].isoformat()} for row in messages]}
        elif operation == 'handover':
            result = await handover(bot, tenant, args, peer)
        else:
            if args.contact and not (settings.get('privacy_notice') and settings.get('notice_version') and
                                     args.notice_version == settings['notice_version']):
                raise HTTPException(409)
            if not available or args.contact:
                result = await handover(bot, tenant, args, peer, args.text)
                if args.contact and not args.quote_product_id:
                    draft = await bot.lead_draft(tenant, UUID(result['conversation_id']), args.text, args.contact.model_dump(exclude_none=True))
                    if draft:
                        result['lead_draft_id'] = str(draft)
                if args.quote_product_id:
                    result = await quote_draft(bot, tenant, args, result)
            else:
                result = await ask(bot, tenant, args, conversation_id=args.conversation_id, visitor_id=args.visitor_id, address=peer)
            result = {k: v for k, v in result.items() if k in {'state', 'answer', 'sources', 'message_id', 'conversation_id', 'visitor_id', 'lead_draft_id', 'quote_draft_id'}}
    return JSONResponse(result, headers={'Access-Control-Allow-Origin': origin, 'Vary': 'Origin', 'Cache-Control': 'no-store'})
