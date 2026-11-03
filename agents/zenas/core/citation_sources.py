"""Keep the pool's canonical source links with checked quotes and saved memos."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import parse_qsl, urlsplit


def public_source_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not host or "." not in host or any(char.isspace() for char in value)
                or host in {"example.com", "example.org", "example.net"}
                or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid", ".example", ".example.com", ".example.org", ".example.net"))
                or any(key.lower() in {"key", "api_key", "apikey", "token", "access_token", "secret", "password"}
                       for key, _ in parse_qsl(parsed.query, keep_blank_values=True))):
            return ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return value
        return ""  # Legal source links use their public publisher's hostname.
    except ValueError:
        return ""


def cited_sources(statements: list[dict[str, Any]], checked: dict[str, Any], passages: list[dict[str, Any]],
                  citations: list[str], *, require_links: bool) -> list[dict[str, str]] | None:
    sources: dict[str, dict[str, str]] = {}
    for statement, result in zip(statements, checked.get("results", []), strict=True):
        citation = str(statement.get("citation") or "")
        if citation not in citations:
            continue
        passage = next((row for row in passages if row.get("doc_id") == result.get("doc_id")
                        and row.get("version_id", "") == result.get("version_id", "")), {})
        url = public_source_url(passage.get("url"))
        if require_links and not url:
            return None
        sources.setdefault(citation, {"citation": citation, "quote": str(statement.get("quote") or ""), **({"url": url} if url else {}),
                                      **{key: str(passage[key]) for key in ("version_date", "known_from", "attribution", "licence") if passage.get(key)}})
    if any(citation not in sources for citation in citations):
        return None
    return [sources[citation] for citation in dict.fromkeys(citations)]
