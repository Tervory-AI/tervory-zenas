"""Saved external input: two independently indexed kinds in one document."""
from datetime import datetime, timezone
from uuid import uuid4

from core.jobs import _call

from core.evidence import save_check


async def seed_mixed_source(ledger, tenant: str, *, fixtures: bool = False) -> str:
    at = datetime.now(timezone.utc)
    batch = str(uuid4())
    points = [{"series_id": "mixed-volume", "batch_id": batch, "title": "Harbor throughput",
               "date": f"{1960 + index // 12}-{index % 12 + 1:02d}-01", "value": str(14474 + index),
               "unit": "TEU", "geo": "US", "geo_level": "national", "frequency": "monthly", "revision": "v1", "source": "mixed_source_proof", "fetched_at": at.isoformat(),
               "region": "us-east", "currency": "none"}
              for index in range(123)]
    sid = tenant + ":mixed-volume"
    await _call(ledger.save_series, {"id": sid, "tenant_id": tenant, "source": "mixed_source_proof", "ref": "volume", "unit": "TEU", "engine": "test"})
    await _call(ledger.save_points, sid, tenant, points, stored=True, source="mixed_source_proof")
    events = [{"date": f"{1960 + index}-01-01", "title": "Harbor notice", "body": "Saved harbor notice",
               "geo": "US", "kind": "notice", "url": "https://publisher.test/notice"} for index in range(60)]
    check = await save_check(ledger, tenant, {"source_id": "mixed_source_proof", "name": "Harbor throughput and notices",
        "points": points, "events": events, "downloads": [], "fixture": fixtures, "note": ""}, at=datetime.now(timezone.utc))
    return check["document_id"]
