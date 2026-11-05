"""Private Tank event corridor; rooms never receive a Redis credential."""
from __future__ import annotations

import logging
import os
import time
from uuid import UUID, NAMESPACE_URL, uuid5

import httpx

log = logging.getLogger(__name__)
_retry_after = 0.0


def source_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))


async def request(http, tenant_id: str, operation: str, body: dict) -> dict:
    if operation not in {"publish", "receive", "settle"}:
        raise ValueError("Unknown event operation")
    tenant = str(UUID(str(tenant_id)))
    key = os.environ.get("TANK_KEY", "")
    if not key:
        raise RuntimeError("Room event corridor is not configured")
    response = await http.post(os.environ.get("TANK_URL", "http://tank:8000").rstrip("/") + "/v1/events/" + operation,
        headers={"X-Tank-Key": key, "X-Tervory-Tenant": tenant,
                 "X-Tervory-Region": os.environ.get("TANK_REGION", "us-east")},
        json=body, timeout=2, follow_redirects=False)
    response.raise_for_status()
    return response.json()


async def publish(tenant_id: str, event: str, reference: str, facts: dict, *, http=None) -> bool:
    """Best effort after commit. Never log data, credentials or exception text."""
    global _retry_after
    if time.monotonic() < _retry_after:
        return False
    try:
        body = {"event": event, "source_id": source_id(reference), "facts": facts}
        if http is None:
            async with httpx.AsyncClient(trust_env=False) as client:
                result = await request(client, tenant_id, "publish", body)
        else:
            result = await request(http, tenant_id, "publish", body)
        if result.get("published") is not True:
            raise RuntimeError
        return True
    except Exception:
        # A down bus adds at most one short attempt per process per 30 seconds.
        _retry_after = time.monotonic() + 30
        log.warning("Event publication unavailable; product work continues")
        return False
