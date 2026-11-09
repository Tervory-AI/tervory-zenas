"""Automatic Rig catalogue watches and durable delivery of confirmed readings.

All product data is fetched per tenant through Tank. No page-reading or job
scheduling implementation lives here: ordinary Tiger watches do that work.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from functools import lru_cache
from decimal import Decimal, InvalidOperation
from uuid import UUID

import httpx

from watch import BadWatch, DuplicateWatch, PgStore, SQL_ENSURE_DISPATCH, _known_stock, ensure_tables

async def ensure_rig_tables(conn, schema: str) -> None:
    await conn.execute(f'ALTER TABLE {schema}.watchlist ADD COLUMN IF NOT EXISTS rig_managed boolean NOT NULL DEFAULT false')
    await conn.execute(f'''CREATE TABLE IF NOT EXISTS {schema}.rig_targets (
        tenant_id uuid NOT NULL, sku text NOT NULL, url text NOT NULL,
        watch_id uuid NOT NULL REFERENCES {schema}.watchlist(id) ON DELETE CASCADE,
        observed_at timestamptz, last_value jsonb, pending jsonb,
        attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, sku))''')
    await conn.execute(f'CREATE INDEX IF NOT EXISTS rig_targets_url ON {schema}.rig_targets(tenant_id,url)')


# Rig Locate confirm re-reads one watched page now. Same 45 second browser
# budget as Tiger's renders (#458). Read only: no observation, alert or delivery.
CHECK_NOW_BUDGET_S = 45


async def check_now(store, tenant_id: str, url: str, render, extract, fetch_text, *,
                    budget: float = CHECK_NOW_BUDGET_S, clock=None) -> dict:
    """One live reading of a page Tiger already watches for this tenant.

    Answers available / unavailable only from an explicit schema.org stock
    state with a stated price (confirmed_value). Everything else is unknown.
    """
    from datetime import timezone
    from urllib.parse import urlsplit

    from watch import ROBOTS_TOKEN, robots_allows

    now = (clock or (lambda: datetime.now(timezone.utc)))()
    unknown = lambda reason: {"status": "unknown", "reason": reason, "url": url}  # noqa: E731
    watch = await store.get_watch(tenant_id, url)
    if watch is None or not watch.get("enabled"):
        return unknown("not_watched")
    parts = urlsplit(url)
    try:
        robots = await asyncio.wait_for(fetch_text(f"{parts.scheme}://{parts.netloc}/robots.txt"), 10)
    except Exception:
        return unknown("robots_unavailable")
    if not robots_allows(robots or "", url, ROBOTS_TOKEN):
        return unknown("robots")
    try:
        page = await asyncio.wait_for(render(url), budget)
    except Exception:
        return unknown("timeout")
    if not isinstance(page, dict) or not page.get("ok") or page.get("challenge") or not page.get("html"):
        return unknown("blocked" if isinstance(page, dict) and page.get("challenge") else "unreadable")
    try:
        extracted = await asyncio.wait_for(extract(page["html"], url), 15)
    except Exception:
        return unknown("unreadable")
    items = [item for item in (extracted or {}).get("items") or [] if isinstance(item, dict)]
    if len(items) != 1:
        return unknown("unreadable" if not items else "ambiguous")
    item = items[0]
    value = confirmed_value({"method": item.get("method") or extracted.get("method"),
                             "product_name": item.get("name"), "availability": item.get("availability"),
                             "price": item.get("price"), "currency": item.get("currency")})
    if value is None:
        return unknown("unconfirmed")
    result = {"status": "available" if value["active"] else "unavailable", "url": url,
              "checked_at": now.isoformat(), "method": str(item.get("method") or extracted.get("method") or "")}
    if value["active"]:
        result.update(price=value["price"], currency=value["currency"])
    if item.get("gtin"):
        result["gtin"] = str(item["gtin"])
    return result


def confirmed_value(observation: dict) -> dict | None:
    if (observation.get('method') in {None, 'blocked', 'robots', 'error', 'none', 'unread'}
            or observation.get('issue') or not observation.get('product_name')):
        return None
    stock = _known_stock(observation.get('availability'))
    currency = str(observation.get('currency') or '').upper()
    try:
        price = Decimal(str(observation.get('price')))
        if not price.is_finite() or price < 0 or stock is None or not re.fullmatch('[A-Z]{3}', currency):
            return None
        return {'price': str(price.quantize(Decimal('.01'))), 'currency': currency,
                'active': stock in {'InStock', 'LimitedAvailability'}}
    except (InvalidOperation, ValueError):
        return None


async def publish_price_changed(store, tenant: str, watch: dict, previous: dict | None, current: dict) -> None:
    """One committed price fact per product (or ordinary watch), irrespective of mail preferences."""
    try:
        before = confirmed_value(previous) if previous else None
        after = confirmed_value(current)
        if not before or not after or before['currency'] != after['currency'] or before['price'] == after['price']:
            return
        from chassis.events import publish
        skus = await store.rig_skus(tenant, watch['id']) if hasattr(store, 'rig_skus') else []
        for sku in skus or [None]:
            await publish(tenant, 'tiger.price.changed', f"{watch['id']}:{current['id']}:{sku or ''}", {
                'watch_id': str(watch['id']), 'observation_id': str(current['id']), 'sku': sku,
                'page_id': hashlib.sha256(watch['url'].encode()).hexdigest(),
                'previous_price': before['price'], 'price': after['price'],
                'currency': after['currency'], 'observed_at': current['ts'],
            })
    except Exception:
        logging.getLogger(__name__).warning('Price event unavailable; the check remains saved')


async def record_observation(conn, schema: str, observation: dict) -> None:
    """Called in the observation INSERT transaction: a crash cannot lose a send."""
    value = confirmed_value(observation)
    if value is None:
        return
    stamp = datetime.fromisoformat(observation['ts'].replace('Z', '+00:00'))
    payload = {**value, 'url': observation['url'], 'observed_at': stamp.isoformat()}
    await conn.execute(f'''UPDATE {schema}.rig_targets SET
        pending=CASE WHEN last_value IS DISTINCT FROM $3::jsonb THEN $4::jsonb ELSE pending END,
        attempts=CASE WHEN last_value IS DISTINCT FROM $3::jsonb THEN 0 ELSE attempts END,
        next_attempt_at=CASE WHEN last_value IS DISTINCT FROM $3::jsonb THEN now() ELSE next_attempt_at END,
        last_value=$3::jsonb, observed_at=$5
        WHERE tenant_id=$1 AND url=$2 AND (observed_at IS NULL OR observed_at<$5)''',
        UUID(observation['tenant_id']), observation['url'], json.dumps(value), json.dumps(payload), stamp)


class TankRig:
    def __init__(self, http):
        self.http = http

    async def request(self, method: str, path: str = '', **kwargs):
        async with asyncio.timeout(30):
            response = await self.http.request(method,
                os.environ.get('TANK_URL', 'http://tank:8000').rstrip('/') + '/v1/access/rig/tiger' + path,
                headers={'X-Tank-Key': os.environ.get('TANK_KEY', '')}, timeout=30, **kwargs)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('Invalid Rig response')
        return data

    async def tenants(self) -> list[str]:
        data = await self.request('GET', '/tenants')
        if not isinstance(data.get('tenants'), list):
            raise ValueError('Invalid Rig tenants')
        return sorted({str(UUID(tenant)) for tenant in data['tenants']})

    async def products(self, tenant: str) -> list[dict]:
        data = await self.request('GET', '/products', params={'tenant_id': tenant})
        if data.get('tenant_id') != tenant or not isinstance(data.get('products'), list):
            raise ValueError('Invalid Rig tenant snapshot')
        seen = set()
        for row in data['products']:
            if (not isinstance(row, dict) or not isinstance(row.get('sku'), str) or not 1 <= len(row['sku']) <= 120
                    or not isinstance(row.get('url'), str) or not row['url'] or row['sku'] in seen):
                raise ValueError('Invalid Rig product snapshot')
            seen.add(row['sku'])
        return data['products']

    async def send(self, tenant: str, sku: str, payload: dict) -> bool:
        data = await self.request('POST', json={**payload, 'tenant_id': tenant, 'sku': sku})
        if type(data.get('updated')) is not bool:
            raise ValueError('Invalid Rig delivery receipt')
        return data['updated']

    async def alert_context(self, tenant: str, url: str) -> dict:
        data = await self.request('GET', '/alert-context', params={'tenant_id': tenant, 'url': url})
        if data.get('tenant_id') != tenant or not isinstance(data.get('urls'), list):
            raise ValueError('Invalid Rig tenant snapshot')
        return data


class RigWatches:
    def __init__(self, watches: PgStore):
        self.watches = watches
        self.pool, self.schema = watches.pool, watches.schema
        self.receipt = {'sent': 0, 'pending': 0, 'failed': 0}

    async def discover(self, client: TankRig, *, dry_run: bool = False) -> int:
        tenants = await client.tenants()
        if dry_run:
            return len(tenants)
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            await conn.executemany(SQL_ENSURE_DISPATCH.format(schema=self.schema), [(UUID(t),) for t in tenants])
        return len(tenants)

    async def managed(self, tenant: str, url: str) -> bool:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            return bool(await conn.fetchval(f'''SELECT rig_managed FROM {self.schema}.watchlist
                WHERE tenant_id=$1 AND url=$2''', UUID(tenant), url))

    async def sync(self, tenant: str, client: TankRig) -> None:
        from watch import check_url
        products = await client.products(tenant)  # An unavailable snapshot never means an empty catalogue.
        known = {w['url']: w for w in await self.watches.list_watches(tenant)}
        wanted = []
        for row in products:
            url = row['url']
            if url not in known:
                try:
                    url = await asyncio.to_thread(check_url, url)
                except BadWatch:
                    continue
                # A concurrent dispatcher must not read it before the Rig binding
                # and managed flag commit together below.
                fields = dict(tenant_id=tenant, url=url, label=str(row.get('name') or '')[:200],
                              engine='', every='6h', enabled=False, threshold=0,
                              alert_price=False, alert_stock=False, alert_new_product=False,
                              alert_below_cost=False, alert_market_gap=False)
                try:
                    known[url] = await self.watches.create_watch(fields)
                except DuplicateWatch:
                    known[url] = await self.watches.get_watch(tenant, url)
            wanted.append((row['sku'], url, UUID(known[url]['id'])))
        fresh = set()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', 'tiger-rig:' + tenant)
                await conn.execute(f'DELETE FROM {self.schema}.rig_targets WHERE tenant_id=$1 AND NOT(sku=ANY($2::text[]))',
                                   UUID(tenant), [r[0] for r in wanted])
                for sku, url, watch_id in wanted:
                    changed = await conn.fetchval(f'''INSERT INTO {self.schema}.rig_targets(tenant_id,sku,url,watch_id)
                        VALUES($1,$2,$3,$4) ON CONFLICT(tenant_id,sku) DO UPDATE SET
                        url=EXCLUDED.url,watch_id=EXCLUDED.watch_id,observed_at=NULL,last_value=NULL,pending=NULL,
                        attempts=0,next_attempt_at=now()
                        WHERE rig_targets.url<>EXCLUDED.url OR rig_targets.watch_id<>EXCLUDED.watch_id RETURNING sku''',
                        UUID(tenant), sku, url, watch_id)
                    if changed:
                        fresh.add(url)
                    await conn.execute(f'UPDATE {self.schema}.watchlist SET rig_managed=true,enabled=true WHERE tenant_id=$1 AND id=$2',
                                       UUID(tenant), watch_id)
                await conn.execute(f'''UPDATE {self.schema}.watchlist w SET enabled=false
                    WHERE tenant_id=$1 AND rig_managed AND NOT EXISTS
                    (SELECT 1 FROM {self.schema}.rig_targets t WHERE t.tenant_id=w.tenant_id AND t.watch_id=w.id AND t.url=w.url)''', UUID(tenant))
                await conn.execute(f'''UPDATE {self.schema}.jobs j SET status='cancelled'
                    WHERE tenant_id=$1 AND kind='check' AND status='queued' AND EXISTS
                    (SELECT 1 FROM {self.schema}.watchlist w WHERE w.tenant_id=j.tenant_id
                     AND w.url=j.input->>'url' AND w.rig_managed AND NOT w.enabled)''', UUID(tenant))
        for url in fresh:
            if not await self.watches.check_open(tenant, url):
                await self.watches.enqueue_check(tenant, url)

    async def deliver(self, tenant: str, client: TankRig) -> int:
        # Reconcile before every retry as well as before every read.
        await self.sync(tenant, client)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(f'''SELECT sku,url,pending,attempts FROM {self.schema}.rig_targets
                WHERE tenant_id=$1 AND pending IS NOT NULL AND attempts<3 AND next_attempt_at<=now()
                ORDER BY next_attempt_at,sku LIMIT 20''', UUID(tenant))
        sent = 0
        for row in rows:
            payload = json.loads(row['pending']) if isinstance(row['pending'], str) else row['pending']
            # Commit the claim before the network boundary. The backoff exceeds
            # the whole-call timeout, so concurrent dispatches cannot send it.
            async with self.pool.acquire() as conn:
                claimed = await conn.fetchval(f'''UPDATE {self.schema}.rig_targets SET attempts=attempts+1,
                    next_attempt_at=now()+make_interval(secs=>60 * power(2, attempts)::integer)
                    WHERE tenant_id=$1 AND sku=$2 AND pending=$3::jsonb AND attempts<3 AND next_attempt_at<=now()
                    RETURNING sku''', UUID(tenant), row['sku'], json.dumps(payload))
            if claimed is None:
                continue
            try:
                async with asyncio.timeout(30):
                    accepted = await client.send(tenant, row['sku'], payload)
            except (httpx.HTTPError, ValueError, TimeoutError):
                continue
            async with self.pool.acquire() as conn:
                # Do not acknowledge a newer reading that arrived during this request.
                await conn.execute(f'''UPDATE {self.schema}.rig_targets SET pending=NULL,attempts=0,
                    last_value=CASE WHEN $4 THEN last_value ELSE NULL END
                    WHERE tenant_id=$1 AND sku=$2 AND pending=$3::jsonb''', UUID(tenant), row['sku'], json.dumps(payload), accepted)
            sent += int(accepted)
        async with self.pool.acquire() as conn:
            pending = await conn.fetchrow(f'''SELECT count(*) FILTER (WHERE attempts<3) AS pending,
                count(*) FILTER (WHERE attempts>=3) AS failed FROM {self.schema}.rig_targets
                WHERE tenant_id=$1 AND pending IS NOT NULL''', UUID(tenant))
        self.receipt = {'sent': sent, 'pending': pending['pending'], 'failed': pending['failed']}
        if self.receipt['pending'] or self.receipt['failed']:
            self.receipt['error'] = 'Tiger is not reachable right now.'
        return sent

# Competitor discovery deliberately shares the room's existing fetch/render path.
# Public host accounting is durable and shared across all workers and tenants.
class DiscoveryStopped(Exception):
    pass


class DiscoveryGate:
    def __init__(self, pool):
        self.pool = pool

    async def call(self, url, operation):
        from urllib.parse import urlsplit
        from datetime import timezone
        from watch import check_url
        safe = await asyncio.to_thread(check_url, url)
        parsed = urlsplit(safe)
        site = f'{parsed.scheme}://{parsed.netloc}'
        checkout = self.pool.acquire(autocommit=True) if hasattr(self.pool, 'raw') else self.pool.acquire()
        async with checkout as conn:
            await conn.execute('SELECT pg_advisory_lock(hashtextextended($1,1))', site)
            try:
                async with conn.transaction():
                    row = await conn.fetchrow('''INSERT INTO agent_tiger.discovery_limits(site) VALUES($1)
                        ON CONFLICT(site) DO UPDATE SET
                        requests=CASE WHEN discovery_limits.day<>(now() AT TIME ZONE 'UTC')::date THEN 0 ELSE discovery_limits.requests END,
                        human_check=CASE WHEN discovery_limits.day<>(now() AT TIME ZONE 'UTC')::date THEN false ELSE discovery_limits.human_check END,
                        day=(now() AT TIME ZONE 'UTC')::date RETURNING *''', site)
                    if row['human_check']:
                        raise DiscoveryStopped('human_check')
                    if row['requests'] >= 500:
                        raise DiscoveryStopped('page_limit')
                    if row['last_at']:
                        delay = 3 - (datetime.now(timezone.utc) - row['last_at']).total_seconds()
                        if delay > 0:
                            await asyncio.sleep(min(delay, 3))
                    await conn.execute('UPDATE agent_tiger.discovery_limits SET requests=requests+1,last_at=clock_timestamp() WHERE site=$1', site)
                # The reservation committed before transport: cancellation or a
                # crashed worker cannot refund an already-started public request.
                result = await operation()
                if isinstance(result, dict) and (result.get('challenge') or result.get('status') in {401,403,429}):
                    await conn.execute('UPDATE agent_tiger.discovery_limits SET human_check=true WHERE site=$1', site)
                return result
            finally:
                await asyncio.shield(conn.execute('SELECT pg_advisory_unlock(hashtextextended($1,1))', site))

    async def block(self, site):
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE agent_tiger.discovery_limits SET human_check=true WHERE site=$1', site)


class DiscoveryLinks:
    """Bounded links and GET search forms; never submit credentials or a POST."""
    @staticmethod
    def parse(html, base):
        from html.parser import HTMLParser
        from urllib.parse import urljoin
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links, self.forms, self.form = [], [], None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'a' and attrs.get('href') and len(self.links) < 1000:
                    self.links.append(urljoin(base, attrs['href']))
                if tag == 'form' and attrs.get('method', 'get').lower() == 'get':
                    self.form = {'action': urljoin(base, attrs.get('action') or base), 'fields': {}, 'query': None}
                if tag == 'input' and self.form is not None:
                    name = attrs.get('name', '')
                    if attrs.get('type') == 'search' or name in {'q','s','search','query','search_query'}:
                        self.form['query'] = name
                    elif attrs.get('type') == 'hidden' and name:
                        self.form['fields'][name] = attrs.get('value', '')
            def handle_endtag(self, tag):
                if tag == 'form' and self.form is not None:
                    if self.form['query']:
                        self.forms.append(self.form)
                    self.form = None
        parser = Parser()
        parser.feed(html[:600000])
        return parser.links, parser.forms


# Hints only decide whether to ask the existing browser human_check detector.
# They never mark a host blocked by themselves.
_DISCOVERY_CHALLENGE_HINT = re.compile(
    r'captcha|turnstile|(?:verify|confirm|prove).{0,30}human|checking your browser|'
    r'security check|cf-error-details|sorry.{0,20}blocked|your request.{0,20}blocked', re.I)


def _discovery_robot_normalize(value):
    def unescape(match):
        char = chr(int(match[1], 16))
        return char if char.isascii() and (char.isalnum() or char in '-._~') else match[0].upper()
    return re.sub(r'%([0-9a-fA-F]{2})', unescape, value)


@lru_cache(maxsize=64)
def _discovery_robot_rules(body, token):
    # Public policy only. Parsing/compilation happens once, rather than once
    # for every loc in a large sitemap. A changed body is a different key.
    from watch import _robot_groups, _rules_for
    groups = _robot_groups(body)
    rules = _rules_for(groups, token)
    if rules is None:
        rules = [rule for agents, group in groups if '*' in agents for rule in group]
    compiled = []
    for kind, rule in rules:
        if not rule:
            continue
        rule = _discovery_robot_normalize(rule)
        end = rule.endswith('$')
        pattern = rule[:-1] if end else rule
        regex = re.compile('^' + '.*'.join(re.escape(part) for part in pattern.split('*')) + ('$' if end else ''))
        compiled.append((regex, len(pattern.replace('*', '').encode()), kind == 'allow'))
    return tuple(compiled)


def discovery_robots_allows(body, url, token='tervorytiger'):
    """Discovery-only RFC 9309 path/query matching; ordinary watches unchanged."""
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    target = _discovery_robot_normalize((parsed.path or '/') + ('?' + parsed.query if parsed.query else ''))
    return max(((length, allow) for regex, length, allow in _discovery_robot_rules(body, token)
                if regex.match(target)), default=(0, True))[1]


def discovery_same(site, url):
    """Syntactic filtering only. Transport validates public DNS at fetch time."""
    from urllib.parse import urlsplit
    try:
        a, b = urlsplit(site), urlsplit(url)
        return (b.scheme == 'https' and not b.username and not b.password
                and (a.hostname.lower(), a.port or 443) == (b.hostname.lower(), b.port or 443)
                and not b.path.lower().endswith('.pdf'))
    except (ValueError, AttributeError):
        return False


def discovery_search_url(url):
    from urllib.parse import urlsplit, parse_qsl
    parsed = urlsplit(url)
    return (parsed.path.rstrip('/').lower() == '/search'
            or bool({'q', 's', 'search', 'query', 'search_query'} & {k for k, _ in parse_qsl(parsed.query)}))


def discovery_sitemap(source, limit=10000):
    from io import StringIO
    from xml.etree import ElementTree
    locations, kind, cut = [], '', False
    try:
        for event, element in ElementTree.iterparse(StringIO(source), events=('start', 'end')):
            tag = element.tag.rsplit('}', 1)[-1]
            if not kind:
                kind = tag
            if event == 'end':
                if tag == 'loc' and element.text:
                    locations.append(element.text.strip())
                    if len(locations) >= limit:
                        cut = True
                        break
                element.clear()
    except ElementTree.ParseError:
        cut = True
    return kind, locations, cut


class CatalogueDiscovery:
    """One transport operation per durable slice; all traversal state is JSON."""
    def __init__(self, fetch, render, gate, publish):
        self.fetch, self.render, self.gate, self.publish = fetch, render, gate, publish

    async def step(self, sites, products, cursor=None):
        from urllib.parse import urljoin, urlencode, urlsplit, urlunsplit, parse_qsl
        state = cursor or {'site_index': 0, 'sites': [], 'active': None}
        if state['site_index'] >= len(sites):
            return state, True
        site = sites[state['site_index']]
        if state['active'] is None:
            state['active'] = {'robots': '', 'queue': [{'kind': 'robots', 'url': site + '/robots.txt'}],
                               'visited': [], 'confirmed': [], 'forms': [], 'pages': 0, 'warnings': []}
        active = state['active']
        def allowed(url):
            return (discovery_same(site, url)
                    and all(discovery_robots_allows(active['robots'], url, token) for token in ('tervorytiger', 'tervory')))
        def enqueue(kind, urls, **extra):
            pending = [{'kind': kind, 'url': url, **extra} for url in dict.fromkeys(urls) if allowed(url)]
            capacity = max(0, 10000 - len(active['queue']))
            if len(pending) > capacity:
                active['warnings'].append({'url': site, 'reason': 'candidate_limit'})
            active['queue'][0:0] = pending[:capacity]
        def finish(status):
            state['sites'].append({'site': site, 'status': status, 'pages': active['pages'], 'warnings': active['warnings']})
            state['site_index'] += 1
            state['active'] = None
        try:
            # Skip bounded local work without occupying a worker for a request.
            for _ in range(1000):
                if not active['queue']:
                    index = active.get('search_index', 0)
                    if active.get('home_finished') and index < len(products):
                        product = products[index]
                        active['search_index'] = index + 1
                        raw_gtin = product.get('gtin')
                        gtin = re.sub(r'[\s-]', '', raw_gtin) if isinstance(raw_gtin, str) else ''
                        if (product['sku'] in active['confirmed']
                                or not re.fullmatch(r'[0-9]{8}|[0-9]{12,14}', gtin) or not gtin.strip('0')):
                            continue
                        # Same GS1 check digit as Tank's tiger_gtin identity rule.
                        total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(gtin[:-1])))
                        if (10 - total % 10) % 10 != int(gtin[-1]):
                            continue
                        searches = [site + '/search?' + urlencode({'q': gtin}), site + '/?' + urlencode({'s': gtin, 'post_type': 'product'})]
                        for form in active['forms'][:20]:
                            parsed = urlsplit(form['action'])
                            fields = {**dict(parse_qsl(parsed.query)), **form['fields'], form['query']: gtin}
                            searches.append(urlunsplit(parsed._replace(query=urlencode(fields), fragment='')))
                        active['queue'].extend({'kind': 'search', 'url': target, 'sku': product['sku']} for target in dict.fromkeys(searches) if allowed(target))
                        # Skip unusable/finished SKUs in this bounded local loop;
                        # no job row or one-second wait is needed per empty SKU.
                        continue
                    finish('completed')
                    return state, state['site_index'] >= len(sites)
                action = active['queue'].pop(0)
                kind, url = action['kind'], action['url']
                if kind != 'robots' and not allowed(url):
                    continue
                if action.get('sku') in active['confirmed']:
                    continue
                key = kind + ':' + url
                if key in active['visited']:
                    continue
                active['visited'].append(key)
                break
            else:
                return state, False
            if kind == 'publish':
                matched = await self.publish(site, url, action['html'], False)
                active['confirmed'] = list(set(active['confirmed']) | {m['sku'] for m in matched.get('matches', []) if m.get('state') == 'confirmed'})
                return state, False
            if kind == 'home':
                active['home_finished'] = True
            response = await self.gate.call(url, lambda: self.fetch(url) if kind in {'robots', 'map'} else self.render(url))
            if response.get('challenge') or response.get('status') in {401, 403, 429}:
                await self.gate.block(site)
                await self.publish(site, url, '', True)
                finish('human_check')
                return state, state['site_index'] >= len(sites)
            if kind == 'robots':
                if response.get('status') not in {200, 404}:
                    finish('unavailable')
                    return state, state['site_index'] >= len(sites)
                active['robots'] = response.get('text', '') if response.get('status') == 200 else ''
                maps = [urljoin(site + '/', m[1]) for m in re.finditer(r'^\s*Sitemap:\s*(\S+)', active['robots'], re.I | re.M)]
                enqueue('map', maps[:500] + [site + '/sitemap.xml'])
                if _DISCOVERY_CHALLENGE_HINT.search(active['robots']):
                    enqueue('probe', [url])
                active['queue'].append({'kind': 'home', 'url': site + '/'})
                return state, False
            if kind == 'map':
                source = response.get('text', '')
                if response.get('status') != 200:
                    active['warnings'].append({'url': url, 'reason': 'sitemap_unavailable'})
                    return state, False
                if (re.search(r'<(?:!doctype\s+html|html|form)\b', source, re.I) or _DISCOVERY_CHALLENGE_HINT.search(source)):
                    if _DISCOVERY_CHALLENGE_HINT.search(source):
                        enqueue('probe', [url])
                    else:
                        active['warnings'].append({'url': url, 'reason': 'sitemap_html'})
                    return state, False
                root, locations, cut = discovery_sitemap(source)
                if cut or response.get('truncated'):
                    active['warnings'].append({'url': url, 'reason': 'sitemap_cut'})
                if root == 'sitemapindex':
                    enqueue('map', [urljoin(url, loc) for loc in locations[:500]])
                elif root == 'urlset':
                    enqueue('product', [urljoin(url, loc) for loc in locations if not discovery_search_url(loc)])
                return state, False
            active['pages'] += 1
            if response.get('reason') == 'redirect' or response.get('status') in {301, 302, 303, 307, 308}:
                target = urljoin(url, response.get('location') or '')
                if kind in {'search', 'product'} and target != url and not discovery_search_url(target):
                    enqueue('product', [target], **({'sku': action['sku']} if action.get('sku') else {}))
                return state, False
            if not response.get('ok') or response.get('url', url).rstrip('/') != url.rstrip('/'):
                return state, False
            html = response.get('html', '')
            if kind == 'product':
                # Search and home responses never become identity evidence.
                if not discovery_search_url(url):
                    enqueue('publish', [url], html=html)
            elif kind == 'home':
                _, active['forms'] = DiscoveryLinks.parse(html, url)
                active['forms'] = active['forms'][:20]
                active['home_finished'] = True
            elif kind == 'search':
                links, _ = DiscoveryLinks.parse(html, url)
                enqueue('product', [link for link in links if not discovery_search_url(link)], sku=action['sku'])
        except DiscoveryStopped as exc:
            finish(str(exc))
        except (httpx.HTTPError, OSError, ValueError):
            active['warnings'].append({'url': url, 'reason': 'transport_error'})
            if kind == 'robots':
                finish('unavailable')
        return state, state['site_index'] >= len(sites)

    async def run(self, sites, products):
        # Offline traversal convenience; the production handler calls step once.
        state, done = None, False
        while not done:
            state, done = await self.step(sites, products, state)
        return {'sites': state['sites'], 'model_calls': 0}
