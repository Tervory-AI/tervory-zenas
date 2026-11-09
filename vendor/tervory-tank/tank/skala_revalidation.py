"""B3: durable policy-change evals through the existing Skala endpoint handler.

No policy selection, approval, cache, shadow, judge or paid search occurs here.
Only the model calls requested by the package's eval cases may execute.
"""
from __future__ import annotations

from tenant_db import tenant_scope

import asyncio
import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from gold import PLATFORM_TENANT

ACTIVE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("skala_revalidation", default=None)
WORKER_LOCK = 73202103
log = logging.getLogger(__name__)


def eval_context(tank: Any, pkg: Any, ctx: Any) -> Any:
    # Manual approval evals must use the configured Tank path too. Saved model
    # answers are for the offline package suite, never approval evidence.
    ctx.live_evaluation = True
    job = ACTIVE.get()
    if job is None:
        return ctx
    policy = job["policy"]
    if isinstance(policy, str):
        policy = json.loads(policy)

    async def model(**kwargs):
        if kwargs["task_type"] != job["task_type"]:
            raise ValueError("Revalidation task type does not match the package")
        messages = list(kwargs["messages"])
        if policy.get("system_prompt"):
            messages.insert(0, {"role": "system", "content": policy["system_prompt"]})
        ceiling = int(policy["max_tokens_out"])
        max_tokens = min(kwargs.get("max_tokens") or ceiling, ceiling)
        owner = job["policy_tenant_id"] or PLATFORM_TENANT
        target = job["model"]
        receipt: dict = {"_paid_task_type": job["task_type"], "_paid_agent": pkg.agent, "_paid_skill": pkg.ref + "#preview"}
        started = time.perf_counter()
        cost, outcome, error = 0.0, "unverified", None
        routellm_decision = None
        served_decision = None
        try:
            if str(target).startswith("routellm:"):
                import routellm_shadow
                routellm_decision = await routellm_shadow.serve(
                    tank, {**policy, "model": target}, messages, tenant_id=owner,
                    task_type=job["task_type"], skill=pkg.ref)
                target = routellm_decision["model"]
            if tank.graduate.is_skill(target):
                request = tank.RouteRequest(tenant_id=owner, agent=pkg.agent,
                    task_type=job["task_type"], skill=pkg.ref + "#preview",
                    messages=[tank.Message(**m) for m in messages])
                content, cost = await tank._run_code_candidate(request, target, messages)
            else:
                if tank.graduate.router_cannot_serve(target):
                    raise ValueError("Unsupported model target")
                blocked = tank.model_call_refusal(
                    "\n".join(str(m.get("content", "")) for m in messages), target,
                    box=tank.box_region(), tenant_id=str(owner), task_type=job["task_type"], skill=pkg.ref)
                if blocked:
                    raise ValueError("Model evaluation is not permitted in this region")
                # Existing Tank helper: disables provider fallbacks when receipt
                # is supplied and refuses an answer from a different model.
                content, cost = await tank._llm(target, messages, max_tokens, receipt=receipt)
                served_decision = routellm_decision
            return {"content": content, "cost_usd": cost}
        except Exception:
            ctx.calls += 1  # SkillContext counts successful calls on return.
            cost = max(cost, float(receipt.get("cost") or 0))
            ctx.cost_usd += cost
            outcome, error = "failed", "skill_revalidation_call_failed"
            raise RuntimeError("Model revalidation call failed") from None
        finally:
            if receipt.get("_paid_ledger_id"):
                ledger_id = uuid.UUID(str(receipt["_paid_ledger_id"]))
                with tenant_scope(PLATFORM_TENANT):
                    async with tank.pool().acquire() as conn:
                        await conn.execute("""UPDATE tank.ledger SET revalidation_id=$3
                            WHERE id=$1 AND tenant_id=$2""", ledger_id, PLATFORM_TENANT, job["id"])
                    if served_decision is not None:
                        await routellm_shadow.record_served(tank, PLATFORM_TENANT, ledger_id, served_decision)
            elif not receipt.get("_paid_waiting"):
                async with tank.pool().acquire() as conn:
                    await conn.execute(
                        """INSERT INTO tank.ledger
                        (tenant_id,region,agent,skill,task_type,rung,model,priority,tokens_in,tokens_out,
                         cost_usd,latency_ms,cache_hit,outcome,error,
                         routellm_model,routellm_score,routellm_threshold,routellm_weak_model,routellm_config,routellm_status,revalidation_id)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,'standard',$8,$9,$10,$11,false,$12,$13,$14,$15,$16,$17,$18,$19,$20)""",
                        PLATFORM_TENANT, tank.box_region(), pkg.agent, pkg.ref + "#preview",
                        job["task_type"], 3 if tank.graduate.is_skill(target) else 1, target,
                        receipt.get("tokens_in", 0), receipt.get("tokens_out", 0), cost,
                        int((time.perf_counter() - started) * 1000), outcome, error,
                        served_decision["model"] if served_decision else None,
                        served_decision["score"] if served_decision else None,
                        served_decision["threshold"] if served_decision else None,
                        served_decision["weak_model"] if served_decision else None,
                        served_decision["config"] if served_decision else None,
                        "served" if served_decision else None,
                        job["id"],
                    )
    ctx._model = model
    return ctx


async def record(tank: Any, result: dict, by: str | None = None) -> dict:
    job = ACTIVE.get()
    if job is None:
        raise RuntimeError("Missing policy change")
    gate = result["score"] >= result["threshold"] and result["eligible"]
    eval_id = uuid.uuid4()
    validation = {
        "model": job["model"], "old_model": job["old_model"], "task_type": job["task_type"],
        "policy_tenant_id": str(job["policy_tenant_id"]) if job["policy_tenant_id"] else None,
        "revalidation_id": str(job["id"]), "eval_id": str(eval_id),
        "score": result["score"], "passed": result["passed"], "total": result["total"],
        "validated": gate, "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    captured = job["policy"]
    captured = json.loads(captured) if isinstance(captured, str) else captured
    roster = captured.get("_roster_fingerprint")
    if roster:
        validation["roster_fingerprint"] = roster
    async with tank.pool().acquire() as conn:
        async with conn.transaction():
            await tank.skala.lock_approvals(conn)
            completed = await conn.fetchval(
                "SELECT completed_at FROM tank.skill_revalidations WHERE id=$1 AND tenant_id=$2 FOR UPDATE",
                job["id"], PLATFORM_TENANT)
            if completed is not None:
                return result
            await conn.execute(
                """INSERT INTO tank.skill_evals
                (skill_id,version,score,passed,total,cost_usd,details,ran_by,id,model,revalidation_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11)""",
                job["skill_id"], job["version"], result["score"], result["passed"], result["total"],
                result["cost_usd"], json.dumps(result["details"]), by or "policy model change",
                eval_id, job["model"], job["id"],
            )
            state = await conn.fetchval("SELECT state FROM tank.skills WHERE id=$1 AND version=$2",
                                        job["skill_id"], job["version"])
            # A rejected swap keeps both the serving model and its skill approval.
            # Manual evaluations retain their existing demotion behavior.
            new_state = state
            await conn.execute(
                """UPDATE tank.skills SET last_eval_score=$3,last_eval_at=now(),state=$4,
                models_validated=models_validated || $5::jsonb,updated_at=now()
                WHERE id=$1 AND version=$2""",
                job["skill_id"], job["version"], result["score"], new_state, json.dumps([validation]),
            )
            await conn.execute(
                "UPDATE tank.skill_revalidations SET completed_at=now(),eval_id=$3,error=$4 WHERE id=$1 AND tenant_id=$2",
                job["id"], PLATFORM_TENANT, eval_id, None if gate else "eval_gate_failed",
            )
            pending_roster = await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM tank.skill_revalidations
                WHERE tenant_id=$1 AND completed_at IS NULL AND policy ? '_roster_fingerprint')""",
                PLATFORM_TENANT)
            observed_roster = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM tank.roster_observations WHERE tenant_id=$1 AND region=$2)",
                PLATFORM_TENANT, tank.box_region())
            if not pending_roster and not observed_roster:
                await conn.execute(
                    "SELECT tank.finish_policy_swap($1,$2,$3)",
                    job["task_type"], job["policy_tenant_id"], job.get("swap_id"),
                )
    result.update(state=new_state, gate="passed" if gate else "failed", model=job["model"])
    return result


def failed_result(tank: Any, job: dict) -> dict:
    pkg = tank._packages.get((job["skill_id"], job["version"]))
    details = []
    if pkg is not None:
        for i, case in enumerate(pkg.cases):
            details.append({"case": i, "name": case.get("name"), "passed": False,
                "why": "error: revalidation unavailable", "goal": case.get("expected", {}), "result": {},
                "case_hash": tank.skala.evidence_hash(case), "package_hash": tank.skala.package_hash(pkg),
                "used_model": False})
    return {"skill": job["skill_id"] + "@" + job["version"], "score": 0.0,
            "threshold": pkg.eval_threshold if pkg else 1.0, "eligible": False,
            "passed": 0, "total": len(details), "cost_usd": 0.0, "details": details}


async def drain(tank: Any, limit: int = 20) -> int:
    """One same-box worker; completing an eval and its job is one transaction."""
    count = 0
    async with tank.pool().acquire() as lock:
        if not await lock.fetchval("SELECT pg_try_advisory_lock($1)", WORKER_LOCK):
            return 0
        try:
            for _ in range(limit):
                row = await lock.fetchrow(
                    "SELECT * FROM tank.skill_revalidations WHERE tenant_id=$1 AND completed_at IS NULL ORDER BY queued_at,id LIMIT 1",
                    PLATFORM_TENANT)
                if row is None:
                    break
                job = dict(row)
                token = ACTIVE.set(job)
                try:
                    pkg = tank.get_package(job["skill_id"], job["version"])
                    captured = job["policy"]
                    captured = json.loads(captured) if isinstance(captured, str) else captured
                    code_only = captured.get("_roster_code") and pkg.task_type is None
                    if not job["model"]:
                        raise ValueError("No serving policy for this approval")
                    if not code_only and pkg.task_type != job["task_type"]:
                        raise ValueError("Package task type changed")
                    # The exact handler used by /v1/skala/{id}/{version}/evals.
                    import paid_steps
                    with paid_steps.run_scope(paid_steps.Run(PLATFORM_TENANT, job["id"],
                            paid_steps.fingerprint([job["id"], job["policy"]]))), paid_steps.background_scope(job["task_type"] + ".revalidation"):
                        await tank.run_skill_evals(pkg.id, pkg.version)
                except __import__("paid_steps").ApprovalRequired:
                    # Leave this committed job queued. The same job/run identity
                    # observes approval on the next worker pass, without marking an eval failed.
                    break
                except Exception:
                    log.warning("Skala model revalidation unavailable for %s@%s", job["skill_id"], job["version"])
                    await record(tank, failed_result(tank, job))
                finally:
                    ACTIVE.reset(token)
                count += 1
            # A target eval may finish before another skill's roster eval.
            # Keep its completed receipt and re-read live membership before
            # releasing a held proposal; never re-run a completed paid eval.
            held_exists = await lock.fetchval("SELECT EXISTS(SELECT 1 FROM tank.policy WHERE held_swap_id IS NOT NULL)")
            if not held_exists:
                return count
            try:
                await __import__("roster_validation").before_policy_change(tank)
            except Exception as exc:
                log.warning("Skala policy release deferred: %s", type(exc).__name__)
                return count
            async with lock.transaction():
                await tank.skala.lock_approvals(lock)
                pending = await lock.fetchval(
                    """SELECT EXISTS(SELECT 1 FROM tank.skill_revalidations
                    WHERE tenant_id=$1 AND completed_at IS NULL AND policy ? '_roster_fingerprint')""",
                    PLATFORM_TENANT)
                if not pending:
                    held = await lock.fetch(
                        "SELECT task_type,tenant_id,held_swap_id FROM tank.policy WHERE held_swap_id IS NOT NULL ORDER BY task_type,tenant_id")
                    for proposal in held:
                        await lock.execute("SELECT tank.finish_policy_swap($1,$2,$3)",
                            proposal["task_type"], proposal["tenant_id"], proposal["held_swap_id"])
        finally:
            await lock.execute("SELECT pg_advisory_unlock($1)", WORKER_LOCK)
    return count


def install(app: Any, tank: Any) -> None:
    task = None

    async def work():
        next_roster = 0.0
        while True:
            if time.monotonic() >= next_roster:
                next_roster = time.monotonic() + 60
                try:
                    await __import__("roster_validation").observe(tank)
                except Exception as exc:
                    log.warning("Skala roster observation: %s", type(exc).__name__)
            try:
                await drain(tank)
            except Exception:
                log.warning("Skala model revalidation deferred; queued work retained")
            await asyncio.sleep(2)

    @app.on_event("startup")
    async def start():
        nonlocal task
        with tenant_scope(None, administrative=True):
            task = asyncio.create_task(work())

    async def stop():
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # Cancel before Tank closes its HTTP client and database pool.
    app.router.on_shutdown.insert(0, stop)
