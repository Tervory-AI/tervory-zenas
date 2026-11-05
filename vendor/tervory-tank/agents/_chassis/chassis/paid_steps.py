"""Resume a waiting Tank call only after its tenant admin resolves approval.

The room key can read its own product's pending state; it cannot approve a step.
The opaque run ID is an idempotency handle, never approval authority.
"""
import asyncio

POLL_SECONDS = 5


async def post(http, base, path, body, headers, *, timeout=None):
    payload = dict(body)
    while True:
        response = await http.post(base + path, json=payload, headers=headers, **({"timeout": timeout} if timeout else {}))
        detail = None
        if response.status_code == 409:
            try:
                error = response.json()
            except ValueError:
                return response
            if isinstance(error, dict):
                detail = error.get("detail")
        if not isinstance(detail, dict) or detail.get("code") != "paid_step_approval_required":
            return response
        tenant = str(payload.get("tenant_id", ""))
        if not tenant and isinstance(payload.get("requests"),list) and payload["requests"]:
            tenants = {str(item.get("tenant_id", "")) for item in payload["requests"]}
            if len(tenants) == 1:
                tenant = tenants.pop()
        if detail.get("tenant_id") != tenant:
            raise RuntimeError("Paid approval does not belong to this tenant")
        while True:
            check = await http.get(base + f"/v1/paid-steps/approvals/{detail['approval_id']}",
                                   params={"tenant_id": tenant}, headers=headers, timeout=30)
            check.raise_for_status()
            state = check.json()["state"]
            if state == "approved":
                payload["paid_run_id"] = detail.get("resume_run_id", detail["run_id"])
                break
            if state == "denied":
                raise RuntimeError("Paid step denied by the tenant administrator")
            await asyncio.sleep(POLL_SECONDS)
