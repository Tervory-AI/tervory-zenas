"""Autumbra's read-only Tank evidence: real receipts, main CI and registered Radix findings."""
from fastapi import HTTPException

from tenant_db import tenant_scope


async def observation(core, target: str, signal: str) -> dict:
    try:
        count = await _count(core, target, signal)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError()
        return {"target": target, "signal": signal, "count": min(count, 1_000_000), "failing": count > 0}
    except Exception:
        raise HTTPException(503, "The read-only evidence feed is not connected.") from None


async def _count(core, target: str, signal: str) -> int:
    if signal == "ci" and target == "tank":
        from command_repo import github, _get, _sha, _checks
        async with github() as client:
            head, _ = await _get(client, "git/ref/heads/main")
            sha = _sha(head["object"]["sha"])
            result = await _checks(client, sha)
            # A changing main or incomplete page set is unavailable, never healthy.
            after, _ = await _get(client, "git/ref/heads/main")
            if after["object"]["sha"] != sha or not result["checks_complete"]:
                raise ValueError()
            if result["checks_state"] not in {"success", "failure"}:
                raise ValueError()
            return int(result["checks_state"] == "failure")
    if signal not in {"errors", "radix"} or target not in {"tank", "workspace"}:
        raise ValueError()
    with tenant_scope(core.PLATFORM_TENANT, administrative=True):
        async with core.pool().acquire() as conn, conn.transaction(readonly=True):
            if signal == "errors":
                # Workspace calls are Tier 0 ledger rows. Exclude Ghost's own
                # evidence receipts so a monitor failure cannot feed itself.
                return await conn.fetchval("""SELECT count(*) FROM tank.ledger
                    WHERE tenant_id=$1 AND region=$2 AND ts >= now() - interval '5 minutes'
                    AND outcome='failed' AND agent<>'ghost'
                    AND ($3='tank' OR agent='tier0')""", core.PLATFORM_TENANT, core.box_region(), target)
            sha = core.tank_sha()
            if not isinstance(sha, str) or len(sha) != 40:
                raise ValueError()
            complete = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM tank.radix_deploy_scans
                WHERE tenant_id=$1 AND commit_sha=$2 AND processed AND complete)""", core.PLATFORM_TENANT, sha)
            if not complete:
                raise ValueError()
            return await conn.fetchval("""SELECT count(*) FROM tank.radix_deploy_findings
                WHERE tenant_id=$1 AND resolved_at IS NULL AND override_at IS NULL
                AND ($2='tank' OR app=$2)""", core.PLATFORM_TENANT, target)
