"""Read published platform matches. No fixture loader, writes, or model calls."""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from .session import watch_payload

_OFFLINE = re.compile(r"fixture|\[TEST\]|CVE-2024-0000|ThreatPayload|00000000-0000-0000-0000", re.I)


def _decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def published_matches(pool: Any, tenant_id: UUID, region: str, match_id: UUID | None = None) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.id, m.problem_id, m.region, m.mode, m.published, m.fairness, m.runs,
                   p.title, p.body
            FROM agent_zenas.moot_matches m
            JOIN agent_zenas.moot_problems p
              ON p.id = m.problem_id AND p.tenant_id = m.tenant_id AND p.region = m.region
            WHERE m.tenant_id = $1 AND m.region = $2 AND m.published = true
              AND ($3::uuid IS NULL OR m.id = $3)
            ORDER BY m.created_at DESC, m.id
            """,
            tenant_id, region, match_id,
        )
    matches = []
    for row in rows:
        body = _decoded(row["body"])
        if not isinstance(body, dict) or not body or body.get("fixture") or not row["title"]:
            continue
        match = {
            "match_id": str(row["id"]), "problem_id": row["problem_id"],
            "problem_title": row["title"], "region": row["region"], "mode": row["mode"],
            "published": row["published"], "fairness": _decoded(row["fairness"]),
            "runs": _decoded(row["runs"]),
        }
        if not match["published"] or _OFFLINE.search(json.dumps([match, body])):
            continue
        runs = match["runs"]
        if isinstance(runs, list) and runs and isinstance(runs[0], dict):
            match["cost_usd"] = runs[0].get("cost_usd")
        # A nonempty stored problem is mandatory; watch_payload must never load a fixture.
        matches.append(watch_payload(match, body))
    return matches
