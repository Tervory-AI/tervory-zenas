"""Durable, metadata-only Langfuse ingestion. One project/key pair per tenant."""
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
import uuid

import httpx


def projects():
    values = json.loads(os.environ.get('LANGFUSE_TENANT_PROJECTS', '{}'))
    used = set()
    keys = set()
    for tenant, entry in values.items():
        uuid.UUID(tenant)
        if not all(entry.get(k) for k in ('project_id', 'public_key', 'secret_key')):
            raise ValueError('Incomplete tenant trace project')
        if (entry['project_id'] in used or entry['public_key'] in keys
                or not re.fullmatch(r'[A-Za-z0-9_-]+', entry['project_id'])):
            raise ValueError('Trace projects must be distinct per tenant')
        used.add(entry['project_id'])
        keys.add(entry['public_key'])
    return values


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid trace amount')
    return value


def safe_metadata(data):
    raw = (data.get('metadata') or {}).get('tank_trace')
    if not isinstance(raw, dict):
        raise ValueError('Model calls require Tank trace context')
    tenant = str(uuid.UUID(raw['tenant_id']))
    if tenant not in projects():
        raise ValueError('Tenant has no isolated trace project')
    task = raw['task_type']
    if not isinstance(task, str) or not re.fullmatch(r'[A-Za-z0-9_.:#-]{1,200}', task):
        raise ValueError('Invalid trace task type')
    if raw.get('rung') not in {'local small', 'local large', 'open-weight', 'frontier', 'local trained'}:
        raise ValueError('Invalid trace rung')
    # Whitelist only. Never copy arbitrary metadata, user, messages, headers,
    # exception text, response output, or provider parameters into Langfuse.
    if not re.fullmatch(r'[A-Za-z0-9_./:@-]{1,200}', raw.get('model', '')):
        raise ValueError('Invalid trace model')
    result = {'tenant_id': tenant, 'task_type': task, 'rung': raw['rung'], 'model': raw['model'],
              'region': raw['region'], 'trace_id': str(uuid.UUID(raw['trace_id']))}
    if result['region'] not in {'us-east', 'za'}:
        raise ValueError('Invalid trace region')
    for key in ('baseline_input_usd_per_m', 'baseline_output_usd_per_m', 'input_usd_per_m', 'output_usd_per_m'):
        result[key] = numeric(raw[key]) if raw.get(key) is not None else None
    return result


class TraceSink:
    def __init__(self, path=None, client_factory=httpx.AsyncClient):
        self.path = path or os.environ.get('LANGFUSE_OUTBOX_PATH', '/var/lib/litellm-traces/outbox.sqlite')
        self.client_factory = client_factory
        self.lock = asyncio.Lock()
        self.worker = None
        self.verified = set()

    def database(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,payload TEXT NOT NULL)')
        return conn

    def enqueue(self, tenant, events):
        with self.database() as db:
            db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?)',
                [(event['id'], tenant, json.dumps(event)) for event in events])

    async def flush(self):
        base = os.environ.get('LANGFUSE_HOST', '').strip().rstrip('/')
        if not base:
            return  # Preserve any queued traces without creating stores or clients.
        url = urlsplit(base)
        # No vendor-cloud defaults or redirects, even after a setting typo.
        if url.scheme != 'http' or url.hostname not in {'langfuse-web', '127.0.0.1', 'localhost', '::1'} or url.username:
            raise ValueError('Trace ingestion must use the local Langfuse service')
        async with self.lock:
            with self.database() as db:
                rows = db.execute('SELECT id,tenant,payload FROM events ORDER BY rowid LIMIT 100').fetchall()
            config = projects()
            for tenant in dict.fromkeys(row[1] for row in rows):
                entry = config.get(tenant)
                if not entry:
                    continue
                batch = [json.loads(row[2]) for row in rows if row[1] == tenant]
                try:
                    async with self.client_factory(timeout=10, follow_redirects=False, trust_env=False) as client:
                        identity = (tenant, entry['project_id'], entry['public_key'])
                        if identity not in self.verified:
                            project = await client.get(base + '/api/public/projects', auth=(entry['public_key'], entry['secret_key']))
                            if (project.status_code != 200 or
                                    {p['id'] for p in project.json().get('data', [])} != {entry['project_id']}):
                                continue
                            self.verified.add(identity)
                        response = await client.post(base + '/api/public/otel/v1/traces',
                            auth=(entry['public_key'], entry['secret_key']), json={'resourceSpans': [{
                                'resource': {'attributes': [attribute('service.name', 'tervory-tank')]},
                                'scopeSpans': [{'scope': {'name': 'tervory-litellm'},
                                                'spans': [event['span'] for event in batch]}]}]})
                    if response.status_code != 200:
                        continue
                    if int(response.json().get('partialSuccess', {}).get('rejectedSpans', 0)):
                        continue
                    acknowledged = {event['id'] for event in batch}
                    with self.database() as db:
                        db.executemany('DELETE FROM events WHERE id=? AND tenant=?', [(x, tenant) for x in acknowledged])
                except (httpx.HTTPError, ValueError, KeyError):
                    continue  # Retained on the persistent volume; no secrets in logs.

    def start(self):
        if self.worker is None or self.worker.done():
            async def retry():
                while True:
                    try:
                        await self.flush()
                    except (ValueError, OSError, sqlite3.Error):
                        pass
                    await asyncio.sleep(5)
            self.worker = asyncio.create_task(retry())

    async def before(self, data):
        meta = safe_metadata(data)
        now = datetime.now(timezone.utc)
        self.enqueue(meta['tenant_id'], [span_event(meta, now, now, 'span')])
        self.start()
        return data

    async def after(self, kwargs, response, start, end, failed=False):
        meta = safe_metadata({'metadata': kwargs.get('litellm_params', {}).get('metadata') or kwargs.get('metadata')})
        if hasattr(response, 'model_dump'):
            response = response.model_dump()
        usage = (response or {}).get('usage', {}) if isinstance(response, dict) else {}
        usage = usage or {}
        known_usage = (any(k in usage for k in ('prompt_tokens', 'input_tokens')) and
                       any(k in usage for k in ('completion_tokens', 'output_tokens')))
        tokens_in = int(numeric(usage.get('prompt_tokens', usage.get('input_tokens', 0))))
        tokens_out = int(numeric(usage.get('completion_tokens', usage.get('output_tokens', 0))))
        cost = kwargs.get('response_cost')
        if cost is not None:
            cost = numeric(cost)
        elif (not failed and meta['input_usd_per_m'] is not None and meta['output_usd_per_m'] is not None
              and (known_usage or meta['input_usd_per_m'] == meta['output_usd_per_m'] == 0)):
            cost = (tokens_in * meta['input_usd_per_m'] + tokens_out * meta['output_usd_per_m']) / 1e6
        would = None
        if known_usage and meta['baseline_input_usd_per_m'] is not None and meta['baseline_output_usd_per_m'] is not None:
            would = (tokens_in * meta['baseline_input_usd_per_m'] + tokens_out * meta['baseline_output_usd_per_m']) / 1e6
        metadata = {k: meta[k] for k in ('tenant_id', 'task_type', 'rung', 'region')}
        metadata.update(cost_usd=cost, would_have_cost_usd=would, cost_status='known' if cost is not None else 'unknown')
        event = span_event(meta, start, end, 'generation', metadata, failed)
        attrs = event['span']['attributes']
        attrs.extend([attribute('langfuse.observation.model.name', meta['model']),
            attribute('langfuse.observation.usage_details', json.dumps({'input': tokens_in, 'output': tokens_out, 'total': tokens_in + tokens_out})),
            attribute('gen_ai.usage.input_tokens', tokens_in), attribute('gen_ai.usage.output_tokens', tokens_out)])
        if cost is not None:
            attrs.append(attribute('langfuse.observation.cost_details', json.dumps({'total': cost})))
        self.enqueue(meta['tenant_id'], [event])
        await self.flush()


def attribute(key, value):
    kind = 'boolValue' if isinstance(value, bool) else 'intValue' if isinstance(value, int) else 'doubleValue' if isinstance(value, float) else 'stringValue'
    return {'key': key, 'value': {kind: str(value) if kind == 'intValue' else value}}


def span_event(meta, start, end, kind, metadata=None, failed=False):
    trace_id = uuid.UUID(meta['trace_id']).hex
    span_id = uuid.uuid5(uuid.NAMESPACE_URL, trace_id + ':' + kind).hex[:16]
    values = metadata or {k: meta[k] for k in ('tenant_id', 'task_type', 'rung', 'region')}
    attrs = [attribute('langfuse.observation.type', kind), attribute('langfuse.trace.name', meta['task_type']),
             attribute('user.id', meta['tenant_id']), attribute('langfuse.trace.public', False),
             attribute('langfuse.observation.level', 'ERROR' if failed else 'DEFAULT')]
    # Use the documented OTEL metadata namespaces, preserving scalar types.
    for key, value in values.items():
        if value is not None:
            attrs.extend(attribute(prefix + key, value) for prefix in
                ('langfuse.observation.metadata.', 'langfuse.trace.metadata.'))
    def nanos(value):
        return str(int(value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp() * 1_000_000_000))
    span = {'traceId': trace_id, 'spanId': span_id, 'name': meta['task_type'], 'kind': 1,
            'startTimeUnixNano': nanos(start), 'endTimeUnixNano': nanos(end),
            'attributes': attrs, 'flags': 1, 'status': {'code': 2 if failed else 1}}
    if kind == 'generation':
        span['parentSpanId'] = uuid.uuid5(uuid.NAMESPACE_URL, trace_id + ':span').hex[:16]
    return {'id': trace_id + ':' + span_id, 'span': span}
