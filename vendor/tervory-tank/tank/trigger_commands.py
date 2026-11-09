"""Code-only voice/feedback planning and durable Command confirmation binding."""
import json
import re
from contextvars import ContextVar
from uuid import UUID

from fastapi import HTTPException

CONFIRMED = ContextVar('trigger_confirmed_action', default=None)
VOICE_TOOLS = ['trigger.voice.show', 'trigger.voice.set', 'trigger.voice.rollback']
VOICE_PREFIX = re.compile(r'\s*trigger(?:\.voice\.|[, ]+voice[ .]+)(show|set|rollback)\b\s*:?[ \t]*(.*)', re.I | re.S)


async def plan(actor, text: str, task_id):
    from command import authorized_actor, require_tool, db, unpack
    crm = re.fullmatch(r'\s*trigger\.crm\.(list|get|write)\b\s*:?[ \t]*(.*)', text, re.IGNORECASE | re.DOTALL)
    if crm:
        from command import PlannedAction, validate_action
        actor = await authorized_actor(actor)
        tool = 'trigger.crm.' + crm[1].lower()
        require_tool(actor, tool)
        source = crm[2].strip()
        try:
            if source.startswith('{'):
                args = json.loads(source)
            elif crm[1].lower() == 'list':
                resource, _, query = source.partition(' ')
                args = {'resource': resource, **(json.loads(query) if query.strip() else {})}
            elif crm[1].lower() == 'get':
                resource, record_id = source.split()
                args = {'resource': resource, 'record_id': record_id}
            else:
                raise ValueError
            action = validate_action(PlannedAction(tool=tool, arguments=args), actor)
        except (ValueError, TypeError):
            raise HTTPException(422, 'Invalid CRM request') from None
        return {'text': '', 'actions': [action.model_dump(mode='json')]}
    explicit = re.fullmatch(r'\s*trigger\.(revise|voice\.learn|write\.batch|scorecard)\s*:?[ \t]*(.*)', text, re.I | re.S)
    if explicit:
        actor = await authorized_actor(actor)
        tool = 'trigger.' + explicit[1].lower()
        require_tool(actor, tool)
        source = explicit[2].strip()
        try:
            if tool == 'trigger.scorecard':
                if source:
                    raise ValueError
                args = {}
            elif tool == 'trigger.voice.learn':
                args = {'write_ids': [str(UUID(v.strip())) for v in source.split(',')]}
            elif tool == 'trigger.revise':
                args = {'write_id': str(UUID(source))}
            elif source.lower().startswith('attachment '):
                args = {'attachment': source[11:].strip()}
            elif source.lower().startswith('skus '):
                args = {'skus': [v.strip() for v in source[5:].split(',')]}
            else:
                raise ValueError
        except ValueError:
            raise HTTPException(422, 'Invalid Trigger request') from None
        return {'text': '', 'actions': [{'tool': tool, 'arguments': args}]}
    match = VOICE_PREFIX.fullmatch(text)
    if match:
        actor = await authorized_actor(actor)
        op, source = match[1].lower(), match[2]
        tool = 'trigger.voice.' + op
        require_tool(actor, tool)
        if op == 'set':
            args = {'text': source}
        elif op == 'rollback':
            if not source.strip().isdigit():
                raise HTTPException(422, 'A voice version is required')
            args = {'target': int(source.strip())}
        elif re.fullmatch(r'version \d+', source.strip(), re.I):
            args = {'target': int(source.strip().split()[1])}
        else:
            if source.strip() and not source.strip().isdigit():
                raise HTTPException(422, 'History offset must be a number')
            args = {'offset': int(source.strip() or '0')}
        return {'text': '', 'actions': [{'tool': tool, 'arguments': args}]}
    match = re.fullmatch(r'\s*trigger[, .]+(accept|reject|changes)(?:\s+last draft)?\s*:?[ \t]*(.*)', text, re.I | re.S)
    if not match:
        return None
    actor = await authorized_actor(actor)
    require_tool(actor, 'trigger.feedback')
    async with db().acquire() as conn:
        raw = await conn.fetchval("SELECT state FROM tank.command_threads WHERE tenant_id=$1 AND user_id=$2 AND state->>'active_turn'=$3", actor.tenant_id, actor.user_id, str(task_id))
    cards = []
    for card in unpack(raw or {}).get('cards', {}).values():
        if card.get('status') != 'confirmed' or card.get('tool') not in {'trigger.write', 'trigger.revise', 'trigger.write.batch'}:
            continue
        for result in card.get('result', {}).get('items', [card.get('result', {})]):
            if result.get('write_id'):
                cards.append({**card, 'result': result})
    selected = re.match(r'([0-9a-fA-F-]{36})\s*:?\s*(.*)', match[2], re.S)
    if selected:
        cards = [c for c in cards if c['result']['write_id'] == selected[1]]
    if not cards:
        raise HTTPException(404, 'Writing record unavailable')
    last = max(cards, key=lambda c: c.get('started_at', ''))
    if not selected and last.get('tool') == 'trigger.write.batch':
        raise HTTPException(422, 'Writing record unavailable')
    status = {'accept': 'accepted', 'reject': 'rejected', 'changes': 'changes_requested'}[match[1].lower()]
    args = {'write_id': last['result']['write_id'], 'status': status, 'changes': selected[2] if selected else match[2]}
    return {'text': '', 'actions': [{'tool': 'trigger.feedback', 'arguments': args}]}


async def prepare(action, actor, state):
    from command import PlannedAction, validate_action
    from trigger_routes import room
    args = dict(action.arguments)
    if action.tool in {'trigger.voice.set', 'trigger.voice.rollback'}:
        if action.tool.endswith('.set'):
            user_text = next((m['content'] for m in reversed(state['messages']) if m['role'] == 'user'), '')
            match = VOICE_PREFIX.fullmatch(user_text)
            source = match[2] if match and match[1].lower() == 'set' else user_text
            if args['text'].strip() != source.strip():
                raise HTTPException(422, 'Voice text must match the user source')
        preview = await room('voice/preview', {'tenant_id': str(actor.tenant_id), 'text': args.get('text', ''), 'target': args.get('target')}, actor=actor)
        args.update(expected_revision=preview['expected_revision'], preview_hash=preview['preview_hash'],
                    brief_markdown=json.dumps(preview['voice'], ensure_ascii=False, indent=2))
    elif action.tool == 'trigger.voice.learn':
        from trigger_learning import learn_notes
        rules = await learn_notes(actor, args['write_ids'])
        preview = await room('voice/preview', {'tenant_id': str(actor.tenant_id), 'rules': rules}, actor=actor)
        args.update(rules=rules, expected_revision=preview['expected_revision'], preview_hash=preview['preview_hash'],
                    brief_markdown=json.dumps(preview['voice'], ensure_ascii=False, indent=2))
    elif action.tool == 'trigger.write.batch':
        from trigger_batch import products
        rows = await products(actor, args, state)
        args.update(products=rows, brief_markdown=json.dumps({'item_count': len(rows), 'products': [r.get('name', '') for r in rows]}, ensure_ascii=False))
    elif action.tool == 'trigger.feedback':
        args['brief_markdown'] = json.dumps({k: args[k] for k in ('write_id', 'status', 'changes')}, ensure_ascii=False)
    else:
        return action
    return validate_action(PlannedAction(tool=action.tool, arguments=args), actor)


async def dispatch_confirmed(action, actor, thread_id, card_id):
    from command import db, unpack, dispatch_action
    async with db().acquire() as conn:
        raw = await conn.fetchval('SELECT state FROM tank.command_threads WHERE thread_id=$1 AND tenant_id=$2 AND user_id=$3', thread_id, actor.tenant_id, actor.user_id)
    card = unpack(raw or {}).get('cards', {}).get(str(card_id), {})
    if card.get('status') != 'executing' or card.get('payload') != action.model_dump(mode='json'):
        raise HTTPException(409, 'Confirmed action required')
    token = CONFIRMED.set((actor.tenant_id, actor.user_id, UUID(str(card_id)), action.tool, action.arguments))
    try:
        return await dispatch_action(action, actor)
    finally:
        CONFIRMED.reset(token)


def confirmation(actor, tool, body):
    bound = CONFIRMED.get()
    if not bound or bound[:2] != (actor.tenant_id, actor.user_id) or bound[3:] != (tool, body):
        raise HTTPException(409, 'Confirmed action required')
    return bound[2]
