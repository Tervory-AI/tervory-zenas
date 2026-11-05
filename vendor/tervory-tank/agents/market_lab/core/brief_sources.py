"""Brief claims come from authoritative saved rows, never inferred source URLs."""
from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

from core.briefs import result
from core.evidence import current_revisions, gated_facts, point_fact, usable_points
from core.predictions import clock
from core.pure import _area_of, _role_of


async def claims(ledger, tenant: str, bindings: dict, areas: list[dict], *, now) -> tuple[str, list[dict]]:
    owner = str(UUID(tenant))
    selected = {row['area'] for row in areas}
    stored = await result(ledger.points_for(owner))
    actual = {(p['series_id'], str(p['date'])[:10]): Decimal(str(p['value'])) for p in stored}
    if hasattr(ledger, 'conn'):
        rows = await ledger.conn.fetch(
            'SELECT content FROM agent_market_lab.feed_documents WHERE tenant_id=$1 '
            'AND fixture=false AND fetched_at <= $2 ORDER BY fetched_at,id', UUID(owner), now)
        documents = [json.loads(r['content']) if isinstance(r['content'], str) else r['content'] for r in rows]
    else:
        documents = [d for d in getattr(ledger, 'feed_documents', []) if str(d['tenant_id']) == owner
                     and not d['fixture'] and clock(d['fetched_at']) <= clock(now)]
    latest = {}
    for doc in documents:
        if doc.get('kind') == 'warning_evidence':
            continue
        revisions = current_revisions(doc['points'])
        for index, point in await usable_points(ledger, owner, doc):
            key = (point['series_id'], str(point['date'])[:10])
            role = _role_of({'source': doc['source_id'], 'ref': point['ref']}, bindings)
            if role is None or _area_of(point['ref'], bindings[role]) not in selected:
                continue
            if revisions[key] != index or key[1] > clock(now).date().isoformat():
                continue
            if actual.get(key) != Decimal(str(point['value'])):
                continue  # Withdrawn, unpooled and superseded values cannot support a claim.
            fact = point_fact(doc, index, point)
            if key[0] not in latest or key[1] >= latest[key[0]][0]:
                latest[key[0]] = (key[1], fact)
    facts = [value[1] for _, value in sorted(latest.items())]
    # Existing document matcher returns the actual saved passage and page/row link.
    from core.documents import matched
    for area in sorted(selected):
        facts.extend(fact for _, _, fact in await matched(ledger, owner, area))
    # Copy only saved prediction statements with a real evidence link. ML4's gate
    # resolves the ID/cohort and verifies private document ownership below.
    for prediction in await result(ledger.predictions_for(owner)):
        if str(prediction['tenant_id']) != owner or prediction.get('outcome') is not None:
            continue
        if clock(prediction['made_at']) > clock(now):
            continue
        evidence = next((s.get('evidence') or s.get('url') for s in prediction.get('sources', [])
                         if isinstance(s, dict) and (s.get('evidence') or s.get('url'))), None)
        if not isinstance(evidence, str) or not evidence.startswith(('https://', '/market-lab/sources/')):
            continue
        facts.append({'text': prediction['statement'], 'href': evidence, 'prediction_id': str(prediction['id'])})
    accepted = await gated_facts(ledger, owner, facts, now=now)
    citations, seen = [], set()
    for fact in accepted:
        text, url = fact['text'], fact.get('href') or fact.get('url')
        if not text or not url or (text, url) in seen:
            continue
        seen.add((text, url))
        if url.startswith('/market-lab/sources/'):
            url = 'https://app.tervory.com' + url
        citations.append({'claim': text, 'url': url, **{k: fact[k] for k in ('prediction_id', 'warning_id') if k in fact}})
        if len(citations) == 50:
            break
    return '\n'.join(c['claim'] for c in citations), citations
