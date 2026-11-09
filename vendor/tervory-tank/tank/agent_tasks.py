"""Typed room task adapter; reuse room policy, accounting and signed forwarding.

A proposal for Head review. No new provider path or deployment configuration.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from task_contract import check_receipt, origin, paid_identity
from command_tools._user_assertion import PostgresNonces, signed_user
from product_identity import actor_scope


class TaskScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    user_id: str = Field(min_length=1, max_length=200)
    run_id: UUID


class RoomTask(TaskScope):
    task_key: str | None = None
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=512)


class DelegatedTask(TaskScope):
    task_key: str = Field(min_length=1, max_length=120)
    input: dict[str, Any]
    recall: list[dict[str, Any]]


def install(router, tank) -> None:
    from agent_skills import Chat, complete, local_region, room_key

    async def actor_for(room: str, body: TaskScope, request: Request):
        if room not in {"tiger", "radix", "ghost", "zenas"}:
            raise HTTPException(403, "Room task unavailable")
        room_key(room, request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        setting = {"radix": "RADIX_BRAIN_ENABLED", "tiger": "TIGER_BRAIN_ENABLED", "ghost": "GHOST_BRAIN_ENABLED", "zenas": "ZENAS_BRAIN_ENABLED"}[room]
        if os.environ.get(setting, "false").lower() not in {"true", "1", "yes"}:
            raise HTTPException(409, {"radix": "Radix brain is off", "tiger": "Tiger brain is off", "ghost": "GHOST_BRAIN_ENABLED is off.", "zenas": "Zenas brain is off"}[room])
        if room == "radix" and request.headers.get("x-home-region") != tank.box_region():
            raise HTTPException(403, "Region mismatch")
        local_region(tank, request.headers.get("x-home-region"), room=room)
        actor = await signed_user(request, tenant_id=str(body.tenant_id), product=room,
                                  required=True, nonces=PostgresNonces(tank.pool(), "tank"))
        if actor.user_id != body.user_id:
            raise HTTPException(403, "Actor mismatch")
        if room == "radix":
            # Recheck the existing Radix grant and tenant home on both legs.
            # Signed role/body selectors never create squad or founder rights.
            from command import Actor, authorized_actor, require_tool
            scoped = await authorized_actor(Actor(body.tenant_id, actor.user_id, False))
            require_tool(scoped, "radix.repo.scan")
            if isinstance(body, DelegatedTask) and (body.task_key != "radix.site_risk" or body.recall):
                raise HTTPException(422, "Invalid Radix task")
        return actor

    @router.post("/v1/agents/{room}/tasks")
    async def room_task(room: str, body: RoomTask, request: Request):
        actor = await actor_for(room, body, request)
        identity = UUID(paid_identity(actor.tenant_id, actor.user_id, str(body.run_id)))
        if body.task_key is not None:
            if room != "zenas" or body.task_key != "zenas.scope":
                raise HTTPException(422, "Unsupported code task")
            from agent_skills import validate_chat, zenas_scope_rules
            import paid_steps
            from tenant_db import tenant_scope
            from trigger_routes import code_receipt
            validate_chat(Chat(messages=body.messages))
            region = local_region(tank, request.headers.get("x-home-region"), room=room)
            version = await tank._pick_version("zenas.scope", False)
            package = tank.get_package("zenas.scope", version)
            result = zenas_scope_rules(package.path).classify(
                json.dumps(body.messages, ensure_ascii=False), "za" if region == "za" else "us")
            digest = paid_steps.fingerprint([body.task_key, body.messages])
            async def finish_scope():
                with tenant_scope(actor.tenant_id):
                    async with tank.pool().acquire() as conn, conn.transaction():
                        ledger = await code_receipt(body.tenant_id, "agent.zenas.think",
                            task_id=identity, agent="zenas", conn=conn)
                        bill = {"ledger_id": str(ledger), "tenant_id": actor.tenant_id,
                            "task_id": str(identity), "room": "zenas", "task_type": "agent.zenas.think",
                            "region": region, "model": "code", "cost_usd": 0,
                            "tokens_in": 0, "tokens_out": 0}
                        await conn.execute("INSERT INTO tank.agent_receipts(ledger_id,tenant_id,room,receipt) "
                            "VALUES($1,$2,'zenas',$3::jsonb)", ledger, body.tenant_id, json.dumps(bill))
                return {"choices": [{"message": {"content": json.dumps(result)}}], "receipt": bill}
            with paid_steps.run_scope(paid_steps.Run(body.tenant_id, identity, digest)):
                return await paid_steps.checkpoint(digest, finish_scope)
        # Same room policy, paid admission/checkpoint, ledger and agent_receipts
        # as the existing chat handler. The adapter never fabricates a receipt.
        return await complete(tank, Chat(messages=body.messages, paid_run_id=identity),
                              body.tenant_id, identity,
                              local_region(tank, request.headers.get("x-home-region"), room=room), room=room)

    @router.post("/v1/agents/{room}/delegate")
    async def delegate_task(room: str, body: DelegatedTask, request: Request):
        if room == "rig":
            from rig_agent_tasks import delegate_task as rig_delegate
            return await rig_delegate(tank, body, request)
        actor = await actor_for(room, body, request)
        unavailable = {"tiger": "Tiger task unavailable", "radix": "Radix task unavailable", "ghost": "Autumbra task unavailable", "zenas": "Zenas task unavailable"}[room]
        try:
            base = origin(os.environ.get("TIGER_BRAIN_URL", "http://tiger-brain:8001") if room == "tiger" else f"http://{room}-brain:8001")
            parsed = urlsplit(base)
            # Private service only; this directory never accepts a caller URL.
            if parsed.scheme != "http" or parsed.hostname != room + "-brain" or parsed.port != 8001:
                raise ValueError("Invalid private origin")
        except ValueError:
            raise HTTPException(503, unavailable) from None
        target = "/tasks?" + urlencode({"tenant_id": actor.tenant_id, "user_id": actor.user_id})
        content = json.dumps({"task_key": body.task_key, "input": body.input,
                              "recall": body.recall, "run_id": str(body.run_id)}).encode()
        if len(content) > 1_000_000:
            raise HTTPException(413, "Task is too large")
        try:
            # Reuse Tank's production paid_http signing hook with the exact
            # configured origin, verified actor, raw target and body.
            with actor_scope(actor):
                if room == "zenas":
                    discovery = await tank._http.get(base + "/health",
                        extensions={"tervory_product_origin": (room, (parsed.scheme, parsed.hostname, parsed.port))},
                        timeout=5, follow_redirects=False)
                    discovery.raise_for_status()
                    health = discovery.json()
                    if not isinstance(health, dict) or health.get("ok") is not True or health.get("room") != room:
                        raise ValueError("Invalid room health")
                response = await tank._http.post(base + target, content=content,
                    headers={"Content-Type": "application/json", "X-Tank-Key": os.environ["TANK_KEY_" + room.upper()],
                             **({"X-Home-Region": tank.box_region()} if room in {"radix", "zenas"} else {})},
                    extensions={"tervory_product_origin": (room, (parsed.scheme, parsed.hostname, parsed.port))},
                    timeout=5 if room == "zenas" else 70, follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            check_receipt(data, actor.tenant_id, actor.user_id, str(body.run_id), room)
            if room == "radix" and data["receipt"].get("region") != tank.box_region():
                raise ValueError("Receipt region mismatch")
            if room == "zenas":
                from tenant_db import tenant_scope
                bill = data["receipt"]
                with tenant_scope(actor.tenant_id):
                    row = await tank.pool().fetchrow("""SELECT a.receipt FROM tank.ledger l
                        JOIN tank.agent_receipts a ON a.tenant_id=l.tenant_id AND a.ledger_id=l.id AND a.room='zenas'
                        WHERE l.tenant_id=$1 AND l.id=$2 AND l.task_id=$3 AND l.region=$4
                          AND l.agent='zenas' AND l.task_type='agent.zenas.think'""",
                        UUID(actor.tenant_id), UUID(bill["ledger_id"]),
                        UUID(paid_identity(actor.tenant_id, actor.user_id, str(body.run_id))),
                        local_region(tank, request.headers.get("x-home-region"), room=room))
                # The ledger has fixed monetary precision; its canonical task
                # receipt preserves provider precision. Compare the whole saved
                # receipt rather than rejecting or rounding a valid cheap call.
                stored = row["receipt"] if row else None
                if isinstance(stored, str):
                    stored = json.loads(stored)
                if stored != bill:
                    raise ValueError("Unreconciled room receipt")
            return data
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if room == "radix":
                # Do not discard an actual billed-failure receipt from complete().
                try:
                    detail = response.json()["detail"]
                    check_receipt(detail, actor.tenant_id, actor.user_id, str(body.run_id), room)
                    if detail["receipt"].get("region") != tank.box_region():
                        raise ValueError("Receipt region mismatch")
                    error = {"message": "Radix task unavailable", "receipt": detail["receipt"]}
                except (ValueError, KeyError, TypeError, OverflowError):
                    error = "Radix task unavailable"
                raise HTTPException(status if 400 <= status < 600 else 502, error) from None
            raise HTTPException(status if 400 <= status < 600 else 502, unavailable) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
            raise HTTPException(502, unavailable) from None
