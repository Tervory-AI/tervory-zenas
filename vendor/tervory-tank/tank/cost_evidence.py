"""Evidence for the cost ladder: which rung-1 calls repeat, and the cheapest model that passes.

Three jobs, all founder/operator tools (nothing here runs on a request path):

* ``repeats``: rank rung-1 call patterns from the ledger and transcripts as
  graduation candidates (rung 4 semantic cache, rung 3 Skala code skill, or
  provider prompt caching only). Aggregates and hashes only; no prompt or
  answer text is printed.
* ``cheapest``: for each rung 1/2 platform policy, the cheapest roster model
  whose latest complete bench run scores at least the serving model's, from
  ``tank.bench_runs`` (the nightly bench, plus replays below).
* ``replay`` / ``score``: replay one task's platform eval suite on a candidate
  model through Tank's provider batch path (half price on OpenAI, Anthropic and
  Vertex Gemini seats) and record the result as a bench run, so evidence is
  produced at the batch rate instead of the synchronous bench's full price.

Usage, on a box with Tank's environment (DATABASE_URL, LITELLM_URL, ...)::

    PYTHONPATH=tank python -m cost_evidence repeats --days 30
    PYTHONPATH=tank python -m cost_evidence cheapest
    PYTHONPATH=tank python -m cost_evidence replay --task zenas.rule_card --model luna
    PYTHONPATH=tank python -m cost_evidence score --task zenas.rule_card --model luna --batch <id>

``replay`` only queues: the running Tank's batch worker submits and polls (paid
pause and the cost caps apply as for any batch). ``score`` writes nothing until
every item has finished.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

PLATFORM_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
REPLAY_NAMESPACE = uuid.UUID("6f0c2a51-7d43-4c55-9a8e-0b6b0c1e5a10")
# A pattern needs this many calls before its shares mean anything.
MIN_CALLS = 30
# Exact/near repeats the semantic cache would answer at rung 4.
CACHE_SHARE = 0.2
# Mostly-JSON answers with a stable shape are what a Skala code skill replaces.
SKILL_SHARE = 0.8


# ---------- repeats (pure) ----------

def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _messages(value: Any) -> list[dict]:
    raw = json.loads(value) if isinstance(value, str) else value
    return [m for m in raw or [] if isinstance(m, dict)]


def fixed_prefix(messages: list[dict]) -> list[dict]:
    """Everything but the final user turn: what provider prompt caching can reuse."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return messages[:i]
    return messages


def _shape(content: str) -> str | None:
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    if isinstance(value, dict):
        return _digest(sorted(value))
    if isinstance(value, list):
        return "list"
    return None


def rank_repeats(rows: list[dict], *, min_calls: int = MIN_CALLS) -> list[dict]:
    """rows: task_type, model, cost_usd, tokens_in, messages, content. One result per task type."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row["task_type"]), []).append(row)
    ranked = []
    for task, items in groups.items():
        calls = len(items)
        full = Counter(_digest(_messages(r["messages"])) for r in items)
        prefix = Counter(_digest(fixed_prefix(_messages(r["messages"]))) for r in items)
        shapes = Counter(_shape(str(r.get("content") or "")) for r in items)
        structured = calls - shapes.get(None, 0)
        top_shape = max((n for s, n in shapes.items() if s is not None), default=0)
        cost = sum(Decimal(str(r.get("cost_usd") or 0)) for r in items)
        exact = 1 - len(full) / calls
        shared_prefix = prefix.most_common(1)[0][1] / calls
        json_share = structured / calls
        stable_shape = top_shape / calls
        if calls < min_calls:
            verdict = "too few calls"
        elif exact >= CACHE_SHARE:
            verdict = "semantic cache (rung 4)"
        elif json_share >= SKILL_SHARE and stable_shape >= SKILL_SHARE:
            verdict = "Skala code skill (rung 3)"
        elif shared_prefix >= 0.5:
            verdict = "prompt caching only"
        else:
            verdict = "no repeat signal"
        ranked.append({
            "task_type": task, "calls": calls, "cost_usd": float(cost),
            "models": sorted({str(r.get("model")) for r in items}),
            "avg_tokens_in": round(sum(int(r.get("tokens_in") or 0) for r in items) / calls),
            "exact_repeat_share": round(exact, 4), "shared_prefix_share": round(shared_prefix, 4),
            "json_share": round(json_share, 4), "stable_shape_share": round(stable_shape, 4),
            "verdict": verdict,
            # Spend that a repeat would move off rung 1, the ranking key.
            "graduation_value_usd": float(cost) * max(exact, min(json_share, stable_shape)) if calls >= min_calls else 0.0,
        })
    return sorted(ranked, key=lambda r: (-r["graduation_value_usd"], -r["cost_usd"], r["task_type"]))


async def repeat_rows(conn, region: str, days: int) -> list[dict]:
    rows = await conn.fetch("""SELECT l.task_type,l.model,l.cost_usd,l.tokens_in,t.messages,t.content
        FROM tank.ledger l JOIN tank.transcripts t ON t.ledger_id=l.id AND t.tenant_id=l.tenant_id
        WHERE l.region=$1 AND l.rung=1 AND l.outcome='delivered' AND NOT l.cache_hit
          AND l.agent NOT IN ('bench','shadow') AND l.task_type NOT LIKE '%#%'
          AND l.ts > now() - make_interval(days=>$2)""", region, days)
    return [dict(r) for r in rows]


# ---------- cheapest passing model (pure) ----------

def cheapest_passing(policies: list[dict], runs: list[dict], prices: dict[str, dict]) -> list[dict]:
    """Latest complete run per (task, model); a candidate passes at >= the serving model's score."""
    latest: dict[tuple[str, str], dict] = {}
    for run in sorted(runs, key=lambda r: (r["finished_at"], str(r["id"]))):
        if run["status"] == "complete" and run.get("score") is not None:
            latest[(run["task_type"], run["model"])] = run
    report = []
    for policy in sorted(policies, key=lambda p: p["task_type"]):
        task, serving = policy["task_type"], policy["model"]
        mine = {model: run for (t, model), run in latest.items() if t == task}
        current = mine.get(serving)
        row = {"task_type": task, "serving": serving, "rung": policy["rung"],
               "serving_price": prices.get(serving), "benched_models": sorted(mine)}
        if not mine:
            row.update(cheapest=None, missing="no complete bench run for this task")
        elif current is None:
            row.update(cheapest=None, missing=f"no complete bench run for the serving model {serving}")
        else:
            passing = [r for m, r in mine.items() if float(r["score"]) >= float(current["score"])
                       and r.get("cost_per_call") is not None]
            best = min(passing, key=lambda r: (float(r["cost_per_call"]), r["model"]))
            row.update(cheapest=best["model"], cheapest_score=float(best["score"]),
                       cheapest_cost_per_call=float(best["cost_per_call"]), cheapest_n=int(best["expected_n"]),
                       serving_score=float(current["score"]),
                       serving_cost_per_call=float(current["cost_per_call"] or 0), missing=None)
        report.append(row)
    return report


async def cheapest_report(rt, conn) -> list[dict]:
    import bench
    policies = [dict(r) for r in await conn.fetch(
        "SELECT task_type,model,rung FROM tank.policy WHERE tenant_id IS NULL AND rung IN (1,2)")]
    runs = [dict(r) for r in await conn.fetch("""SELECT id,task_type,model,score,cost_per_call,expected_n,status,finished_at
        FROM tank.bench_runs WHERE tenant_id=$1 AND region=$2 AND finished_at IS NOT NULL""", PLATFORM_TENANT, rt.box_region())]
    book = bench.price_book(await rt._roster_models())
    prices = {m: {"input_per_m": round(float(p["input"]) * 1e6, 6), "output_per_m": round(float(p["output"]) * 1e6, 6)}
              for m, p in book.items()}
    return cheapest_passing(policies, runs, prices)


# ---------- replay through the batch path ----------

def replay_policy(policy: dict, model: str) -> dict:
    """The serving policy on another seat, with every side path off.

    No semantic-cache write, shadow, RouteLLM sample or MoA: a candidate's
    answer must never be served to anyone or train a route.
    """
    return {**policy, "model": model, "semantic_cache": False, "cache_scope": "none",
            "candidate_model": None, "shadow_rate": 0, "routellm_weak_model": None,
            "routellm_threshold": None, "moa_enabled": False}


async def replay(rt, task: str, model: str, *, cap_usd: Decimal | None = None) -> dict:
    import batch_providers
    import bench
    region = rt.box_region()
    try:
        policy = dict(await rt.load_policy(task, PLATFORM_TENANT))
    except rt.HTTPException:
        return {"status": "refused", "reason": "no platform policy for this task"}
    async with rt.pool().acquire() as conn:
        cases = await bench.cases_for(conn, task, region)
    if not cases:
        return {"status": "refused", "reason": "no platform evals, package cases or held-out gold for this task"}
    prices = await rt._batch_price(model)
    plan = replay_policy(policy, model)
    max_tokens = int(policy["max_tokens_out"])
    requests, items = [], []
    for position, case in enumerate(cases):
        messages = bench.messages_for(case["input"], policy.get("system_prompt"))
        if not batch_providers.compatible(prices, messages, {}):
            return {"status": "refused", "reason": f"{model} has no provider batch; use the nightly bench"}
        req = rt.RouteRequest(tenant_id=PLATFORM_TENANT, agent="bench", task_type=task, priority="batch",
                              interactive=False, case_ref=f"bench-replay:{case['ref']}",
                              messages=[rt.Message(**m) for m in messages if m["role"] != "system"])
        item = rt._DeferredRoute(req, plan, messages, max_tokens, {}, [], None)
        item.position = position
        requests.append(req)
        items.append(item)
    reserve = sum(rt._call_upper(i.messages, max_tokens, prices) * prices["discount"] for i in items)
    cap = cap_usd if cap_usd is not None else Decimal(os.environ.get("TANK_BENCH_MAX_USD") or "5")
    if Decimal(str(reserve)) > cap:
        return {"status": "refused", "reason": f"reserve ${reserve:.4f} is over the bench cap ${cap}"}
    day = datetime.now(timezone.utc).date().isoformat()
    suite = hashlib.sha256(json.dumps(cases, sort_keys=True, default=str).encode()).hexdigest()
    body = rt.BatchRequest(id=uuid.uuid5(REPLAY_NAMESPACE, f"{region}|{task}|{model}|{suite}|{day}"), requests=requests)
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json", exclude={"id"}), sort_keys=True).encode()).hexdigest()
    queued = await rt._enqueue_batch(body, "bench", [], items, fingerprint)
    return {"status": queued["status"], "batch_id": str(body.id), "cases": len(cases),
            "reserved_usd": round(float(reserve), 6), "suite_hash": suite}


async def score(rt, task: str, model: str, batch_id: uuid.UUID) -> dict:
    import bench
    import graduate
    region = rt.box_region()
    batch = await rt.batch_status(batch_id, PLATFORM_TENANT)
    if batch["status"] not in {"completed", "failed"}:
        return {"status": "pending", "batch_status": batch["status"]}
    if batch["task_type"] != task or batch["model"] != model:
        return {"status": "refused", "reason": "this batch is a different task or model"}
    policy = dict(await rt.load_policy(task, PLATFORM_TENANT))
    async with rt.pool().acquire() as conn:
        cases = {c["ref"]: c for c in await bench.cases_for(conn, task, region)}
        refs = {r["position"]: json.loads(r["request"]).get("case_ref") if isinstance(r["request"], str) else r["request"].get("case_ref")
                for r in await conn.fetch("SELECT position,request FROM tank.batch_items WHERE batch_id=$1 AND tenant_id=$2",
                                          batch_id, PLATFORM_TENANT)}
    scores, list_costs, batch_costs, unverified = [], [], [], 0
    for item in batch["results"]:
        ref = str(refs.get(item["position"]) or "").removeprefix("bench-replay:")
        case = cases.get(ref)
        if case is None or item["status"] != "completed" or item.get("model") != model or not item.get("content"):
            unverified += 1
            continue
        scores.append(graduate.score_pair(case["expected"], item["content"],
                                          tolerance=float(policy.get("number_tolerance") or .01)))
        list_costs.append(Decimal(str(item.get("baseline_cost_usd") or item.get("cost_usd") or 0)))
        batch_costs.append(Decimal(str(item.get("cost_usd") or 0)))
    status = "complete" if scores and not unverified else "failed"
    result = {"task_type": task, "model": model, "n": len(scores), "unverified": unverified, "status": status,
              "score": sum(scores) / len(scores) if scores else None,
              # Comparable with the synchronous bench: what one live call would cost.
              "cost_per_call": float(sum(list_costs) / len(list_costs)) if list_costs else None,
              "batch_cost_per_call": float(sum(batch_costs) / len(batch_costs)) if batch_costs else None}
    async with rt.pool().acquire() as conn, conn.transaction():
        exists = await conn.fetchval("""SELECT id FROM tank.bench_runs WHERE tenant_id=$1 AND region=$2
            AND task_type=$3 AND model=$4 AND suite_hash=$5""", PLATFORM_TENANT, region, task, model, f"batch:{batch_id}")
        if exists:
            return {**result, "bench_run_id": str(exists), "recorded": False}
        run_id = await conn.fetchval("""INSERT INTO tank.bench_runs(tenant_id,region,task_type,model,comparison_model,
            suite_hash,expected_n,score,cost_per_call,status,error,finished_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now()) RETURNING id""",
            PLATFORM_TENANT, region, task, model, policy["model"], f"batch:{batch_id}", len(batch["results"]),
            result["score"], result["cost_per_call"], status,
            None if status == "complete" else f"{unverified} item(s) unverified; no evidence recorded")
        if status == "complete":
            await conn.execute("""INSERT INTO tank.route_evidence(task_type,model,rung,bench_run_id,bench_score,bench_cost_per_call,bench_n,bench_at)
                VALUES($1,$2,1,$3,$4,$5,$6,now()) ON CONFLICT(task_type,model,rung) DO UPDATE SET
                bench_run_id=$3,bench_score=$4,bench_cost_per_call=$5,bench_n=$6,bench_at=now()""",
                task, model, run_id, result["score"], result["cost_per_call"], len(scores))
    return {**result, "bench_run_id": str(run_id), "recorded": True}


# ---------- CLI ----------

async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cost_evidence", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("repeats")
    rep.add_argument("--days", type=int, default=30)
    rep.add_argument("--min-calls", type=int, default=MIN_CALLS)
    sub.add_parser("cheapest")
    for name in ("replay", "score"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--task", required=True)
        cmd.add_argument("--model", required=True)
        if name == "score":
            cmd.add_argument("--batch", required=True, type=uuid.UUID)
    args = parser.parse_args(argv)
    import gold
    from tenant_db import tenant_scope
    rt = gold.runtime()
    await rt._startup()
    if args.command == "repeats":
        # Every tenant's rung-1 traffic: needs TANK_ADMIN_DATABASE_URL (BYPASSRLS). Aggregates only.
        if rt._admin_pool is None:
            raise SystemExit("repeats reads all tenants: set TANK_ADMIN_DATABASE_URL")
        with tenant_scope(None, administrative=True):
            async with rt.pool().acquire(readonly=True) as conn:
                out: Any = rank_repeats(await repeat_rows(conn, rt.box_region(), args.days), min_calls=args.min_calls)
    else:
        with tenant_scope(PLATFORM_TENANT):
            if args.command == "cheapest":
                async with rt.pool().acquire(readonly=True) as conn:
                    out = await cheapest_report(rt, conn)
            elif args.command == "replay":
                out = await replay(rt, args.task, args.model)
            else:
                out = await score(rt, args.task, args.model, args.batch)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
