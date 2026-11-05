"""B4 retirement eligibility; a clean 30-day window, measured in UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from gold import PLATFORM_TENANT


def clean_window(approved_at: datetime, failed_at: datetime | None,
                 rollback_at: datetime | None, now: datetime) -> tuple[bool, datetime]:
    start = max(stamp for stamp in (approved_at, failed_at, rollback_at) if stamp is not None)
    eligible_at = start + timedelta(days=30)
    return now >= eligible_at, eligible_at


async def evidence(conn: Any, skill_id: str, version: str, *, now: datetime | None = None) -> dict:
    from skala import version_key
    rows = await conn.fetch("SELECT * FROM tank.skills WHERE id=$1 AND state='approved' AND present", skill_id)
    candidates = [r for r in rows if version_key(r["version"]) > version_key(version)]
    if not candidates:
        return {"allowed": False, "code": "newer_approved_version_required"}
    newer = max(candidates, key=lambda r: version_key(r["version"]))
    if newer["approved_at"] is None:
        return {"allowed": False, "code": "approval_date_required"}
    failed_at = await conn.fetchval(
        "SELECT max(ran_at) FROM tank.skill_evals WHERE skill_id=$1 AND version=$2 AND (score<$3 OR passed<total)",
        skill_id, newer["version"], newer["eval_threshold"])
    rollback_at = await conn.fetchval(
        "SELECT max(acted_at) FROM tank.skill_approvals WHERE tenant_id=$1 AND skill_id=$2 AND action='rollback'",
        PLATFORM_TENANT, skill_id)
    pending = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM tank.skill_revalidations WHERE tenant_id=$1 AND skill_id=$2 AND version=$3 AND completed_at IS NULL)",
        PLATFORM_TENANT, skill_id, newer["version"])
    allowed, eligible_at = clean_window(newer["approved_at"], failed_at, rollback_at,
                                        now or datetime.now(timezone.utc))
    return {"allowed": allowed and not pending, "code": "retirement_probation",
            "newer_version": newer["version"], "eligible_at": eligible_at.isoformat(),
            "pending_revalidation": pending}
