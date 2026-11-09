"""One request bracket around real handlers; stateful commands never reuse output."""
from dataclasses import dataclass
import copy

from agents.zenas import gates_memory as gm
from agents.zenas.memory_api import is_moot_path

# Only stateless business computations may replay across fresh command IDs.
REUSABLE = frozenset({'/answers', '/drafts', '/contract-reviews', '/ladder/apply',
                      '/ladder/compose', '/ladder/plain'})
FIELDS = {'facts': gm.FACT, 'parties': gm.PARTY, 'documents': gm.DOCUMENT,
          'positions': gm.POSITION, 'house_style': gm.HOUSE_STYLE, 'clauses': gm.CLAUSE,
          'deadlines': gm.DEADLINE, 'student_context': gm.STUDENT, 'visitor_context': gm.VISITOR,
          'jurisdiction': gm.JURISDICTION}


@dataclass
class Task:
    gate: object = None
    enabled: bool = False
    status: str = 'off'
    hit: object = None
    worked_law: int = 0
    payload: object = None
    action: str = ''

    async def cached(self, request):
        if not self.hit:
            return None
        meta = self.hit['meta']
        value = copy.deepcopy(meta.get('original_result') or {})
        correction = copy.deepcopy(meta['result'])
        if isinstance(value, dict) and isinstance(correction, dict):
            if isinstance(value.get('memo'), dict) and isinstance(correction.get('memo'), dict):
                correction['memo'] = {**value['memo'], **correction['memo']}
            value.update(correction)
        else:
            value = correction
        if not isinstance(value, dict):
            return None
        for key in ('receipt', 'savings_receipt', 'brain_receipt', 'ledger_ids', 'memory'):
            value.pop(key, None)
        if self.hit['kind'] == gm.CORRECTION and self.payload.get('matter_id') and value.get('memo', {}).get('text'):
            from .store import PgStore
            store = PgStore(request.app.state.pool)
            version = await store.next_version(self.gate.tenant, self.payload['matter_id'], 'memo')
            document = await store.add_document({'matter_id': self.payload['matter_id'], 'tenant_id': self.gate.tenant,
                'kind': 'memo', 'version': version, 'body': value['memo']['text'],
                'sources': value['memo'].get('sources') or [], 'state': value.get('state', 'in_review'), 'reviewed': False})
            value['document_id'] = document['id']
            value['delivered'] = value.get('state') == 'released'
        value['memory_hit'] = True
        return value

    async def remember(self, output, bill):
        if is_moot_path(self.action) or not self.enabled or self.hit:
            return
        fields = {**(self.payload.get('features') or {}), **self.payload}
        existing = await self.gate.view()
        for key, kind in FIELDS.items():
            value = fields.get(key)
            if value and not any(r['kind'] == kind and (r.get('meta') or {}).get('value') == value for r in existing):
                await self.gate._remember(kind, 'zenas '+key, {'value': value})
        if self.action in REUSABLE:
            self.hit = await self.gate.remember_result(self.action, self.payload, output, bill)


async def prepare(request, payload):
    task = Task(payload=payload, action=request.url.path)
    if is_moot_path(task.action):
        return task
    actor = getattr(request.state, 'zenas_actor', None)
    memory = getattr(request.app.state, 'memory', None)
    if actor is None or memory is None or not gm.memory_enabled_env():
        return task
    task.gate = gm.GateMemory(memory, str(actor.tenant_id), actor.user_id,
        **{key: str(payload[key]) if payload.get(key) else None
           for key in ('firm_id', 'attorney_id', 'client_id', 'matter_id')})
    try:
        task.enabled = await task.gate.enabled()
        task.status = 'ok' if task.enabled else 'off'
        if task.enabled:
            task.worked_law = len(await task.gate.approved_worked_law())
            if task.action in REUSABLE:
                task.hit = await task.gate.lookup(task.action, payload)
            if task.hit is None:
                query = str(payload.get('question') or payload.get('narrative') or request.url.path)
                recalled = await gm.memory_call(memory.recall(str(actor.tenant_id), query, user_id=actor.user_id))
                request.state.zenas_memory_context = [row for row in recalled if task.gate._owns(row)]
    except gm.HTTPException:
        raise
    except Exception:
        task.enabled, task.status = False, 'memory unavailable'
    return task
