"""Average cost per call for one skill version, from this box's ledger.

The skills list is not priced. The number is the mean of tank.ledger.cost_usd
for that id@version over the last 30 days, and only for the platform tenant
in this process's region. Another tenant's rows are not included.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

CALL_COST_SQL = """
SELECT skill,
       count(*)::int AS calls_30d,
       avg(cost_usd) FILTER (WHERE cost_status='set') AS avg_cost_usd
FROM tank.ledger
WHERE tenant_id = $1
  AND region = $2
  AND ts > now() - interval '30 days'
  AND skill IS NOT NULL
GROUP BY skill
"""


def skill_ref(skill_id: str, version: str) -> str:
    return f"{skill_id}@{version}"


def _avg(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def cost_by_skill(rows: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        skill = row["skill"]
        if not skill:
            continue
        calls = int(row["calls_30d"] or 0)
        out[str(skill)] = {
            "calls_30d": calls,
            "avg_cost_usd": _avg(row["avg_cost_usd"]) if calls > 0 else None,
        }
    return out


async def fetch_call_costs(conn: Any, tenant_id: uuid.UUID, region: str) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(CALL_COST_SQL, tenant_id, region)
    return cost_by_skill(rows)


def attach_call_costs(skills: list[dict[str, Any]], costs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    attached: list[dict[str, Any]] = []
    for skill in skills:
        row = dict(skill)
        found = costs.get(skill_ref(str(row["id"]), str(row["version"])))
        if found and int(found["calls_30d"]) > 0:
            row["calls_30d"] = int(found["calls_30d"])
            row["avg_cost_usd"] = found["avg_cost_usd"]
        else:
            row["calls_30d"] = 0
            row["avg_cost_usd"] = None
        attached.append(row)
    return attached
