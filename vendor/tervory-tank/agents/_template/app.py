"""The template agent: a ping job and a five-minute heartbeat.

Copy this folder to start a new Squad member. It knows no industry.
"""

from __future__ import annotations

import logging
import os

from chassis.app import create_app
from chassis.jobs import JobContext

AGENT = os.environ.get("AGENT_NAME", "template")
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


app = create_app(
    agent=AGENT,
    handlers={"ping": ping},
    schedules=[
        {
            "tenant_id": HEARTBEAT_TENANT,
            "cron": "*/5 * * * *",
            "kind": "ping",
            "input": {"heartbeat": True},
        }
    ],
)
