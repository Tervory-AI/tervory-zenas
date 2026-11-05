"""Signed WhatsApp text ingress; shared bot engine and conservative free-tier claims.

No template corridor, provider retry or plaintext customer identity persistence.
The founder supplies the account's timezone; a missing/invalid zone fails closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from tenant_db import tenant_scope
from trigger_bot_shared import PgRateLimits
from vault import key, open_seal, seal

router = APIRouter()
GRAPH = 'https://graph.facebook.com/v26.0/'  # Official Meta text-message example, checked 2026-11-03.
CAP = 1000


def now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Binding:
    tenant: UUID
    token_env: str
    zone: ZoneInfo | None


def bindings():
    try:
        values = json.loads(os.environ.get('TRIGGER_WHATSAPP_BINDINGS', '{}'))
        if not isinstance(values, dict):
            return {}
    except ValueError:
        return {}
    result = {}
    for phone, value in values.items():
        if not isinstance(phone, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', phone) or not isinstance(value, dict):
            continue
        try:
            tenant = UUID(value['tenant_id'])
            token_env = value['token_env']
            if not isinstance(token_env, str) or not re.fullmatch(r'TRIGGER_WHATSAPP_TOKEN_[A-Z0-9_]{1,64}', token_env):
                continue
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        zone = None
        try:
            if isinstance(value.get('timezone'), str):
                zone = ZoneInfo(value['timezone'])
        except (ZoneInfoNotFoundError, ValueError):
            pass
        result[phone] = Binding(tenant, token_env, zone)
    return result


def month(binding, stamp=None):
    if binding.zone is None:
        return None
    return (stamp or now()).astimezone(binding.zone).date().replace(day=1)


def fingerprint(tenant, phone, value):
    return hmac.new(key(), f'trigger:whatsapp:v1:{tenant}:{phone}:{value}'.encode(), hashlib.sha256).hexdigest()


def context(tenant, conversation):
    return f'trigger:whatsapp:v1:{tenant}:{conversation}:sender'


def in_window(stamp):
    return 0 <= (now() - stamp).total_seconds() < 24 * 60 * 60


async def reply_count(pool, tenant):
    total = 0
    for phone, binding in bindings().items():
        if binding.tenant == tenant and binding.zone is not None:
            total += await pool.fetchval('''SELECT COALESCE(sum(count),0) FROM tank.trigger_whatsapp_months
                WHERE tenant_id=$1 AND phone_number_id=$2 AND month=$3''', tenant, phone, month(binding))
    return total


async def available(pool, phone, binding):
    if binding.zone is None:
        return False
    row = await pool.fetchrow('''SELECT count,timezone FROM tank.trigger_whatsapp_months
        WHERE tenant_id=$1 AND phone_number_id=$2 AND month=$3''', binding.tenant, phone, month(binding))
    return row is None or (row['count'] < CAP and row['timezone'] == binding.zone.key)


async def reserve(conn, phone, binding, event):
    """Called inside the message transaction. A committed attempt is never refunded."""
    period = month(binding)
    if period is None:
        return None
    value = await conn.fetchval('''INSERT INTO tank.trigger_whatsapp_months
        (tenant_id,phone_number_id,month,timezone,count) VALUES($1,$2,$3,$4,1)
        ON CONFLICT DO NOTHING RETURNING count''', binding.tenant, phone, period, binding.zone.key)
    if value is None:
        value = await conn.fetchval('''UPDATE tank.trigger_whatsapp_months SET count=count+1
            WHERE tenant_id=$1 AND phone_number_id=$2 AND month=$3 AND timezone=$4 AND count<1000
            RETURNING count''', binding.tenant, phone, period, binding.zone.key)
    if value is None:
        return None
    await conn.execute('''UPDATE tank.trigger_whatsapp_events SET state='sending'
        WHERE tenant_id=$1 AND event_hash=$2 AND state='claimed' ''', binding.tenant, event)
    return period


def reply_text(result):
    answer = result.get('answer')
    if not isinstance(answer, str) or not answer.strip():
        return None
    urls = list(dict.fromkeys(source['url'] for source in result.get('sources', []) if source.get('url')))
    text = '\n'.join([answer, *urls])
    return text if len(text) <= 2000 else None


class WhatsApp:
    def __init__(self, bot):
        self.bot, self.pool = bot, bot.pool

    async def send(self, phone, token, sender, text):
        # Private client, no shared request hooks, redirects, proxies or response logging.
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            response = await client.post(GRAPH + phone + '/messages',
                headers={'Authorization': 'Bearer ' + token}, json={
                    'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': sender,
                    'type': 'text', 'text': {'preview_url': False, 'body': text}})
        return response.is_success

    async def receive(self, phone, binding, message):
        sender, ident = message.get('from'), message.get('id')
        text = message.get('text', {}).get('body') if isinstance(message.get('text'), dict) else None
        if (message.get('type') != 'text' or not isinstance(sender, str) or not 1 <= len(sender) <= 128
                or not isinstance(ident, str) or not 1 <= len(ident) <= 256
                or not isinstance(text, str) or not text.strip() or len(text) > 2000):
            return
        try:
            stamp = datetime.fromtimestamp(int(message['timestamp']), timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            stamp = datetime.min.replace(tzinfo=timezone.utc)  # Invalid window: hand over, never send.
        tenant = binding.tenant
        visitor = UUID(fingerprint(tenant, phone, sender)[:32])
        # A new account month starts a fresh session, including senders handed over at last month's cap.
        conversation = uuid5(visitor, 'trigger.whatsapp.conversation:' + str(month(binding) or 'unconfigured'))
        event = fingerprint(tenant, phone, ident)
        # Rate limits use an opaque keyed sender id, never the Meta server IP or raw phone.
        address = str(visitor)
        if await self.pool.fetchval('''SELECT 1 FROM tank.trigger_whatsapp_events
                WHERE tenant_id=$1 AND event_hash=$2''', tenant, event):
            return
        decision = await PgRateLimits(self.pool).consume(str(tenant), address, '/v1/rig/ask')
        if decision.hold or decision.retry_after:
            return
        if not await self.pool.fetchval('SELECT enabled FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant):
            return
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute('''INSERT INTO tank.trigger_bot_conversations
                (tenant_id,id,visitor_id,transport,whatsapp_sealed) VALUES($1,$2,$3,'whatsapp',$4)
                ON CONFLICT DO NOTHING''', tenant, conversation, visitor, seal({'sender': sender}, context(tenant, conversation)))
            claimed = await conn.fetchval('''INSERT INTO tank.trigger_whatsapp_events
                (tenant_id,event_hash,conversation_id) VALUES($1,$2,$3)
                ON CONFLICT DO NOTHING RETURNING event_hash''', tenant, event, conversation)
        if claimed is None:
            return
        token = os.environ.get(binding.token_env, '')
        forced = not token or not in_window(stamp) or not await available(self.pool, phone, binding)
        period, outgoing = None, None

        async def gate(conn, result):
            nonlocal period, outgoing
            outgoing = reply_text(result)
            if outgoing is None or not token or not in_window(stamp):
                return False
            period = await reserve(conn, phone, binding, event)
            return period is not None

        result = await self.bot.ask(tenant, text, conversation_id=conversation, visitor_id=visitor,
                                   address=address, force_handover=forced, reply_gate=gate)
        if result['answer'] is None:
            await self.state(tenant, event, 'handed_over')
            return
        # Crossing the account's month/window during generation cannot spend another month's allowance.
        if not in_window(stamp) or month(binding) != period:
            await self.handover(tenant, conversation, text, address)
            await self.state(tenant, event, 'handed_over')
            return
        sealed = await self.pool.fetchval('''SELECT whatsapp_sealed FROM tank.trigger_bot_conversations
            WHERE tenant_id=$1 AND id=$2 AND transport='whatsapp' ''', tenant, conversation)
        if sealed is None:
            return  # Retention/deletion wins; the committed attempt remains spent.
        try:
            recipient = open_seal(sealed, context(tenant, conversation))['sender']
            if not in_window(stamp) or month(binding) != period:
                await self.handover(tenant, conversation, text, address)
                await self.state(tenant, event, 'handed_over')
                return
            sent = await self.send(phone, token, recipient, outgoing)
        except Exception:  # No exception bodies, tokens or customer metadata in logs/responses; no replay.
            sent = False
        await self.state(tenant, event, 'sent' if sent else 'uncertain')
        if not sent:
            await self.handover(tenant, conversation, text, address)

    async def state(self, tenant, event, state):
        await self.pool.execute('''UPDATE tank.trigger_whatsapp_events SET state=$3
            WHERE tenant_id=$1 AND event_hash=$2''', tenant, event, state)

    async def handover(self, tenant, conversation, text, address):
        await self.pool.execute('''UPDATE tank.trigger_bot_conversations SET status='handed_over'
            WHERE tenant_id=$1 AND id=$2 AND status='bot' ''', tenant, conversation)
        recipient = await self.pool.fetchval('SELECT handover_email FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant)
        await self.bot.notify(tenant, conversation, recipient, text, address=address)


def messages(payload):
    if not isinstance(payload, dict) or payload.get('object') != 'whatsapp_business_account':
        return
    entries = payload.get('entry', [])
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('changes'), list):
            continue
        for change in entry['changes']:
            if not isinstance(change, dict) or change.get('field') != 'messages':
                continue
            value = change.get('value')
            if not isinstance(value, dict) or value.get('messaging_product') != 'whatsapp':
                continue
            metadata, incoming = value.get('metadata'), value.get('messages')
            if not isinstance(metadata, dict) or not isinstance(incoming, list):
                continue
            phone = metadata.get('phone_number_id')
            if isinstance(phone, str):
                for message in incoming:
                    if isinstance(message, dict):
                        yield phone, message


@router.get('/v1/bot/whatsapp')
async def challenge(request: Request):
    expected = os.environ.get('TRIGGER_WHATSAPP_VERIFY_TOKEN', '')
    supplied = request.headers.get('x-whatsapp-verify-token', '')
    mode = request.headers.get('x-whatsapp-mode', '')
    value = request.headers.get('x-whatsapp-challenge', '')
    if (not expected or not hmac.compare_digest(expected.encode(), supplied.encode())
            or mode != 'subscribe' or not re.fullmatch(r'[0-9]{1,128}', value)):
        raise HTTPException(403)
    return Response(value, media_type='text/plain', headers={'Cache-Control': 'no-store'})


@router.post('/v1/bot/whatsapp')
async def webhook(request: Request):
    secret = os.environ.get('TRIGGER_WHATSAPP_APP_SECRET', '')
    signature = request.headers.get('x-hub-signature-256', '')
    if not secret or not re.fullmatch(r'sha256=[a-fA-F0-9]{64}', signature):
        raise HTTPException(403)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 262144:
            raise HTTPException(413)
    expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise HTTPException(403)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(422) from None
    from trigger_bot_api import make_bot
    configured = bindings()
    for phone, message in messages(payload):
        binding = configured.get(phone)
        if binding is None:
            continue
        with tenant_scope(binding.tenant):
            try:
                await WhatsApp(make_bot()).receive(phone, binding, message)
            except HTTPException as exc:
                if exc.status_code not in (404, 409):
                    raise HTTPException(503) from None
            except Exception:
                # Durable inbound claim prevents automatic model/send replay after an uncertain result.
                raise HTTPException(503) from None
    return Response(status_code=200, headers={'Cache-Control': 'no-store'})
