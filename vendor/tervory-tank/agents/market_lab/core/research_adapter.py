"""A research rung after local source lookups; Tank alone owns activation and cost."""
from __future__ import annotations

import os
from typing import Any

import httpx


async def research_web(tenant_id: str, question: str) -> dict[str, Any] | None:
    """The room reaches the internal researcher only through its Tank corridor."""
    key = os.environ.get("TANK_KEY", "").strip()
    if not question.strip() or not key:
        return None
    base = os.environ.get("TANK_URL", "http://tank:8000").rstrip("/")
    async with httpx.AsyncClient(timeout=170, follow_redirects=False) as client:
        response = await client.post(base + "/v1/market-lab/internal/research",
            headers={"X-Tank-Key": key}, json={"tenant_id": tenant_id, "question": question,
                "home_region": os.environ.get("TANK_REGION", "us-east")})
        if response.status_code == 503:
            # Tank reports the researcher is not connected or could not finish. Keep the free
            # "no sources yet" answer (as the old research path did when nothing was promoted)
            # instead of failing the whole question.
            return None
        response.raise_for_status()
        return response.json()


# Retained for compatibility and policy tests; no Market Lab runtime calls this
# Perplexity path. research_web uses the existing market.research routing policy.
async def research_if_promoted(tenant_id: str, question: str) -> dict[str, Any] | None:
    key = os.environ.get("TANK_KEY", "").strip()
    if not question.strip() or not key:
        return None  # Empty or unconfigured research retains the free no-sources answer.
    base = os.environ.get("TANK_URL", "http://tank:8000").rstrip("/")
    body = {"tenant_id": tenant_id, "question": question,
            "home_region": os.environ.get("TANK_REGION", "us-east")}
    headers = {"X-Tank-Key": key}
    async with httpx.AsyncClient(timeout=190) as client:
        status = await client.post(base + "/v1/research/status", json=body, headers=headers)
        if status.status_code == 409:
            return None  # Policy has not promoted a research preset. No paid call occurred.
        if status.status_code != 200:
            raise RuntimeError(f"research status unavailable ({status.status_code})")
        response = await client.post(base + "/v1/research", json=body, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"research failed ({response.status_code})")
    result = response.json()
    if not isinstance(result, dict) or not result.get("sources") or not result.get("receipt"):
        raise RuntimeError("research receipt is incomplete")
    return {"answer": result["content"], "citations": result["sources"],
            "receipt": result["receipt"], "ledger_id": result["ledger_id"],
            "cost_usd": result["cost_usd"]}
