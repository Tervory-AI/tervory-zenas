"""Tenant-bound task/result states and feedback for the approved Workspace frames."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from command import Actor, identity


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["useful", "not_useful", "wrong"]
    note: str | None = Field(default=None, max_length=1000)


def present(row):
    item = dict(row)
    outcome = item.pop("outcome", None)
    if outcome in {"delivered", "unverified", "failed", "queued", "sent"}:
        item["status"] = "running" if outcome == "sent" else outcome
    elif item["status"] == "done":
        item["status"] = "unverified"
    if item["status"] in {"queued", "running", "delivered"}:
        item["error"] = None
    return item


SELECT = """SELECT t.id,t.title,t.agent,t.task_type,t.status,coalesce(t.result,x.content) AS result,t.error,
    coalesce(t.ledger_id,l.id) AS ledger_id,t.created_at,t.started_at,t.finished_at,l.outcome,
    l.feedback,l.feedback_kind,l.feedback_note,
    CASE WHEN l.cost_status='set' AND l.research_cost_status IS DISTINCT FROM 'unknown'
         THEN l.cost_usd ELSE NULL END AS cost_usd,
    (l.id IS NULL OR l.cost_status IS DISTINCT FROM 'set' OR l.research_cost_status IS NOT DISTINCT FROM 'unknown') AS cost_pending,
    CASE WHEN l.outcome IN ('delivered','unverified','failed') THEN l.latency_ms ELSE NULL END AS latency_ms
    FROM tank.tasks t LEFT JOIN LATERAL (
    SELECT l.* FROM tank.ledger l WHERE l.tenant_id=t.tenant_id
    AND (l.id=t.ledger_id OR (t.ledger_id IS NULL AND l.task_id=t.id))
    ORDER BY (l.id=t.ledger_id) DESC NULLS LAST,l.ts DESC,l.id LIMIT 1
    ) l ON true LEFT JOIN tank.transcripts x ON x.tenant_id=t.tenant_id AND x.ledger_id=l.id"""


def build_router(tank):
    router = APIRouter(prefix="/v1/workspace", tags=["workspace"])

    @router.get("/tasks")
    async def tasks(
        limit: int = Query(50, ge=1, le=200), actor: Actor = Depends(identity)
    ):
        async with tank.pool().acquire() as conn:
            rows = await conn.fetch(
                SELECT
                + " WHERE t.tenant_id=$1 ORDER BY t.created_at DESC,t.id LIMIT $2",
                actor.tenant_id,
                limit,
            )
        return {"tasks": [present(row) for row in rows]}

    @router.get("/results")
    async def results(
        limit: int = Query(50, ge=1, le=200), actor: Actor = Depends(identity)
    ):
        async with tank.pool().acquire() as conn:
            rows = await conn.fetch(
                SELECT
                + """ WHERE t.tenant_id=$1 AND NULLIF(coalesce(t.result,x.content),'') IS NOT NULL
                AND (l.outcome IN ('delivered','unverified') OR (l.id IS NULL AND t.status='done'))
                ORDER BY t.created_at DESC,t.id LIMIT $2""",
                actor.tenant_id,
                limit,
            )
        # No invented download links: only the result actually recorded by Tank.
        return {"results": [present(row) for row in rows]}

    @router.post("/tasks/{task_id}/feedback")
    async def feedback(
        task_id: uuid.UUID, body: Feedback, actor: Actor = Depends(identity)
    ):
        async with tank.pool().acquire() as conn:
            row = await conn.fetchrow(
                SELECT + " WHERE t.id=$1 AND t.tenant_id=$2", task_id, actor.tenant_id
            )
        if row is None:
            raise HTTPException(404, "No delivered result for this tenant.")
        if row["outcome"] != "delivered":
            raise HTTPException(409, "Feedback requires a delivered result.")
        answer = await tank.ledger_feedback(
            row["ledger_id"],
            tank.Feedback(
                tenant_id=actor.tenant_id,
                by=actor.user_id,
                note=body.note,
                feedback="accepted" if body.verdict == "useful" else "rejected",
                verdict=body.verdict,
            ),
        )
        return {**answer, "task_id": str(task_id), "verdict": body.verdict}

    return router


async def queued_task(tank, task_id, tenant_id, answer):
    """Bind a deferred receipt without regressing a worker that finished first."""
    import priority_queue

    async with tank.pool().acquire() as conn:
        await conn.execute(
            """UPDATE tank.tasks SET status='queued',ledger_id=$3,error=NULL
            WHERE id=$1 AND tenant_id=$2 AND status='running'""",
            task_id,
            tenant_id,
            answer.ledger_id,
        )
    priority_queue.kick(tank, answer.id, tenant_id)
