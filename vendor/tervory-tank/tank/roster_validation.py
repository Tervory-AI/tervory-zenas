"""Live roster changes reuse Skala's durable eval queue and existing policy-swap gate."""

from __future__ import annotations

import hashlib
import json
import uuid

from gold import PLATFORM_TENANT
from tenant_db import tenant_scope
from fastapi import HTTPException


def fingerprint(rows):
    members = []
    for row in rows:
        name = row.get("model_name")
        if not isinstance(name, str) or not name:
            raise ValueError("Invalid public roster row")
        params, info = row.get("litellm_params") or {}, row.get("model_info") or {}
        members.append(
            (
                name,
                str(params.get("model") or ""),
                str(params.get("api_base") or ""),
                str(info.get("host") or ""),
            )
        )
    if not members:
        raise ValueError("Cannot validate an empty roster")
    return hashlib.sha256(
        json.dumps(sorted(members), separators=(",", ":")).encode()
    ).hexdigest()


async def observe(tank):
    # Do not compare independent five-minute caches or a stale config-file fallback.
    response = await tank._http.get(
        tank.LITELLM_URL + "/model/info",
        headers={"Authorization": "Bearer " + tank.LITELLM_MASTER_KEY},
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()["data"]
    current = fingerprint(rows)
    with tenant_scope(PLATFORM_TENANT, administrative=True):
        async with tank.pool().acquire() as conn, conn.transaction():
            await tank.skala.lock_approvals(conn)
            previous = await conn.fetchval(
                """SELECT fingerprint FROM tank.roster_observations
            WHERE tenant_id=$1 AND region=$2 FOR UPDATE""",
                PLATFORM_TENANT,
                tank.box_region(),
            )
            if previous == current:
                return 0
            await conn.execute(
                """INSERT INTO tank.roster_observations(tenant_id,region,fingerprint) VALUES($1,$2,$3)
            ON CONFLICT(tenant_id,region) DO UPDATE
            SET fingerprint=EXCLUDED.fingerprint,observed_at=now()""",
                PLATFORM_TENANT,
                tank.box_region(),
                current,
            )
            # Establish an upgrade baseline without inventing a roster change.
            if previous is None:
                return 0
            approved = await conn.fetch(
                "SELECT id,version,task_type FROM tank.skills WHERE state='approved' AND present ORDER BY id,version"
            )
            queued = 0
            for skill in approved:
                saved = await conn.fetch(
                    """SELECT * FROM tank.policy WHERE task_type=$1
                ORDER BY tenant_id NULLS FIRST""",
                    skill["task_type"],
                )
                policies = []
                for row in saved:
                    serving = dict(row)
                    policies.append(serving)
                    if serving.get("held_policy") and serving.get("held_swap_id"):
                        held = serving["held_policy"]
                        held = json.loads(held) if isinstance(held, str) else dict(held)
                        policies.append(
                            {
                                **held,
                                "tenant_id": serving["tenant_id"],
                                "_roster_swap_id": serving["held_swap_id"],
                                "_roster_old_model": serving["model"],
                            }
                        )
                policies = policies or [{"model": "", "max_tokens_out": 0}]
                owners = await conn.fetch(
                    """SELECT tenant_id FROM tank.agent_skill_submissions
                WHERE skill_id=$1 AND version=$2 AND region=$3""",
                    skill["id"],
                    skill["version"],
                    tank.box_region(),
                )
                if owners:
                    # A private package's cases never use another tenant's model
                    # permissions. A default policy still evaluates as its owner.
                    by_owner = []
                    for owner in owners:
                        targets = [
                            row
                            for row in policies
                            if row.get("tenant_id") == owner["tenant_id"]
                        ]
                        targets = targets or [
                            row for row in policies if row.get("tenant_id") is None
                        ]
                        for target in targets or [{"model": "", "max_tokens_out": 0}]:
                            by_owner.append({**target, "tenant_id": owner["tenant_id"]})
                    policies = by_owner
                if skill["task_type"] is None:
                    policies = [
                        {
                            "model": "skill:" + skill["id"] + "@" + skill["version"],
                            "max_tokens_out": 0,
                            "_roster_code": True,
                        }
                    ]
                for policy in policies:
                    captured = {**policy, "_roster_fingerprint": current}
                    await conn.execute(
                        """INSERT INTO tank.skill_revalidations
                        (id,tenant_id,policy_tenant_id,task_type,old_model,model,policy,skill_id,version,swap_id)
                    VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)""",
                        uuid.uuid4(),
                        PLATFORM_TENANT,
                        policy.get("tenant_id"),
                        skill["task_type"] or "@code/" + skill["id"],
                        policy.get("_roster_old_model", policy["model"]),
                        policy["model"],
                        json.dumps(captured, default=str),
                        skill["id"],
                        skill["version"],
                        policy.get("_roster_swap_id"),
                    )
                    queued += 1
            return queued


async def before_policy_change(tank):
    """Read the live roster before routine policy writes; retain the serving row.

    The observer establishes the first deployment baseline. Later membership
    changes queue every approved package before a proposal can change policy.
    Completed failures stay in models_validated; the existing target-model
    policy-swap gate still decides whether a new proposal may serve.
    """
    with tenant_scope(PLATFORM_TENANT, administrative=True):
        async with tank.pool().acquire() as conn:
            baseline = await conn.fetchval(
                "SELECT fingerprint FROM tank.roster_observations WHERE tenant_id=$1 AND region=$2",
                PLATFORM_TENANT,
                tank.box_region(),
            )
        if not baseline:
            return
        try:
            await observe(tank)
        except Exception:
            raise HTTPException(
                503,
                "The model roster could not be checked. The serving policy is unchanged.",
            ) from None
        async with tank.pool().acquire() as conn:
            pending = await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM tank.skill_revalidations
                WHERE tenant_id=$1 AND completed_at IS NULL AND policy ? '_roster_fingerprint')""",
                PLATFORM_TENANT,
            )
        if pending:
            raise HTTPException(
                409,
                "Approved skills are being checked against the updated roster. The serving policy is unchanged.",
            )
