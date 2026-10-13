"""Licence gate. Text is stored only after the source row exists and bulk use is allowed."""

from __future__ import annotations

from agents.zenas.pool.model import SourceSpec


class PoolError(Exception):
    pass


class LicenceError(PoolError):
    """Raised when text would be copied against the recorded terms."""


class FetchNotConfigured(PoolError):
    """Live fetch needs a key or a saved file this process does not have."""


def refusal_reason(source: SourceSpec) -> str | None:
    """None means the enacted text may be stored. Otherwise a short reason."""
    terms = (source.terms or "").strip()
    if not terms or terms.lower() == "unknown":
        return "terms unknown"
    if source.bulk_use == "unknown":
        return "bulk use unknown"
    if source.bulk_use == "forbidden":
        return "bulk use forbidden"
    if source.bulk_use != "allowed":
        return "bulk use not allowed"
    if not source.store_full_text:
        return "full text must not be stored"
    if not (source.licence or "").strip():
        return "licence missing"
    return None


def may_store_text(source: SourceSpec) -> bool:
    return refusal_reason(source) is None
