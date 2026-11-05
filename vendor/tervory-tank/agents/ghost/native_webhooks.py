"""Provider-native Watch verification. Public Google keys come through Tank only."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode

import jwt

from .durable_state import locked_state
from .webhooks import WebhookRejected


def replay_once(identity: str, bucket: str, *, expires_at: float = 0, allow_duplicate: bool = False) -> None:
    key = hashlib.sha256(identity.encode()).hexdigest()
    now = time.time()
    try:
        path = Path(os.environ.get('DATA_DIR', '/data')) / 'webhook-replay.json'
        with locked_state(path) as state:
            for stale in [k for k, expiry in state.items() if expiry < now]:
                del state[stale]
            if key in state:
                if allow_duplicate:
                    return
                raise ValueError
            if len(state) >= 10000:
                raise ValueError
            state[key] = max(now + 600, expires_at)
    except (OSError, ValueError, TypeError):
        raise WebhookRejected('replayed or unavailable webhook state') from None


def verify_cloudflare_notification(body: bytes, header: str | None) -> None:
    secret = os.environ.get('AUTUMBRA_CLOUDFLARE_WEBHOOK_SECRET', '').strip()
    if not secret or not header or not hmac.compare_digest(secret.encode(), header.encode()):
        raise WebhookRejected('invalid webhook authentication')
    # Cloudflare documents a static secret header, not a signed timestamp.
    # Bound duplicate-body suppression without storing notification contents.
    replay_once('cloudflare:' + hashlib.sha256(body).hexdigest(), 'edge')


async def verify_google_push(body: bytes, authorization: str | None, tank, *, config: dict | None = None, allow_duplicate: bool = False) -> None:
    audience = (config['audience'] if config is not None else os.environ.get('AUTUMBRA_GOOGLE_PUSH_AUDIENCE', '')).strip()
    account = (config['service_account'] if config is not None else os.environ.get('AUTUMBRA_GOOGLE_PUSH_SERVICE_ACCOUNT', '')).strip()
    if not audience or not account or not authorization or not authorization.startswith('Bearer '):
        raise WebhookRejected('invalid webhook authentication')
    token = authorization[7:]
    try:
        if len(token) > 16384:
            raise ValueError
        header = jwt.get_unverified_header(token)
        kid = header.get('kid')
        if header.get('alg') != 'RS256' or not isinstance(kid, str) or not 1 <= len(kid) <= 200:
            raise ValueError
        # Never forward a bearer token, claims, subscription or body to Tank.
        keyset = await tank.call('GET', '/v1/ghost/google/signing-keys?' + urlencode({'kid': kid}))
        matches = [k for k in keyset['keys'] if k.get('kid') == kid]
        if len(matches) != 1:
            raise ValueError
        key = matches[0]
        if key.get('kty') != 'RSA' or key.get('alg', 'RS256') != 'RS256' or key.get('use', 'sig') != 'sig':
            raise ValueError
        public = jwt.PyJWK.from_dict(key, algorithm='RS256').key
        claims = jwt.decode(token, public, algorithms=['RS256'], audience=audience,
                            issuer=['accounts.google.com', 'https://accounts.google.com'],
                            options={'require': ['exp', 'iss', 'aud', 'email', 'email_verified']})
        if claims['aud'] != audience or claims['email'] != account or claims['email_verified'] is not True:
            raise ValueError
        # Tokens may legitimately be reused for many push messages. Deduplicate
        # the authenticated subscription/message ID, never the bearer token.
        envelope = json.loads(body)
        message = envelope['message']['messageId']
        subscription = envelope['subscription']
        if (not isinstance(message, str) or not 1 <= len(message) <= 200
                or not isinstance(subscription, str) or not 1 <= len(subscription) <= 1000):
            raise ValueError
        if config is not None and subscription != config['subscription']:
            raise ValueError
        replay_once('google:' + audience + ':' + account + ':' + subscription + ':' + message,
                    'mailbox', expires_at=float(claims['exp']), allow_duplicate=allow_duplicate)
    except Exception:
        # Provider bodies, token claims and transport errors never reach output.
        raise WebhookRejected('invalid webhook authentication') from None
