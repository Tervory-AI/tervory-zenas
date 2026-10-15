"""One box, one region. The loader never reads or writes the other region's law."""

from __future__ import annotations

from agents.zenas.pool.licence import PoolError
from agents.zenas.pool.model import SourceSpec

KNOWN_REGIONS = frozenset({"us-east", "za"})


class RegionError(PoolError):
    """This deployment will not touch a source that belongs to another region."""


def known_region(region: str | None) -> bool:
    return region in KNOWN_REGIONS


def assert_box_region(region: str | None) -> str:
    """Refuse a missing or unknown box. Returns the region when it is known."""
    if not known_region(region):
        shown = region if region else "missing"
        raise RegionError(f"refusing to run: this box's region is {shown}")
    return region or ""


def bind_store(store, region: str | None) -> None:
    """Bind a store to one known region. A missing or unknown region is refused."""
    box = assert_box_region(region)
    current = getattr(store, "home_region", None)
    if not current:
        store.home_region = box
        return
    if not known_region(current):
        raise RegionError(f"store is bound to {current}, which is not a region")
    if current != box:
        raise RegionError(f"store is bound to {current}, not {box}")


def assert_source_region(source: SourceSpec, region: str | None) -> None:
    """Refuse before any fetch, parse, or write.

    A missing or unknown box region is refused, and so is a source whose own
    region is missing or unknown. A known source from the other region is refused.
    """
    box = assert_box_region(region)
    source_region = getattr(source, "region", None)
    if not known_region(source_region):
        shown = source_region if source_region else "missing"
        raise RegionError(f"refusing {source.source_id}: its region is {shown}")
    if source_region != box:
        raise RegionError(
            f"refusing {source.source_id}: it belongs to {source_region}, and this box is {box}"
        )
