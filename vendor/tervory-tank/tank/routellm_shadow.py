"""Local RouteLLM evidence and founder-promoted, fingerprint-pinned serving."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import random
import threading
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import admin
from gold import runtime
import graduate

log = logging.getLogger(__name__)
REVISION = "86237e3df400762178ea98379477b8296e66d5e4"
SAMPLE_RATE = 0.1
_lock = threading.Lock()
_bert: Any = None
_pending = 0
router = APIRouter(dependencies=[Depends(admin.require_admin)])


@dataclass(frozen=True)
class Configuration:
    strong: str
    weak: str
    threshold: float

    @property
    def fingerprint(self) -> str:
        fields = [self.strong, self.weak, self.threshold, REVISION]
        return hashlib.sha256(json.dumps(fields).encode()).hexdigest()[:16]

    @property
    def candidate(self) -> str:
        # Selected easy turns must never be mistaken for proof that the weak
        # model can replace the strong one on EVERY turn of this task.
        return f"routellm:{self.weak}:{self.fingerprint}"


def configuration(policy: dict) -> Configuration | None:
    weak, threshold = policy.get("routellm_weak_model"), policy.get("routellm_threshold")
    strong = str(policy.get("model") or "").strip()
    if weak is None or threshold is None:
        return None
    weak = str(weak).strip()
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return None
    if (not strong or not weak or weak == strong or int(policy.get("rung") or 1) != 1
            or not math.isfinite(threshold) or not 0 <= threshold <= 1
            or any(graduate.router_cannot_serve(name) or graduate.is_skill(name) for name in (strong, weak))):
        return None
    return Configuration(strong, weak, threshold)


class Settings(BaseModel):
    tenant_id: uuid.UUID | None = None
    routellm_weak_model: str | None = Field(default=None, min_length=1, max_length=200)
    routellm_threshold: float | None = Field(default=None, ge=0, le=1)


@router.put("/v1/policy/{task_type}/routellm")
async def configure(task_type: str, body: Settings) -> dict:
    """Founder-only policy row configuration; NULL disables the experiment."""
    tank = runtime()
    async with tank.pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow("SELECT * FROM tank.policy WHERE task_type=$1 AND tenant_id IS NOT DISTINCT FROM $2::uuid FOR UPDATE", task_type, body.tenant_id)
        if row is None:
            raise HTTPException(404, "no such policy")
        if str(row["model"]).startswith("routellm:"):
            raise HTTPException(409, graduate.ROUTER_BLOCK)
        policy = {**dict(row), **body.model_dump()}
        both = body.routellm_weak_model is not None and body.routellm_threshold is not None
        neither = body.routellm_weak_model is None and body.routellm_threshold is None
        config = configuration(policy)
        if not neither and (not both or config is None):
            raise HTTPException(422, "set both RouteLLM fields, or clear both")
        if config:
            names = {r["name"] for r in await tank.list_models()}
            if config.strong not in names or config.weak not in names:
                raise HTTPException(422, "both models must be on the roster")
        await conn.execute("""UPDATE tank.policy SET routellm_weak_model=$3,routellm_threshold=$4,updated_at=now()
            WHERE task_type=$1 AND tenant_id IS NOT DISTINCT FROM $2::uuid""",
            task_type, body.tenant_id, config.weak if config else None, config.threshold if config else None)
    return {"ok": True}


def score(prompt: str) -> float:
    """Run the pinned upstream BERT classifier on CPU, offline and serialized."""
    global _bert
    with _lock:
        if _bert is None:
            root = Path(os.environ.get("TANK_ROUTELLM_PATH", "/opt/routellm"))
            if (root / "REVISION").read_text().strip() != REVISION:
                raise RuntimeError("RouteLLM checkpoint revision mismatch")
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            spec = importlib.util.spec_from_file_location("_tank_routellm_bert", root / "routellm_bert.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.torch.set_num_threads(2)
            _bert = module.BERTRouter(str(root / "checkpoint"))
            _bert.model.to("cpu").eval()
        value = float(_bert.calculate_strong_win_rate(prompt))
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("RouteLLM returned an invalid score")
    return value


# Bound added foreground latency. Cold loading/scoring may finish in the
# background; its slot remains occupied until the actual thread completes.
SERVE_TIMEOUT_SECONDS = 0.5


def promoted_configuration(policy: dict) -> Configuration | None:
    return configuration({**policy, "model": policy.get("previous_model")})


async def serve(tank: Any, policy: dict, messages: list[dict], *, tenant_id: uuid.UUID,
                priority: str = "standard", task_type: str | None = None,
                skill: str | None = None, caller: str | None = None, pool_read: bool = False) -> dict:
    """Resolve a promoted conditional route; every unavailable path uses its strong model."""
    global _pending
    strong = str(policy.get("previous_model") or "").strip()
    if not strong or graduate.router_cannot_serve(strong) or graduate.is_skill(strong):
        raise ValueError("Invalid promoted RouteLLM strong model")
    config = promoted_configuration(policy)
    decision = {"model": strong, "score": None,
                "threshold": config.threshold if config else None,
                "weak_model": config.weak if config else None,
                "config": config.fingerprint if config else None}
    if (config is None or policy.get("model") != config.candidate
            or not policy.get("promoted_at") or priority == "urgent"):
        return decision
    refusal_messages = list(messages)
    system = policy.get("system_prompt")
    if system and (not messages or messages[0] != {"role": "system", "content": system}):
        refusal_messages.insert(0, {"role": "system", "content": system})
    sent = "\n".join(str(message.get("content") or "") for message in refusal_messages)
    if tank.model_call_refusal(sent, config.weak, box=tank.box_region(), tenant_id=str(tenant_id),
                               task_type=task_type, skill=skill, caller=caller, pool_read=pool_read):
        return decision
    if _pending >= 4:
        return decision
    prompt = next((str(message.get("content") or "") for message in reversed(messages)
                   if message.get("role") == "user"), "")
    _pending += 1
    task = asyncio.create_task(asyncio.to_thread(score, prompt))

    def completed(work):
        global _pending
        _pending -= 1
        if not work.cancelled():
            work.exception()  # Consume errors after a caller timed out; never log prompt text.

    task.add_done_callback(completed)
    try:
        value = float(await asyncio.wait_for(asyncio.shield(task), SERVE_TIMEOUT_SECONDS))
        if not math.isfinite(value) or not 0 <= value <= 1:
            return decision
    except Exception as exc:
        log.warning("RouteLLM serving scorer unavailable (%s)", type(exc).__name__)
        return decision
    return {**decision, "model": config.strong if value >= config.threshold else config.weak, "score": value}


def serving_policy(policy: dict, decision: dict) -> dict:
    # Keep the JSON-safe decision through existing deferred/offpeak policy snapshots.
    # Clearing observation settings suppresses both ordinary shadow and observe().
    return {**policy, "model": decision["model"], "rung": 1, "candidate_model": None,
            "shadow_rate": 0, "routellm_weak_model": None, "routellm_threshold": None,
            "_routellm_served": dict(decision)}


async def record_served(tank: Any, tenant: uuid.UUID, ledger: uuid.UUID, decision: dict) -> None:
    async with tank.pool().acquire() as conn:
        await conn.execute("""UPDATE tank.ledger SET routellm_model=$3,routellm_score=$4,
            routellm_threshold=$5,routellm_weak_model=$6,routellm_config=$7,routellm_status='served'
            WHERE id=$1 AND tenant_id=$2""", ledger, tenant, decision["model"], decision["score"],
            decision["threshold"], decision["weak_model"], decision["config"])


async def status(tank: Any, tenant: uuid.UUID, ledger: uuid.UUID, value: str) -> None:
    async with tank.pool().acquire() as conn:
        await conn.execute("UPDATE tank.ledger SET routellm_status=$3 WHERE id=$1 AND tenant_id=$2", ledger, tenant, value)


async def observe(tank: Any, req: Any, policy: dict, messages: list[dict], max_tokens: int,
                  ledger_id: uuid.UUID, primary: str, cost: float, answered: str, cost_known: bool) -> None:
    """Background only. Any failure leaves the live answer and policy intact."""
    global _pending
    config = configuration(policy)
    if config is None:
        return
    entered = False
    try:
        if _pending >= 4:
            await status(tank, req.tenant_id, ledger_id, "busy")
            return
        _pending += 1
        entered = True
        # RouteLLM BERT's training/input contract uses the last user prompt.
        prompt = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        value = await asyncio.to_thread(score, prompt)
        chosen = config.strong if value >= config.threshold else config.weak
        async with tank.pool().acquire() as conn:
            await conn.execute("""UPDATE tank.ledger SET routellm_model=$3,routellm_score=$4,
                routellm_threshold=$5,routellm_weak_model=$6,routellm_config=$7,routellm_status='scored'
                WHERE id=$1 AND tenant_id=$2""", ledger_id, req.tenant_id, chosen, value,
                config.threshold, config.weak, config.fingerprint)
        if chosen != config.weak:
            return
        if answered != config.strong:
            await status(tank, req.tenant_id, ledger_id, "primary_fallback")
            return
        # Reuse the policy's configured shadow share, or sample 10% when the
        # founder enabled RouteLLM without an ordinary graduation candidate.
        rate = float(policy.get("shadow_rate") or SAMPLE_RATE)
        if random.random() >= rate:
            return
        if tank.model_call_refusal(json.dumps(messages), config.weak, box=tank.box_region(), tenant_id=str(req.tenant_id)):
            await status(tank, req.tenant_id, ledger_id, "provider_refused")
            return
        if not await tank._shadow_budget_open(req.task_type):
            await status(tank, req.tenant_id, ledger_id, "budget")
            return
        await tank._shadow(req, {**policy, "candidate_model": config.weak}, messages, max_tokens,
                           ledger_id, primary, cost, routellm=(config, cost_known))
    except Exception as exc:
        # Exceptions can include prompt text or signed download URLs: type only.
        log.warning("RouteLLM observation failed (%s)", type(exc).__name__)
        await status(tank, req.tenant_id, ledger_id, "unavailable")
    finally:
        if entered:
            _pending -= 1


async def save(tank: Any, req: Any, config: Configuration, ledger_id: uuid.UUID, primary: str,
               primary_cost: float, primary_known: bool, content: str | None, total_cost: float,
               receipt: dict, agreement: float | None, method: str, fields: dict, quality: dict | None = None) -> None:
    """Persist the existing shadow runner's result, with measured model cost."""
    from tenant_db import tenant_scope
    with tenant_scope(req.tenant_id):
        async with tank.pool().acquire() as conn, conn.transaction():
            parent = await conn.fetchrow("SELECT id FROM tank.ledger WHERE id=$1 AND tenant_id=$2", ledger_id, req.tenant_id)
            if parent is None:
                raise ValueError("shadow parent is not owned by this tenant")
            if receipt.get("_paid_ledger_id"):
                import paid_steps
                shadow_id = uuid.UUID(receipt["_paid_ledger_id"])
                async with paid_steps.connection(tank.PLATFORM_TENANT) as paid_conn:
                    await paid_conn.execute("""UPDATE tank.ledger SET routellm_model_cost_usd=$3,
                        routellm_parent_id=$4,routellm_sample_tenant=$5 WHERE tenant_id=$1 AND id=$2""",
                        tank.PLATFORM_TENANT, shadow_id, receipt.get("cost"), ledger_id, req.tenant_id)
            else:
                shadow_id = await conn.fetchval("""INSERT INTO tank.ledger
                    (tenant_id,region,agent,task_type,rung,model,priority,tokens_in,tokens_out,cost_usd,latency_ms,cache_hit,outcome,
                     routellm_model_cost_usd,routellm_parent_id,routellm_sample_tenant)
                    VALUES($1,$2,'shadow',$3,1,$4,'batch',$5,$6,$7,0,false,$8,$9,$10,$11) RETURNING id""",
                    tank.PLATFORM_TENANT, req.region or tank.DEFAULT_REGION, req.task_type+"#shadow", receipt.get("model", config.weak),
                    receipt.get("tokens_in", 0), receipt.get("tokens_out", 0), total_cost,
                    "delivered" if content is not None else "failed", receipt.get("cost"), ledger_id, req.tenant_id)
            await conn.execute("""INSERT INTO tank.shadow
                (ledger_id,tenant_id,task_type,primary_model,candidate_model,primary_content,candidate_content,
                 candidate_cost_usd,primary_cost_usd,agreement,method,fields,routellm_config,candidate_ledger_id,quality)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,$15::jsonb)""",
                ledger_id, req.tenant_id, req.task_type, config.strong, config.candidate, primary[:20000],
                (content or "")[:20000], total_cost, primary_cost, agreement, method, json.dumps(fields),
                config.fingerprint, shadow_id, json.dumps(quality or {}))
            await conn.execute("""UPDATE tank.ledger SET routellm_status=$3,routellm_primary_cost_known=$4
                WHERE id=$1 AND tenant_id=$2""", ledger_id, req.tenant_id,
                "sampled" if content is not None else "sample_failed", primary_known)
            if not receipt.get("_paid_ledger_id"):
                await tank._add_spend(conn, req.task_type, total_cost)


async def ladder(tank: Any, tenant: uuid.UUID | None, founder_mix: bool) -> dict[str, list[dict]]:
    """Existing ladder figures; no activation path for this shadow experiment."""
    async with tank.pool().acquire() as conn:
        rows = await conn.fetch("""SELECT s.task_type,s.candidate_model,count(*) AS n,avg(s.agreement) AS agreement,
            avg(c.routellm_model_cost_usd) AS cost
            FROM tank.shadow s JOIN tank.ledger l ON l.id=s.ledger_id AND l.tenant_id=s.tenant_id
            JOIN tank.ledger c ON c.id=s.candidate_ledger_id AND c.routellm_parent_id=l.id AND c.routellm_sample_tenant=s.tenant_id
            WHERE s.routellm_config IS NOT NULL AND l.region=$3
              AND (($1::uuid IS NOT NULL AND s.tenant_id=$1) OR $2::bool)
            GROUP BY s.task_type,s.candidate_model""", tenant, founder_mix, tank.box_region())
    result: dict[str, list[dict]] = {}
    for row in rows:
        result.setdefault(row["task_type"], []).append({
            "label": "Cheaper model", "name": row["candidate_model"], "current": False,
            "samples": row["n"], "agreement": float(row["agreement"]) if row["agreement"] is not None else None,
            "cost_per_call": float(row["cost"]) if row["cost"] is not None else None,
            "monthly_saving_usd": None,
        })
    return result
