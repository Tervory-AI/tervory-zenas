"""Same-box SQL and #388's pinned encoder for new records only."""

import os
import asyncpg
from qdrant_client import QdrantClient
from hermes.auth import agent_key_hashes
from hermes.record_index import RecordIndex
from hermes.records import Records

try:
    from hermes.memory_encoder import DIMENSIONS, ENCODER_ID, LocalEncoder
except ModuleNotFoundError:
    from tank.semantic_encoder import DIMENSIONS, ENCODER_ID, LocalEncoder


async def connect_records() -> Records:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Regional compose supplies only its local database. No cross-region fallback.
    from urllib.parse import urlsplit

    if urlsplit(url).hostname != "postgres":
        raise RuntimeError("Memory database must be on this box")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    index = RecordIndex(
        QdrantClient(
            url="http://qdrant:6333",
            api_key=os.environ.get("QDRANT_API_KEY") or None,
            timeout=10,
            check_compatibility=False,
            trust_env=False,
            follow_redirects=False,
        ),
        DIMENSIONS,
    )
    records = Records(
        pool, index, LocalEncoder(), ENCODER_ID, ["tank", *agent_key_hashes()]
    )
    try:
        await records.setup()
    except Exception:
        await pool.close()
        index.client.close()
        raise
    return records
