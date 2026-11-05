"""Public layout facts only; client observations stay in the tenant store.

No HTML, prices, product names, tenant IDs, credentials or raw page URLs are
accepted by the shared store. Page fingerprints deduplicate public paths;
queries and fragments cannot manufacture additional confirming pages.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

from page_guard import STRUCTURED, fingerprint_selector

_HASH = r'[a-f0-9]{64}'
_FINGERPRINT = re.compile(rf'(?:tag:{_HASH}(?:\.{_HASH})?|scope:{_HASH}(?:\.{_HASH})?\|{_HASH}(?:\.{_HASH})?)')


def public_marker(layout: dict | None) -> dict | None:
    if not isinstance(layout, dict):
        return None
    primary = layout.get('primary')
    if primary in STRUCTURED:
        return {'primary': primary}
    hashed = layout.get('dom_hash')
    if primary == 'dom' and isinstance(hashed, str) and _FINGERPRINT.fullmatch(hashed):
        return {'primary': 'dom', 'dom_hash': hashed}
    selector = layout.get('dom')
    if primary != 'dom' or not isinstance(selector, str):
        return None
    if not selector.startswith(('scope:', 'tag:')):
        return None
    # Only digests of structural tokens cross the public-state boundary. A
    # selector can contain a custom class without persisting that raw value.
    return {'primary': 'dom', 'dom_hash': fingerprint_selector(selector)}


def public_page(url: str, page: dict) -> tuple[str, str] | None:
    """The browser only returns price-reader HTML for public, allowed pages."""
    if (not page.get('ok') or not page.get('html') or page.get('challenge')
            or page.get('blocked_by_check') or page.get('portal_item')
            or page.get('private') or page.get('login_status')
            or page.get('status') in {401, 403, 429}):
        return None
    try:
        parsed = urlsplit(page.get('url') or url)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
                or parsed.port not in {None, 80, 443} or '.' not in host
                or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
                or host.endswith(('.local', '.localhost', '.internal'))):
            return None
        try:
            ipaddress.ip_address(host)
            return None  # A playbook describes a shop hostname, never an IP.
        except ValueError:
            pass
    except (ValueError, UnicodeError, TypeError):
        return None
    # Scheme/query/fragment differences are not independent pages. No raw path
    # survives this call, including any client parameter embedded in the URL.
    fingerprint = hashlib.sha256((host + (parsed.path or '/')).encode()).hexdigest()
    return host, fingerprint


def _empty() -> dict:
    return {'generation': 0, 'marker': None, 'failures': 0, 'confirmations': {}}


def _snapshot(state: dict) -> dict:
    return {key: copy.deepcopy(state[key]) for key in ('generation', 'marker', 'failures')}


def _advance(state: dict, page_hash: str, generation: int, attempted: bool | None, confirmed: dict | None) -> bool:
    if state['generation'] != generation or not re.fullmatch('[a-f0-9]{64}', page_hash):
        return False  # An in-flight result cannot revive a retired generation.
    if state['marker'] is not None:
        if attempted is not None:
            state['failures'] = 0 if attempted else state['failures'] + 1
            if state['failures'] == 3:
                state.update(marker=None, generation=generation + 1, confirmations={})
        return True
    marker = public_marker(confirmed)
    if marker is None:
        return state['confirmations'].pop(page_hash, None) is not None
    state['confirmations'][page_hash] = marker
    if sum(value == marker for value in state['confirmations'].values()) >= 3:
        state.update(marker=marker, failures=0, generation=generation + 1, confirmations={})
    return True


_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]*$")

SQL_CREATE_PLAYBOOKS = """CREATE TABLE IF NOT EXISTS {schema}.domain_playbooks (
        domain text PRIMARY KEY,
        generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),
        marker jsonb,
        failures smallint NOT NULL DEFAULT 0 CHECK(failures BETWEEN 0 AND 3),
        confirmations jsonb NOT NULL DEFAULT '{{}}'::jsonb)"""
SQL_INSERT_DOMAIN = "INSERT INTO {schema}.domain_playbooks(domain) VALUES($1) ON CONFLICT DO NOTHING"
SQL_GET_PLAYBOOK = "SELECT generation,marker,failures FROM {schema}.domain_playbooks WHERE domain=$1"
SQL_LOCK_PLAYBOOK = "SELECT generation,marker,failures,confirmations FROM {schema}.domain_playbooks WHERE domain=$1 FOR UPDATE"
SQL_UPDATE_PLAYBOOK = """UPDATE {schema}.domain_playbooks SET generation=$2,
                    marker=$3::jsonb,failures=$4,confirmations=$5::jsonb WHERE domain=$1"""


def _sql(statement: str, schema: str) -> str:
    """Fixed statement text with the room's own schema name; every value stays a $n parameter."""
    if not _SCHEMA.fullmatch(schema):
        raise ValueError("invalid schema name")
    return statement.format(schema=schema)


async def ensure_tables(conn, schema: str) -> None:
    await conn.execute(_sql(SQL_CREATE_PLAYBOOKS, schema))


async def approved_advance(store, state, page_hash, generation, attempted, confirmed):
    runner = getattr(store, "skill_runner", None)
    result = await runner({"operation": "advance", "state": state, "page_hash": page_hash,
                           "generation": generation, "attempted": attempted,
                           "confirmed": confirmed}) if runner is not None else None
    if result is None:
        return _advance(state, page_hash, generation, attempted, confirmed)
    value = result["value"]
    state.clear()
    state.update(value["state"])
    return value["changed"]


class MemoryPlaybooks:
    async def get_playbook(self, domain: str) -> dict:
        if not hasattr(self, 'playbooks'):
            self.playbooks = {}
        return _snapshot(self.playbooks.setdefault(domain, _empty()))

    async def record_playbook(self, domain: str, page_hash: str, generation: int,
                              attempted: bool | None, confirmed: dict | None) -> None:
        await approved_advance(self, self.playbooks[domain], page_hash, generation, attempted, confirmed)


class PgPlaybooks:
    async def get_playbook(self, domain: str) -> dict:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            await conn.execute(_sql(SQL_INSERT_DOMAIN, self.schema), domain)
            row = await conn.fetchrow(_sql(SQL_GET_PLAYBOOK, self.schema), domain)
        result = dict(row)
        if isinstance(result['marker'], str):
            result['marker'] = json.loads(result['marker'])
        return result

    async def record_playbook(self, domain: str, page_hash: str, generation: int,
                              attempted: bool | None, confirmed: dict | None) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(_sql(SQL_LOCK_PLAYBOOK, self.schema), domain)
                if row is None:
                    return
                state = dict(row)
                for key in ('marker', 'confirmations'):
                    if isinstance(state[key], str):
                        state[key] = json.loads(state[key])
                if not await approved_advance(self, state, page_hash, generation, attempted, confirmed):
                    return
                await conn.execute(_sql(SQL_UPDATE_PLAYBOOK, self.schema),
                    domain, state['generation'], json.dumps(state['marker']) if state['marker'] else None,
                    state['failures'], json.dumps(state['confirmations']))
