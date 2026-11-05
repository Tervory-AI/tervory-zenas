"""Hugging Face Jobs adapter; the token never reaches job environments or receipts."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time

from huggingface_hub import HfApi


# Linux caps one environment string (name=value) at 128 KiB (MAX_ARG_STRLEN).
# FORGE_TRANSFERS reaches the job as one variable, so refuse oversized link sets
# before any budget is reserved instead of paying for a job that cannot start.
TRANSFERS_LIMIT_BYTES = 120_000


class GPUError(RuntimeError):
    pass


class GPU:
    def __init__(self, api=None, *, wait=asyncio.sleep, clock=time.monotonic):
        self.api = api
        self.wait, self.clock = wait, clock

    async def run(self, scope: dict, method: str, *, transfers: dict, timeout: int,
                  cap: str, relay, save_provider, namespace: str) -> str:
        if method == "trellis2" and os.environ.get("RIG_FORGE_TRELLIS_ENABLED", "").lower() != "true":
            raise GPUError("TRELLIS is disabled pending the founder's licence decision.")
        token = os.environ.get("HF_JOBS_TOKEN", "")
        if self.api is None and not token:
            raise GPUError("HF_JOBS_TOKEN is not configured.")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}", namespace):
            raise GPUError("HF_JOBS_NAMESPACE is not configured.")
        secret = json.dumps(transfers, separators=(",", ":"))
        if len(secret.encode()) > TRANSFERS_LIMIT_BYTES:
            raise GPUError("Too many photo links for one GPU job; use fewer or shorter-named photos.")
        api = self.api or HfApi(endpoint="https://huggingface.co", token=token)
        authorization = await relay("reserve", {**scope, "method": method,
                                               "timeout_seconds": timeout, "cost_cap_usd": cap})
        if (authorization.get("tenant_id") != scope["tenant_id"]
                or authorization.get("job_id") != scope["job_id"]
                or not re.fullmatch(r"[a-z0-9][a-z0-9./_-]+@sha256:[a-f0-9]{64}", authorization.get("image", ""))):
            raise GPUError("Tank returned an invalid GPU authorization.")
        await relay("claim", scope)  # Once only. A lost submission response is not retried.
        provider_id, outcome = "unknown", "unverified"
        deadline = self.clock() + authorization["timeout_seconds"]
        try:
            job = await asyncio.wait_for(asyncio.to_thread(api.run_job, image=authorization["image"],
                command=["python", "/opt/forge/gpu/entrypoint.py", method],
                env={"FORGE_TENANT_ID": scope["tenant_id"], "FORGE_JOB_ID": scope["job_id"],
                     "HF_HUB_DISABLE_TELEMETRY": "1"},
                secrets={"FORGE_TRANSFERS": secret},
                namespace=namespace, flavor=authorization["flavor"],
                timeout=authorization["timeout_seconds"]), timeout=30)
            provider_id = job.id
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", provider_id):
                raise GPUError("Provider returned an invalid job identifier.")
            await save_provider(provider_id)
            await relay("steps", {**scope, "stage": "gpu_submit", "outcome": "delivered"})
            while self.clock() < deadline:
                job = await asyncio.wait_for(asyncio.to_thread(api.inspect_job,
                    job_id=provider_id, namespace=namespace), timeout=30)
                state = job.status.stage
                if state == "COMPLETED":
                    outcome = "delivered"
                    await relay("steps", {**scope, "stage": "gpu_poll", "outcome": "delivered"})
                    return provider_id
                if state in {"ERROR", "FAILED", "CANCELED", "DELETED"}:
                    outcome = "failed"
                    raise GPUError("GPU job failed; output is not approved.")
                await self.wait(min(5, max(0, deadline - self.clock())))
            raise GPUError("GPU job exceeded its timeout.")
        except BaseException:
            if provider_id != "unknown":
                try:
                    await asyncio.wait_for(asyncio.to_thread(api.cancel_job,
                        job_id=provider_id, namespace=namespace), timeout=30)
                    await relay("steps", {**scope, "stage": "gpu_cancel", "outcome": "delivered"})
                except Exception:
                    outcome = "unverified"  # Retain budget for an uncertain paid job.
            raise GPUError("GPU job did not complete; its reservation remains recorded.") from None
        finally:
            await relay("complete", {**scope, "provider_job_id": provider_id, "outcome": outcome})
