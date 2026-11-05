"""Market Lab's private document memory, retained with file/page provenance."""
from __future__ import annotations

import inspect
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone


async def call(fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


async def accept(ledger, tenant: str, ident: str, name: str, extracted: dict, *, region: str) -> dict:
    """Guard tabular series before admitting their passages to answers."""
    owner = str(uuid.UUID(tenant))
    file_id = str(uuid.UUID(ident))
    source = f'upload:{file_id}'
    now = datetime.now(timezone.utc).isoformat()
    groups = defaultdict(list)
    for point in extracted['tables']:
        groups[(point['label'], point['series'])].append(point)
    blocked = set()
    for index, ((label, title), points) in enumerate(groups.items()):
        series_id = f'{owner}:upload:{file_id}:{index}'
        await call(ledger.save_series, {'id': series_id, 'tenant_id': owner, 'source': source,
            'ref': name, 'unit': points[0]['unit'], 'currency': points[0]['currency'], 'region': region, 'engine': ''})
        count = await call(ledger.save_points, series_id, owner,
            [{**point, 'fetched_at': now, 'source': source, 'region': region} for point in points], stored=True, source=source)
        if count != len(points):
            blocked.add(label)
    passages = [{**p, 'index': index, 'usable': p.get('table') not in blocked} for index, p in enumerate(extracted['passages'])]
    record = {'id': file_id, 'name': name, 'fetched_at': now, 'kind': 'uploaded_file', 'passages': passages,
        'status': 'learned', 'reason': ' '.join(filter(None, [extracted.get('note', ''), 'Some table figures failed the data checks and are not used in answers.' if blocked else ''])),
        'rows': extracted['rows'], 'pages': extracted['pages']}
    if hasattr(ledger, 'conn'):
        await ledger.conn.execute('''INSERT INTO agent_market_lab.uploaded_files(tenant_id,id,content)
            VALUES($1,$2,$3::jsonb) ON CONFLICT(tenant_id,id) DO UPDATE SET content=excluded.content''',
            uuid.UUID(owner), uuid.UUID(file_id), json.dumps(record))
    else:
        ledger.uploaded_files = getattr(ledger, 'uploaded_files', {})
        ledger.uploaded_files[(owner, file_id)] = record
    return record


async def records(ledger, tenant: str, ident: str | None = None) -> list[dict]:
    owner = uuid.UUID(tenant)
    if hasattr(ledger, 'conn'):
        rows = await ledger.conn.fetch('''SELECT content FROM agent_market_lab.uploaded_files
            WHERE tenant_id=$1 AND ($2::uuid IS NULL OR id=$2) ORDER BY created_at DESC''', owner, uuid.UUID(ident) if ident else None)
        return [json.loads(row['content']) if isinstance(row['content'], str) else row['content'] for row in rows]
    return [row for (t, i), row in getattr(ledger, 'uploaded_files', {}).items() if t == str(owner) and (ident is None or ident == i)]


async def source_page(ledger, tenant: str, ident: str, offset: int, row: int | None) -> dict | None:
    found = await records(ledger, tenant, ident)
    if not found:
        return None
    doc = found[0]
    count = len(doc['passages'])
    offset = min(max(0, (row if row is not None else offset) // 50 * 50), max(0, (count - 1) // 50 * 50))
    return {**doc, 'offset': offset, 'page_size': 50, 'passage_count': count,
            'passages': doc['passages'][offset:offset + 50], 'points': [], 'events': [], 'downloads': [], 'point_count': 0, 'event_count': 0}


async def matched(ledger, tenant: str, question: str) -> list[tuple]:
    from core.evidence import _words
    terms = _words(question)
    result = []
    if not terms:
        return result
    for doc in await records(ledger, tenant):
        name_words = _words(doc['name'])
        for passage in doc['passages']:
            words = _words(passage['text'])
            if not passage['usable'] or not terms <= words | name_words or not terms & words:
                continue
            citation = f'{doc["name"]} · {passage["label"]}'
            result.append((3 * len(terms & words), doc['fetched_at'], {'text': passage['text'], 'name': citation,
                'href': f'/market-lab/sources/{doc["id"]}?row={passage["index"]}#passage-{passage["index"]}'}))
    return result


async def forget(ledger, tenant: str, ident: str) -> None:
    owner, file_id = uuid.UUID(tenant), uuid.UUID(ident)
    source = f'upload:{file_id}'
    if hasattr(ledger, 'conn'):
        conn = ledger.conn
        # Caller holds the file's advisory lock and transaction. No stale series survive.
        await conn.execute('''DELETE FROM agent_market_lab.points p USING agent_market_lab.series s
            WHERE p.series_id=s.id AND s.tenant_id=$1 AND s.source=$2''', owner, source)
        await conn.execute('''DELETE FROM agent_market_lab.events p USING agent_market_lab.series s
            WHERE p.series_id=s.id AND s.tenant_id=$1 AND s.source=$2''', owner, source)
        await conn.execute('DELETE FROM agent_market_lab.batch_points WHERE tenant_id=$1 AND source=$2', owner, source)
        await conn.execute('DELETE FROM agent_market_lab.batches WHERE tenant_id=$1 AND source=$2', owner, source)
        await conn.execute('DELETE FROM agent_market_lab.series WHERE tenant_id=$1 AND source=$2', owner, source)
        await conn.execute('DELETE FROM agent_market_lab.uploaded_files WHERE tenant_id=$1 AND id=$2', owner, file_id)
    else:
        getattr(ledger, 'uploaded_files', {}).pop((str(owner), str(file_id)), None)
        for batch in list(ledger.pool.batches.values()):
            if batch['tenant_id'] == str(owner) and batch['source'] == source:
                ledger.remove_batch(str(owner), batch['id'])
        ledger.series = [s for s in ledger.series if not (s['tenant_id'] == str(owner) and s['source'] == source)]
