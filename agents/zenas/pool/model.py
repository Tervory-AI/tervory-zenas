"""Common document model for the Zenas knowledge pool.

The same types are used in both regions. Jurisdiction is a field, not a fork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Level = Literal["federal", "state", "national", "provincial", "international"]
Kind = Literal[
    "statute",
    "regulation",
    "case",
    "court_rule",
    "treaty",
    "form",
    "guidance",
]
BulkUse = Literal["allowed", "forbidden", "unknown"]


@dataclass
class Section:
    path: str
    heading: str
    text: str
    children: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "heading": self.heading,
            "text": self.text,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class LegalDocument:
    """One version of one instrument. `id` is stable across versions."""

    id: str
    jurisdiction: str
    level: Level
    body: str
    kind: Kind
    citation: str
    title: str
    date: date | None
    effective_from: date | None
    effective_to: date | None
    status: str
    url: str
    licence: str
    text: str
    sections: list[Section]
    source_id: str
    version_id: str = ""
    content_hash: str = ""
    # First day this text was seen. Separate from the official effective date.
    known_from: date | None = None


@dataclass
class Chunk:
    chunk_id: str
    version_id: str
    document_id: str
    jurisdiction: str
    path: str
    heading: str
    text: str
    citation: str
    citation_key: str
    ordinal: int


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    name: str
    region: str
    jurisdiction: str
    licence: str
    terms: str
    terms_url: str
    bulk_use: BulkUse
    store_full_text: bool
    canonical_url: str
    notes: str
    adapter: str
    # The founder turns a source off. Terms never do.
    enabled: bool = True
    # Environment variable name. Empty when the source has no key.
    # The value is never stored on this object.
    key_name: str = ""
    # When true, a live load is skipped until key_name is set.
    # A public source may still name an optional key and keep this false.
    key_required: bool = False


@dataclass(frozen=True)
class VersionHead:
    version_id: str
    content_hash: str
    effective_from: date | None
    effective_to: date | None
    known_from: date | None = None


@dataclass
class RunRecord:
    source_id: str
    last_update: datetime | None
    last_status: str
    documents: int
    detail: str
