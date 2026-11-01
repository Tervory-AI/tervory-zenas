"""Offline EUR-Lex discovery, text ingestion and actual research retrieval."""
import json
from datetime import date
from dataclasses import asdict
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import pytest

from agents.zenas.core.regulation_sources import SOURCES
from agents.zenas.core.service import _passages_from_pool
from agents.zenas.core.law_pool import AsyncpgLawPool
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.adapters.eur_lex import EurLexAdapter, legal_text, source_url
from agents.zenas.pool.catalogue import CATALOGUE, sources_for_region
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.store import MemoryStore

CELEX = '32013R0952'
TITLE = 'Regulation (EU) No 952/2013 laying down the Union Customs Code'
URL = source_url(CELEX)
# Isolated transport data only; never supplied to a live screen or store.
CIVIL = ('This Regulation establishes the Union Customs Code laying down the general rules '
         'and procedures applicable to goods brought into or taken out of the customs territory of the Union.')
TEXT = 'Article 1\n\n' + CIVIL + '\n\nArticle 2\n\nCriminal prosecution and sentencing.'


def spec():
    return next(s for s in CATALOGUE if s.source_id == 'eur-lex')


def envelope(**overrides):
    return json.dumps(dict(id=CELEX, source='eur-lex', title=TITLE, link=URL,
                           date='2013-10-09', text=TEXT, **overrides)).encode()


def test_source_is_listed_once_with_existing_region_and_terms():
    source = spec()
    assert len([s for s in CATALOGUE if s.source_id == source.source_id]) == 1
    assert source in sources_for_region(SOURCES['eur-lex'][0])
    assert source not in sources_for_region('za')
    assert source.region == 'us-east'
    assert '2011/833/EU' in source.terms
    assert '2026-11-01' in source.terms
    assert urlsplit(source.terms_url).hostname == 'eur-lex.europa.eu'
    assert isinstance(get_adapter(source), EurLexAdapter)


@pytest.mark.asyncio
async def test_fetch_reuses_existing_sparql_and_extracts_only_legal_text():
    calls = []

    async def get(url, headers):
        calls.append(url)
        if url.startswith('https://publications.europa.eu/webapi/rdf/sparql?'):
            return json.dumps({'results': {'bindings': [{
                'celex': {'value': CELEX}, 'title': {'value': TITLE},
                'date': {'value': '2013-10-09'},
            }]}}).encode()
        assert url == URL
        return ('<html><body>Navigation and third party promotion'
                '<div id="docHtml"><p>Article 1</p><p>' + CIVIL + '</p>'
                '<script>do not copy</script><p>Article 2</p>'
                '<p>Criminal prosecution and sentencing.</p></div>'
                '<footer>Unrelated material</footer></body></html>').encode()

    blobs = await get_adapter(spec()).fetch(get)
    assert len(blobs) == 1  # Three discovery queries share a CELEX identity.
    doc, = get_adapter(spec()).parse(blobs[0], spec())
    assert doc.url == URL
    assert doc.citation == 'CELEX:' + CELEX
    assert doc.sections[0].path == 'Article 1'
    assert 'customs territory' in doc.text
    assert 'Criminal' not in doc.text
    assert 'Navigation' not in doc.text
    assert 'Unrelated' not in doc.text
    assert 'do not copy' not in doc.text
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_existing_research_returns_citations_with_exact_eur_lex_links():
    store = MemoryStore(home_region="us-east")
    assert await ingest_snapshot(store, spec(), [envelope()], on=date(2026, 11, 1), region='us-east') == 1
    class Database:
        @asynccontextmanager
        async def acquire(self):
            yield self

        async def fetch(self, query, *args):
            if query.lstrip().startswith('SELECT d.document_id'):
                assert args[0:2] == ('us', 'us-east')
                return [{**asdict(doc), 'document_id': doc.id, 'attribution': doc.body}
                        for doc in store.docs.values() if doc.version_id in args[2]]
            assert args[0] == 'us'
            return [{**asdict(chunk), 'url': store.docs[chunk.version_id].url}
                        for chunk in store.chunks.values()]

    # Real production bridge and source metadata query, with only DB transport
    # replaced. Explicit no-op semantic backends guarantee no model/network call.
    law = AsyncpgLawPool(Database(), home_region='us-east', embedder=NoOpEmbedder(), index=NoOpVectorIndex())
    passages = await _passages_from_pool(law, query='customs', citation='', jurisdiction='us',
                                         as_at=None, level=None, kind=None)
    assert passages
    for passage in passages:
        assert passage['citation'].startswith('CELEX:' + CELEX)
        assert urlsplit(passage['url']).hostname == 'eur-lex.europa.eu'
        assert passage['url'] == URL
        assert 'European Union' in passage['licence']
        assert 'Criminal' not in passage['text']


@pytest.mark.parametrize('celex', ['62013CJ0001', '52013DC0001', 'https://other.example', '32013R0952/../../'])
def test_only_supported_eu_legislation_identities_have_citation_links(celex):
    with pytest.raises(ValueError):
        source_url(celex)


@pytest.mark.parametrize('html', [
    '<html>JavaScript is disabled. Verify that you are not a robot.</html>',
    '<div id="docHtml"><p>International Accounting Standards. All rights reserved.</p></div>',
    '<div id="docHtml">incomplete',
])
def test_challenge_incomplete_or_special_rights_text_is_never_ingested(html):
    with pytest.raises(FetchNotConfigured):
        legal_text(html.encode())


def test_foreign_source_url_is_rejected():
    row = json.loads(envelope())
    row['link'] = 'https://third-party.example/text'
    with pytest.raises(ValueError):
        get_adapter(spec()).parse(json.dumps(row).encode(), spec())
