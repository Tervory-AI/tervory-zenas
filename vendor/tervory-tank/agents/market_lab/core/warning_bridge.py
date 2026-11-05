"""Read permitted series and deliver durable outcome records through Tank only."""

import os

import httpx

from core import warning_store as store
from core.ledger import PostgresLedger


async def call(operation: str, payload: dict) -> dict:
    key = os.environ.get("TANK_KEY", "")
    if not key:
        raise RuntimeError("The series connection is not configured")
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            response = await http.post(
                os.environ.get("TANK_URL", "http://tank:8000").rstrip("/")
                + "/v1/market-lab/internal/"
                + operation,
                json=payload,
                headers={"x-tank-key": key},
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("The saved-series connection is unavailable") from None


async def deliver(ledger: PostgresLedger, tenant: str) -> bool:
    for event in await store.pending(ledger, tenant):
        try:
            await call("training", {"tenant_id": tenant, "event_id": event["id"]})
        except RuntimeError:
            return False  # Durable outbox: retry on the next scheduled or page run.
        await store.mark_delivered(ledger, tenant, event["id"])
    return True
