"""Live gates 7+8 wire for Skala paid/model work.

Paid/model endpoints in Tank (vendor, READ ONLY — wire via UNAPPLIED tank-patches):
  - POST /v1/skala/{skill_id}/run  → make_ctx → SkillContext.model → _model_for_skill
  - SkillContext.model (tank/skala.py) — every skill model call
  - eval / preview paths that build SkillContext via make_ctx

CURRENT product surface (this module): wrap a paid callable with run_with_memory
using verified actor + Skala MemoryClient. Tank main/skala import of
wrap_paid_model / attach_skala_memory_routes stays in tank-patches/gates-7-8.patch
(UNAPPLIED) until Codex syncs.

Auth: verified tenant+user from set_verified_actor / request.state.verified_actor only.
Body/query/header identity selectors are not authority.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable

from skala_gates_memory import (
    MemoryScopeError,
    require_verified_memory_actor,
    run_with_memory,
    set_verified_actor,
)
from skala_memory_client import memory_client_for_skala


async def wrap_paid_model(
    *,
    memory: Any,
    tenant_id: str,
    user_id: str,
    task_key: str,
    task: dict[str, Any],
    paid_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    memory_control: str | None = None,
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Recall-before / remember-after bracket around one paid Tank agent invocation."""
    # Provenance must already be bound; refuse body-only assertions.
    proof = require_verified_memory_actor()
    if proof["tenant_id"] != str(tenant_id) or proof["user_id"] != str(user_id):
        raise MemoryScopeError(
            "Skala memory requires a verified signed-in tenant+user; "
            "body/query/header identity is not authority"
        )
    return await run_with_memory(
        memory,
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        task_key=task_key,
        task=deepcopy(task),
        paid_call=paid_call,
        memory_control=memory_control,
        code_free=code_free,
    )


def bind_verified_from_signin(signin: dict[str, Any] | None) -> dict[str, str]:
    """Map Trigger-sign-in proof → verified actor. Missing proof refuses."""
    if not isinstance(signin, dict) or not signin.get("tenant_id") or not signin.get("user_id"):
        raise MemoryScopeError(
            "Skala memory requires a verified signed-in tenant+user; "
            "body/query/header identity is not authority"
        )
    set_verified_actor(tenant_id=str(signin["tenant_id"]), user_id=str(signin["user_id"]))
    return {"tenant_id": str(signin["tenant_id"]), "user_id": str(signin["user_id"])}


def attach_skala_memory_routes(app: Any) -> None:
    """Mount view/edit/forget/off on a FastAPI app (live when Tank syncs)."""
    from skala_memory_api import attach_memory_routes

    attach_memory_routes(app)


def production_memory_or_raise() -> Any:
    """Live client: own key only. Call sites must not construct MemoryClient bare."""
    return memory_client_for_skala()


async def model_through_memory(runtime: Any, ctx: Any, call: Callable, **kw: Any) -> dict[str, Any]:
    """Current Tank make_ctx integration; existing route retains paid checkpoints."""
    import os
    from product_identity import current
    from skala_gates_memory import clear_verified_actor
    actor = current.get()
    if getattr(ctx, "live_evaluation", False):
        # Approval evidence must execute the model; never reuse a client's memory.
        return await call(**kw)
    if os.environ.get("SKALA_MEMORY_ENABLED", "").lower() not in {"1", "true", "yes", "on"} or actor is None:
        return await call(**kw)
    if actor.tenant_id != str(kw["tenant_id"]):
        raise MemoryScopeError("Skala memory tenant mismatch")
    clear_verified_actor()
    set_verified_actor(tenant_id=actor.tenant_id, user_id=actor.user_id)
    task = {key: kw.get(key) for key in ("messages", "skill", "task_type", "max_tokens", "home_region")}

    async def paid(_task):
        from fastapi.encoders import jsonable_encoder
        # RouteResponse.model_dump includes UUID ledger IDs. Persist a JSON value,
        # not a Python UUID that would fail after the provider already charged.
        answer = jsonable_encoder(await call(**kw))
        return {"cost_usd": answer.get("cost_usd"), "tokens_in": answer.get("tokens_in"),
                "tokens_out": answer.get("tokens_out"), "result": {
                    **answer, "outcome": answer.get("content"),
                    "skills_used": [kw["skill"]], "versions_worked": [kw["skill"].split("@")[-1]],
                    "failures": [answer["error"]] if answer.get("error") else [],
                }}
    try:
        receipt = await wrap_paid_model(
            memory=memory_client_for_skala(user_id=actor.user_id), tenant_id=actor.tenant_id,
            user_id=actor.user_id, task_key=kw["skill"], task=task, paid_call=paid,
        )
        result = dict(receipt["result"])
        if not receipt["paid_calls"]:
            request = runtime.RouteRequest(tenant_id=kw["tenant_id"], agent=kw["agent"],
                task_type=kw["task_type"], skill=kw["skill"], messages=kw["messages"])
            policy = await runtime.load_policy(kw["task_type"], kw["tenant_id"])
            saved = await runtime._record_local(request, policy, model="cache:skala-memory", rung=4,
                content=str(result.get("content", "")), outcome="delivered", task_id=kw.get("task_id"))
            result["ledger_id"] = str(saved.ledger_id)
        result.update(cost_usd=receipt["cost_usd"], tokens_in=receipt["tokens_in"], tokens_out=receipt["tokens_out"])
        receipt["ledger_id"] = str(result.get("ledger_id", ""))
        ctx.memory_receipts.append(receipt)
        return result
    except MemoryScopeError as exc:
        from fastapi import HTTPException
        raise HTTPException(403, "Skala memory authentication refused") from exc
    finally:
        clear_verified_actor()


async def run_skill_with_identity(skill_id: str, body: Any, caller: str | None,
                                  invoke: Callable) -> dict[str, Any]:
    """Carry the HTTP run identity into Tank's durable paid checkpoint.

    The caller supplies an idempotency ID, never an actor. The authenticated
    actor and complete input are bound in the existing checkpoint fingerprint.
    A retry returns the same receipt, including after a post-payment remember
    outage; concurrent workers use the existing SQL claim, never process state.
    """
    import uuid
    import os
    import paid_steps
    from fastapi import HTTPException
    from product_identity import current

    # DECISION The opt-in memory wrapper must not add checkpoints or database
    # access to the existing skill path when its setting is absent or false.
    if os.environ.get("SKALA_MEMORY_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return await invoke(skill_id, body, caller)

    actor = current.get()
    if actor is not None and actor.tenant_id != str(body.tenant_id):
        raise HTTPException(403, "Skill tenant does not match signed-in caller")
    user_id = actor.user_id if actor is not None else None
    payload = ["skala.run", str(body.tenant_id), user_id, caller, skill_id,
               body.model_dump(mode="json", exclude={"paid_run_id"})]
    inherited = paid_steps.current.get()
    if inherited is not None:
        if inherited.tenant_id != body.tenant_id or (inherited.user_id is not None and inherited.user_id != user_id):
            raise HTTPException(403, "Skill actor does not match its paid run")
        if body.paid_run_id is not None and body.paid_run_id != inherited.run_id:
            raise HTTPException(409, "Skill run does not match its paid run")
        return await paid_steps.checkpoint(paid_steps.fingerprint(payload), lambda: invoke(skill_id, body, caller))
    run = paid_steps.Run(body.tenant_id, body.paid_run_id or uuid.uuid4(),
                         paid_steps.fingerprint(payload), user_id=user_id)
    with paid_steps.run_scope(run):
        return await paid_steps.checkpoint(paid_steps.fingerprint(["skala.run"]),
                                           lambda: invoke(skill_id, body, caller))
