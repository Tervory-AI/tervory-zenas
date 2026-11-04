"""Send conservative official-court splits to Tank without any model call."""

from __future__ import annotations

import os
from typing import Any

import httpx

from agents.zenas.core.judgment_split import split_judgment
from agents.zenas.core.store import schema_statements
from agents.zenas.pool.adapters.za_archive import ZA_ARCHIVE_SOURCES
from agents.zenas.pool.deadline import check_deadline

_SPLIT_MAX = 500
_ROWS = """
SELECT DISTINCT ON (d.document_id)
       d.source_id,d.document_id,d.version_id,d.title,d.text
FROM zenas_pool.documents d
LEFT JOIN agent_zenas.judgment_split_exports e
  ON e.region='za' AND e.version_id=d.version_id
WHERE d.jurisdiction='za' AND d.kind='case'
  AND d.effective_to IS NULL AND d.status IS DISTINCT FROM 'superseded'
  AND d.source_id=ANY($1::text[]) AND e.version_id IS NULL
ORDER BY d.document_id,d.captured_at DESC
LIMIT $2
""".strip()


async def run_scheduled(conn: Any, *, region: str) -> dict[str, int]:
    counts = {"split": 0, "excluded": 0}
    if region != "za":
        return counts
    for statement in schema_statements():
        await conn.execute(statement)
    rows = await conn.fetch(_ROWS, sorted(ZA_ARCHIVE_SOURCES), _SPLIT_MAX)
    key = os.environ.get("TANK_KEY", "")
    if not key:
        return counts
    url = (
        os.environ.get("TANK_URL", "http://tank:8000").rstrip("/")
        + "/v1/training/zenas/court-text"
    )
    async with httpx.AsyncClient(
        timeout=60, follow_redirects=False, trust_env=False
    ) as client:
        for row in rows:
            check_deadline()
            result = split_judgment(str(row["title"] or ""), str(row["text"] or ""))
            payload = {
                "source_id": str(row["source_id"]),
                "document_id": str(row["document_id"]),
                "version_id": str(row["version_id"]),
                "status": result["status"],
                "reason": result.get("reason", ""),
            }
            if result["status"] == "split":
                payload.update(input=result["input"], output=result["output"])
            response = await client.post(url, json=payload, headers={"X-Tank-Key": key})
            check_deadline()
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError("Tank court-text writer unavailable")
            await conn.execute(
                """INSERT INTO agent_zenas.judgment_split_exports
                (region,source_id,document_id,version_id,status)
                VALUES('za',$1,$2,$3,$4)
                ON CONFLICT(region,version_id) DO NOTHING""",
                row["source_id"],
                row["document_id"],
                row["version_id"],
                result["status"],
            )
            counts[result["status"]] += 1
    return counts
