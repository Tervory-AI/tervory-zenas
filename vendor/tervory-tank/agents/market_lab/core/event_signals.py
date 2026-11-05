"""Engine-configured saved events become source-cited warning episodes, without fetching."""
from __future__ import annotations

import re
from collections import defaultdict
from uuid import UUID

from core.warning_store import decoded, event_day
from feeds.keys import key_is_set
from feeds.geo import US_POSTAL_SET


async def series(ledger, tenant: str, plan: dict, base: dict, now) -> dict:
    rules = [rule for rule in plan.get('event_signals', []) if key_is_set(rule['key_name'])]
    if not rules:
        return {}
    regions = {}
    for rule in rules:
        regions[rule['ref']] = {row['ref'][len(rule['region_prefix']):] for row in base.values()
            if row.get('source') == rule['region_source'] and row.get('ref', '').startswith(rule['region_prefix'])
            and row.get('points') and row['ref'][len(rule['region_prefix']):] in US_POSTAL_SET}
    if not any(regions.values()):
        return {}
    records = await ledger.conn.fetch(
        'SELECT content FROM agent_market_lab.feed_documents WHERE tenant_id=$1 '
        'AND fixture=false AND fetched_at <= $2 ORDER BY fetched_at,id', UUID(tenant), now)
    groups = defaultdict(lambda: defaultdict(dict))
    names = {r['source']: r['label'] for r in plan['signals']}
    for record in records:
        doc = decoded(record['content'])
        for rule in rules:
            if doc.get('source_id') != rule['source']:
                continue
            for index, event in enumerate(doc.get('events', [])):
                day = event_day(event)
                if day is None or day > now.date() or event.get('kind') != rule['kind']:
                    continue
                if event.get('geo') not in regions[rule['ref']]:
                    continue
                if rule.get('stage'):
                    match = re.fullmatch(rule['stage_pattern'], str(event.get('title') or ''))
                    if match is None or match.group('stage') != rule['stage']:
                        continue  # No inference from ambiguous action descriptions.
                url = event.get('url')
                if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
                    continue
                groups[rule['ref']][day.isoformat()].setdefault(url, {
                    'document_id': doc['id'], 'row': index, 'url': url,
                    'event_title': event['title'],
                    'href': f"/market-lab/sources/{doc['id']}?row={index}&kind=event#event-{index}"})
    result = {}
    for rule in rules:
        days = groups[rule['ref']]
        if not days:
            continue
        ident = rule['source'] + ':' + rule['ref']
        result[ident] = {'id': ident, 'source': rule['source'], 'ref': rule['ref'],
            'name': names[rule['source']], 'unit': 'count', 'points': [
                {'date': stamp, 'value': str(len(events)), 'unit': 'count',
                 **next(iter(events.values())), 'evidence': list(events.values())}
                for stamp, events in sorted(days.items())]}
    return result
