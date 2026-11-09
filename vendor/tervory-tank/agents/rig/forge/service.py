"""Private start/poll worker with durable tenant-scoped state and no Tank key."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .gpu import GPU
from .model_check import Budget
from .pipeline import run
from .retention import cleanup, save, sweep
from .storage import Storage
from .progress import event, category

gate = asyncio.Semaphore(1)
tasks = {}


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    home_region: Literal["us-east"]
    method: Literal["scan", "photogrammetry", "trellis2"]
    inputs: list[str] = Field(min_length=1, max_length=150)
    unit_mm: float = Field(gt=0, le=1000)
    dimensions_mm: tuple[float, float, float]
    max_triangles: int = Field(default=100_000, ge=12, le=100_000)
    max_bytes: int = Field(default=8_000_000, ge=1024, le=8_000_000)
    timeout_seconds: int = Field(default=600, ge=60, le=3600)
    cost_cap_usd: str = Field(default="0", pattern=r"^\d{1,3}(?:\.\d{1,6})?$")

    @model_validator(mode="after")
    def inputs_fit_method(self):
        storage = Storage(str(self.tenant_id), str(self.job_id), client=object())
        for name in self.inputs:
            storage.key("inputs/" + name)
        if len(set(self.inputs)) != len(self.inputs) or any(v <= 0 or v > 100_000 for v in self.dimensions_mm):
            raise ValueError("Duplicate files or invalid measured dimensions.")
        suffixes = {Path(name).suffix.lower() for name in self.inputs}
        if self.method == "scan":
            allowed = {".obj", ".ply", ".glb"}
            if len(self.inputs) != 1 or not suffixes <= allowed:
                raise ValueError("One scan or AI mesh asset is required.")
        else:
            low, high = (50, 150) if self.method == "photogrammetry" else (1, 4)
            if not low <= len(self.inputs) <= high or not suffixes <= {".jpg", ".jpeg", ".png", ".webp"}:
                raise ValueError("Photo count or format is invalid for this method.")
        return self

    def scope(self):
        return self.model_dump(mode="json", include={"tenant_id", "job_id", "home_region"})


def directory(tenant: uuid.UUID, job: uuid.UUID, *, create: bool = True) -> Path:
    root = Path(os.environ.get("FORGE_DATA_DIR", "/data"))
    work = root / str(tenant) / str(job)
    if root.is_symlink() or work.parent.is_symlink() or work.is_symlink():
        raise HTTPException(409, "Invalid job storage.")
    if create:
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
    return work


async def brain_addresses():
    answers = await asyncio.get_running_loop().getaddrinfo("rig-brain", 8001, type=socket.SOCK_STREAM)
    return {item[4][0] for item in answers}


async def relay(action: str, body: dict) -> dict:
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        while True:
            response = await asyncio.wait_for(client.post("http://rig-brain:8001/forge/relay/" + action, json=body), timeout=30)
            detail = response.json().get("detail") if response.status_code == 409 else None
            if isinstance(detail, dict) and detail.get("code") == "paid_step_approval_required":
                if detail.get("tenant_id") != body["tenant_id"] or detail.get("run_id") != body["job_id"]:
                    raise RuntimeError("Paid approval does not match this forge job")
                while True:
                    check = await asyncio.wait_for(client.post("http://rig-brain:8001/forge/relay/approval", json={
                        **{key: body[key] for key in ("tenant_id", "job_id", "home_region")},
                        "approval_id": detail["approval_id"]}), timeout=30)
                    check.raise_for_status()
                    status = check.json()
                    if status.get("run_id") != body["job_id"]:
                        raise RuntimeError("Paid approval does not match this forge job")
                    if status["state"] == "approved":
                        break
                    if status["state"] == "denied":
                        raise RuntimeError("Paid forge step denied by tenant administrator")
                    await asyncio.sleep(5)
                continue
            if response.status_code != 200:
                raise RuntimeError("Tank receipt or budget authorization is unavailable.")
            return response.json()



async def perform(body: Job, work: Path, store: Storage):
    started = time.monotonic()
    state = {**body.scope(), "status": "running", "stage": "intake", "receipts": [], "progress": []}
    def progress(stage, outcome='delivered'):
        state['progress'].append(event(len(state['progress']), category(stage),
            elapsed_ms=(time.monotonic() - started) * 1000, receipts=state['receipts'], outcome=outcome))
        save(work / 'state.json', state)
    progress('intake', 'running')
    async def receipt(stage, outcome):
        state["stage"] = stage
        # Persist delivery intent before HTTP. If the brain is unavailable, the
        # job stays failed; a repeated delivery of the same receipt is idempotent.
        payload = {**body.scope(), "stage": stage, "outcome": outcome}
        save(work / "pending-receipt.json", payload)
        delivered = await relay("steps", payload)
        state["receipts"].append(delivered)
        progress(stage, outcome)
        (work / "pending-receipt.json").unlink()
    async def provider(identifier):
        state["provider_job_id"] = identifier
        save(work / "state.json", state)
    async def gpu_relay(action, payload):
        state["stage"] = payload.get("stage", "gpu_" + action)
        # Launch carries short-lived R2 capabilities. Persist only delivery
        # intent; never write those capabilities to state, receipts or logs.
        retained = body.scope() if action == 'launch' else payload
        save(work / "pending-gpu-relay.json", {"action": action, "payload": retained})
        result = await relay(action, payload)
        state["receipts"].append(result)
        progress(state['stage'], 'failed' if payload.get('outcome') == 'failed' else 'delivered')
        (work / "pending-gpu-relay.json").unlink()
        return result
    async with gate:
        save(work / "state.json", state)
        try:
            if body.method in {"photogrammetry", "trellis2"}:
                transfers = await asyncio.to_thread(store.transfers, body.model_dump(mode="json"))
                await receipt("intake", "delivered")
                progress('gpu_submit', 'running')
                await GPU().run(body.scope(), body.method, transfers=transfers,
                    timeout=body.timeout_seconds, cap=body.cost_cap_usd, relay=gpu_relay, save_provider=provider,
                    namespace=os.environ.get("HF_JOBS_NAMESPACE", ""))
                state["stage"] = "gpu_download"
                source = work / "generated.glb"
                await asyncio.to_thread(store.download, "gpu/result.glb", source)
                await receipt("gpu_download", "delivered")
                unit_mm = 1000.0
            else:
                source = work / body.inputs[0]
                await asyncio.to_thread(store.download, "inputs/" + body.inputs[0], source)
                await receipt("intake", "delivered")
                unit_mm = body.unit_mm
            report = await run(source, work, unit_mm=unit_mm, dimensions_mm=body.dimensions_mm,
                               budget=Budget(body.max_triangles, body.max_bytes), receipt=receipt)
            state["stage"] = "upload"
            await asyncio.to_thread(store.upload, "drafts/model.glb", work / "draft.glb")
            await asyncio.to_thread(store.upload, "drafts/report.json", work / "report.json")
            await receipt("upload", "delivered")
            state.update(status="draft", report=report, asset_key=store.key("drafts/model.glb"))
        except (Exception, asyncio.CancelledError):
            pending = (work / "pending-receipt.json").exists() or (work / "pending-gpu-relay.json").exists()
            recorded = any(row.get("stage") == state["stage"] for row in state["receipts"])
            if not pending and not recorded and state["stage"] in {"intake", "gpu_download", "upload"}:
                try:
                    await receipt(state["stage"], "failed")
                except Exception:
                    pass  # The pending receipt is durable; no following work runs.
            state.update(status="failed", error="Forge job failed at " + state["stage"] + ". No model was approved.")
            progress(state['stage'], 'failed')
            if (work / "report.json").exists():
                state["report"] = json.loads((work / "report.json").read_text())
        finally:
            state["finished_at"] = time.time()
            save(work / "state.json", state)
            try:
                cleanup(work)
            except OSError:
                logging.getLogger(__name__).error("Forge intermediates could not be removed; retention will retry.")


@asynccontextmanager
async def lifespan(app):
    root = Path(os.environ.get("FORGE_DATA_DIR", "/data"))
    sweep(root, set(tasks))
    async def maintain():
        while True:
            await asyncio.sleep(60)
            try:
                sweep(root, set(tasks))
            except OSError:
                logging.getLogger(__name__).error("Forge retention will retry.")
    maintenance = asyncio.create_task(maintain())
    try:
        yield
    finally:
        maintenance.cancel()
        for task in list(tasks.values()):
            task.cancel()
        await asyncio.gather(maintenance, *list(tasks.values()), return_exceptions=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def private_peer(request: Request, call_next):
    if request.url.path in {"/health", "/health/configuration"} and request.client and request.client.host in {"127.0.0.1", "::1"}:
        return await call_next(request)
    try:
        allowed = await brain_addresses()
    except OSError:
        allowed = set()
    if not request.client or request.client.host not in allowed:
        return JSONResponse({"detail": "Only Rig's brain may use the forge."}, status_code=401)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"ok": True, "room": "rig", "service": "forge"}


@app.get('/health/configuration')
async def configuration():
    # Names only. The sealed forge does not require database or Tank credentials.
    groups = {'storage': ('R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID_RIG', 'R2_SECRET_ACCESS_KEY_RIG'),
              'gpu': ('HF_JOBS_NAMESPACE',)}
    return {group: {'ready': not (missing := [name for name in names if not os.environ.get(name, '').strip()]),
                    'missing_settings': missing} for group, names in groups.items()}


@app.post("/jobs")
async def submit(body: Job):
    if body.method == "trellis2" and os.environ.get("RIG_FORGE_TRELLIS_ENABLED", "").lower() != "true":
        raise HTTPException(503, "TRELLIS is disabled pending the founder's licence decision.")
    if any(os.environ.get(name) for name in ("TANK_KEY", "TANK_API_KEY", "TANK_KEY_RIG")):
        raise HTTPException(503, "A Tank credential must never be installed in the forge.")
    try:
        store = Storage(str(body.tenant_id), str(body.job_id))
    except ValueError:
        raise HTTPException(503, "Rig R2 storage is not configured.") from None
    if body.method in {"trellis2", "photogrammetry"} and not os.environ.get('HF_JOBS_NAMESPACE', '').strip():
        raise HTTPException(503, "HF_JOBS_NAMESPACE is not configured.")
    work = directory(body.tenant_id, body.job_id)
    identity = (body.tenant_id, body.job_id)
    submitted = work / "request.json"
    if submitted.exists():
        if json.loads(submitted.read_text()) != body.model_dump(mode="json"):
            raise HTTPException(409, "Job id has different inputs.")
        return await poll(body.job_id, body.tenant_id)
    if len(tasks) >= 8:
        raise HTTPException(429, "Forge is busy.")
    save(submitted, body.model_dump(mode="json"))
    tasks[identity] = asyncio.create_task(perform(body, work, store))
    tasks[identity].add_done_callback(lambda task: tasks.pop(identity, None))
    return JSONResponse({**body.scope(), "status": "queued"}, status_code=202)


@app.get("/jobs/{job_id}")
async def poll(job_id: uuid.UUID, tenant_id: uuid.UUID):
    # Lookups never create folders: an unknown job id leaves no trace on disk.
    work = directory(tenant_id, job_id, create=False)
    path = work / "state.json"
    if not path.exists():
        if (tenant_id, job_id) in tasks:
            return JSONResponse({"status": "queued"}, status_code=202)
        if (work / "request.json").exists():
            state = {"tenant_id": str(tenant_id), "job_id": str(job_id), "status": "failed",
                     "error": "Forge restarted before execution. This job is not relaunched."}
            save(path, state)
            return JSONResponse(state)
        raise HTTPException(404, "Forge job not found.")
    state = json.loads(path.read_text())
    if state["status"] == "running" and (tenant_id, job_id) not in tasks:
        state.update(status="failed", error="Forge restarted. Inspect the existing GPU reservation; this job is not relaunched.")
        save(path, state)
    return JSONResponse(state, status_code=202 if state["status"] == "running" else 200)
