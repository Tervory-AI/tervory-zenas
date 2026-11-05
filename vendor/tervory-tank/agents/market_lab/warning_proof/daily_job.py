"""Exercise the existing queue claim/worker and daily resolution handler unchanged."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "agents/market_lab"), str(ROOT / "agents/_chassis")]
import asyncpg
from app import resolve
from chassis.jobs import JobContext
from chassis.worker import claim_next, run_claimed
from engines.ffl.warning_fixture import ENGINE
from fixture_proof.server import database_url
from warning_proof.seed import RESOLVED


async def main() -> None:
    conn = await asyncpg.connect(database_url())
    try:
        ident = await conn.fetchval(
            """INSERT INTO agent_market_lab.jobs (tenant_id,kind,input,status,run_at)
            VALUES ($1,'resolve',$2::jsonb,'queued','epoch') RETURNING id""",
            UUID(RESOLVED),
            json.dumps({"engine": ENGINE}),
        )
        job = await claim_next(conn, "market_lab")
        assert job["id"] == ident

        def context(row):
            return JobContext(
                agent="market_lab",
                tenant_id=str(row["tenant_id"]),
                job_id=str(row["id"]),
                kind=row["kind"],
                input=json.loads(row["input"]),
                files=None,
                tank=None,
                memory=None,
                browser=None,
            )

        await run_claimed(conn, "market_lab", job, {"resolve": resolve}, context)
        saved = await conn.fetchrow(
            "SELECT status,result,error FROM agent_market_lab.jobs WHERE tenant_id=$1 AND id=$2",
            UUID(RESOLVED),
            ident,
        )
        assert saved["status"] == "done", dict(saved)
        result = json.loads(saved["result"])
        print(
            json.dumps(
                {
                    "daily_job": "done",
                    "statuses": sorted(
                        w["status"] for w in result["warnings"]["warnings"]
                    ),
                }
            )
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
