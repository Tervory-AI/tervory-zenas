"""RIG-5F connection and receipt relay only; the forge never receives our Tank key."""
import asyncio
import json
import os
import socket
import sqlite3
import uuid
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .room import authorize

router = APIRouter(prefix="/forge")


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    home_region: Literal["us-east"]


class PaidApprovalStatus(Scope):
    approval_id: uuid.UUID


class Approval(Scope):
    method: Literal["scan", "photogrammetry", "trellis2"]
    inputs: list[str] = Field(min_length=1, max_length=150)
    unit_mm: float = Field(gt=0, le=1000)
    dimensions_mm: tuple[float, float, float]
    max_triangles: int = Field(default=100_000, ge=12, le=100_000)
    max_bytes: int = Field(default=8_000_000, ge=1024, le=8_000_000)
    timeout_seconds: int = Field(default=600, ge=60, le=3600, strict=True)
    cost_cap_usd: Decimal = Field(default=Decimal(0), ge=0, le=100, max_digits=12, decimal_places=6)


class Reserve(Scope):
    method: Literal["photogrammetry", "trellis2"]
    timeout_seconds: int = Field(ge=60, le=3600, strict=True)
    cost_cap_usd: Decimal = Field(gt=0, le=100, max_digits=12, decimal_places=6)


class Complete(Scope):
    outcome: Literal["delivered", "failed", "unverified"]
    provider_job_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")


class Step(Scope):
    stage: Literal["intake", "preflight", "blender", "dedup", "simplify", "meshopt", "resize",
                   "gltfpack", "ktx2", "meshopt_final", "model_check", "validate", "upload",
                   "gpu_submit", "gpu_poll", "gpu_cancel", "gpu_download"]
    outcome: Literal["delivered", "failed"]


def parsed(model, body):
    try:
        return model.model_validate(body)
    except ValidationError:
        raise HTTPException(422, "Invalid forge request.") from None


def trellis_enabled():
    if os.environ.get("RIG_FORGE_TRELLIS_ENABLED", "").lower() != "true":
        raise HTTPException(503, "TRELLIS is disabled pending the founder's licence decision.")


def approval(scope: Scope, *, create: Approval | None = None) -> Approval:
    """Brain-owned durable authority; Forge has no mount or route to write it.

    Commit before forwarding: a lost response can be retried with the identical
    request, but can never replace the scope/cap of an already launched job.
    """
    root = Path(os.environ.get("DATA_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    path = root / "forge-approvals.sqlite3"
    with closing(sqlite3.connect(path)) as database, database:
        path.chmod(0o600)
        database.execute("CREATE TABLE IF NOT EXISTS jobs (tenant TEXT, job TEXT, request TEXT NOT NULL, PRIMARY KEY(tenant,job))")
        identity = (str(scope.tenant_id), str(scope.job_id))
        if create is not None:
            value = json.dumps(create.model_dump(mode="json"), sort_keys=True)
            database.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?)", (*identity, value))
        row = database.execute("SELECT request FROM jobs WHERE tenant=? AND job=?", identity).fetchone()
        if row is None:
            raise HTTPException(403, "No brain-approved job matches this receipt.")
        if create is not None and row[0] != value:
            raise HTTPException(409, "Job id has different approved inputs.")
        return Approval.model_validate_json(row[0])


async def forge_addresses():
    addresses = await asyncio.get_running_loop().getaddrinfo("rig-forge", 8010, type=socket.SOCK_STREAM)
    return {row[4][0] for row in addresses}


async def forward(method, base, path, *, key=None, **kwargs):
    try:
        async with asyncio.timeout(40), httpx.AsyncClient(trust_env=False, timeout=40) as client:
            response = await client.request(method, base + path,
                headers={"X-Tank-Key": key} if key else {}, **kwargs)
        if response.status_code not in {200, 202, 400, 401, 403, 404, 409, 422, 429, 503}:
            raise ValueError("upstream unavailable")
        return JSONResponse(response.json(), status_code=response.status_code)
    except (httpx.HTTPError, ValueError, TimeoutError):
        raise HTTPException(503, "Forge or Tank is unavailable; no success is claimed.") from None


@router.post("/jobs")
async def create(body: dict, x_tank_key: str = Header(default="")):
    authorize(x_tank_key, body.get("home_region", ""))
    job = parsed(Approval, body)
    if job.method == "trellis2":
        trellis_enabled()
    approval(job, create=job)
    return await forward("POST", "http://rig-forge:8010", "/jobs", json=job.model_dump(mode="json"))


@router.get("/jobs/{job_id}")
async def poll(job_id: uuid.UUID, tenant_id: uuid.UUID, home_region: str,
               x_tank_key: str = Header(default="")):
    authorize(x_tank_key, home_region)
    return await forward("GET", "http://rig-forge:8010", "/jobs/" + str(job_id),
                         params={"tenant_id": str(tenant_id)})


@router.post("/relay/{action}")
async def relay(action: Literal["reserve", "claim", "complete", "steps", "approval"], body: dict, request: Request):
    try:
        allowed = await forge_addresses()
    except OSError:
        allowed = set()
    if not request.client or request.client.host not in allowed:
        raise HTTPException(401, "Only Rig's forge may relay receipts.")
    key = os.environ.get("TANK_KEY", "")
    authorize(key, body.get("home_region", ""))
    payload = parsed({"reserve": Reserve, "claim": Scope, "complete": Complete, "steps": Step, "approval": PaidApprovalStatus}[action], body)
    job = approval(payload)
    if job.home_region != payload.home_region:
        raise HTTPException(403, "Job region does not match its approval.")
    if action != "steps" or payload.stage.startswith("gpu_"):
        if job.method not in {"trellis2", "photogrammetry"}:
            raise HTTPException(403, "GPU work was not approved for this job.")
    if action in {"reserve", "claim"} and job.method == "trellis2":
        trellis_enabled()
    if action == "reserve" and (payload.method != job.method or payload.timeout_seconds != job.timeout_seconds
                                 or payload.cost_cap_usd != job.cost_cap_usd):
        raise HTTPException(403, "GPU request does not match the brain's approved method, timeout and cap.")
    if action == "approval":
        return await forward("GET", os.environ.get("TANK_URL", "http://tank:8000").rstrip("/"),
            "/v1/paid-steps/approvals/" + str(payload.approval_id), key=key,
            params={"tenant_id": str(payload.tenant_id)})
    return await forward("POST", os.environ.get("TANK_URL", "http://tank:8000").rstrip("/"),
                         "/v1/agents/rig/forge/" + action, key=key, json=payload.model_dump(mode="json"))
