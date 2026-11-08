"""Founder memory corridor. Uses existing founder auth; no new page or credentials."""

import json
import os
import re
import uuid
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from admin import require_admin
from gold import Reviewer, require_reviewer

router = APIRouter(prefix="/v1/memory")
_PATHS = {
    "records": ("GET", "/founder/records"),
    "publish-policy": ("POST", "/founder/publish-policy"),
    "publish": ("POST", "/founder/publish"),
}


async def hermes(
    method: str,
    path: str,
    actor: str,
    *,
    body: dict | None = None,
    params: dict | None = None,
    tenant: str | None = None,
) -> dict:
    key = os.environ.get("TANK_API_KEY", "")
    if not key:
        raise HTTPException(503, "memory store unreachable")
    from command_tools._user_assertion import bind_personal_headers

    raw = b""
    if body is not None:
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    tenant_id = tenant
    if tenant_id is None and body and body.get("tenant_id") is not None:
        tenant_id = str(body["tenant_id"])
    if tenant_id is None and params and params.get("tenant_id") is not None:
        tenant_id = str(params["tenant_id"])
    if not tenant_id:
        raise HTTPException(422, "tenant required for memory")
    target = httpx.URL("http://hermes:8000" + path)
    if params:
        target = target.copy_merge_params(params)
    headers = {
        "X-Tank-Key": key,
        "X-Tervory-Admin": actor,
        **bind_personal_headers(
            key, method, target.raw_path.decode("ascii"), tenant=tenant_id, user=actor, body=raw
        ),
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        async with httpx.AsyncClient(
            timeout=40, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.request(
                method,
                "http://hermes:8000" + path,
                content=raw if body is not None else None,
                params=params,
                headers=headers,
            )
        if response.status_code >= 400:
            if response.status_code in (403, 409):
                detail = response.json().get("detail")
                if isinstance(detail, dict) and detail.get("result") in {"disabled", "missing", "refused"}:
                    raise HTTPException(response.status_code, {"result": detail["result"], "published_row": None})
            if response.status_code == 422:
                detail = response.json().get("detail")
                if isinstance(detail, list):
                    raise HTTPException(422, detail)
            raise HTTPException(
                response.status_code
                if response.status_code in (403, 404, 409, 422, 503)
                else 502,
                "Memory request refused",
            )
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid memory response")
        return data
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "memory store unreachable") from None


@router.get("/records/{id}")
async def record(id: uuid.UUID, actor: Reviewer = Depends(require_reviewer)):
    if not actor.founder:
        raise HTTPException(404, "not found")
    return await hermes(
        "GET",
        "/founder/records/" + str(id),
        actor.user,
        tenant=str(actor.tenant),
    )


@router.post("/withdraw/{id}", dependencies=[Depends(require_admin)])
async def withdraw_record(id: uuid.UUID, request: Request, x_tervory_admin: str = Header(default="")):
    return await hermes("POST", "/founder/records/" + str(id) + "/withdraw", x_tervory_admin,
                        body=await request.json())


@router.api_route("/{operation}", methods=["GET", "POST"], dependencies=[Depends(require_admin)])
async def control(
    operation: str, request: Request, x_tervory_admin: str = Header(default="")
):
    configured = _PATHS.get(operation)
    if not configured or request.method != configured[0]:
        raise HTTPException(404, "not found")
    if request.method == "GET":
        return await hermes(
            "GET", configured[1], x_tervory_admin, params=dict(request.query_params)
        )
    return await hermes(
        "POST", configured[1], x_tervory_admin, body=await request.json()
    )


async def remember_turn(actor, messages: list[dict], task_id: uuid.UUID | None):
    """Exact explicit founder instruction. No model, new card or UI wording.

    The existing chat answer displays the stored claim and ID as DATA. A turn-stable id
    makes replay safe; tenant and user come exclusively from authenticated actor.
    """
    last = messages[-1] if messages else {}
    if last.get("role") != "user" or task_id is None:
        return None
    match = re.fullmatch(
        r"\s*remember(?:\s+for\s+([a-z][a-z0-9_]{0,40}))?(?:\s+that|\s*:)?\s+(.+?)\s*",
        last.get("content", ""),
        flags=re.I | re.S,
    )
    if not match:
        return None
    from command import authorized_actor

    actor = await authorized_actor(actor)
    if not actor.founder:
        return None
    agent, claim = (match[1] or "tank").lower(), match[2]
    row_id = str(uuid.uuid5(task_id, "memory:" + agent))
    result = await hermes(
        "POST",
        "/founder/memories",
        actor.user_id,
        body={
            "id": row_id,
            "tenant_id": str(actor.tenant_id),
            "agent": agent,
            "claim": claim,
            "evidence": ["urn:tervory:command:" + str(task_id)],
            "confidence": 0.5,
        },
    )
    if (
        result.get("id") != row_id
        or result.get("tenant_id") != str(actor.tenant_id)
        or result.get("claim") != claim
    ):
        raise HTTPException(502, "Memory record mismatch")
    from main import pool, box_region

    ledger_id = await pool().fetchval(
        """INSERT INTO tank.ledger
        (tenant_id,region,agent,task_type,rung,model,cost_usd,tokens_in,tokens_out,task_id)
        VALUES($1,$2,$3,'command.turn',3,'code',0,0,0,$4) RETURNING id""",
        actor.tenant_id,
        box_region(),
        agent,
        task_id,
    )
    return SimpleNamespace(
        content=json.dumps({"text": result["claim"] + "\n\n" + result["id"], "actions": []}),
        ledger_id=ledger_id,
    )
