"""Storage gate. Terms are recorded as information. The founder decides use.

The only switch that stops a copy is the per-source ``enabled`` flag.
``bulk_use``, ``store_full_text`` and the terms text are not a block.
"""

from __future__ import annotations

from agents.zenas.pool.model import SourceSpec


class PoolError(Exception):
    pass


class LicenceError(PoolError):
    """Raised when text would be copied for a source that is not enabled."""


class FetchNotConfigured(PoolError):
    """Live fetch needs a key or a saved file this process does not have."""


def terms_recorded(source: SourceSpec) -> bool:
    """A source row needs a real licence and the publisher's terms, as information."""
    terms = (source.terms or "").strip()
    licence = (source.licence or "").strip()
    if not terms or not licence:
        return False
    return terms.lower() != "unknown"


def refusal_reason(source: SourceSpec) -> str | None:
    """None means text may be stored. The only switch is ``enabled``."""
    if not source.enabled:
        return "source disabled"
    return None


def may_store_text(source: SourceSpec) -> bool:
    return source.enabled
