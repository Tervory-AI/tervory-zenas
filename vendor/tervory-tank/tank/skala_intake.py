"""Stage this intake's free packages through the existing Skala eval lifecycle.

This module never approves, retires or rolls back a version. Successful recorded
results make the packages available to the existing founder approval controls.
"""
from __future__ import annotations

from tenant_db import tenant_scope

import asyncio
import json
import logging

IDS = frozenset({"trigger.proselint", "trigger.languagetool", "trigger.agent_style",
                 "trigger.house_style", "trigger.readability", "trigger.voice_check", "tiger.playbook"})
VERSIONS = {skill_id: "1.0.0" for skill_id in IDS}
VERSIONS["trigger.languagetool"] = "1.1.0"
log = logging.getLogger(__name__)


def guard_preview(skill_id, caller, preview):
    if skill_id in IDS and caller and preview:
        from fastapi import HTTPException
        raise HTTPException(403, "Room keys may run only approved skill versions")


async def stage_ready(tank):
    pending = []
    for skill_id in sorted(IDS):
        pkg = tank._packages.get((skill_id, VERSIONS[skill_id]))
        if pkg is None:
            continue
        async with tank.pool().acquire() as conn:
            state = await conn.fetchval("SELECT state FROM tank.skills WHERE id=$1 AND version=$2", pkg.id, pkg.version)
            if state not in {"candidate", "staging"}:
                continue
            last = await conn.fetchrow(
                "SELECT score,details FROM tank.skill_evals WHERE skill_id=$1 AND version=$2 ORDER BY ran_at DESC,id DESC LIMIT 1",
                pkg.id, pkg.version,
            )
        if last and state == "staging":
            details = json.loads(last["details"]) if isinstance(last["details"], str) else last["details"]
            fingerprint = tank.skala.package_hash(pkg)
            if (float(last["score"]) >= pkg.eval_threshold and len(details) == len(pkg.cases)
                    and all(d.get("passed") and d.get("package_hash") == fingerprint for d in details)):
                continue
        try:
            result = await tank.run_skill_evals(pkg.id, pkg.version)
            if result["gate"] != "passed":
                pending.append(skill_id)
        except Exception:
            # No user text, response bodies or configuration values in logs.
            pending.append(skill_id)
            log.warning("Intake evaluation unavailable for %s; approval stays pending", skill_id)
    return pending


def install(app, tank):
    task = None

    async def evaluate():
        # The room may start after Tank. Retry only this intake's pending evals;
        # completed evidence is retained. No client requests or paid/model work.
        for attempt in range(10):
            if not await stage_ready(tank):
                return
            if attempt < 9:
                await asyncio.sleep(30)
        log.warning("Intake still pending; use the existing Skala eval control after the room is available")

    @app.on_event("startup")
    async def start():
        nonlocal task
        with tenant_scope(None, administrative=True):
            task = asyncio.create_task(evaluate())

    @app.on_event("shutdown")
    async def stop():
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
