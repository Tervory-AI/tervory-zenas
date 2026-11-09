"""Shared gold sets. Only trusted Workspace reviewers or founders may read them.

The router lifespan imports packages after Tank startup. No skill lifecycle or
routing policy is changed here. A final holdout check is an explicit operation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

PLATFORM_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
AGENTS = ("tank", "base", "tiger", "market_lab", "zenas", "radix", "tisan", "rig", "ghost", "trigger")


def runtime():
    import main
    return main


def obj(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def input_hash(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def is_holdout(tenant: uuid.UUID, skill: str, value: Any) -> bool:
    # Same input in multiple package versions or sources always stays on the
    # same side. Adding a case, correcting an answer or restarting cannot move it.
    # Content-only splitting also seals copied examples across tenants/skills
    # when their approved training pairs reach the same per-agent dataset.
    return int(input_hash(value), 16) % 5 == 0


@dataclass(frozen=True)
class Reviewer:
    user: str
    tenant: uuid.UUID
    founder: bool


async def require_reviewer(
    x_tank_key: str = Header(default=""),
    x_tervory_user: str = Header(default=""),
    x_tervory_tenant: uuid.UUID | None = Header(default=None),
    x_tervory_role: str = Header(default=""),
) -> Reviewer:
    expected = os.environ.get("TANK_API_KEY", "")
    founders = __import__("trigger_signin_config").founders()
    if not expected or not hmac.compare_digest(expected, x_tank_key) or not x_tervory_user:
        raise HTTPException(404, "not found")
    founder = x_tervory_user in founders
    if not founder and x_tervory_role != "reviewer":
        raise HTTPException(404, "not found")
    if x_tervory_tenant is None:
        raise HTTPException(404, "not found")
    return Reviewer(x_tervory_user, x_tervory_tenant, founder)


def allowed_agent(actor: Reviewer, agent: str) -> None:
    if agent not in AGENTS or (not actor.founder and agent != "zenas"):
        raise HTTPException(404, "not found")


async def require_job(x_tank_key: str = Header(default="")) -> None:
    # A dedicated job key, never an agent key or a URL parameter.
    expected = os.environ.get("TRAINING_JOB_KEY", "")
    if not expected or not hmac.compare_digest(expected, x_tank_key):
        raise HTTPException(404, "not found")


async def add_candidate(conn, *, tenant: uuid.UUID, skill: str, agent: str,
                        value: Any, expected: Any, source: str, ref: str,
                        tolerance: str = "exact", holdout: bool = False) -> uuid.UUID:
    return await conn.fetchval(
        """INSERT INTO tank.gold_examples
        (tenant_id,region,skill_id,agent,input,expected,source,source_ref,input_hash,holdout,tolerance)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10,$11)
        ON CONFLICT (tenant_id,region,skill_id,source,source_ref)
        DO UPDATE SET source_ref=EXCLUDED.source_ref RETURNING id""",
        tenant, runtime().box_region(), skill, agent, encoded(value), encoded(expected),
        source, ref, input_hash(value), holdout or is_holdout(tenant, skill, value), tolerance,
    )


async def import_packages() -> None:
    async with runtime().pool().acquire() as conn:
        for pkg in runtime()._packages.values():
            for case in pkg.cases:
                # Refusal cases (expected_error only) are eval checks, not gold answers.
                if "expected" not in case:
                    continue
                value, expected = case["input"], case["expected"]
                await add_candidate(conn, tenant=PLATFORM_TENANT, skill=pkg.id,
                                    agent=pkg.agent, value=value, expected=expected,
                                    source="package", ref=input_hash([value, expected]),
                                    tolerance=case.get("tolerance", "exact"))


@asynccontextmanager
async def lifespan(_app):
    await import_packages()
    yield


router = APIRouter(prefix="/v1/gold", lifespan=lifespan)


@router.get("/summary")
async def summary(actor: Reviewer = Depends(require_reviewer)) -> dict[str, Any]:
    region = runtime().box_region()
    async with runtime().pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT agent,skill_id,count(DISTINCT input_hash) FILTER (WHERE status='checked' AND (NOT auto_label OR auto_approved)) AS checked,
            count(DISTINCT input_hash) FILTER (WHERE status='checked' AND (NOT auto_label OR auto_approved) AND holdout) AS holdout,
            count(*) FILTER (WHERE status='candidate') AS candidates
            FROM tank.gold_examples g WHERE tenant_id=$1 AND region=$2
            AND ($3 OR agent='zenas') GROUP BY agent,skill_id ORDER BY agent,skill_id""",
            actor.tenant, region, actor.founder,
        )
        pairs = await conn.fetch(
            """SELECT agent,count(*) AS n FROM tank.training_pairs
            WHERE tenant_id=$1 AND region=$2 AND active AND ($3 OR agent='zenas') GROUP BY agent""",
            actor.tenant, region, actor.founder,
        )
        scores = await conn.fetch(
            """SELECT skill_id,version,score,ran_at FROM (
              SELECT *,row_number() OVER (PARTITION BY skill_id ORDER BY ran_at DESC,id DESC) AS rn
              FROM tank.gold_scores WHERE tenant_id=$1 AND region=$2 AND NOT final_check
            ) s WHERE rn<=2 ORDER BY skill_id,rn""", actor.tenant, region,
        )
    history: dict[str, list] = {}
    for score in scores:
        history.setdefault(score["skill_id"], []).append(dict(score))
    skills = []
    for row in rows:
        item = dict(row)
        recent = history.get(item["skill_id"], [])
        item.update(target=100, last_score=float(recent[0]["score"]) if recent else None,
                    ran_at=recent[0]["ran_at"] if recent else None,
                    trend=round(float(recent[0]["score"])-float(recent[1]["score"]), 4) if len(recent)>1 else None)
        # Scores are the durable drop record; the existing founder card renders
        # this signed trend, including a planted three-point regression.
        item["drop"] = item["trend"] is not None and item["trend"] < -0.02
        skills.append(item)
    skills.sort(key=lambda row: (max(0, 100 - row["checked"]), row["skill_id"]))
    return {"skills": skills, "pairs": {r["agent"]: r["n"] for r in pairs}, "region": region}



def retire_gold_rows(rows: list[dict[str, Any]], batch_id: str) -> list[dict[str, Any]]:
    """Gold that came from a removed batch is retired and cannot train."""
    retired: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("batch_id") or "") == batch_id and row.get("status") != "rejected":
            retired.append({**row, "status": "rejected", "note": "Batch removed"})
        else:
            retired.append(dict(row))
    return retired


def source_verdict(statuses: list[str]) -> str:
    if len(statuses) < 10 or "candidate" in statuses:
        return "pending"
    return "passed" if statuses.count("checked") >= 9 else "review"


# Separate explicit families from exact-source fallbacks, even if their text matches.
FAMILY_SQL = "CASE WHEN label_family IS NULL THEN 'source:' || label_source ELSE 'family:' || label_family END"


def label_family(row: dict[str, Any]) -> str | None:
    if row.get("label_family") is not None:
        return row["label_family"]
    source = row.get("label_source")
    return source


def label_group(row: dict[str, Any]) -> str | None:
    value = label_family(row)
    if value is None:
        return None
    return ("family:" if row.get("label_family") is not None else "source:") + value


async def settle_auto_source(conn, row: dict[str, Any]) -> str:
    """Only ten original spot checks can release a label family."""
    from training import pair_from_gold
    args = (row["tenant_id"], row["region"], row["skill_id"], row["source"], label_group(row))
    sample = await conn.fetch(
        f"""SELECT status FROM tank.gold_examples WHERE tenant_id=$1 AND region=$2
        AND skill_id=$3 AND source=$4 AND {FAMILY_SQL} IS NOT DISTINCT FROM $5 AND auto_label ORDER BY created_at,id LIMIT 10""", *args,
    )
    verdict = source_verdict([r["status"] for r in sample])
    if verdict == "pending":
        return verdict
    if verdict == "review":
        await conn.execute(
            f"""UPDATE tank.gold_examples SET status='rejected',note='Source needs code review'
            WHERE tenant_id=$1 AND region=$2 AND skill_id=$3 AND source=$4 AND {FAMILY_SQL} IS NOT DISTINCT FROM $5
            AND auto_label AND status='candidate'""", *args,
        )
        return verdict
    await conn.execute(
        f"""UPDATE tank.gold_examples SET status='checked',auto_approved=true,checked_by=coalesce(checked_by,'source spot check'),checked_at=coalesce(checked_at,now())
        WHERE tenant_id=$1 AND region=$2 AND skill_id=$3 AND source=$4 AND {FAMILY_SQL} IS NOT DISTINCT FROM $5
        AND auto_label AND status IN ('candidate','checked')""", *args,
    )
    rows = await conn.fetch(
        f"""SELECT * FROM tank.gold_examples WHERE tenant_id=$1 AND region=$2
        AND skill_id=$3 AND source=$4 AND {FAMILY_SQL} IS NOT DISTINCT FROM $5 AND auto_label AND status='checked'""", *args,
    )
    for checked in rows:
        await pair_from_gold(conn, dict(checked))
    return verdict


async def sample_queue(conn, actor: Reviewer, agent: str, skill: str) -> list[dict]:
    rows = await conn.fetch(
        f"""WITH ranked AS (
          SELECT *,{FAMILY_SQL} AS family,
          row_number() OVER (PARTITION BY skill_id,source,{FAMILY_SQL},auto_label ORDER BY created_at,id) AS sample_n
          FROM tank.gold_examples WHERE tenant_id=$1 AND region=$2 AND agent=$3 AND skill_id=$4
        ), waiting AS (
          SELECT * FROM ranked WHERE status='candidate' AND (NOT auto_label OR sample_n<=10)
        ) SELECT id,input,expected,source,label_source,label_family,auto_label FROM waiting
        WHERE NOT auto_label OR (source,family) IS NOT DISTINCT FROM
          (SELECT ROW(source,family) FROM waiting WHERE auto_label ORDER BY created_at,id LIMIT 1)
        ORDER BY id""", actor.tenant, runtime().box_region(), agent, skill,
    )
    return [dict(row) for row in rows]

@router.get("/next")
async def next_example(agent: str, skill: str, after: uuid.UUID | None = None,
                       actor: Reviewer = Depends(require_reviewer)) -> dict[str, Any] | None:
    allowed_agent(actor, agent)
    async with runtime().pool().acquire() as conn:
        rows = await sample_queue(conn, actor, agent, skill)
        row = next((r for r in rows if after is None or r["id"] > after), None)
    if not row:
        return None
    return {**dict(row), "input": obj(row["input"]), "expected": obj(row["expected"])}


class Verdict(BaseModel):
    action: Literal["right", "wrong", "skip"]
    correction: dict[str, Any] | None = None
    note: str = Field(default="", max_length=2000)


@router.post("/{example_id}/check")
async def check(example_id: uuid.UUID, body: Verdict,
                actor: Reviewer = Depends(require_reviewer)) -> dict[str, bool]:
    from training import pair_from_gold
    async with runtime().pool().acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(871226)")
        row = await conn.fetchrow(
            """SELECT * FROM tank.gold_examples WHERE id=$1 AND tenant_id=$2 AND region=$3 FOR UPDATE""",
            example_id, actor.tenant, runtime().box_region(),
        )
        if row is None:
            raise HTTPException(404, "not found")
        allowed_agent(actor, row["agent"])
        if body.action == "skip":
            return {"ok": True}
        if row["status"] != "candidate":
            raise HTTPException(409, "This example has already been checked.")
        if body.action == "wrong" and not body.correction:
            raise HTTPException(422, "A corrected JSON answer is required.")
        if row["auto_label"]:
            sample = await conn.fetch(
                f"""SELECT id FROM tank.gold_examples WHERE tenant_id=$1 AND region=$2
                AND skill_id=$3 AND source=$4 AND {FAMILY_SQL} IS NOT DISTINCT FROM $5 AND auto_label ORDER BY created_at,id LIMIT 10""",
                actor.tenant, row["region"], row["skill_id"], row["source"], label_group(dict(row)),
            )
            if example_id not in {r["id"] for r in sample}:
                raise HTTPException(409, "Review the source sample first.")
        expected = obj(row["expected"]) if body.action == "right" else body.correction
        # Preserve the wrong original and queue its correction for a separate check.
        if body.action == "wrong":
            await conn.execute(
                """UPDATE tank.gold_examples SET status='rejected',checked_by=$3,checked_at=now(),note=$4
                WHERE id=$1 AND tenant_id=$2""", example_id, actor.tenant, actor.user, body.note,
            )
            await add_candidate(conn, tenant=actor.tenant, skill=row["skill_id"], agent=row["agent"],
                                value=obj(row["input"]), expected=expected, source="correction",
                                ref=str(example_id), tolerance=row["tolerance"], holdout=row["holdout"])
        else:
            updated = await conn.fetchrow(
                """UPDATE tank.gold_examples SET status='checked',checked_by=$3,checked_at=now(),note=$4
                WHERE id=$1 AND tenant_id=$2 RETURNING *""", example_id, actor.tenant, actor.user, body.note,
            )
            if not row["auto_label"]:
                await pair_from_gold(conn, dict(updated))
        if row["auto_label"]:
            await settle_auto_source(conn, dict(row))
    return {"ok": True}


async def run_retests(*, final: bool = False, tenant: uuid.UUID = PLATFORM_TENANT,
                      skill: str | None = None, version: str | None = None,
                      since: datetime | None = None) -> list[dict[str, Any]]:
    import skala
    rt = runtime()
    results = []
    async with rt.pool().acquire() as conn:
        # Session lock prevents overlapping jobs from recording misleading trends.
        if not await conn.fetchval("SELECT pg_try_advisory_lock(871221)"):
            raise HTTPException(409, "A re-test is already running.")
        try:
            skills = await conn.fetch(
                """SELECT id,version FROM tank.skills WHERE present
                AND (($2::text IS NULL AND state='approved') OR ($2 IS NOT NULL AND version=$2 AND state='staging'))
                AND ($1::text IS NULL OR id=$1) ORDER BY id""", skill, version,
            )
            for record in skills:
                pkg = rt.get_package(record["id"], record["version"])
                # A retry after partial completion must not re-score completed
                # skills and erase their previous-run comparison.
                if since is not None and await conn.fetchval(
                    """SELECT 1 FROM tank.gold_scores WHERE tenant_id=$1 AND region=$2
                    AND skill_id=$3 AND NOT final_check AND ran_at >= $4 LIMIT 1""",
                    tenant, rt.box_region(), pkg.id, since,
                ):
                    continue
                cases = await conn.fetch(
                    """SELECT DISTINCT ON (input_hash) input,expected,tolerance FROM tank.gold_examples
                    WHERE tenant_id=$1 AND region=$2 AND skill_id=$3 AND status='checked' AND (NOT auto_label OR auto_approved) AND holdout=$4
                    ORDER BY input_hash,checked_at DESC,id""", tenant, rt.box_region(), pkg.id, final,
                )
                if not cases:
                    continue
                passed = 0
                for case in cases:
                    try:
                        # Like existing Skala evals, re-test spend belongs to the platform.
                        # The corpus and scores above/below remain scoped to its owner.
                        ctx = rt.make_ctx(pkg, PLATFORM_TENANT, preview=True, home_region=rt.box_region())
                        output = await skala.run_skill(pkg, obj(case["input"]), ctx)
                        ok, _ = skala.score_case(obj(case["expected"]), output, case["tolerance"])
                        passed += int(ok)
                    except Exception:
                        # No inputs, outputs, upstream exceptions or identifiers in logs/results.
                        pass
                score = round(passed / len(cases), 4)
                previous = await conn.fetchval(
                    """SELECT score FROM tank.gold_scores WHERE tenant_id=$1 AND region=$2
                    AND skill_id=$3 AND final_check=$4 ORDER BY ran_at DESC,id DESC LIMIT 1""",
                    tenant, rt.box_region(), pkg.id, final,
                )
                await conn.execute(
                    """INSERT INTO tank.gold_scores (tenant_id,region,skill_id,version,score,n,final_check)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)""", tenant, rt.box_region(), pkg.id, pkg.version, score, len(cases), final,
                )
                results.append({"skill_id": pkg.id, "version": pkg.version, "score": score, "n": len(cases),
                                "previous": float(previous) if previous is not None else None,
                                "drop": previous is not None and round(float(previous)-score, 4)>0.02})
        finally:
            await conn.execute("SELECT pg_advisory_unlock(871221)")
    return results


async def retest(*, since: datetime | None = None) -> dict[str, Any]:
    results = []
    async with runtime().pool().acquire() as conn:
        scopes = await conn.fetch(
            "SELECT DISTINCT tenant_id FROM tank.gold_examples WHERE region=$1 AND status='checked' AND (NOT auto_label OR auto_approved) AND NOT holdout",
            runtime().box_region(),
        )
    for scope in scopes:
        results.extend(await run_retests(tenant=scope["tenant_id"], since=since))
    return {"results": results}


@router.post("/final/{skill}/{version}")
async def final_check(skill: str, version: str, actor: Reviewer = Depends(require_reviewer)) -> dict[str, Any]:
    if not actor.founder:
        raise HTTPException(404, "not found")
    results = await run_retests(final=True, tenant=actor.tenant, skill=skill, version=version)
    if not results:
        raise HTTPException(409, "A staging version and checked holdout examples are required.")
    return {"results": results}


@router.post("/jobs/retest", dependencies=[Depends(require_job)])
async def retest_job() -> dict[str, Any]:
    return await retest()
