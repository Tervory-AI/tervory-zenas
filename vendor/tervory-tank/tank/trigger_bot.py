"""Trigger B1 runtime. Private tenant storage; one engine for visitors and Command."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from tenant_db import tenant_scope
from trigger_bot_engine import respond
from trigger_bot_shared import SmtpMailer, PgRateLimits
from trigger_bot_sources import Sources


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


class Bot:
    def __init__(self, runtime):
        self.rt = runtime
        self.pool = runtime.pool()
        self.sources = Sources(self.pool, runtime.box_region())

    async def has_rig(self, tenant):
        return bool(await self.pool.fetchval("SELECT 1 FROM tank.tenant_squad WHERE tenant_id=$1 AND agent='rig'", tenant))

    async def rig_read(self, tenant, product_id=None, question=''):
        if not await self.has_rig(tenant):
            raise HTTPException(403)
        if product_id and not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', product_id):
            raise ValueError('product_id')
        path = '/v1/rig/products/' + product_id if product_id else '/v1/rig/search'
        params = [('tenant_id', str(tenant)), ('source_record', '1')]
        if not product_id:
            params += [('q', question[:200]), ('limit', '50')]
        response = self.rt._rig_response(await self.rt._rig_call('GET', path, params, None, timeout=30), tenant)
        if response.status_code != 200:
            raise ValueError('rig_unavailable')
        data = json.loads(response.body)
        return data.get('data', data)

    async def rig_pages(self, tenant, question):
        data = await self.rig_read(tenant, question=question)
        rows = data.get('results', [])
        return [self.product_page(row) for row in rows if isinstance(row, dict) and row.get('url') and row.get('id')]

    @staticmethod
    def product_page(row):
        # Copy only actual product facts. Never invent currency/price or a URL.
        row = {**row, **row.get('source_record', {})}
        fields = {k: row[k] for k in ('id', 'sku', 'name', 'brand', 'description', 'price', 'currency', 'stock', 'availability', 'attributes') if k in row}
        return {'text': json.dumps(fields, ensure_ascii=False, default=str), 'url': row['url'],
                'title': str(row.get('name') or row.get('sku') or row['id']),
                'sku': str(row.get('sku') or row['id']), 'product_id': str(row['id'])}

    async def retrieve(self, tenant, question):
        rig_source = await self.pool.fetchval("""SELECT id FROM tank.trigger_bot_sources WHERE tenant_id=$1
            AND kind='rig' AND status='ready' AND checked_at>now()-interval '7 days' LIMIT 1""", tenant)
        if rig_source is not None:
            # Query the actual catalogue for each question, not just an initial capped import.
            for page in await self.rig_pages(tenant, question):
                point = uuid5(rig_source, 'product:' + page['product_id'])
                await self.sources.index.put(tenant, self.rt.box_region(), point, page['text'])
                await self.pool.execute('''INSERT INTO tank.trigger_bot_passages
                    (tenant_id,id,source_id,text,url,title,kind,sku,product_id)
                    VALUES($1,$2,$3,$4,$5,$6,'rig',$7,$8) ON CONFLICT(tenant_id,id)
                    DO UPDATE SET text=EXCLUDED.text,url=EXCLUDED.url,title=EXCLUDED.title,sku=EXCLUDED.sku''',
                    tenant, point, rig_source, page['text'], page['url'], page['title'], page['sku'], page['product_id'])
        return await self.sources.search(tenant, question)

    async def live_rig(self, tenant, passage):
        row = await self.pool.fetchrow('''SELECT product_id FROM tank.trigger_bot_passages
            WHERE tenant_id=$1 AND id=$2 AND source_id=$3''', tenant, passage.id, passage.source_id)
        if not row or not row['product_id']:
            return None
        try:
            data = await self.rig_read(tenant, row['product_id'])
            page = self.product_page(data)
            if page['product_id'] != row['product_id']:
                return None
            return replace(passage, text=page['text'], url=page['url'], sku=page['sku'])
        except (ValueError, KeyError, HTTPException):
            return None

    async def voice(self, tenant):
        # Existing Trigger voice endpoint owns canonical storage and scope.
        from trigger_routes import room
        value = await room('voice/show', {'tenant_id': str(tenant)})
        return value.get('voice', value)

    async def route(self, tenant, messages):
        return await self.rt.run_route(self.rt.RouteRequest(tenant_id=tenant, agent='trigger',
            task_type='trigger.bot.answer', home_region=self.rt.box_region(),
            messages=[self.rt.Message(**m) for m in messages]))

    async def receipt(self, tenant, task):
        from trigger_routes import code_receipt
        return await code_receipt(tenant, task)

    async def retain_conversations(self, tenant, *, batch_size=100):
        """Founder decision, 2026-11-03: 12 calendar months after last message.

        Each bounded batch and its counts-only receipt commit together. A full
        queue continues on the next maintenance tick; a completed tenant waits
        24 hours. Transaction locks serialize multiple maintenance instances.
        """
        from trigger_routes import code_receipt
        async with self.pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended('trigger.bot.retention:' || $1::text,0))", str(tenant)):
                return None
            completed = await conn.fetchval('''SELECT 1 FROM tank.ledger l
                JOIN tank.agent_receipts r ON r.tenant_id=l.tenant_id AND r.ledger_id=l.id
                WHERE l.tenant_id=$1 AND l.task_type='trigger.bot.retention'
                AND l.ts>now()-interval '1 day' AND r.receipt->>'complete'='true' LIMIT 1''', tenant)
            if completed:
                return None
            candidates = await conn.fetch('''SELECT c.id FROM tank.trigger_bot_conversations c
                WHERE c.tenant_id=$1 AND COALESCE((SELECT max(m.created_at)
                    FROM tank.trigger_bot_messages m WHERE m.tenant_id=$1 AND m.conversation_id=c.id),c.created_at)
                    <=now()-interval '12 months'
                ORDER BY c.id FOR UPDATE OF c SKIP LOCKED LIMIT $2''', tenant, batch_size)
            # Recheck after acquiring the conversation lock: a message writer
            # may have committed between the candidate snapshot and that lock.
            ids = await conn.fetch('''SELECT c.id FROM tank.trigger_bot_conversations c
                WHERE c.tenant_id=$1 AND c.id=ANY($2::uuid[]) AND COALESCE((SELECT max(m.created_at)
                    FROM tank.trigger_bot_messages m WHERE m.tenant_id=$1 AND m.conversation_id=c.id),c.created_at)
                    <=now()-interval '12 months' ''', tenant, [r['id'] for r in candidates])
            identities = [r['id'] for r in ids]
            kept = await conn.fetch('''UPDATE tank.trigger_bot_drafts SET conversation_id=NULL
                WHERE tenant_id=$1 AND conversation_id=ANY($2::uuid[])
                AND state='confirmed' AND kind IN ('lead','quote') RETURNING id''', tenant, identities)
            drafts = await conn.fetch('''DELETE FROM tank.trigger_bot_drafts
                WHERE tenant_id=$1 AND conversation_id=ANY($2::uuid[]) RETURNING id''', tenant, identities)
            messages = await conn.fetch('''DELETE FROM tank.trigger_bot_messages
                WHERE tenant_id=$1 AND conversation_id=ANY($2::uuid[]) RETURNING id,ledger_id''', tenant, identities)
            transcripts = await conn.fetch('''DELETE FROM tank.transcripts
                WHERE tenant_id=$1 AND ledger_id=ANY($2::uuid[]) RETURNING ledger_id''',
                tenant, [r['ledger_id'] for r in messages if r['ledger_id'] is not None])
            conversations = await conn.fetch('''DELETE FROM tank.trigger_bot_conversations
                WHERE tenant_id=$1 AND id=ANY($2::uuid[]) RETURNING id''', tenant, identities)
            remaining = await conn.fetchval('''SELECT 1 FROM tank.trigger_bot_conversations c
                WHERE c.tenant_id=$1 AND COALESCE((SELECT max(m.created_at)
                    FROM tank.trigger_bot_messages m WHERE m.tenant_id=$1 AND m.conversation_id=c.id),c.created_at)
                    <=now()-interval '12 months' LIMIT 1''', tenant)
            counts = {'conversations': len(conversations), 'messages': len(messages), 'transcripts': len(transcripts),
                      'drafts': len(drafts), 'confirmed_drafts_kept': len(kept), 'complete': not remaining}
            ledger = await code_receipt(tenant, 'trigger.bot.retention', conn=conn)
            await conn.execute('''INSERT INTO tank.agent_receipts(ledger_id,tenant_id,room,receipt)
                VALUES($1,$2,'trigger',$3::jsonb)''', ledger, tenant, json.dumps(counts))
            return counts

    async def ask(self, tenant, text, *, conversation_id=None, visitor_id=None, contact=None, address="command",
                  force_handover=False, reply_gate=None):
        visitor_id = visitor_id or uuid4()
        conversation_id = conversation_id or uuid4()
        config = await self.pool.fetchrow('SELECT * FROM tank.trigger_bot_config WHERE tenant_id=$1', tenant)
        if not config or not config['enabled']:
            raise HTTPException(409)
        await self.pool.execute('''INSERT INTO tank.trigger_bot_conversations(tenant_id,id,visitor_id)
            VALUES($1,$2,$3) ON CONFLICT DO NOTHING''', tenant, conversation_id, visitor_id)
        conversation = await self.pool.fetchrow('''SELECT * FROM tank.trigger_bot_conversations
            WHERE tenant_id=$1 AND id=$2 AND visitor_id=$3''', tenant, conversation_id, visitor_id)
        if conversation is None:
            raise HTTPException(404)
        result = None
        if conversation['status'] == 'bot' and not force_handover:
            try:
                result = await respond(tenant, text, retrieve=self.retrieve, route=self.route,
                                       live_rig=self.live_rig, receipt=self.receipt, voice=self.voice)
            except Exception as exc:  # noqa: BLE001 — fail closed at service boundary
                logging.getLogger(__name__).warning('Bot service unavailable (%s)', type(exc).__name__)
        if result is None:
            result = {'state': 'handed_over' if conversation['status'] != 'closed' else 'closed',
                      'answer': None, 'sources': [], 'retrieved': [], 'cited': [], 'outcome': 'handed_over',
                      'ledger_id': str(await self.receipt(tenant, 'trigger.bot.answer'))}
        message_id = uuid4()
        async with self.pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval('''SELECT status FROM tank.trigger_bot_conversations
                WHERE tenant_id=$1 AND id=$2 AND visitor_id=$3 FOR UPDATE''', tenant, conversation_id, visitor_id)
            if current != 'bot':
                result.update(state=current, answer=None, sources=[], cited=[], outcome='handed_over')
            if result['answer'] is not None and reply_gate is not None and not await reply_gate(conn, result):
                result.update(state='handed_over', answer=None, sources=[], cited=[], outcome='handed_over')
            await conn.execute('''INSERT INTO tank.trigger_bot_messages
                (tenant_id,id,conversation_id,question,answer,retrieved,cited,outcome,ledger_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)''', tenant, message_id, conversation_id, text,
                result['answer'], [UUID(x) for x in result['retrieved']], [UUID(x) for x in result['cited']],
                result['outcome'], UUID(result['ledger_id']))
            await conn.execute('''UPDATE tank.trigger_bot_conversations SET status=$4,updated_at=now()
                WHERE tenant_id=$1 AND id=$2 AND visitor_id=$3''', tenant, conversation_id, visitor_id, result['state'])
        draft = await self.lead_draft(tenant, conversation_id, text, contact)
        result.update(message_id=str(message_id), conversation_id=str(conversation_id), visitor_id=str(visitor_id))
        if draft:
            result['lead_draft_id'] = str(draft)
        if result['state'] == 'handed_over':
            result['notification'] = await self.notify(tenant, conversation_id, config['handover_email'], text, address=address)
        return result

    async def lead_draft(self, tenant, conversation, text, contact):
        enquiry = re.sub(r'(?im)^(?:Name|Email|Phone):[^\n]*(?:\n|$)', '', text).strip()
        if not enquiry:
            return None
        if not contact:
            # Explicit labels only; never infer a person's identity from prose.
            contact = {key.lower(): value.strip() for key, value in re.findall(r'(?im)^(Name|Email|Phone):\s*([^\n]+)', text)}
        if not contact or not contact.get('name') or not (contact.get('email') or contact.get('phone')):
            return None
        draft = uuid4()
        values = {'first_name': contact.get('first_name') or contact['name'], 'last_name': contact.get('last_name'),
                  'email': contact.get('email'), 'phone': contact.get('phone'),
                  'description': text, 'source_ref': str(conversation), 'assigned_to': [], 'contacts': [], 'teams': []}
        await self.pool.execute('''INSERT INTO tank.trigger_bot_drafts(tenant_id,id,conversation_id,kind,data)
            VALUES($1,$2,$3,'lead',$4::jsonb)''', tenant, draft, conversation, json.dumps({k: v for k, v in values.items() if v is not None}))
        return draft

    async def notify(self, tenant, conversation, recipient, text, *, address="command"):
        # Claim before SMTP. Interrupted/uncertain sends are never automatically replayed.
        marker = uuid5(conversation, 'handover-mail')
        claimed = await self.pool.fetchval('''INSERT INTO tank.trigger_bot_drafts
            (tenant_id,id,conversation_id,kind,data,state) VALUES($1,$2,$3,'handover','{}','sending')
            ON CONFLICT DO NOTHING RETURNING id''', tenant, marker, conversation)
        if claimed is None:
            return 'already_claimed'
        decision = await PgRateLimits(self.pool).consume(str(tenant), address, '/v1/trigger/bot/handover')
        if decision.hold or decision.retry_after:
            await self.pool.execute('UPDATE tank.trigger_bot_drafts SET state=$3 WHERE tenant_id=$1 AND id=$2', tenant, marker, 'held')
            return 'held'
        status = 'unconfigured'
        if not recipient:
            import os
            try:
                recipient = json.loads(os.environ.get('TRIGGER_BOT_HANDOVER_ADDRESSES', '{}')).get(str(tenant))
            except (ValueError, AttributeError):
                recipient = None
        if recipient and not re.fullmatch(r'[^\s@,;\r\n]+@[^\s@,;\r\n]+\.[^\s@,;\r\n]+', recipient):
            recipient = None
        sender = SmtpMailer.from_env()
        if sender is not None and recipient:
            try:
                await sender.send({'to': recipient, 'subject': 'trigger.bot.handover',
                                   'text': json.dumps({'conversation_id': str(conversation), 'question': text}, ensure_ascii=False)})
                status = 'sent'
            except Exception as exc:  # noqa: BLE001 — fail closed at service boundary
                logging.getLogger(__name__).warning('Bot mail state uncertain (%s)', type(exc).__name__)
                status = 'uncertain'
        await self.pool.execute('UPDATE tank.trigger_bot_drafts SET state=$3 WHERE tenant_id=$1 AND id=$2', tenant, marker, status)
        return status


async def retention_due(runtime):
    admin = runtime._admin_pool or runtime.pool()
    with tenant_scope(None, administrative=True):
        tenants = await admin.fetch('''SELECT t.tenant_id FROM (
            SELECT tenant_id FROM tank.trigger_bot_conversations UNION SELECT tenant_id FROM tank.trigger_bot_config) t
            WHERE NOT EXISTS(SELECT 1 FROM tank.ledger l JOIN tank.agent_receipts r
                ON r.tenant_id=l.tenant_id AND r.ledger_id=l.id WHERE l.tenant_id=t.tenant_id
                AND l.task_type='trigger.bot.retention' AND l.ts>now()-interval '1 day'
                AND r.receipt->>'complete'='true') ORDER BY t.tenant_id LIMIT 50''')
    for row in tenants:
        with tenant_scope(row['tenant_id']):
            await Bot(runtime).retain_conversations(row['tenant_id'])


async def refresh_loop(runtime):
    while True:
        try:
            await retention_due(runtime)
            admin = runtime._admin_pool or runtime.pool()
            with tenant_scope(None, administrative=True):
                rows = await admin.fetch('''SELECT tenant_id,id FROM tank.trigger_bot_sources
                    WHERE status<>'removed' AND next_check<=now() ORDER BY next_check LIMIT 50''')
            for row in rows:
                with tenant_scope(row['tenant_id']):
                    bot = Bot(runtime)
                    try:
                        await bot.sources.refresh(row['tenant_id'], row['id'], bot.rig_pages)
                    except Exception as exc:  # noqa: BLE001 — fail closed at service boundary
                        logging.getLogger(__name__).warning('Bot service unavailable (%s)', type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — fail closed at service boundary
            logging.getLogger(__name__).warning('Bot maintenance unavailable (%s)', type(exc).__name__)
        await asyncio.sleep(5)
