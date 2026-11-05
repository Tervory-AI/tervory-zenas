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


def require_commercial_intel(source: SourceSpec) -> None:
    """Intel is opt-in by exact reviewed source, not an untrusted licence label."""
    from feeds.licences import CISA_KEV, INTEL_CHECKED, OSV_GHSA
    registry = {"cisa_kev": CISA_KEV, "osv_ghsa": OSV_GHSA}
    require_copy(source)
    if (registry.get(source.id) != source.licence
            or source.terms_checked_at != INTEL_CHECKED
            or source.key_required or source.key_name):
        raise LicenceBlocked(f"{source.id}: no recorded keyless commercial-use licence")
