"""Store-owned quote notices and receipts; no customer policy is assumed."""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from rigbox.service import RigError


def notice(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"text", "url"}:
        raise ValueError("The request is not valid.")
    text, url = value["text"], value["url"]
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("The request is not valid.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError, AttributeError):
        raise ValueError("The request is not valid.") from None
    if (not isinstance(url, str) or len(url) > 2048 or parsed.scheme != "https"
            or not parsed.hostname or parsed.username or parsed.password or port == 0
            or any(c.isspace() or ord(c) < 32 for c in url)):
        raise ValueError("The request is not valid.")
    version = hashlib.sha256(json.dumps([text, url], ensure_ascii=False).encode()).hexdigest()
    return {"text": text, "url": url, "version": version}


def receipt(configured: dict[str, str] | None, shown_version: str | None) -> dict[str, str] | None:
    if configured is None and shown_version is None:
        return None
    if configured is None or shown_version != configured["version"]:
        raise RigError("notice_changed", "The request is not valid.", 409)
    return {**configured, "submitted_at": datetime.now(timezone.utc).isoformat()}
