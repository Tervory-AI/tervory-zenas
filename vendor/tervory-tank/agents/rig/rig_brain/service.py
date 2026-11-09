"""One bounded Hermes process per Rig task, with durable tenant-scoped results."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from rig_actor_context import reset_verified_actor, set_verified_actor
from brain.security import tenant_dir
from chassis.memory import MemoryClient
from chassis.sqlite_nonces import SQLiteNonces
from chassis.user_assertion import HEADER, signed_user
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from rig_memory import run_with_memory

from rig_brain.room import Question, authorize


@asynccontextmanager
async def lifespan(app):
    app.state.assertion_nonces = SQLiteNonces(Path(os.environ.get("DATA_DIR", "/data")) / "assertion-nonces.sqlite3")
    yield


app = FastAPI(lifespan=lifespan)
gate = asyncio.Semaphore(1)
jobs: dict[tuple[str, str], asyncio.Task] = {}


@app.post("/delegate")
async def delegate(body: Question, request: Request, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, body.home_region)
    actor = await signed_user(request, tenant_id=str(body.tenant_id), product="rig", required=True,
                              nonces=getattr(request.app.state, "assertion_nonces", None))
    await start(body, request, x_tank_key)
    job = jobs.get((str(body.tenant_id), str(body.task_id)))
    if job is not None:
        try:
            await asyncio.wait_for(asyncio.shield(job), timeout=60)
        except asyncio.TimeoutError:
            pass  # The durable task continues; a caller retry never resubmits it.
    value = await poll(body.task_id, body.tenant_id, body.home_region, request, x_tank_key)
    if isinstance(value, JSONResponse):
        value = json.loads(value.body)
    value.update(room="rig", user_id=actor.user_id, region=body.home_region)
    return {"result": {"content": value.get("summary", "")}, "receipt": value}


def directory(tenant: uuid.UUID, task: uuid.UUID) -> Path:
    root = tenant_dir(Path(os.environ.get("DATA_DIR", "/data")), str(tenant))
    work = root / "runs" / str(task)
    if work.is_symlink() or work.parent.is_symlink():
        raise HTTPException(409, "Invalid task directory.")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    return work


def save(work: Path, receipt: dict) -> None:
    temporary = work / "receipt.tmp"
    temporary.write_text(json.dumps(receipt, indent=2))
    temporary.replace(work / "receipt.json")


async def step(client, body: Question, action: str, status: str, count=None) -> dict:
    payload = {"id": str(uuid.uuid4()), "tenant_id": str(body.tenant_id), "task_id": str(body.task_id),
               "home_region": body.home_region, "action": action, "status": status, "count": count}
    response = await asyncio.wait_for(client.post(os.environ.get("TANK_URL", "http://tank:8000").rstrip("/") +
                                 "/v1/agents/rig/steps", json=payload), timeout=60)
    response.raise_for_status()
    result = response.json()
    if result.get("ledger_id") != payload["id"] or result.get("tenant_id") != payload["tenant_id"]:
        raise RuntimeError("Receipt scope mismatch")
    return result


async def run_task(body: Question, actor=None) -> dict:
    work = directory(body.tenant_id, body.task_id)
    receipt = {"tenant_id": str(body.tenant_id), "task_id": str(body.task_id), "room": "rig",
               "user_id": actor.user_id if actor else None,
               "region": body.home_region, "status": "running", "steps": [], "model_receipts": []}
    save(work, receipt)
    token = set_verified_actor(tenant_id=actor.tenant_id, user_id=actor.user_id) if actor else None
    async with httpx.AsyncClient(timeout=60, trust_env=False,
                                headers={"X-Tank-Key": os.environ.get("TANK_KEY", "")}) as client:
        memory = MemoryClient(os.environ.get("HERMES_URL", "http://hermes:8000"),
                              os.environ.get("TANK_KEY", ""), "rig", client)
        async def paid(task):
            request = {**body.model_dump(mode="json"), "question": task.get("question", body.question),
                       "user_id": actor.user_id if actor else None, "memories": task.get("memories", [])}
            (work / "request.json").write_text(json.dumps(request))
            allowed = {"PATH", "PYTHONPATH", "LANG", "TANK_URL", "TANK_KEY", "TANK_REGION",
                       "RIG_BROWSER_URL", "RIG_SANDBOX_URL", "SANDBOX_KEY",
                       "HERMES_BUNDLED_SKILLS", "HERMES_OPTIONAL_SKILLS"}
            env = {key: value for key, value in os.environ.items() if key in allowed}
            env.update(HERMES_HOME=str(work / "hermes"), PYTHONUNBUFFERED="1")
            child = await asyncio.create_subprocess_exec(sys.executable, "-m", "rig_brain.runner", str(work),
                env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(child.wait(), timeout=600)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                child.kill()
                await child.wait()
                raise
            if child.returncode != 0:
                raise RuntimeError("Brain did not complete")
            result = json.loads((work / "child-receipt.json").read_text())
            if result.get("tenant_id") != str(body.tenant_id) or result.get("task_id") != str(body.task_id):
                raise RuntimeError("Receipt scope mismatch")
            receipt["steps"].extend(result["steps"])
            receipt["model_receipts"] = result["model_receipts"]
            models = result["model_receipts"]
            cost = sum(float(r["cost_usd"]) for r in models) if models and all(r.get("cost_usd") is not None for r in models) else None
            return {"result": {"content": result["summary"], "summary": result["summary"]},
                    "ledger_id": models[-1]["ledger_id"] if models else None,
                    "ledger_ids": [r["ledger_id"] for r in models],
                    "paid_calls": len(models), "cost_usd": cost,
                    "tokens_in": sum(int(r.get("tokens_in") or 0) for r in models),
                    "tokens_out": sum(int(r.get("tokens_out") or 0) for r in models)}
        async def recalled(count):
            receipt["steps"].append(await step(client, body, "recall", "done", count))

        try:
            if actor:
                outcome = await run_with_memory(memory, tenant_id=actor.tenant_id, user_id=actor.user_id,
                    task_key="rig.build", task={"question": body.question, "home_region": body.home_region},
                    paid_call=paid, on_recall=recalled)
                receipt.update({k: v for k, v in outcome.items() if k != "steps"})
                receipt["memory_steps"] = outcome["steps"]
                receipt["summary"] = (outcome.get("result") or {}).get("content", "")
            else:
                outcome = await paid({"question": body.question})
                receipt.update(outcome, memory_status="skipped_unverified", answered_from_memory=False)
                receipt["summary"] = outcome["result"]["content"]
            memory_steps = receipt.get("memory_steps", [])
            saved = sum(item.get("action") == "remember" for item in memory_steps)
            failed = any(item.get("action") == "remember_failed" for item in memory_steps)
            if saved or failed:
                receipt["steps"].append(await step(client, body, "remember", "failed" if failed else "done", saved))
            receipt["status"] = "done"
        except asyncio.CancelledError:
            receipt.update(status="cancelled", error="Rig task cancelled; completed work retains its Tank receipts.")
            raise
        except Exception:
            receipt.update(status="failed", error="Rig task could not finish; check memory, Tank and the task receipts.")
        finally:
            try:
                response = await asyncio.wait_for(client.get(os.environ.get("TANK_URL", "http://tank:8000").rstrip("/") +
                    "/v1/agents/rig/receipts", params={"tenant_id": str(body.tenant_id), "task_id": str(body.task_id)},
                    headers={"X-Home-Region": body.home_region}), timeout=60)
                response.raise_for_status()
                durable = response.json()["receipts"]
                if any(r.get("tenant_id") != str(body.tenant_id) or r.get("task_id") != str(body.task_id) or
                       r.get("room") != "rig" for r in durable):
                    raise RuntimeError("Receipt scope mismatch")
                receipt["tank_receipts"] = durable
                if receipt["status"] != "done":
                    models = [r for r in durable if r.get("task_type") == "agent.rig.think"]
                    receipt["model_receipts"] = models
                    receipt["paid_calls"] = len(models)
                    receipt["tokens_in"] = sum(int(r.get("tokens_in") or 0) for r in models)
                    receipt["tokens_out"] = sum(int(r.get("tokens_out") or 0) for r in models)
                    receipt["cost_usd"] = sum(float(r["cost_usd"]) for r in models) if models and all(r.get("cost_usd") is not None for r in models) else None
            except Exception:
                receipt.update(status="failed", receipt_delivery="failed",
                               error="Tank receipts are unavailable; no success is claimed.")
            save(work, receipt)
            if token is not None:
                reset_verified_actor(token)
    return receipt


async def perform(body: Question, actor=None):
    async with gate:
        return await run_task(body, actor)


@app.get("/health")
async def health():
    if not os.environ.get('TANK_KEY', '').strip():
        return JSONResponse({'ok': False, 'missing_settings': ['TANK_KEY']}, status_code=503)
    return {"ok": True, "room": "rig", "hermes_version": "0.22.2"}


@app.post("/tasks")
async def start(body: Question, request: Request, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, body.home_region)
    actor = await signed_user(request, tenant_id=str(body.tenant_id), product="rig",
                              nonces=getattr(request.app.state, "assertion_nonces", None),
                              required=bool(request.headers.get(HEADER)))
    work = directory(body.tenant_id, body.task_id)
    identity = (str(body.tenant_id), str(body.task_id))
    payload = {**body.model_dump(mode="json"), "user_id": actor.user_id if actor else None}
    stored = work / "submitted.json"
    if stored.exists():
        if json.loads(stored.read_text()) != payload:
            raise HTTPException(409, "Task id already has a different question.")
        return await poll(body.task_id, body.tenant_id, body.home_region, request, x_tank_key)
    if sum(not job.done() for job in jobs.values()) >= 16:
        raise HTTPException(429, "Rig's brain is busy.")
    # No await between duplicate check, durable intent, and registration.
    try:
        with stored.open("x") as handle:
            json.dump(payload, handle)
    except FileExistsError:
        raise HTTPException(409, "Task is already submitted.") from None
    jobs[identity] = asyncio.create_task(perform(body, actor))
    jobs[identity].add_done_callback(lambda job: jobs.pop(identity, None))
    return JSONResponse({"tenant_id": identity[0], "task_id": identity[1], "status": "running"}, status_code=202)


@app.get("/tasks/{task_id}")
async def poll(task_id: uuid.UUID, tenant_id: uuid.UUID, home_region: str, request: Request, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, home_region)
    work = directory(tenant_id, task_id)
    await require_task_owner(request, tenant_id, work)
    identity = (str(tenant_id), str(task_id))
    if identity in jobs and not jobs[identity].done():
        return JSONResponse({"tenant_id": identity[0], "task_id": identity[1], "status": "running"}, status_code=202)
    path = work / "receipt.json"
    if not path.exists():
        if (work / "submitted.json").exists():
            return {"tenant_id": identity[0], "task_id": identity[1], "status": "failed",
                    "error": "Brain restarted before completion; submit a new task id."}
        raise HTTPException(404, "Task not found.")
    result = json.loads(path.read_text())
    if result["status"] == "running":
        result.update(status="failed", error="Brain restarted; completed work retains its Tank receipts.")
        save(work, result)
    return result


@app.delete("/tasks/{task_id}")
async def cancel(task_id: uuid.UUID, tenant_id: uuid.UUID, home_region: str, request: Request, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, home_region)
    await require_task_owner(request, tenant_id, directory(tenant_id, task_id))
    job = jobs.get((str(tenant_id), str(task_id)))
    cancelled = bool(job and not job.done())
    if cancelled:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    return {"cancelled": cancelled}

# RIG-5F: private forge connection and receipt relay.
from rig_brain.forge import router as forge_router

app.include_router(forge_router)


async def require_task_owner(request, tenant_id, work):
    stored = work / "submitted.json"
    if stored.exists():
        owner = json.loads(stored.read_text()).get("user_id")
        if owner:
            actor = await signed_user(request, tenant_id=str(tenant_id), product="rig", required=True,
                                      nonces=getattr(request.app.state, "assertion_nonces", None))
            if actor.user_id != owner:
                raise HTTPException(404, "Task not found.")
