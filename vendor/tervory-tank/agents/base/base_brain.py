"""Bridge from the existing Base worker to its room-owned Hermes process."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import socket
import ipaddress
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Header, HTTPException
from pydantic import BaseModel


@dataclass
class ActiveBatch:
    pool: Any
    cfg: dict
    call: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ran: bool = False
    finished: bool = False
    job: asyncio.Task | None = None
    operation: str | None = None
    result: dict | None = None


active: dict[tuple[uuid.UUID, uuid.UUID], ActiveBatch] = {}


async def run_code_first(pool: Any, skills: Any, task_id: uuid.UUID, cfg: dict, call: Any) -> None:
    """Run the existing free passes before asking the brain to recover gaps."""
    from room import _run_batch

    tenant = uuid.UUID(str(cfg["tenant_id"]))
    if cfg.get("preview"):
        raise RuntimeError("Base's brain may use only approved skills")

    async def free_skill(skill_id: str, payload: dict) -> dict:
        return await call(skill_id, {**payload, "_base_brain_task": str(task_id), "_base_brain_free": True})

    await _run_batch(pool, task_id, cfg, free_skill)
    outcome = await results(ActiveBatch(pool, cfg, call), Task(tenant_id=tenant, task_id=task_id))
    # A pause/failure is not permission to spend. Completed, sourced code
    # answers need no model conversation or paid continuation.
    if outcome["pipeline_status"] != "done" or not outcome["unresolved"]:
        return
    await drive_batch(pool, skills, task_id, cfg, call, free_result=outcome)


async def drive_batch(pool, skills, task_id, cfg, call, *, free_result: dict | None = None):
    """Recover unresolved work; the queue enters through run_code_first."""
    tenant = uuid.UUID(str(cfg["tenant_id"]))
    identity = (tenant, task_id)
    if cfg.get("preview"):
        raise RuntimeError("Base's brain may use only approved skills")
    async def scoped_call(skill_id, payload):
        return await call(skill_id, {**payload, "_base_brain_task": str(task_id)})
    batch = ActiveBatch(pool, cfg, scoped_call)
    if free_result is not None:
        # The brain's pipeline_run tool receives the completed free pass. It
        # must not repeat reads or charge for work the queue already did.
        batch.ran, batch.operation, batch.result = True, "pipeline", free_result
    active[identity] = batch
    receipt = None
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent_base.tasks SET status='running',started_at=coalesce(started_at,now()),finished_at=NULL "
                "WHERE tenant_id=$1 AND id=$2", tenant, task_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(os.environ["BASE_BRAIN_URL"] + "/tasks",
                    headers=skills._headers(), json={"tenant_id": str(tenant), "task_id": str(task_id),
                        "home_region": os.environ.get("TANK_REGION", "us-east"), "batch": cfg["batch"],
                        "count": cfg["count"], "engine": cfg.get("engine")})
                deadline = time.monotonic() + 86400
                while response.status_code == 202:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Base brain polling deadline reached")
                    await asyncio.sleep(2)
                    response = await client.get(os.environ["BASE_BRAIN_URL"] + "/tasks/" + str(task_id),
                        headers=skills._headers(), params={"tenant_id": str(tenant),
                            "home_region": os.environ.get("TANK_REGION", "us-east")})
            if response.status_code != 200:
                raise RuntimeError("Base brain unavailable")
            receipt = response.json()
            if receipt.get("tenant_id") != str(tenant) or receipt.get("task_id") != str(task_id):
                receipt = None
                raise RuntimeError("Brain receipt scope mismatch")
            if receipt.get("status") != "done":
                raise RuntimeError("Brain did not finish")
        except Exception as exc:
            logging.getLogger("base.brain").warning("Brain flow failed (%s)", type(exc).__name__)
            # The unchanged pipeline remains available if memory, policy, the
            # agent or its service failed before it started any pipeline work.
            if not batch.ran:
                from room import _run_batch
                # A lost start response must not allow a late brain callback to
                # race the fallback. Cancel its job and close the pipeline gate.
                batch.ran, batch.operation = True, "fallback"
                try:
                    async with httpx.AsyncClient(timeout=5) as cancel_client:
                        await cancel_client.delete(os.environ["BASE_BRAIN_URL"] + "/tasks/" + str(task_id),
                            headers=skills._headers(), params={"tenant_id": str(tenant),
                                "home_region": os.environ.get("TANK_REGION", "us-east")})
                except Exception:
                    pass
                receipt = receipt if isinstance(receipt, dict) else {
                    "tenant_id": str(tenant), "task_id": str(task_id), "room": "base", "model_receipts": [], "steps": []}
                receipt.update(status="fallback", error="Brain unavailable before pipeline; default pipeline used.")
                receipt["steps"].append({"action": "pipeline_fallback", "reason": "brain failed before pipeline_run"})
                try:
                    response = await skills._http.get(skills._base + "/v1/agents/base/receipts",
                        headers=skills._headers(), params={"tenant_id": str(tenant), "task_id": str(task_id)}, timeout=15)
                    if response.status_code == 200:
                        receipt["model_receipts"] = response.json()["receipts"]
                except Exception:
                    pass
                previous = float(cfg.get("brain_spend") or 0)
                cost = max(previous, sum(float(row["cost_usd"]) for row in receipt.get("model_receipts", [])))
                cfg["brain_spend"] = cost
                cfg["model_spend"] = float(cfg.get("model_spend") or 0) - previous + cost
                cfg["brain_receipt"] = receipt
                await _run_batch(pool, task_id, cfg, scoped_call)
                return
            if not batch.finished:
                # Close callbacks before stopping the agent, then let any free
                # pass settle before using the existing paid continuation.
                async with batch.lock:
                    batch.finished = True
                    batch.operation = "finish_fallback"
                try:
                    async with httpx.AsyncClient(timeout=5) as cancel_client:
                        await cancel_client.delete(os.environ["BASE_BRAIN_URL"] + "/tasks/" + str(task_id),
                            headers=skills._headers(), params={"tenant_id": str(tenant),
                                "home_region": os.environ.get("TANK_REGION", "us-east")})
                except Exception:
                    pass
                if batch.job:
                    await asyncio.shield(batch.job)
                receipt = receipt or {"tenant_id": str(tenant), "task_id": str(task_id), "room": "base",
                    "steps": [], "model_receipts": []}
                batch.job = asyncio.create_task(run_phase(batch, Task(tenant_id=tenant, task_id=task_id, finish=True)))
                await asyncio.shield(batch.job)
                result = batch.result or {"pipeline_status": "failed"}
                receipt["steps"].append({"action": "pipeline_continue_fallback", "status": result["pipeline_status"],
                    "summary": result.get("summary"), "reason": "brain failed after pipeline_run"})
                receipt["status"] = "done" if result["pipeline_status"] == "done" else "failed"
                receipt["error"] = None if receipt["status"] == "done" else "Default pipeline continuation did not finish."
            else:
                if batch.job and not batch.job.done():
                    await asyncio.shield(batch.job)
                if not receipt:
                    receipt = {"tenant_id": str(tenant), "task_id": str(task_id), "room": "base", "status": "failed",
                        "error": "Brain unavailable after pipeline started; completed work retained.", "steps": [], "model_receipts": []}
            try:
                response = await skills._http.get(skills._base + "/v1/agents/base/receipts",
                    headers=skills._headers(), params={"tenant_id": str(tenant), "task_id": str(task_id)}, timeout=15)
                if response.status_code == 200:
                    receipt["model_receipts"] = response.json()["receipts"]
            except Exception:
                pass
        cfg["brain_receipt"] = receipt
        cost = sum(float(r["cost_usd"]) for r in receipt.get("model_receipts", []))
        previous_brain_cost = float(cfg.get("brain_spend") or 0)
        cost = max(previous_brain_cost, cost)
        cfg["brain_spend"] = cost
        cfg["model_spend"] = float(cfg.get("model_spend") or 0) - previous_brain_cost + cost
        failed = receipt.get("status") != "done"
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent_base.tasks SET input=$3::jsonb,status=$4,error=$5,ledger_id=$6,finished_at=now() "
                "WHERE tenant_id=$1 AND id=$2", tenant, task_id, json.dumps(cfg), "failed" if failed else "done",
                receipt.get("error"), uuid.UUID(receipt["model_receipts"][-1]["ledger_id"]) if receipt.get("model_receipts") else None)
        if failed:
            raise RuntimeError(receipt.get("error") or "Base's brain did not finish")
    finally:
        active.pop(identity, None)


class Task(BaseModel):
    tenant_id: uuid.UUID
    task_id: uuid.UUID
    finish: bool = False


class Retry(Task):
    record_id: uuid.UUID
    website: str


def selected(body: Task, key: str) -> ActiveBatch:
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not key or not hmac.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(401, "Base room key required.")
    batch = active.get((body.tenant_id, body.task_id))
    if batch is None:
        raise HTTPException(404, "No active batch for this tenant.")
    return batch


async def results(batch: ActiveBatch, body: Task) -> dict:
    async with batch.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id,raw,profile,status FROM agent_base.records WHERE tenant_id=$1 AND task_id=$2 "
            "AND (status<>'done' OR confidence<80) ORDER BY created_at LIMIT 20", body.tenant_id, body.task_id)
        task = await conn.fetchrow("SELECT status,result FROM agent_base.tasks WHERE tenant_id=$1 AND id=$2", body.tenant_id, body.task_id)
    unresolved = []
    for row in rows:
        raw, profile = row["raw"], row["profile"]
        raw = json.loads(raw) if isinstance(raw, str) else raw
        profile = json.loads(profile) if isinstance(profile, str) else profile
        def value(key):
            item = profile.get(key) or raw.get(key) or ""
            return str(item.get("value") or "") if isinstance(item, dict) else str(item)
        unresolved.append({"id": str(row["id"]), "name": value("name")[:120],
            "website": value("website")[:160], "status": row["status"]})
    return {"pipeline_status": task["status"], "summary": task["result"], "unresolved": unresolved,
            "skills": batch.cfg.get("refs", {}), "cost_usd": batch.cfg.get("model_spend", 0)}


async def pipeline_result(batch: ActiveBatch, body: Task) -> dict:
    result = await results(batch, body)
    # The code pipeline finished, but recall/recovery/remember still belong to
    # this task. Publish terminal status only when the complete receipt arrives.
    async with batch.pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_base.tasks SET status='running',finished_at=NULL WHERE tenant_id=$1 AND id=$2",
            body.tenant_id, body.task_id)
    return result


async def batch_receipts(conn, tenant: uuid.UUID) -> dict:
    """Expose the receipt through the existing Tank-proxied batches response."""
    rows = await conn.fetch(
        "SELECT id,status,error,input->>'batch' AS batch,input->'brain_receipt' AS receipt "
        "FROM agent_base.tasks WHERE tenant_id=$1 AND task_type='base.enrich' "
        "ORDER BY created_at DESC LIMIT 200", tenant)
    batches: dict[str, list] = {}
    for row in rows:
        receipt = row["receipt"]
        if isinstance(receipt, str):
            receipt = json.loads(receipt)
        batches.setdefault(row["batch"], []).append({"task_id": str(row["id"]), "status": row["status"],
                                                    "error": row["error"], "receipt": receipt})
    return batches


def free_call(batch: ActiveBatch):
    async def call(skill_id, payload):
        return await batch.call(skill_id, {**payload, "_base_brain_free": True})
    return call


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


async def check_website(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422, "A public website URL without credentials is required.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != (443 if parsed.scheme == "https" else 80):
        raise HTTPException(422, "Use a standard public web port.")
    try:
        answers = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not answers or not all(public_address(answer[4][0]) for answer in answers):
            raise ValueError("private address")
    except (ValueError, OSError):
        raise HTTPException(403, "Private or internal website refused.") from None


async def phase_result(batch: ActiveBatch) -> dict:
    if batch.job is None and batch.result is not None:
        return batch.result
    if batch.job is None or not batch.job.done():
        return {"pipeline_status": "running"}
    return batch.result or {"pipeline_status": "failed", "error": "Pipeline could not finish."}


async def run_phase(batch: ActiveBatch, body: Task, retry: Retry | None = None):
    from room import _run_batch
    try:
        if retry:
            async with batch.pool.acquire() as conn:
                changed = await conn.fetchval(
                    "UPDATE agent_base.records SET raw=jsonb_set(raw,'{website}',to_jsonb($4::text)),status='queued',error=NULL "
                    "WHERE tenant_id=$1 AND task_id=$2 AND id=$3 AND (status<>'done' OR confidence<80) RETURNING id",
                    body.tenant_id, body.task_id, retry.record_id, retry.website)
            if changed is None:
                raise RuntimeError("No unresolved record in this task")
        if body.finish:
            async with batch.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE agent_base.records SET status='queued',error=NULL WHERE tenant_id=$1 AND task_id=$2 "
                    "AND (status<>'done' OR confidence<80 OR passes->'2c' ? 'skipped' OR passes->'5' ? 'skipped')",
                    body.tenant_id, body.task_id)
        await _run_batch(batch.pool, body.task_id, batch.cfg, batch.call if body.finish else free_call(batch))
        batch.result = await pipeline_result(batch, body)
        if batch.result["pipeline_status"] == "queued":
            batch.result["pipeline_status"] = "paused"
    except Exception:
        batch.result = {"pipeline_status": "failed", "error": "Pipeline could not finish; completed work retained."}


def register(app):
    from fastapi.responses import JSONResponse

    @app.post("/brain/pipeline")
    async def pipeline(body: Task, x_tank_key: str = Header(default="")):
        batch = selected(body, x_tank_key)
        operation = "finish" if body.finish else "pipeline"
        async with batch.lock:
            if batch.operation == operation and (batch.job or batch.result is not None):
                result = await phase_result(batch)
                return JSONResponse(result, status_code=202 if batch.job and not batch.job.done() else 200)
            if batch.job and not batch.job.done():
                raise HTTPException(409, "Another pipeline operation is still running.")
            if body.finish:
                if not batch.ran or batch.finished:
                    raise HTTPException(409, "Free recovery must finish once before paid continuation.")
                batch.finished = True
            elif batch.ran:
                raise HTTPException(409, "This pipeline already ran.")
            else:
                batch.ran = True
            batch.operation, batch.result = operation, None
            batch.job = asyncio.create_task(run_phase(batch, body))
            return JSONResponse({"pipeline_status": "running"}, status_code=202)

    @app.get("/brain/pipeline")
    async def poll_pipeline(tenant_id: uuid.UUID, task_id: uuid.UUID, finish: bool = False,
                            x_tank_key: str = Header(default="")):
        body = Task(tenant_id=tenant_id, task_id=task_id, finish=finish)
        batch = selected(body, x_tank_key)
        if batch.operation != ("finish" if finish else "pipeline"):
            raise HTTPException(404, "No matching pipeline operation.")
        return await phase_result(batch)

    @app.post("/brain/retry")
    async def retry(body: Retry, x_tank_key: str = Header(default="")):
        batch = selected(body, x_tank_key)
        await check_website(body.website)
        async with batch.lock:
            if not batch.ran or batch.finished or (batch.job and not batch.job.done()):
                raise HTTPException(409, "The default pipeline must finish first.")
            batch.operation, batch.result = "retry:" + str(body.record_id), None
            batch.job = asyncio.create_task(run_phase(batch, body, retry=body))
            return JSONResponse({"pipeline_status": "running"}, status_code=202)

    @app.get("/brain/retry")
    async def poll_retry(tenant_id: uuid.UUID, task_id: uuid.UUID, record_id: uuid.UUID,
                        x_tank_key: str = Header(default="")):
        batch = selected(Task(tenant_id=tenant_id, task_id=task_id), x_tank_key)
        if batch.operation != "retry:" + str(record_id):
            raise HTTPException(404, "No matching retry.")
        return await phase_result(batch)
