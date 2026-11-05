"""Public layout facts only; client observations stay in the tenant store.

No HTML, prices, product names, tenant IDs, credentials or raw page URLs are
accepted by the shared store. Page fingerprints deduplicate public paths;
queries and fragments cannot manufacture additional confirming pages.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location("playbook_page_guard", Path(__file__).with_name("page_guard.py"))
_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_guard)
STRUCTURED, fingerprint_selector = _guard.STRUCTURED, _guard.fingerprint_selector

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


