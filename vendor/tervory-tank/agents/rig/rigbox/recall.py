"""Finished searches, split into parts.

Search already splits a request at a relation: the words in front are the
part, and the words after are what it goes on. "and" separates two parts.
A later search for this retailer reuses a part when that part and what it
goes on are the same, on the same catalogue version. A part that is not
stored is ranked in code. Catalogue search always gets a decision from
that code, so this path does not call a model.
"""

from __future__ import annotations

import os
import re
from typing import Any

from rigbox.rank import _intent

# A later search only reads this many of the newest saved rows.
SEARCH_RECALL_CAP = 500
_DEFAULT_RECALL_DAYS = 90

_AND = re.compile(r" and ", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def parts_of(query: str) -> tuple[str, list[str]]:
    """The host, then each part, in the order the shopper wrote them."""
    query = " ".join(query.split())
    sought, host = _intent(query)
    host_n = _norm(host)
    source = sought if host else query
    pieces: list[str] = []
    seen: set[str] = set()
    for raw in _AND.split(source.strip()):
        part = _norm(raw)
        if not part or part in seen:
            continue
        seen.add(part)
        pieces.append(part)
    return host_n, pieces


def signature(pieces: list[str]) -> str:
    return "\n".join(pieces)


def part_query(part: str, host: str) -> str:
    if host:
        return f"{part} for {host}"
    return part


def recall_days() -> int:
    """How many days of saved searches are read and kept. Default 90."""
    raw = os.environ.get("RIG_SEARCH_RECALL_DAYS", "").strip()
    if not raw:
        return _DEFAULT_RECALL_DAYS
    try:
        days = int(raw)
    except ValueError:
        return _DEFAULT_RECALL_DAYS
    if days < 1:
        return _DEFAULT_RECALL_DAYS
    return days


def latest(
    rows: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """First row wins. Callers pass rows newest first."""
    wholes: dict[tuple[str, str], dict[str, Any]] = {}
    parts: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        host = str(row.get("host") or "")
        key = (host, str(row.get("signature") or ""))
        if key not in wholes:
            wholes[key] = row
        for part in row.get("parts") or []:
            name = str(part.get("part") or "")
            part_key = (host, name)
            if name and part_key not in parts:
                parts[part_key] = part
    return wholes, parts


def whole_hit(
    wholes: dict[tuple[str, str], dict[str, Any]],
    host: str,
    pieces: list[str],
    cap: int,
) -> dict[str, Any] | None:
    row = wholes.get((host, signature(pieces)))
    if row is None or int(row.get("limit") or 0) < cap:
        return None
    return row


def part_hit(
    parts: dict[tuple[str, str], dict[str, Any]],
    host: str,
    piece: str,
    cap: int,
) -> dict[str, Any] | None:
    row = parts.get((host, piece))
    if row is None or int(row.get("limit") or 0) < cap:
        return None
    return row
