"""Tenant brief detail assembled from saved records and openable stored sources."""
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from core.briefs import result
from core.evidence import document_page


async def detail(ledger, tenant: str, report_id: str, *, engine: str) -> dict | None:
    reports = await result(ledger.briefs_for(tenant, str(UUID(report_id))))
    if not reports:
        return None
    report = reports[0]
    predictions = {str(row['id']): row for row in await result(ledger.predictions_for(tenant))
                   if str(row['tenant_id']) == str(tenant) and row.get('outcome') is None}
    sources, signals = [], []
    for citation in report['citations']:
        url = citation.get('url') or citation.get('href') or ''
        parsed = urlsplit(url)
        stored_href, stored = None, None
        # Never turn an arbitrary publisher URL into an invented stored passage.
        if parsed.netloc in ('', 'app.tervory.com', 'market-lab.tervory.com'):
            prefix = '/market-lab/sources/'
            if parsed.path.startswith(prefix):
                try:
                    ident = str(UUID(parsed.path[len(prefix):]))
                    query = parse_qs(parsed.query)
                    anchor = parsed.fragment.split('-', 1)
                    index = int(query['row'][0]) if 'row' in query else int(anchor[1]) if len(anchor) == 2 else None
                    kind = query.get('kind', [anchor[0] if anchor else ''])[0]
                    if index is not None and not 0 <= index <= 10000000:
                        raise ValueError()
                    stored = await document_page(ledger, tenant, ident, row=index,
                        row_kind=kind if kind in ('point', 'event') else None, engine=engine)
                    rows = (stored or {}).get('passages', []) if kind == 'passage' else (stored or {}).get('events', []) if kind == 'event' else (stored or {}).get('points', [])
                    if stored is not None and (index is None or any(row.get('index') == index for row in rows)):
                        stored_href = parsed.path + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')
                except (ValueError, KeyError, IndexError):
                    pass
        prediction = predictions.get(str(citation.get('prediction_id')))
        sources.append({**citation, 'stored_href': stored_href})
        signals.append({'claim': citation.get('claim') or citation.get('text'),
                        'prediction_id': str(prediction['id']) if prediction else None,
                        'kind': prediction.get('kind') if prediction else stored.get('source_id') if stored else None,
                        'feed': prediction.get('feed') if prediction else stored.get('source_id') if stored else None,
                        'confidence': prediction.get('confidence') if prediction else None,
                        'horizon_seconds': prediction.get('horizon_seconds') if prediction else None,
                        'stored_href': stored_href})
    history = [{'kind': 'report', 'happened_at': report['made_at']}]
    history.extend(await result(ledger.brief_history(tenant, report_id)))
    return {**report, 'sources': sources, 'signals': signals, 'history': history}
