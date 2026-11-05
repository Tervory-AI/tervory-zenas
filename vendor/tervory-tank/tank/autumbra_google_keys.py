"""Google public OIDC keys. Only Tank fetches; no tokens/claims enter this cache."""
from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urlsplit

DISCOVERY = 'https://accounts.google.com/.well-known/openid-configuration'


def lifetime(headers: dict) -> int:
    headers = {k.lower(): v for k, v in headers.items()}
    control = headers.get('cache-control', '').lower()
    if re.search(r'(?:^|,)\s*(?:no-cache|no-store)\b', control):
        return 0
    age = int(headers.get('age', '0'))
    match = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', control)
    return max(0, int(match[1]) - max(0, age)) if match else 0


async def read_json(url: str) -> tuple[dict, int]:
    from access import public_get
    raw, headers = await public_get(url, limit=262144)
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError('Invalid public keys')
    return body, lifetime(headers)


class GoogleKeys:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.keys: list[dict] = []
        self.expires = 0.0
        self.uri = ''
        self.discovery_expires = 0.0
        self.refresh_after = float('-inf')
        self.task = None
        self.lock = asyncio.Lock()

    async def refresh(self):
        now = self.clock()
        if not self.uri or now >= self.discovery_expires:
            discovery, ttl = await read_json(DISCOVERY)
            uri = discovery.get('jwks_uri')
            parsed = urlsplit(uri) if isinstance(uri, str) else None
            if (discovery.get('issuer') != 'https://accounts.google.com' or parsed is None
                    or parsed.scheme != 'https' or parsed.hostname not in {'www.googleapis.com', 'accounts.google.com'}
                    or parsed.port not in {None, 443} or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise ValueError('Invalid public keys')
            self.uri = uri
            self.discovery_expires = self.clock() + ttl
        body, ttl = await read_json(self.uri)
        keys = body.get('keys')
        if not isinstance(keys, list) or not 1 <= len(keys) <= 100:
            raise ValueError('Invalid public keys')
        cleaned = []
        for key in keys:
            if (not isinstance(key, dict) or key.get('kty') != 'RSA' or key.get('alg', 'RS256') != 'RS256'
                    or key.get('use', 'sig') != 'sig' or not all(isinstance(key.get(k), str) and key[k] for k in ('kid', 'n', 'e'))
                    or any(k in key for k in ('d', 'p', 'q', 'dp', 'dq', 'qi'))):
                raise ValueError('Invalid public keys')
            cleaned.append({k: key[k] for k in ('kty', 'kid', 'n', 'e')} | {'alg': 'RS256', 'use': 'sig'})
        self.keys, self.expires = cleaned, self.clock() + ttl
        return {'keys': [dict(k) for k in cleaned]}

    async def get(self, kid: str = '') -> dict:
        now = self.clock()
        async with self.lock:
            fresh = bool(self.keys) and now < self.expires
            known = not kid or any(k['kid'] == kid for k in self.keys)
            if fresh and known:
                return {'keys': [dict(k) for k in self.keys]}
            if self.task is not None and not self.task.done():
                task = self.task
            elif fresh and now < self.refresh_after:
                return {'keys': [dict(k) for k in self.keys]}
            elif now >= self.refresh_after:
                self.refresh_after = now + 60
                task = self.task = asyncio.create_task(self.refresh())
            else:
                raise ValueError('Public keys unavailable')
        # No shared lock is held across DNS/HTTP. Coalesce requests while fetching.
        return await asyncio.shield(task)


CACHE = GoogleKeys()
