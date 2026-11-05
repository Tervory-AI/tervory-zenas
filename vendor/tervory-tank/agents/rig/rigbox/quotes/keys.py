"""One origin-bound key issuer for the existing API and Tank retailer corridor."""

import html
import secrets
from typing import Any
from urllib.parse import urlsplit

from rigbox.service import RigError, hash_key


async def issue_embed_key(store: Any, tenant: str, origins: list[str]) -> dict:
    if not isinstance(origins, list) or not 1 <= len(origins) <= 10:
        raise RigError("invalid", "List one to ten exact HTTPS origins without paths.", 422)
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except (ValueError, TypeError, AttributeError):
            raise RigError("invalid", "List exact HTTPS origins without paths.", 422) from None
        if (not isinstance(origin, str) or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or any(c.isspace() for c in origin) or "*" in origin or port == 0):
            raise RigError("invalid", "List exact HTTPS origins without paths.", 422)
    key = "rk_pub_" + secrets.token_hex(24)
    await store.put_key(tenant, hash_key(key), "publishable", origins)
    return {"publishable_key": key, "origins": origins}


def embed_snippet(key: str, *, theme: str = "neutral") -> str:
    from .hosting import manifest

    if theme not in ("neutral", "custom-nation"):
        raise ValueError()
    from rigbox.rooms import api_base
    base = api_base()
    bundle = manifest()
    return (f'<script src="https://rig.tervory.com/widget/assets/{html.escape(bundle["file"], quote=True)}" '
            f'integrity="{html.escape(bundle["integrity"], quote=True)}" crossorigin="anonymous" defer></script>\n'
            f'<tervory-rig api-base="{base}" rig-key="{html.escape(key, quote=True)}" '
            f'theme="{theme}"></tervory-rig>')
