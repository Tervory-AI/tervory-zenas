"""Citation keys, hashes, and version planning. No I/O."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from agents.zenas.pool.model import VersionHead

_TOKEN = re.compile(r"[^a-z0-9.()]+")


def citation_key(citation: str) -> str:
    """Fold a citation so '49 U.S.C. § 14706(a)' and '49 USC 14706(a)' match."""
    text = citation.lower().replace("§", " ")
    for src, dst in (
        ("fed. r. civ. p.", "frcp"),
        ("fed. r. evid.", "fre"),
        ("u.s.c.", "usc"),
        ("c.f.r.", "cfr"),
        ("del. c.", "delc"),
        ("f.3d", "f3d"),
        ("f.2d", "f2d"),
        ("f.4th", "f4th"),
        ("u.s.", "us"),
    ):
        text = text.replace(src, dst)
    text = text.replace("(", " ").replace(")", " ")
    text = _TOKEN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    folded = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()


def version_id_for(
    document_id: str,
    digest: str,
    effective_from: date | None = None,
    known_from: date | None = None,
) -> str:
    """Identity includes the span, so wording A can become current again after B."""
    start = effective_from.isoformat() if effective_from else "undated"
    seen = known_from.isoformat() if known_from else "unseen"
    return f"{document_id}:{digest[:16]}:{start}:{seen}"


def document_id_for(source_id: str, citation: str) -> str:
    return f"{source_id}:{citation_key(citation)}"


@dataclass(frozen=True)
class VersionPlan:
    action: str  # skip | insert
    close_ids: tuple[str, ...]
    close_on: date | None
    # Set when this row is born already closed by a later version.
    effective_to: date | None = None


def _span_start(
    effective_from: date | None,
    known_from: date | None,
    fallback: date,
) -> date:
    if effective_from is not None:
        return effective_from
    if known_from is not None:
        return known_from
    return fallback


def plan_version(
    existing: list[VersionHead],
    *,
    content_hash: str,
    effective_from: date | None,
    on: date,
    known_from: date | None = None,
) -> VersionPlan:
    """Skip text we already store, including a closed copy of the same edition.

    A law may go A, then B, then back to A. The restored A is a new row
    because its official start date is new. Re-feeding a closed row with
    the same text and the same official start does not insert another copy.
    A version that arrives late, with an earlier start than one already
    stored, is born closed at the next version's start. It does not leave
    two versions open.
    """
    open_rows = [row for row in existing if row.effective_to is None]
    if any(row.content_hash == content_hash for row in open_rows):
        return VersionPlan("skip", (), None)
    if effective_from is not None and any(
        row.content_hash == content_hash and row.effective_from == effective_from
        for row in existing
    ):
        return VersionPlan("skip", (), None)
    new_start = _span_start(effective_from, known_from, on)
    close_ids: list[str] = []
    later: list[date] = []
    for row in open_rows:
        row_start = row.effective_from if row.effective_from is not None else row.known_from
        if row_start is not None and new_start < row_start:
            later.append(row_start)
            continue
        close_ids.append(row.version_id)
    born_closed = min(later) if later else None
    return VersionPlan(
        "insert",
        tuple(close_ids),
        new_start if close_ids else None,
        born_closed,
    )


def applies(
    effective_from: date | None,
    effective_to: date | None,
    as_at: date | None,
    known_from: date | None = None,
) -> bool:
    """`as_at` None means the open version. A dated query needs a known start.

    The official effective date wins. When that date was never published,
    the first-seen date is the floor, so a dated search can still find the
    text from the day it was recorded.
    """
    if as_at is None:
        return effective_to is None
    start = effective_from if effective_from is not None else known_from
    if start is None or as_at < start:
        return False
    if effective_to is not None and as_at >= effective_to:
        return False
    return True


def flatten_text(sections: list) -> str:
    parts: list[str] = []

    def walk(nodes: list) -> None:
        for node in nodes:
            if node.heading:
                parts.append(node.heading)
            if node.text:
                parts.append(node.text)
            walk(node.children)

    walk(sections)
    return "\n".join(part.strip() for part in parts if part and part.strip())


# "15 U.S.C. § 80a-64" -> base "15 U.S.C. § 80a". Hyphen or en dash.
_SPLIT_CHILD = re.compile(r"^(?P<base>.+§\s*\d+[A-Za-z]*)[-\u2013]\S+$")


def split_parents(documents) -> dict[str, tuple[set[str], set[str]]]:
    """Section numbers that a parse split into children but did not produce itself.

    Returns {parent citation: (child content hashes, child titles)}. When an
    older parser collapsed "§ 80a-1" ... "§ 80a-64" onto "§ 80a", that row kept the
    text and heading of whichever child it saw last. A parent that the new parse
    does produce (a real "§ 300gg" beside "§ 300gg-1") is never listed.
    """
    produced = {str(doc.citation) for doc in documents}
    parents: dict[str, tuple[set[str], set[str]]] = {}
    for doc in documents:
        match = _SPLIT_CHILD.match(str(doc.citation or ""))
        if not match:
            continue
        base = match.group("base")
        if base in produced:
            continue
        hashes, titles = parents.setdefault(base, (set(), set()))
        if doc.content_hash:
            hashes.add(doc.content_hash)
        if doc.title:
            titles.add(doc.title)
    return parents
