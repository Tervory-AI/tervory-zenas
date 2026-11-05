"""Deterministic first rung. A diagnosis is never treated as a production command."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid5

from .models import Look, Observation, PLATFORM_TENANT, Resolution, Signal
from .store import Store


class Investigator:
    def __init__(self, store: Store, probes: Any, receipts: Any):
        self.store, self.probes, self.receipts = store, probes, receipts

    async def record(self, item: dict, step: str, outcome: str = "delivered") -> None:
        receipt_id = str(uuid5(UUID(item["id"]), step))
        await self.receipts.record(receipt_id, step, outcome)
        if not any(row["step"] == step for row in item["steps"]):
            item["steps"].append({"step": step, "ledger_id": receipt_id, "cost_usd": 0})
        self.store.save(PLATFORM_TENANT, item)

    async def alert(self, observation: Observation) -> dict | None:
        if not observation.failing:
            return None
        return await self.look(Look(target=observation.target, signal=observation.signal), "alert")

    async def look(self, request: Look, origin: str = "look") -> dict:
        if origin not in {"look", "alert", "message"}:
            raise ValueError("Unknown issue source.")
        item = self.store.open(PLATFORM_TENANT, request.target.value, request.signal.value, origin)
        if item["state"] == "blocked" and origin == "look":
            item["state"] = "open"
        if item["state"] != "open":
            return item
        await self.record(item, "opened")
        item["previous"] = await self.store.recall(PLATFORM_TENANT, f"{item['target']}:{item['signal']}")
        await self.record(item, "recall")
        if request.signal == Signal.human_check:
            item.update(state="human_required", summary="A human check needs a person. Autumbra Watch has stopped.")
            await self.record(item, "human_required")
            return item
        try:
            evidence = await self.probes.reproduce(request)
            if evidence.target != request.target or evidence.signal not in {request.signal, Signal.human_check}:
                raise ValueError("The evidence does not match the requested check.")
        except Exception:
            # Exception strings, browser output and HTTP bodies may contain secrets.
            item.update(state="blocked", summary="Evidence is unavailable. No action was taken.")
            await self.record(item, "evidence_unavailable", "failed")
            return item
        item["evidence"] = evidence.model_dump(mode="json")
        await self.record(item, "reproduce")
        if evidence.signal == Signal.human_check:
            item.update(state="human_required", summary="A human check needs a person. Autumbra Watch has stopped.")
        elif not evidence.failing:
            item.update(state="not_reproduced", summary="The check passed. The reported problem was not reproduced.")
        else:
            item.update(state="needs_review", summary="The problem was reproduced. A fix needs review; the cause is not yet confirmed.")
            item["brief"] = self.brief(item)
        await self.record(item, "reported")
        return item

    @staticmethod
    def brief(item: dict) -> dict:
        # This is a proposal, not a shell command, patch, or unrestricted worker job.
        return {"incident_id": item["id"], "status": "awaiting_founder_approval",
                "title": f"Investigate {item['signal']} on {item['target']}",
                "problem": item["evidence"], "cause": "Not yet confirmed",
                "instructions": ["Reproduce the recorded failure and confirm the cause.",
                                 "Ask Tank to hand code to sealed Radix; Autumbra Watch must not scan code.",
                                 "Implement only the founder-approved file scope in a worker branch.",
                                 "Open a PR with regression tests and receipts. Never merge or deploy."],
                "files_you_may_change": [], "receipt_ids": [s["ledger_id"] for s in item["steps"]]}

    async def resolve(self, incident: str, resolution: Resolution) -> dict:
        item = self.store.get(PLATFORM_TENANT, incident)
        if item["state"] == "solved":
            return item
        if item["state"] not in {"needs_review", "not_reproduced", "blocked"}:
            raise ValueError("This issue cannot be marked solved.")
        check = await self.probes.reproduce(Look(target=item["target"], signal=item["signal"]))
        if check.failing or check.signal.value != item["signal"]:
            raise ValueError("The original check still fails. Nothing was marked solved.")
        await self.record(item, "verified")
        item.update(cause=resolution.cause, fix=resolution.fix,
                    solved_at=datetime.now(timezone.utc).isoformat(),
                    summary="The founder confirmed the cause and fix. The original check now passes.")
        await self.record(item, "remembered")
        # Only independently confirmed, rechecked solutions enter memory/gold.
        await self.store.remember(PLATFORM_TENANT, {key: item[key] for key in
                                  ("id", "target", "signal", "cause", "fix", "solved_at")})
        item["state"] = "solved"
        self.store.save(PLATFORM_TENANT, item)
        return item


def authorize_action(action: str, target: str, observed_state: str) -> None:
    allowed = {
        "rerun_failed_deploy": ({"workspace", "site", "tank", "base", "tiger", "market_lab", "rig", "tisan", "radix", "zenas", "ghost"}, "failed"),
        "restart_crashed_room": ({"base", "tiger", "market_lab", "rig", "tisan", "radix", "zenas", "ghost"}, "crashed"),
    }
    rule = allowed.get(action)
    if rule is None or target not in rule[0] or observed_state != rule[1]:
        raise PermissionError("This action is not permitted. No production change was made.")
