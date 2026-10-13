"""Zenas knowledge pool.

Same package in the US and South Africa. TANK_REGION chooses which sources run.
Embeddings default to a no-op until Hermes (C7) supplies one.
"""

from agents.zenas.pool.catalogue import CATALOGUE, sources_for_region
from agents.zenas.pool.config import current_region, jurisdiction_for
from agents.zenas.pool.embed import NoOpEmbedder, collection_name
from agents.zenas.pool.nightly import packaged_snapshots, run_nightly
from agents.zenas.pool.status import pool_status

__all__ = [
    "CATALOGUE",
    "NoOpEmbedder",
    "collection_name",
    "current_region",
    "jurisdiction_for",
    "packaged_snapshots",
    "pool_status",
    "run_nightly",
    "sources_for_region",
]
