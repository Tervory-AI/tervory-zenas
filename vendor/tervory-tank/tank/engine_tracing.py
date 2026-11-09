"""Only approved scalar trace fields cross from Tank to LiteLLM."""
import json
import os
import engine_runtime
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
import uuid

import engine_ladder

_batch_sink = None


def batch_sink():
    global _batch_sink
    if _batch_sink is None:
        from trace_sink import TraceSink
        _batch_sink = TraceSink()
    return _batch_sink


async def resume_batches():
    if engine_runtime.traces_enabled():
        batch_sink().start()


async def batch_trace(rt, batch, item, response=None, cost=None, failed=False):
    """Native provider batches bypass LiteLLM's completion callback."""
    if not engine_runtime.traces_enabled():
        return
    def obj(value):
        return json.loads(value) if isinstance(value, str) else value
    req, policy = obj(item['request']), obj(item['policy'])['routing']
    data = await metadata(rt, batch['tenant_id'], req['task_type'], batch['model'], policy)
    # Stable across provider polling/restarts; no duplicate calls or trace IDs.
    data['metadata']['tank_trace']['trace_id'] = str(item['item_id'])
    sink = batch_sink()
    if response is None:
        await sink.before(data)
    else:
        now = datetime.now(timezone.utc)
        await sink.after({'metadata': data['metadata'], 'response_cost': cost}, response,
                         batch['submitted_at'] or now, now, failed=failed)


async def metadata(rt, tenant_id, task_type, model, policy=None):
    if not engine_runtime.traces_enabled():
        return {}
    policy = policy or {}
    roster = {row['name']: row for row in await rt.list_models()}
    baseline = policy.get('_engine', {}).get('baseline_model') or policy.get('model') or model
    prices, actual = roster.get(baseline, {}), roster.get(model, {})
    rung = engine_ladder.rung(model)
    if rung == 'frontier' and all(actual.get(k) == 0 for k in ('input_usd_per_m', 'output_usd_per_m')):
        rung = 'local trained'
    return {'metadata': {'tank_trace': {'trace_id': str(uuid.uuid4()), 'tenant_id': str(tenant_id),
        'task_type': task_type, 'model': model, 'rung': rung, 'region': rt.box_region(),
        'baseline_input_usd_per_m': prices.get('input_usd_per_m'),
        'baseline_output_usd_per_m': prices.get('output_usd_per_m'),
        'input_usd_per_m': actual.get('input_usd_per_m'), 'output_usd_per_m': actual.get('output_usd_per_m')}}}


def traces_url(tenant_id, task_type):
    if not engine_runtime.traces_enabled():
        return None
    # Links use public project IDs only, never the ingestion credentials.
    project = json.loads(os.environ.get('LANGFUSE_TENANT_PROJECT_IDS', '{}')).get(str(tenant_id))
    base = os.environ.get('LANGFUSE_UI_URL', '').rstrip('/')
    # This must match the audited ingress origin, not an arbitrary URL containing
    # a familiar hostname or a cloud service used as an accidental fallback.
    if not project or base != 'https://langfuse.tervory.com':
        return None
    filters = [{'column': 'name', 'type': 'string', 'operator': '=', 'value': task_type}]
    return base + '/project/' + quote(project, safe='') + '/traces?' + urlencode({'filter': json.dumps(filters)})
