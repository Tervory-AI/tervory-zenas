"""Tenant-local embedding cache. Postgres remains the authority; Qdrant is an index.

A miss never changes today's answer. Only explicit founder approval plus current
99%/200 distinct-input evidence can authorize a hit, for that tenant/task/policy.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from typing import Any
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from admin import require_admin
from semantic_encoder import DIMENSIONS, ENCODER_ID, LocalEncoder

log = logging.getLogger("tank.semantic_cache")
CANDIDATE = "cache:embedding:" + hashlib.sha256(ENCODER_ID.encode()).hexdigest()[:16]
THRESHOLD = 0.90
_encoder = LocalEncoder()
_index = None


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def collection(tenant: uuid.UUID) -> str:
    return "tank_cache_" + uuid.UUID(str(tenant)).hex


def eligible(req: Any, policy: dict) -> bool:
    return (req.task_type != "command.turn" and policy.get("cache_scope") in {"tenant", "platform", "region"}
            and (not policy.get("matter_matching") or bool(req.case_ref)))


NEGATION = re.compile(r"\b(?:no|not|never|neither|nor|without|except|unless|cannot|exclude|excluding|excluded|avoid|deny|denied|false)\b|n['’]t\b", re.I)
NUMBER = re.compile(r"[+-]?\d+(?:[.,:/-]\d+)*%?|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b", re.I)


def guarded_input(messages: list[dict]) -> tuple[str, str] | None:
    # One short user turn only: do not silently truncate or flatten a conversation.
    users = [m["content"] for m in messages if m["role"] == "user"]
    if len(users) != 1 or any(m["role"] not in {"user", "system", "developer"} for m in messages):
        return None
    text = users[0]
    if not text.strip() or len(text) > 1000 or NEGATION.search(text):
        return None
    # Exact ordered numeric tokens; different quantities/dates/units cannot be
    # waved through by cosine similarity. Negated inputs never enter this index.
    return text, digest(NUMBER.findall(text.casefold()))


class VectorIndex:
    def __init__(self, client: Any = None) -> None:
        from qdrant_client import AsyncQdrantClient
        self.client = client if client is not None else AsyncQdrantClient(
            url=os.environ.get("QDRANT_URL", "http://qdrant:6333"),
            api_key=os.environ.get("QDRANT_API_KEY") or None, timeout=5,
        )
        self.ready: set[str] = set()

    async def ensure(self, name: str) -> None:
        from qdrant_client.models import Distance, VectorParams
        if name in self.ready:
            return
        if not await self.client.collection_exists(name):
            try:
                await self.client.create_collection(name, vectors_config=VectorParams(size=DIMENSIONS, distance=Distance.COSINE))
            except Exception:
                if not await self.client.collection_exists(name):
                    raise
        self.ready.add(name)

    async def search(self, tenant: uuid.UUID, vector: list[float], scope: dict) -> list:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        name = collection(tenant)
        if not await self.client.collection_exists(name):
            return []
        result = await self.client.query_points(
            collection_name=name, query=vector, limit=10, score_threshold=THRESHOLD,
            query_filter=Filter(must=[FieldCondition(key=k, match=MatchValue(value=v)) for k, v in scope.items()]),
            with_payload=True,
        )
        # Repeat the scope checks even if a buggy store returns an unfiltered hit.
        return [p for p in result.points if p.score >= THRESHOLD
                and all((p.payload or {}).get(k) == v for k, v in scope.items())]

    async def add(self, tenant: uuid.UUID, vector: list[float], payload: dict) -> None:
        from qdrant_client.models import PointStruct
        name = collection(tenant)
        await self.ensure(name)
        point = uuid.uuid5(tenant, digest([payload["region"], payload["task_type"], payload["context_hash"], payload["input_hash"], ENCODER_ID]))
        await self.client.upsert(name, points=[PointStruct(id=str(point), vector=vector, payload=payload)], wait=True)


def index() -> VectorIndex:
    global _index
    if _index is None:
        _index = VectorIndex()
    return _index


def scope(req: Any, region: str, identity: tuple, guard: str) -> dict:
    return dict(tenant_id=str(req.tenant_id), region=region, task_type=req.task_type,
                context_hash=identity[0], guard=guard, encoder_id=ENCODER_ID)


@dataclass
class Probe:
    status: str = "miss"
    content: str | None = None
    source_id: uuid.UUID | None = None
    similarity: float | None = None
    vector: list[float] | None = None
    guard: str | None = None


async def lookup(rt: Any, req: Any, policy: dict, identity: tuple, messages: list[dict]) -> Probe | None:
    if not eligible(req, policy):
        return None
    guarded = guarded_input(messages)
    if guarded is None:
        return Probe(status="guarded")
    text, guard = guarded
    probe = Probe(guard=guard)
    try:
        probe.vector = await asyncio.to_thread(_encoder.embed, text)
        points = await index().search(req.tenant_id, probe.vector, scope(req, rt.box_region(), identity, guard))
        for point in points:
            payload = point.payload or {}
            async with rt.pool().acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT c.ledger_id,t.content FROM tank.route_cache c
                    JOIN tank.ledger l ON l.id=c.ledger_id AND l.tenant_id=c.tenant_id
                    JOIN tank.transcripts t ON t.ledger_id=l.id AND t.tenant_id=c.tenant_id
                    WHERE c.tenant_id=$1 AND c.region=$2 AND c.task_type=$3 AND c.context_hash=$4
                      AND c.input_hash=$5 AND c.ledger_id=$6 AND c.expires_at>now()
                      AND l.region=c.region AND l.task_type=c.task_type
                      AND l.outcome='delivered' AND l.feedback IS DISTINCT FROM 'rejected'""",
                    req.tenant_id, rt.box_region(), req.task_type, identity[0], payload.get("input_hash"),
                    uuid.UUID(payload["ledger_id"]),
                )
            if row is not None:
                probe.status, probe.content = "hit", row["content"]
                probe.source_id, probe.similarity = row["ledger_id"], float(point.score)
                break
    except Exception as exc:
        # Do not echo prompts, service URLs or credentials from driver exceptions.
        log.warning("Semantic cache lookup unavailable (%s)", type(exc).__name__)
        probe.status, probe.content, probe.source_id = "unavailable", None, None
    return probe


async def evidence(conn: Any, tenant: uuid.UUID, region: str, task: str, policy_hash: str) -> dict:
    row = await conn.fetchrow(
        """SELECT count(*)::int AS samples,avg(agreement) AS agreement FROM (
          SELECT DISTINCT ON (o.input_hash) o.agreement
          FROM tank.semantic_observations o
          JOIN tank.ledger l ON l.id=o.ledger_id AND l.tenant_id=o.tenant_id
          JOIN tank.ledger s ON s.id=o.source_id AND s.tenant_id=o.tenant_id
          WHERE o.tenant_id=$1 AND o.region=$2 AND o.task_type=$3 AND o.policy_hash=$4
            AND o.encoder_id=$5 AND o.status='hit' AND o.agreement IS NOT NULL
            AND l.region=o.region AND s.region=o.region AND l.task_type=o.task_type AND s.task_type=o.task_type
            AND l.outcome='delivered' AND l.feedback IS DISTINCT FROM 'rejected'
            AND s.outcome='delivered' AND s.feedback IS DISTINCT FROM 'rejected'
          ORDER BY o.input_hash,o.created_at DESC,o.ledger_id
        ) distinct_inputs""", tenant, region, task, policy_hash, ENCODER_ID,
    )
    return dict(row)


def proven(value: dict) -> bool:
    return value["samples"] >= 200 and value["agreement"] is not None and float(value["agreement"]) >= .99


async def may_serve(rt: Any, req: Any, policy: dict, probe: Probe | None) -> bool:
    if probe is None or probe.status != "hit" or not eligible(req, policy):
        return False
    try:
        async with rt.pool().acquire() as conn:
            approved = await conn.fetchval(
                """SELECT approved FROM tank.semantic_approvals
                WHERE tenant_id=$1 AND region=$2 AND task_type=$3 AND policy_hash=$4 AND encoder_id=$5""",
                req.tenant_id, rt.box_region(), req.task_type, digest(policy), ENCODER_ID,
            )
            return bool(approved) and proven(await evidence(conn, req.tenant_id, rt.box_region(), req.task_type, digest(policy)))
    except Exception as exc:
        log.warning("Semantic cache approval unavailable (%s)", type(exc).__name__)
        return False


async def finish(rt: Any, req: Any, policy: dict, identity: tuple, messages: list[dict],
                 probe: Probe, ledger_id: uuid.UUID, content: str, cost: float) -> None:
    """Background-only evidence. The actual answer has already been selected."""
    try:
        agreement = None
        if probe.status == "hit":
            result = await rt._shadow(req, policy, messages, int(policy.get("max_tokens_out") or 1024),
                                      ledger_id, content, cost, cache_content=probe.content)
            agreement = result.get("agreement") if result else None
        async with rt.pool().acquire() as conn:
            await conn.execute(
                """INSERT INTO tank.semantic_observations
                (tenant_id,region,task_type,ledger_id,policy_hash,encoder_id,input_hash,status,
                 source_id,candidate_content,similarity,agreement,primary_cost_usd)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT(tenant_id,ledger_id) DO NOTHING""",
                req.tenant_id, rt.box_region(), req.task_type, ledger_id, digest(policy), ENCODER_ID,
                identity[1], probe.status, probe.source_id, probe.content, probe.similarity, agreement, cost,
            )
        if probe.vector is not None and probe.guard is not None:
            await index().add(req.tenant_id, probe.vector,
                              {**scope(req, rt.box_region(), identity, probe.guard),
                               "input_hash": identity[1], "ledger_id": str(ledger_id)})
    except Exception as exc:
        log.warning("Semantic cache evidence/index unavailable (%s)", type(exc).__name__)


def schedule(rt: Any, req: Any, policy: dict, identity: tuple, messages: list[dict],
             probe: Probe | None, ledger_id: uuid.UUID, content: str, cost: float) -> None:
    if probe is None or str(policy.get("model") or "").strip().lower().startswith("build-"):
        return
    task = asyncio.create_task(finish(rt, req, policy, identity, messages, probe, ledger_id, content, cost))
    rt._background.add(task)
    task.add_done_callback(rt._background.discard)


class Approval(BaseModel):
    tenant_id: uuid.UUID
    policy_hash: str
    encoder_id: str
    approved: bool = True


def build_router(rt: Any) -> APIRouter:
    router = APIRouter(prefix="/v1/semantic-cache", dependencies=[Depends(require_admin)])

    @router.get("/{task_type}")
    async def status(task_type: str, tenant_id: uuid.UUID) -> dict:
        policy = await rt.load_policy(task_type, tenant_id)
        async with rt.pool().acquire() as conn:
            data = await evidence(conn, tenant_id, rt.box_region(), task_type, digest(policy))
            approval = await conn.fetchval(
                """SELECT approved FROM tank.semantic_approvals WHERE tenant_id=$1 AND region=$2
                AND task_type=$3 AND policy_hash=$4 AND encoder_id=$5""",
                tenant_id, rt.box_region(), task_type, digest(policy), ENCODER_ID)
        return {**data, "tenant_id": str(tenant_id), "task_type": task_type,
                "policy_hash": digest(policy), "encoder_id": ENCODER_ID,
                "approved": bool(approval), "proven": proven(data), "excluded": task_type == "command.turn"}

    @router.post("/{task_type}/approval")
    async def approve(task_type: str, body: Approval, x_tervory_admin: str = Header(default="")) -> dict:
        if task_type == "command.turn":
            raise HTTPException(409, "command.turn is exact-match only")
        policy = await rt.load_policy(task_type, body.tenant_id)
        if body.policy_hash != digest(policy) or body.encoder_id != ENCODER_ID:
            raise HTTPException(409, "Semantic cache policy or encoder changed")
        async with rt.pool().acquire() as conn, conn.transaction():
            data = await evidence(conn, body.tenant_id, rt.box_region(), task_type, digest(policy))
            if body.approved and not proven(data):
                raise HTTPException(409, "Semantic cache requires 99% agreement over 200 distinct inputs")
            await conn.execute(
                """INSERT INTO tank.semantic_approvals
                (tenant_id,region,task_type,policy_hash,encoder_id,approved,approved_by)
                VALUES($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT(tenant_id,region,task_type,policy_hash,encoder_id)
                DO UPDATE SET approved=EXCLUDED.approved,approved_by=EXCLUDED.approved_by,updated_at=now()""",
                body.tenant_id, rt.box_region(), task_type, digest(policy), ENCODER_ID, body.approved, x_tervory_admin)
        return {**data, "approved": body.approved}

    return router


def batch_snapshot(policy: dict, identity: tuple, probe: Probe | None) -> dict | None:
    """Keep the enqueue-time observation across provider polling/restarts."""
    if probe is None:
        return None
    from dataclasses import asdict
    return {"policy": policy, "identity": identity, "probe": asdict(probe), "encoder_id": ENCODER_ID}


def schedule_batch(rt: Any, req: Any, snapshot: dict, ledger_id: uuid.UUID, content: str, cost: float) -> None:
    saved = snapshot.get("semantic_cache")
    if not saved or saved.get("encoder_id") != ENCODER_ID or rt._storage_mask(content) != content:
        return

    async def complete() -> None:
        try:
            policy, identity = saved["policy"], tuple(saved["identity"])
            probe = Probe(**saved["probe"])
            if probe.source_id is not None:
                probe.source_id = uuid.UUID(str(probe.source_id))
            await rt._cache_write(req, policy, identity, ledger_id)
            await finish(rt, req, policy, identity, snapshot["messages"], probe, ledger_id, content, cost)
        except Exception as exc:
            log.warning("Semantic batch evidence unavailable (%s)", type(exc).__name__)

    task = asyncio.create_task(complete())
    rt._background.add(task)
    task.add_done_callback(rt._background.discard)
