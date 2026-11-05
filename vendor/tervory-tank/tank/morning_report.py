"""Code-built 07:00 Pacific founder digest. No model, deployment or approval calls."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
import json
import logging
import os
import smtplib
import ssl
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from command import Actor, identity, require_founder
from gold import PLATFORM_TENANT
from tenant_db import tenant_scope

PACIFIC = ZoneInfo("America/Los_Angeles")
log = logging.getLogger(__name__)


def period(at):
    local = at.astimezone(PACIFIC)
    day = local.date() - timedelta(days=local.hour < 7)
    return (
        day,
        datetime.combine(day, time(), PACIFIC).astimezone(timezone.utc),
        datetime.combine(day, time(7), PACIFIC).astimezone(timezone.utc),
    )


async def record_release(tank):
    sha = tank.tank_sha()
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        return False  # Unknown/short metadata is not a verified deployment event.
    with tenant_scope(PLATFORM_TENANT, administrative=True):
        async with tank.pool().acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                "platform-release/" + tank.box_region(),
            )
            previous = await conn.fetchval(
                """SELECT sha FROM tank.platform_releases WHERE tenant_id=$1 AND region=$2
            ORDER BY observed_at DESC,id DESC LIMIT 1""",
                PLATFORM_TENANT,
                tank.box_region(),
            )
            if previous == sha:
                return False
            await conn.execute(
                "INSERT INTO tank.platform_releases(tenant_id,region,sha) VALUES($1,$2,$3)",
                PLATFORM_TENANT,
                tank.box_region(),
                sha,
            )
    return True


async def deployment_checks(tank, since, until):
    """Read deployment-workflow metadata; this is not application-health proof."""
    token = (os.environ.get("TANK_GITHUB_READ_TOKEN") or "").strip()
    if not token:
        return {"status": "not_configured", "checks": []}
    checks = []
    try:
        response = await tank._http.get(
            "https://api.github.com/repos/Tervory-AI/tervory-tank/actions/workflows/deploy-on-merge.yml/runs",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
            params={"branch": "main", "per_page": 100},
            timeout=10,
        )
        response.raise_for_status()
        complete = 'rel="next"' not in response.headers.get("link", "")
        rows = response.json()["workflow_runs"]
        for row in rows:
            stamp = datetime.fromisoformat(row["run_started_at"].replace("Z", "+00:00"))
            if not since <= stamp < until:
                continue
            sha = row["head_sha"]
            if (
                not isinstance(sha, str)
                or len(sha) != 40
                or any(c not in "0123456789abcdef" for c in sha)
            ):
                raise ValueError("Invalid deployment metadata")
            state = row.get("conclusion") or row.get("status")
            if state not in {
                "queued",
                "in_progress",
                "completed",
                "success",
                "failure",
                "neutral",
                "cancelled",
                "skipped",
                "timed_out",
                "action_required",
                "stale",
                "waiting",
                "pending",
                "requested",
                "startup_failure",
            }:
                state = "unknown"
            checks.append({"sha": sha, "state": state, "started_at": stamp.isoformat()})
        return {"status": "complete" if complete else "incomplete", "checks": checks}
    except Exception as exc:
        log.warning("Founder deployment metadata: %s", type(exc).__name__)
        return {"status": "incomplete", "checks": checks}


async def build(tank, at):
    import proof

    day, since, until = period(at)
    region = tank.box_region()
    counts = {
        "calls": 0,
        "known_spend_usd": 0.0,
        "unpriced_calls": 0,
        "comparable_savings_usd": 0.0,
        "comparable_calls": 0,
        "failed_jobs": 0,
        "unverified_calls": 0,
    }
    with tenant_scope(PLATFORM_TENANT, administrative=True):
        async with (
            tank.pool().acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            tenants = await conn.fetch(
                """SELECT DISTINCT tenant_id FROM tank.ledger
            WHERE region=$1 AND coalesce(sent_at,ts) >= $2 AND coalesce(sent_at,ts) < $3
            UNION SELECT tenant_id FROM tank.tasks
            WHERE status='failed' AND finished_at >= $2 AND finished_at < $3
            UNION SELECT tenant_id FROM tank.offpeak_jobs WHERE region=$1
            AND status IN ('failed','uncertain') AND finished_at >= $2 AND finished_at < $3
            UNION SELECT tenant_id FROM tank.batches WHERE region=$1
            AND status IN ('failed','uncertain') AND finished_at >= $2 AND finished_at < $3
            UNION SELECT tenant_id FROM tank.skill_revalidations
            WHERE completed_at >= $2 AND completed_at < $3 AND error IS NOT NULL""",
                region,
                since,
                until,
            )
            for owner in tenants:
                tenant = owner["tenant_id"]
                row = await conn.fetchrow(
                    """SELECT count(*) AS calls,
                coalesce(sum(cost_usd) FILTER(WHERE cost_status='set' AND research_cost_status IS DISTINCT FROM 'unknown'),0) AS spent,
                count(*) FILTER(WHERE cost_status='not_set' OR research_cost_status='unknown') AS unpriced,
                count(*) FILTER(WHERE outcome='unverified') AS unverified
                FROM tank.ledger WHERE tenant_id=$1 AND region=$2
                AND coalesce(sent_at,ts) >= $3 AND coalesce(sent_at,ts) < $4""",
                    tenant,
                    region,
                    since,
                    until,
                )
                saved = await conn.fetchrow(
                    proof.savings_cte()
                    + """SELECT count(*) AS calls,
                coalesce(sum(standard_cost_usd-cost_usd),0) AS saved
                FROM eligible WHERE coalesce(sent_at,ts) >= $4 AND coalesce(sent_at,ts) < $3""",
                    tenant,
                    region,
                    until,
                    since,
                )
                failed = await conn.fetchval(
                    """SELECT count(*) FROM (
                  SELECT t.id AS job FROM tank.tasks t
                  WHERE t.tenant_id=$1 AND t.finished_at >= $3 AND t.finished_at < $4
                  AND t.status='failed' AND NOT EXISTS(
                    SELECT 1 FROM tank.ledger l WHERE l.tenant_id=$1
                    AND (l.id=t.ledger_id OR l.task_id=t.id)
                    AND l.outcome IN ('delivered','unverified','queued','sent'))
                  UNION SELECT coalesce(j.task_id,j.id) FROM tank.offpeak_jobs j
                  WHERE j.tenant_id=$1 AND j.region=$2 AND j.status='failed'
                  AND j.finished_at >= $3 AND j.finished_at < $4
                  AND NOT EXISTS(SELECT 1 FROM tank.ledger l
                    WHERE l.tenant_id=$1 AND l.id=j.id AND l.outcome='delivered')
                  UNION SELECT coalesce(j.task_id,b.id) FROM tank.batches b
                  LEFT JOIN tank.offpeak_jobs j ON j.id=b.id AND j.tenant_id=b.tenant_id
                  WHERE b.tenant_id=$1 AND b.region=$2 AND b.status='failed'
                  AND b.finished_at >= $3 AND b.finished_at < $4
                  UNION SELECT coalesce(l.revalidation_id,l.task_id,j.task_id,l.offpeak_job_id,l.batch_id,l.id)
                  FROM tank.ledger l LEFT JOIN tank.offpeak_jobs j
                  ON j.id=l.offpeak_job_id AND j.tenant_id=l.tenant_id
                  WHERE l.tenant_id=$1 AND l.region=$2 AND l.outcome='failed'
                  AND coalesce(l.sent_at,l.ts) >= $3 AND coalesce(l.sent_at,l.ts) < $4
                  UNION SELECT id FROM tank.skill_revalidations
                  WHERE tenant_id=$1 AND completed_at >= $3 AND completed_at < $4
                  AND error IS NOT NULL) failed""",
                    tenant,
                    region,
                    since,
                    until,
                )
                counts["calls"] += row["calls"]
                counts["known_spend_usd"] += float(row["spent"])
                counts["unpriced_calls"] += row["unpriced"]
                counts["comparable_savings_usd"] += float(saved["saved"])
                counts["comparable_calls"] += saved["calls"]
                counts["failed_jobs"] += failed
                counts["unverified_calls"] += row["unverified"]
            releases = await conn.fetch(
                """SELECT sha,observed_at FROM tank.platform_releases WHERE tenant_id=$1 AND region=$2
            AND observed_at >= $3 AND observed_at < $4 ORDER BY observed_at,id""",
                PLATFORM_TENANT,
                region,
                since,
                until,
            )
            policies = await conn.fetch(
                "SELECT task_type,candidate_model FROM tank.policy WHERE tenant_id IS NULL AND candidate_model IS NOT NULL ORDER BY task_type"
            )
            candidates = []
            for policy in policies:
                evidence = await conn.fetchrow(
                    """SELECT count(*) AS samples,avg(s.agreement) AS agreement FROM tank.shadow s
                JOIN tank.ledger l ON l.id=s.ledger_id AND l.tenant_id=s.tenant_id
                WHERE s.task_type=$1 AND s.candidate_model=$2 AND l.region=$3
                AND s.created_at >= $4 AND s.created_at < $5
                AND s.agreement IS NOT NULL AND l.outcome='delivered'""",
                    policy["task_type"],
                    policy["candidate_model"],
                    region,
                    since,
                    until,
                )
                candidates.append(
                    {
                        **dict(policy),
                        "night_samples": evidence["samples"],
                        "night_agreement": float(evidence["agreement"])
                        if evidence["agreement"] is not None
                        else None,
                    }
                )
    return {
        "day": day.isoformat(),
        "region": region,
        "timezone": str(PACIFIC),
        "since": since.isoformat(),
        "until": until.isoformat(),
        **counts,
        "deploys": [
            {"sha": r["sha"], "observed_at": r["observed_at"].isoformat()}
            for r in releases
        ],
        "deploy_coverage": "Tank process-start observations and GitHub deployment-workflow metadata; room health is not attested",
        "deployment_checks": await deployment_checks(tank, since, until),
        "graduation_candidates": candidates,
        "promotion": "Founder review required",
    }


def render(report):
    lines = [
        f"Tank morning report — {report['day']} ({report['region']})",
        "Night: 00:00–07:00 America/Los_Angeles",
        f"Calls: {report['calls']}",
        f"Known spend: USD {report['known_spend_usd']:.6f}; unpriced calls: {report['unpriced_calls']}",
        f"Comparable savings: USD {report['comparable_savings_usd']:.6f} across {report['comparable_calls']} calls",
        f"Failed jobs: {report['failed_jobs']}; unverified calls: {report['unverified_calls']}",
        "Deploys: " + report["deploy_coverage"],
    ]
    lines += [r["sha"] + " observed " + r["observed_at"] for r in report["deploys"]]
    lines.append(
        "Deployment-workflow coverage: " + report["deployment_checks"]["status"]
    )
    lines += [
        r["sha"] + ": deployment workflow " + r["state"]
        for r in report["deployment_checks"]["checks"]
    ]
    lines.append("Graduation candidates (night evidence; founder review required):")
    lines += [
        f"{r['task_type']}: {r['candidate_model']}; samples={r['night_samples']}; agreement={r['night_agreement']}"
        for r in report["graduation_candidates"]
    ]
    return "\n".join(lines) + "\n"


def transport():
    def value(suffix):
        return (
            os.environ.get("FOUNDER_REPORT_SMTP_" + suffix)
            or os.environ.get("SMTP_" + suffix)
            or ""
        ).strip()

    recipient = (os.environ.get("FOUNDER_REPORT_TO") or "").strip()
    if not recipient or not value("HOST"):
        return None
    config = {
        name: value(name) for name in ("HOST", "PORT", "USER", "PASSWORD", "FROM")
    }
    if not config["FROM"]:
        raise ValueError("SMTP sender is required")
    if bool(config["USER"]) != bool(config["PASSWORD"]):
        raise ValueError("Incomplete SMTP login")
    config["PORT"] = int(config["PORT"] or "587")
    config["TO"] = recipient
    return config


def smtp_send(config, message):
    port = config["PORT"]
    client = (
        smtplib.SMTP_SSL(
            config["HOST"], port, timeout=10, context=ssl.create_default_context()
        )
        if port == 465
        else smtplib.SMTP(config["HOST"], port, timeout=10)
    )
    with client as smtp:
        if port != 465:
            smtp.starttls(context=ssl.create_default_context())
        if config["USER"]:
            smtp.login(config["USER"], config["PASSWORD"])
        smtp.send_message(message)


async def send_once(tank, at):
    config = transport()
    if config is None:
        return {"sent": False, "reason": "not_configured"}
    report = await build(tank, at)
    message = EmailMessage()
    message["From"], message["To"] = config["FROM"], config["TO"]
    message["Subject"] = (
        "Tank morning report — " + report["day"] + " (" + report["region"] + ")"
    )
    message.set_content(render(report))
    claim = uuid.uuid4()
    with tenant_scope(PLATFORM_TENANT, administrative=True):
        async with tank.pool().acquire() as conn:
            claimed = await conn.fetchval(
                """INSERT INTO tank.founder_morning_reports(id,tenant_id,region,report_day,report,status)
            VALUES($1,$2,$3,$4,$5::jsonb,'sending')
            ON CONFLICT(tenant_id,region,report_day) DO NOTHING RETURNING id""",
                claim,
                PLATFORM_TENANT,
                report["region"],
                period(at)[0],
                json.dumps(report),
            )
    if claimed is None:
        return {"sent": False, "reason": "already_claimed"}
    state = "sent"
    try:
        await asyncio.to_thread(smtp_send, config, message)
    except Exception as exc:
        state = "uncertain"  # SMTP may have accepted it. Never resend automatically.
        log.warning("Founder report transport: %s", type(exc).__name__)
    with tenant_scope(PLATFORM_TENANT, administrative=True):
        async with tank.pool().acquire() as conn:
            await conn.execute(
                "UPDATE tank.founder_morning_reports SET status=$3 WHERE id=$1 AND tenant_id=$2",
                claim,
                PLATFORM_TENANT,
                state,
            )
    return {"sent": state == "sent", "status": state}


async def loop(tank):
    try:
        await record_release(tank)
    except Exception as exc:
        log.warning("Founder release observation: %s", type(exc).__name__)
    last_day = None
    while True:
        at = datetime.now(timezone.utc)
        local = at.astimezone(PACIFIC)
        if local.hour >= 7 and local.date() != last_day:
            last_day = local.date()
            try:
                await send_once(tank, at)
            except Exception as exc:
                log.warning("Founder report: %s", type(exc).__name__)
        day = local.date() + timedelta(days=local.hour >= 7)
        due = datetime.combine(day, time(7), PACIFIC).astimezone(timezone.utc)
        await asyncio.sleep(
            min(60, max(0.1, (due - datetime.now(timezone.utc)).total_seconds()))
        )


def build_router(tank):
    router = APIRouter(prefix="/v1/platform", tags=["platform"])

    @router.get("/morning-report")
    async def preview(actor: Actor = Depends(identity)):
        require_founder(actor)
        return await build(tank, datetime.now(timezone.utc))

    return router
