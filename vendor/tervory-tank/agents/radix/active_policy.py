"""Founder-owned active targets; no request or environment can extend this list."""
import json
from pathlib import Path

from passive_store import target_url

BOUNDS = {'max_concurrent': 2, 'max_requests_per_minute': 120,
          'max_response_bytes': 2_000_000, 'max_duration_seconds': 30}
LEASE_SECONDS = 1800
SCAN_SECONDS = 1200


def active_target(url: str) -> tuple[str, str]:
    target, domain = target_url(url)
    domains = json.loads(Path(__file__).with_name('active-domains.json').read_text())
    if not isinstance(domains, list) or domain not in domains:
        raise PermissionError('scan not authorized')
    return target, domain
