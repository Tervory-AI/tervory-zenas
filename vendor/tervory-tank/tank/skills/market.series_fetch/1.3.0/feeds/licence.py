"""Block a copy until the source's licence and terms date are on record."""

from __future__ import annotations

from feeds.models import SourceSpec


class LicenceBlocked(Exception):
    pass


def require_copy(source: SourceSpec) -> None:
    if not source.licence.strip() or not source.terms_checked_at.strip():
        raise LicenceBlocked(f"{source.id}: licence and terms date are required before a copy")
    if source.licence.startswith("Linked only.") or not source.allow_bulk:
        raise LicenceBlocked(f"{source.id}: terms do not allow a copy; linked only")
