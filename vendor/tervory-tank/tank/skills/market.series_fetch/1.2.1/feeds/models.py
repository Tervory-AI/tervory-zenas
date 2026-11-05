"""Source records. The licence is filled in before any adapter runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    kind: str
    url: str
    licence: str
    terms_checked_at: str
    cadence: str
    enabled: bool
    allow_bulk: bool
    adapter: str
    config: dict[str, Any] = field(default_factory=dict)
    # Env var name only. The value stays in the environment and is never stored.
    key_name: str = ""
    key_required: bool = False
