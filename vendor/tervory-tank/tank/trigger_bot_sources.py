"""Guarded same-site ingestion and tenant-filtered hybrid evidence, never web search."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import UUID, uuid5

from access import public_get, public_stream
from semantic_cache import VectorIndex
from semantic_encoder import ENCODER_ID, LocalEncoder
from trigger_bot_grounding import Passage, chunks, fuse

_encoder = LocalEncoder()


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.links, self.hidden = [], [], 0
        self.title, self.in_title = [], False

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript'}:
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'a' and not self.hidden:
            self.links.extend(v for k, v in attrs if k == 'href' and v)

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag in {'script', 'style', 'noscript'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden:
            self.text.append(text)
            if self.in_title:
                self.title.append(text)


async def robots_text(url):
    # Same SSRF/DNS/TLS guard as pages; public_stream never follows redirects.
    try:
        async with asyncio.timeout(25), public_stream(url, headers={'User-Agent': 'TervoryBot'}) as response:
            if 400 <= response.status_code < 500 and response.status_code != 429:
                return b''
            if response.status_code != 200:
                return b'User-agent: *\nDisallow: /'
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 200_000:
                    return b'User-agent: *\nDisallow: /'
            return bytes(data)
    except Exception:
        return b'User-agent: *\nDisallow: /'


async def crawl(url: str, get=public_get) -> list[dict]:
    origin = urlsplit(url)
    if origin.scheme != 'https' or origin.username or origin.password or not origin.hostname:
        raise ValueError('https_source_required')
    site = f'https://{origin.netloc}'
    if get is public_get:
        robots = await robots_text(site + '/robots.txt')
    else:
        robots, _ = await get(site + '/robots.txt', limit=200_000)
    rules = RobotFileParser()
    rules.parse(robots.decode('utf-8', 'replace').splitlines())
    pending, visited, pages, size = [urldefrag(url)[0]], set(), [], 0
    while pending and len(visited) < 200:
        current = pending.pop(0)
        parsed = urlsplit(current)
        if current in visited or parsed.scheme != 'https' or parsed.netloc != origin.netloc:
            continue
        visited.add(current)
        if not rules.can_fetch('TervoryBot', current):
            continue
        try:
            body, headers = await get(current, headers={'User-Agent': 'TervoryBot'}, limit=1_000_000)
        except (ValueError, OSError):
            continue
        size += len(body)
        if size > 20_000_000:
            break
        if 'text/html' not in headers.get('content-type', ''):
            continue
        page = Page()
        page.feed(body.decode('utf-8', 'replace'))
        text = ' '.join(' '.join(page.text).split())
        if text:
            title = ' '.join(' '.join(page.title).split()) or current
            pages.append({'url': current, 'title': title, 'text': text, 'checked_at': datetime.now(UTC)})
        pending.extend(urldefrag(urljoin(current, link))[0] for link in page.links[:500])
    return pages


class BotIndex(VectorIndex):
    async def put(self, tenant: UUID, region: str, point: UUID, text: str):
        from qdrant_client.models import PointStruct
        name = 'trigger_bot_' + tenant.hex
        await self.ensure(name)
        vector = await asyncio.to_thread(_encoder.embed, text)
        await self.client.upsert(name, points=[PointStruct(id=str(point), vector=vector, payload={
            'tenant_id': str(tenant), 'region': region, 'encoder_id': ENCODER_ID})], wait=True)

    async def query(self, tenant: UUID, region: str, text: str):
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        name = 'trigger_bot_' + tenant.hex
        if not await self.client.collection_exists(name):
            return []
        vector = await asyncio.to_thread(_encoder.embed, text)
        scope = {'tenant_id': str(tenant), 'region': region, 'encoder_id': ENCODER_ID}
        response = await self.client.query_points(collection_name=name, query=vector, limit=24,
            score_threshold=0.65, with_payload=True,
            query_filter=Filter(must=[FieldCondition(key=k, match=MatchValue(value=v)) for k, v in scope.items()]))
        return [(UUID(str(p.id)), float(p.score)) for p in response.points
                if all((p.payload or {}).get(k) == v for k, v in scope.items())]


class Sources:
    def __init__(self, pool, region: str, index=None):
        self.pool, self.region = pool, region
        self._index = index

    @property
    def index(self):
        if self._index is None:
            self._index = BotIndex()
        return self._index

    async def add(self, tenant: UUID, operation: UUID, kind: str, title: str, url=None, content=''):
        source = uuid5(tenant, 'bot-source:' + str(operation))
        await self.pool.execute('''INSERT INTO tank.trigger_bot_sources
            (tenant_id,id,kind,title,url,content,operation_id,status) VALUES($1,$2,$3,$4,$5,$6,$7,'queued')
            ON CONFLICT(tenant_id,operation_id) DO NOTHING''', tenant, source, kind, title, url, content, operation)
        return source

    async def refresh(self, tenant: UUID, source: UUID, rig=None):
        row = await self.pool.fetchrow('''UPDATE tank.trigger_bot_sources SET status='indexing', next_check=now()+interval '30 minutes'
            WHERE tenant_id=$1 AND id=$2 AND status<>'removed'
              AND (status<>'indexing' OR next_check<=now()) RETURNING *''', tenant, source)
        if row is None:
            return
        try:
            if row['kind'] == 'website':
                pages = await crawl(row['url'])
            elif row['kind'] == 'rig':
                pages = await rig(tenant, '') if rig else []
            else:
                data = json.loads(row['content']) if row['kind'] == 'faq' else None
                pages = [{'text': data['question'] if data else row['content'], 'url': row['url'],
                          'title': row['title'], 'question': data['question'] if data else None,
                          'approved_answer': data['answer'] if data else None}]
            if not pages and row['kind'] != 'rig':
                raise ValueError('no_eligible_pages')
            staged = []
            for page in pages[:200]:
                texts = [page['text']] if row['kind'] == 'faq' else chunks(page['text'])
                for ordinal, text in enumerate(texts):
                    point = uuid5(source, json.dumps([page.get('url'), text, ordinal], sort_keys=True))
                    await self.index.put(tenant, self.region, point, text)
                    staged.append((point, text, page))
            async with self.pool.acquire() as conn, conn.transaction():
                # Removal during the crawl wins: a refresh cannot reactivate it.
                current = await conn.fetchval('''SELECT status FROM tank.trigger_bot_sources
                    WHERE tenant_id=$1 AND id=$2 FOR UPDATE''', tenant, source)
                if current != 'indexing':
                    return
                await conn.execute('DELETE FROM tank.trigger_bot_passages WHERE tenant_id=$1 AND source_id=$2', tenant, source)
                for point, text, page in staged:
                    await conn.execute('''INSERT INTO tank.trigger_bot_passages
                        (tenant_id,id,source_id,text,url,title,kind,sku,product_id,question,approved_answer,checked_at)
                        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)''', tenant, point, source, text,
                        page.get('url'), page.get('title', row['title']), row['kind'], page.get('sku'),
                        page.get('product_id'), page.get('question'), page.get('approved_answer'), page.get('checked_at', datetime.now(UTC)))
                await conn.execute('''UPDATE tank.trigger_bot_sources SET status='ready',checked_at=now(),
                    next_check=now()+interval '7 days' WHERE tenant_id=$1 AND id=$2''', tenant, source)
        except Exception:
            await self.pool.execute('''UPDATE tank.trigger_bot_sources SET status='failed',
                next_check=now()+interval '1 day' WHERE tenant_id=$1 AND id=$2 AND status='indexing' ''', tenant, source)
            raise

    async def search(self, tenant: UUID, question: str) -> list[Passage]:
        rows = await self.pool.fetch('''SELECT p.* FROM tank.trigger_bot_passages p JOIN tank.trigger_bot_sources s
                 ON s.tenant_id=p.tenant_id AND s.id=p.source_id WHERE p.tenant_id=$1
                 AND s.status='ready' AND s.checked_at>now()-interval '7 days'
                 AND (p.kind<>'faq' OR NOT EXISTS(SELECT 1 FROM tank.trigger_bot_sources newer
                   WHERE newer.tenant_id=p.tenant_id AND newer.kind='faq' AND newer.status<>'removed'
                   AND lower(trim(newer.content::jsonb->>'question'))=lower(trim(p.question))
                   AND (newer.created_at,newer.id)>(s.created_at,s.id))) AND (p.search @@ plainto_tsquery('english',$2)
                     OR (p.kind='faq' AND lower(trim(p.question))=lower(trim($2))))
            ORDER BY ts_rank_cd(p.search,plainto_tsquery('english',$2)) DESC LIMIT 24''', tenant, question)
        terms = set(re.findall(r'\w+', question.casefold()))
        def score(row):
            other = set(re.findall(r'\w+', row['text'].casefold()))
            denominator = len(terms | other) if row['kind'] == 'faq' else len(terms)
            return len(terms & other) / max(1, denominator)
        lexical = [{**dict(r), 'score': score(r)} for r in rows]
        exact_faq = any(r['kind'] == 'faq' and ' '.join(r['text'].split()).casefold()
                        == ' '.join(question.split()).casefold() for r in rows)
        try:
            points = [] if exact_faq else await self.index.query(tenant, self.region, question)
        except Exception as exc:  # noqa: BLE001 — fail closed at service boundary
            logging.getLogger(__name__).warning('Bot vector lookup unavailable (%s)', type(exc).__name__)
            points = []  # Full-text evidence stays authoritative; never query another tenant.
        scores = dict(points)
        vector_rows = await self.pool.fetch('''SELECT p.* FROM tank.trigger_bot_passages p JOIN tank.trigger_bot_sources s
                 ON s.tenant_id=p.tenant_id AND s.id=p.source_id WHERE p.tenant_id=$1
                 AND s.status='ready' AND s.checked_at>now()-interval '7 days'
                 AND (p.kind<>'faq' OR NOT EXISTS(SELECT 1 FROM tank.trigger_bot_sources newer
                   WHERE newer.tenant_id=p.tenant_id AND newer.kind='faq' AND newer.status<>'removed'
                   AND lower(trim(newer.content::jsonb->>'question'))=lower(trim(p.question))
                   AND (newer.created_at,newer.id)>(s.created_at,s.id))) AND p.id=ANY($2::uuid[])''', tenant, list(scores)) if scores else []
        vectors = [{**dict(r), 'score': score(r) if r['kind'] == 'faq' else scores[r['id']]} for r in vector_rows]
        return [Passage(r['id'], tenant, r['source_id'], r['text'], r['url'], r['title'], r['kind'],
                        r['score'], r.get('sku'), r.get('approved_answer')) for r in fuse(tenant, lexical, vectors)]
