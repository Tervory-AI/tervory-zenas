"""Calls Tank with this agent's own key. No model or provider keys live here."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
import uuid

import httpx

# A provider batch may take its whole 24h window; allow an hour on top.
_BACKGROUND_WAIT_S = 25 * 3600
_FINISHED = {"completed", "failed", "uncertain"}


class TankClient:
    def __init__(self, base_url: str, api_key: str, agent: str, http: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self.agent = agent
        self._http = http

    def __repr__(self) -> str:
        return f"TankClient(agent={self.agent!r})"

    async def route(
        self,
        tenant_id: str,
        task_type: str,
        messages: list[dict[str, str]],
        *,
        background: bool = False,
    ) -> dict[str, Any]:
        """One model call through Tank.

        background=True is for work nobody is waiting on (nightly jobs, evals):
        Tank queues it as priority=batch with interactive=false and, when the
        seat supports it, answers through a provider batch at half price. This
        waits for the queued job and returns the same answer shape. Leave it
        False for anything a person is waiting on.
        """
        body: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "agent": self.agent,
            "task_type": task_type,
            "messages": messages,
        }
        if background:
            body["priority"] = "batch"
            body["interactive"] = False
        data = await self._post("/v1/route", body)
        if background and _queued(data):
            data = await self._wait_job(str(tenant_id), str(data["id"]))
        _check_execution(data, str(tenant_id), task_type)
        return data

    async def _wait_job(self, tenant_id: str, job_id: str) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("tank key is not set")
        deadline = time.monotonic() + _wait_limit()
        delay = 5.0
        while True:
            response = await self._http.get(
                self._base + f"/v1/route/jobs/{job_id}",
                params={"tenant_id": tenant_id},
                headers={"X-Tank-Key": self._api_key},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"tank answered {response.status_code}")
            job = response.json()
            if not isinstance(job, dict):
                raise RuntimeError("tank answer was not an object")
            state = str(job.get("status") or "")
            if state == "completed":
                result = job.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("tank job finished without an answer")
                return result
            if state in _FINISHED:
                raise RuntimeError(f"tank job {state}: {job.get('error') or 'no detail'}")
            if time.monotonic() + delay > deadline:
                raise RuntimeError("tank job is still queued; stopped waiting")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 60.0)

    async def run_skill(self, tenant_id: str, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            f"/v1/skala/{skill_id}/run",
            {"tenant_id": str(tenant_id), "input": payload},
        )

    async def approved_skill(self, tenant_id: str, skill_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Only an explicit absence of approval permits the legacy implementation.

        Do not cache the version: approval and rollback take effect on the next
        call. Run without a version pin so Skala rechecks approval at execution.
        Failures after approval never silently fall back to room code.
        """
        if not self._api_key:
            raise RuntimeError("tank key is not set")
        response = await self._http.get(
            self._base + f"/v1/skala/{skill_id}/run",
            headers={"X-Tank-Key": self._api_key},
        )
        if (response.status_code == 409 and response.json().get("detail")
                == f"{skill_id} has no approved version"):
            return None
        response.raise_for_status()
        return await self.run_skill(tenant_id, skill_id, payload)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("tank key is not set")
        from .paid_steps import post as paid_post
        response = await paid_post(self._http, self._base, path, body,
                                   {"X-Tank-Key": self._api_key})
        if response.status_code >= 400:
            raise RuntimeError(f"tank answered {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("tank answer was not an object")
        return data


def _queued(data: dict[str, Any]) -> bool:
    return data.get("status") == "queued" and "id" in data and "content" not in data


def _wait_limit() -> float:
    raw = os.environ.get("TANK_BACKGROUND_WAIT_S", "").strip()
    try:
        value = float(raw) if raw else _BACKGROUND_WAIT_S
    except ValueError:
        return _BACKGROUND_WAIT_S
    return value if value > 0 else _BACKGROUND_WAIT_S


def _same_uuid(a: Any, b: Any) -> bool:
    """Tenant ids bind by value: case and hyphens in the caller's form never break a call."""
    try:
        return uuid.UUID(str(a)) == uuid.UUID(str(b))
    except (TypeError, ValueError, AttributeError):
        return False


def _check_execution(data: dict[str, Any], tenant_id: str, task_type: str) -> None:
    """Legacy/unknown receipts pass through; present evidence must bind this call."""
    execution = data.get("execution")
    if execution is None:
        return
    if not isinstance(execution, dict) or execution.get("version") != 1 or any((
        not _same_uuid(execution.get("tenant_id"), tenant_id), execution.get("task_type") != task_type,
        execution.get("ledger_id") != str(data.get("ledger_id")), execution.get("model") != data.get("model"),
    )):
        raise RuntimeError("tank execution receipt does not match this call")
    # Dispatched parameters do not attest gateway/provider defaults. Never promote
    # that audit hash into the resolved-settings field used by comparisons.
    if execution.get("settings_hash") is not None or data.get("settings_hash") is not None:
        raise RuntimeError("tank effective settings attestation is unsupported")
