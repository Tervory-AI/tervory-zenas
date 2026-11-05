"""One bounded Hermes process per Rig task, with durable tenant-scoped results."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from rig_brain.room import Question, authorize
from brain.security import tenant_dir

app = FastAPI()
gate = asyncio.Semaphore(1)
jobs: dict[tuple[str, str], asyncio.Task] = {}


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
    response = await client.post(os.environ.get("TANK_URL", "http://tank:8000").rstrip("/") +
                                 "/v1/agents/rig/steps", json=payload)
    response.raise_for_status()
    result = response.json()
    if result.get("ledger_id") != payload["id"] or result.get("tenant_id") != payload["tenant_id"]:
        raise RuntimeError("Receipt scope mismatch")
    return result


async def memory(client, path, payload):
    response = await client.post(os.environ.get("HERMES_URL", "http://hermes:8000").rstrip("/") + path, json=payload)
    response.raise_for_status()
    return response.json()


async def run_task(body: Question) -> dict:
    work = directory(body.tenant_id, body.task_id)
    receipt = {"tenant_id": str(body.tenant_id), "task_id": str(body.task_id), "room": "rig",
               "region": body.home_region, "status": "running", "steps": [], "model_receipts": []}
    save(work, receipt)
    async with httpx.AsyncClient(timeout=60, trust_env=False,
                                headers={"X-Tank-Key": os.environ.get("TANK_KEY", "")}) as client:
        action = "recall"
        try:
            recalled = await memory(client, "/recall", {"tenant_id": str(body.tenant_id),
                "query": body.question, "kind": "rig.task", "limit": 8})
            memories = recalled.get("memories")
            if not isinstance(memories, list) or any(not isinstance(m, dict) or
                    m.get("tenant_id") != str(body.tenant_id) for m in memories):
                raise RuntimeError("Memory tenant boundary failed")
            receipt["steps"].append(await step(client, body, action, "done", len(memories)))
            request = {**body.model_dump(mode="json"), "memories": [
                {"id": m["id"], "text": str(m.get("text", ""))[:1000]} for m in memories[:8]]}
            (work / "request.json").write_text(json.dumps(request))
            action = "brain"
            # The child has no database/R2/provider/control-plane credentials.
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
            receipt["summary"] = result["summary"]
            action = "remember"
            saved = await memory(client, "/memories", {"tenant_id": str(body.tenant_id),
                "text": str(result["summary"])[:6000], "kind": "rig.task", "source": "rig:task:" + str(body.task_id)})
            if not saved.get("id"):
                raise RuntimeError("Memory was not saved")
            receipt["steps"].append(await step(client, body, action, "done", 1))
            receipt["status"] = "done"
        except asyncio.CancelledError:
            receipt.update(status="cancelled", error="Rig task cancelled; completed work retains its Tank receipts.")
            raise
        except Exception:
            receipt.update(status="failed", error="Rig task could not finish; check memory, Tank and the task receipts.")
            # A missing receipt is never represented as successful work.
            if action != "brain":
                try:
                    receipt["steps"].append(await step(client, body, action, "failed"))
                except Exception:
                    receipt["receipt_delivery"] = "failed"
        finally:
            # Recover billed/failed model receipts even when the child exits early.
            try:
                response = await client.get(os.environ.get("TANK_URL", "http://tank:8000").rstrip("/") +
                    "/v1/agents/rig/receipts", params={"tenant_id": str(body.tenant_id), "task_id": str(body.task_id)},
                    headers={"X-Home-Region": body.home_region})
                response.raise_for_status()
                durable = response.json()["receipts"]
                if any(r.get("tenant_id") != str(body.tenant_id) or r.get("task_id") != str(body.task_id) or
                       r.get("room") != "rig" for r in durable):
                    raise RuntimeError("Receipt scope mismatch")
                receipt["tank_receipts"] = durable
            except Exception:
                receipt.update(status="failed", receipt_delivery="failed",
                               error="Tank receipts are unavailable; no success is claimed.")
            save(work, receipt)
    return receipt


async def perform(body: Question):
    async with gate:
        return await run_task(body)


@app.get("/health")
async def health():
    return {"ok": True, "room": "rig", "hermes_version": "0.22.2"}


@app.post("/tasks")
async def start(body: Question, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, body.home_region)
    work = directory(body.tenant_id, body.task_id)
    identity = (str(body.tenant_id), str(body.task_id))
    payload = body.model_dump(mode="json")
    stored = work / "submitted.json"
    if stored.exists():
        if json.loads(stored.read_text()) != payload:
            raise HTTPException(409, "Task id already has a different question.")
        return await poll(body.task_id, body.tenant_id, body.home_region, x_tank_key)
    if sum(not job.done() for job in jobs.values()) >= 16:
        raise HTTPException(429, "Rig's brain is busy.")
    # No await between duplicate check, durable intent, and registration.
    stored.write_text(json.dumps(payload))
    jobs[identity] = asyncio.create_task(perform(body))
    jobs[identity].add_done_callback(lambda job: jobs.pop(identity, None))
    return JSONResponse({"tenant_id": identity[0], "task_id": identity[1], "status": "running"}, status_code=202)


@app.get("/tasks/{task_id}")
async def poll(task_id: uuid.UUID, tenant_id: uuid.UUID, home_region: str, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, home_region)
    work = directory(tenant_id, task_id)
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
async def cancel(task_id: uuid.UUID, tenant_id: uuid.UUID, home_region: str, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, home_region)
    job = jobs.get((str(tenant_id), str(task_id)))
    cancelled = bool(job and not job.done())
    if cancelled:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    return {"cancelled": cancelled}

# RIG-5F: private forge connection and receipt relay.
from rig_brain.forge import router as forge_router
app.include_router(forge_router)
