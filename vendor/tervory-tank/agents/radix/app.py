"""Radix: the security auditor. A ping job, a five-minute heartbeat, and code scans.

Scans run on a tarball or git bundle the caller hands over. Outbound HTTP other
than Tank and Hermes goes through net.py. A scan does not use it.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from chassis.app import create_app
from chassis.jobs import JobContext
from chassis.logs import log_line
from scanlib.orchestrate import scan_job
from verify.routes import attach as attach_verification
from gold_sources import attach as attach_gold

AGENT = os.environ.get("AGENT_NAME", "radix")
# Platform tenant. The heartbeat is the box checking itself, not a client's work.
HEARTBEAT_TENANT = "00000000-0000-0000-0000-000000000001"

log = logging.getLogger("agent")


async def ping(ctx: JobContext) -> dict:
    result: dict = {"pong": True, "agent": ctx.agent}
    if ctx.input.get("heartbeat"):
        # The same text every time, so Hermes folds it into one memory and
        # counts it (seen). A Hermes outage must not fail the heartbeat.
        try:
            saved = await ctx.memory.remember(
                ctx.tenant_id,
                f"{ctx.agent} heartbeat",
                "heartbeat",
                "schedule:*/5",
            )
            result["heartbeats_seen"] = saved.get("seen")
        except Exception as exc:  # noqa: BLE001 — class name only, never the message
            log.warning("heartbeat memory failed (%s)", type(exc).__name__)
            result["memory"] = "unreachable"
    return result


async def scan(ctx: JobContext) -> dict:
    result = await scan_job(ctx)
    # Counts only. The finding text can carry a file path, never a secret value.
    log_line(AGENT, "scan finished", tenant_id=str(ctx.tenant_id), job_id=str(ctx.job_id))
    return result


async def watch_tick(ctx: JobContext) -> dict:
    from passive_watch import tick
    return await tick(app.state.pool)


app = create_app(
    agent=AGENT,
    handlers={"ping": ping, "scan": scan, "watch_tick": watch_tick},
    # One scheduled phase may be a 20-minute infrastructure run. Keep the
    # worker outside that scan/control budget and inside its 30-minute lease.
    job_timeouts={"scan": 360, "watch_tick": 1260},
    schedules=[
        {
            "tenant_id": HEARTBEAT_TENANT,
            "cron": "* * * * *",
            "kind": "watch_tick",
            "input": {},
        },
        {
            "tenant_id": HEARTBEAT_TENANT,
            "cron": "*/5 * * * *",
            "kind": "ping",
            "input": {"heartbeat": True},
        }
    ],
)

attach_verification(app)
attach_gold(app)

from passive_routes import attach as attach_passive
attach_passive(app)


# Room-key-only execution of the candidate IaC engine; no skill approval here.
from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
import hmac
from scanlib import iac


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    tenant_id: UUID
    input: dict


@app.post("/evals/run")
async def eval_case(body: EvalCase, x_tank_key: str = Header(default="")) -> dict:
    key = os.environ.get("TANK_KEY", "")
    if not key or not hmac.compare_digest(key, x_tank_key):
        raise HTTPException(401, "room key required")
    if (body.skill_id, body.version) != ("scan.iac", "1.0.0"):
        raise HTTPException(404, "unknown eval skill")
    # Checkov is a bounded subprocess; keep the room's event loop responsive.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(iac.run_skill, body.input)


# Subscribe only after the passive tables exist; stop before chassis pools close.
from contextlib import asynccontextmanager
import asyncio
from regulation_events import Queue, consume_loop

_regulation_inner = app.router.lifespan_context


@asynccontextmanager
async def _regulation_lifespan(app):
    async with _regulation_inner(app):
        consumer = asyncio.create_task(consume_loop(Queue(app.state.pool), app.state.http))
        try:
            yield
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)


app.router.lifespan_context = _regulation_lifespan
