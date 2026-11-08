"""Gates 7+8: Zenas recalls before its brain and remembers after.

Uses the room's existing chassis memory client (``ctx.memory`` / MemoryClient),
which reaches Tank's tenant-scoped Hermes memory with the room key. This is not
a second memory system: no store, collection or index is added. Rows are
tenant-scoped by Hermes and further isolated here by firm, attorney, client,
matter and user through ``meta``.

What Zenas remembers (``REMEMBERS``) is fixed: approved worked law, task results
with Tank receipts, corrections, matter outcomes, and the memory on/off setting.

An exact repeat (same tenant, user, firm, attorney, client, matter, task key and
input hash) is answered from memory before any brain or Tank call. Exact lookup
uses the SQL record list, never an embedding search, so a hit costs no model
call and no tokens. Corrections invalidate and override stale results.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from typing import Any, Awaitable, Callable

from fastapi import HTTPException


async def memory_call(operation):
    """Storage outages may degrade; authentication must fail closed."""
    try:
        return await operation
    except RuntimeError as exc:
        if str(exc) in {"hermes answered 401", "hermes answered 403"}:
            raise HTTPException(int(str(exc)[-3:]), "Memory ownership required") from None
        raise

RESULT = "zenas.result"
CORRECTION = "zenas.correction"
WORKED_LAW = "zenas.worked_law"
OUTCOME = "zenas.outcome"
RECEIPT = "zenas.receipt"
SETTING = "zenas.memory_setting"
FACT = "zenas.fact"
PARTY = "zenas.party"
DOCUMENT = "zenas.document"
POSITION = "zenas.position"
DRAFT = "zenas.draft"
CLAUSE = "zenas.clause"
HOUSE_STYLE = "zenas.house_style"
JURISDICTION = "zenas.jurisdiction"
DEADLINE = "zenas.deadline"
STUDENT = "zenas.student_context"
VISITOR = "zenas.visitor_context"
# Personal by default. Only kinds listed in TENANT_WIDE may omit meta.user_id.
# Central Hermes (unapplied tank-patches) enforces this; product still stamps actor.user_id.
REMEMBERS = {
    WORKED_LAW: "approved worked law the firm already settled for this scope",
    RESULT: "task outcome with its Tank receipt",
    CORRECTION: "user corrections; they override earlier results",
    OUTCOME: "matter and task outcomes",
    RECEIPT: "Tank ledger receipts (calls, cost, tokens)",
    SETTING: "whether this user's memory is on",
    FACT: "matter facts and case facts the attorney recorded",
    PARTY: "parties on a matter",
    DOCUMENT: "document handles and versions on a matter",
    POSITION: "positions and holdings the firm settled",
    DRAFT: "draft wording the attorney kept",
    CLAUSE: "clause library entries for this firm",
    HOUSE_STYLE: "house style and drafting preferences",
    JURISDICTION: "jurisdiction choices for a matter or ask",
    DEADLINE: "deadlines the matter tracks",
    STUDENT: "Street Law / student clinic context",
    VISITOR: "moot / visitor hearing context",
}
# No Zenas kind is tenant-wide today: every remember stamps the signed-in user.
TENANT_WIDE: frozenset[str] = frozenset()
SOURCE = "zenas:gates-7-8"


def brain_enabled() -> bool:
    """The brain is explicitly opt-in."""
    return os.environ.get("ZENAS_BRAIN_ENABLED", "false").strip().lower() not in {
        "0", "false", "no", "off"
    }


def memory_enabled_env() -> bool:
    """Global memory switch; users can still turn their own memory off in-store."""
    return os.environ.get("ZENAS_MEMORY_ENABLED", "false").strip().lower() not in {
        "0", "false", "no", "off"
    }


def input_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _uuid(value: Any, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"{name} must be a UUID") from None


def trusted_user(value: Any) -> str:
    """Caller passes only an identity its guard already verified (never a body field alone)."""
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.:@|-]{1,200}", value) is None:
        raise ValueError("verified user is required")
    return value


def trusted_scope_id(value: Any, name: str, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"{name} is required")
        return None
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:@|-]{1,200}", value):
        return value
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"{name} must be a stable id") from None


def scope_parts(
    tenant: str,
    user: str,
    *,
    firm_id: str | None = None,
    attorney_id: str | None = None,
    client_id: str | None = None,
    matter_id: str | None = None,
) -> dict[str, str]:
    """Firm defaults to tenant; attorney defaults to user. Client and matter stay explicit."""
    firm = trusted_scope_id(firm_id, "firm_id") or tenant
    attorney = trusted_scope_id(attorney_id, "attorney_id") or user
    client = trusted_scope_id(client_id, "client_id") or ""
    matter = trusted_scope_id(matter_id, "matter_id") or ""
    return {
        "tenant_id": tenant,
        "user_id": user,
        "firm_id": firm,
        "attorney_id": attorney,
        "client_id": client,
        "matter_id": matter,
    }


def scope_key(parts: dict[str, str], task_key: str, digest: str) -> str:
    joined = "\x1f".join(
        (
            parts["tenant_id"],
            parts["user_id"],
            parts["firm_id"],
            parts["attorney_id"],
            parts["client_id"],
            parts["matter_id"],
            task_key,
            digest,
        )
    )
    return hashlib.sha256(joined.encode()).hexdigest()


def free_receipt(memory_id: str | None, source: str, *, would_have_cost: dict | None = None) -> dict:
    would = would_have_cost or {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}
    return {
        "source": source,
        "memory_id": memory_id,
        "paid_calls": 0,
        "cost_usd": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "ledger_ids": [],
        "answered_from_memory": True,
        "would_have_cost": {
            "cost_usd": float(would.get("cost_usd") or 0.0),
            "tokens_in": int(would.get("tokens_in") or 0),
            "tokens_out": int(would.get("tokens_out") or 0),
        },
    }


def tank_receipt(replies: list[dict]) -> dict:
    """Summarise Tank route replies; missing prices stay None (pending), never guessed."""
    costs = [r.get("cost_usd") for r in replies]
    return {
        "source": "tank",
        "memory_id": None,
        "paid_calls": len(replies),
        "cost_usd": None if any(c is None for c in costs) else round(sum(float(c) for c in costs), 10),
        "tokens_in": sum(int(r.get("tokens_in") or 0) for r in replies),
        "tokens_out": sum(int(r.get("tokens_out") or 0) for r in replies),
        "ledger_ids": [r["ledger_id"] for r in replies if r.get("ledger_id")],
    }


class GateMemory:
    def __init__(
        self,
        memory: Any,
        tenant: str,
        user: str,
        *,
        firm_id: str | None = None,
        attorney_id: str | None = None,
        client_id: str | None = None,
        matter_id: str | None = None,
    ) -> None:
        if memory is None:
            raise ValueError("memory client is required")
        self.memory = memory
        self.tenant = _uuid(tenant, "tenant")
        self.user = trusted_user(user)
        self.parts = scope_parts(
            self.tenant,
            self.user,
            firm_id=firm_id,
            attorney_id=attorney_id,
            client_id=client_id,
            matter_id=matter_id,
        )

    def _owns(self, row: dict) -> bool:
        meta = row.get("meta") or {}
        if row.get("kind") not in REMEMBERS:
            return False
        if meta.get("user") != self.user and meta.get("user_id") != self.user:
            return False
        if meta.get("firm_id", self.parts["firm_id"]) != self.parts["firm_id"]:
            return False
        if meta.get("attorney_id", self.parts["attorney_id"]) != self.parts["attorney_id"]:
            return False
        if meta.get("client_id", "") != self.parts["client_id"]:
            return False
        if meta.get("matter_id", "") != self.parts["matter_id"]:
            return False
        return True

    async def _all_rows(self) -> list[dict]:
        rows = await memory_call(self.memory.records(self.tenant, user_id=self.user))
        return [r for r in rows if str(r.get("tenant_id")) == self.tenant
                and (r.get("meta") or {}).get("user_id") == self.user]

    async def _rows(self) -> list[dict]:
        rows = await memory_call(self.memory.records(self.tenant, user_id=self.user))
        mine = []
        for row in rows:
            if row.get("tenant_id") not in (None, self.tenant):
                raise RuntimeError("Memory tenant boundary failed")
            if self._owns(row):
                mine.append(row)
        return mine

    async def view(self) -> list[dict]:
        """Everything Zenas remembers for this firm/attorney/client/matter/user, oldest first."""
        return sorted(await self._rows(), key=lambda r: int((r.get("meta") or {}).get("seq") or 0))

    async def _remember(self, kind: str, text: str, meta: dict) -> dict:
        if kind not in REMEMBERS:
            raise ValueError("unsupported Zenas memory kind")
        if kind not in TENANT_WIDE and not self.user:
            raise ValueError("verified user is required for personal memory")
        seq = max([int((r.get("meta") or {}).get("seq") or 0) for r in await self._rows()] + [0]) + 1
        payload = {
            **meta,
            "user": self.user,
            "user_id": self.user,
            "firm_id": self.parts["firm_id"],
            "attorney_id": self.parts["attorney_id"],
            "client_id": self.parts["client_id"],
            "matter_id": self.parts["matter_id"],
            "seq": seq,
        }
        return await memory_call(self.memory.remember(
            self.tenant, text, kind, SOURCE, confidence=1.0, meta=payload,
            index=False, user_id=self.user,
        ))

    async def enabled(self) -> bool:
        if not memory_enabled_env():
            return False
        settings = [r for r in await self._all_rows() if r.get("kind") == SETTING]
        return bool((settings[-1].get("meta") or {}).get("enabled", True)) if settings else True

    async def set_enabled(self, flag: bool) -> dict:
        for row in [r for r in await self._all_rows() if r.get("kind") == SETTING]:
            await memory_call(self.memory.forget(row["id"], self.tenant, user_id=self.user))
        return await self._remember(
            SETTING, "memory " + ("on" if flag else "off"), {"enabled": bool(flag)}
        )

    async def forget(self, memory_id: str | None = None) -> int:
        """Forget one of this scope's rows, or all of them (the setting survives)."""
        rows = [r for r in await self._rows() if r.get("kind") != SETTING]
        if memory_id is not None:
            targets = [r for r in rows if r.get("id") == memory_id]
            if not targets:
                raise KeyError("memory not found for this scope")
            target = targets[0]
            key = (target.get('meta') or {}).get('key')
            rows = [r for r in rows if r['id'] == memory_id or
                    r['kind'] in {RESULT, CORRECTION} and
                    (not key or (r.get('meta') or {}).get('key') == key)]

        for row in rows:
            await memory_call(self.memory.forget(row["id"], self.tenant, user_id=self.user))
        return len(rows)

    async def edit(self, memory_id: str, value: Any) -> dict:
        """Replace one remembered value for this scope; results become corrections."""
        rows = [r for r in await self._rows() if r.get("id") == memory_id and r.get("kind") != SETTING]
        if not rows:
            raise KeyError("memory not found for this scope")
        row, meta = rows[0], dict(rows[0].get("meta") or {})
        await memory_call(self.memory.forget(row["id"], self.tenant, user_id=self.user))
        if row.get("kind") in {RESULT, CORRECTION, WORKED_LAW}:
            return await self._remember(
                CORRECTION,
                str(meta.get("task_key")) + " correction",
                {
                    "key": meta.get("key"),
                    "task_key": meta.get("task_key"),
                    "result": value,
                    "original_result": meta.get("original_result", meta.get("result")),
                    "receipt": meta.get("receipt") or {},
                },
            )
        for cached in await self._rows():
            if cached['kind'] in {RESULT, CORRECTION}:
                await memory_call(self.memory.forget(cached['id'], self.tenant, user_id=self.user))
        meta.pop("seq", None)
        meta.pop("user", None)
        meta.pop("user_id", None)
        return await self._remember(
            row["kind"],
            json.dumps({"value": value}, ensure_ascii=False),
            {**meta, "value": value},
        )

    async def approved_worked_law(self) -> list[dict]:
        """Approved worked-law rows for this firm/attorney/client/matter before paid work."""
        return [r for r in await self.view() if r.get("kind") == WORKED_LAW]

    async def remember_worked_law(self, subject: str, value: Any) -> dict | None:
        if not await self.enabled():
            return None
        return await self._remember(
            WORKED_LAW,
            json.dumps({"subject": subject, "value": value}, ensure_ascii=False),
            {"subject": subject, "value": value, "approved": True},
        )

    async def lookup(self, task_key: str, payload: Any) -> dict | None:
        key = scope_key(self.parts, task_key, input_hash(payload))
        rows = [
            r
            for r in await self.view()
            if (r.get("meta") or {}).get("key") == key
            and r.get("kind") in {RESULT, CORRECTION, WORKED_LAW}
        ]
        return rows[-1] if rows else None

    async def remember_result(self, task_key: str, payload: Any, result: Any, receipt: dict) -> dict:
        key = scope_key(self.parts, task_key, input_hash(payload))
        return await self._remember(
            RESULT,
            task_key + " result",
            {"key": key, "task_key": task_key, "input": payload, "result": result, "receipt": receipt, "approved": True},
        )

    async def correct(self, task_key: str, payload: Any, result: Any) -> dict:
        """Store a correction and forget the stale results it replaces."""
        key = scope_key(self.parts, task_key, input_hash(payload))
        for row in await self._rows():
            meta = row.get("meta") or {}
            if meta.get("key") == key and row.get("kind") in {RESULT, CORRECTION, WORKED_LAW}:
                await memory_call(self.memory.forget(row["id"], self.tenant, user_id=self.user))
        return await self._remember(
            CORRECTION,
            task_key + " correction",
            {"key": key, "task_key": task_key, "result": result},
        )


Brain = Callable[[], Awaitable[tuple[Any, dict]]]


async def run(
    memory: Any,
    tenant: str,
    user: str,
    task_key: str,
    payload: Any,
    brain: Brain,
    *,
    firm_id: str | None = None,
    attorney_id: str | None = None,
    client_id: str | None = None,
    matter_id: str | None = None,
) -> dict:
    """Recall approved worked law / exact result, then (only on a miss) run brain through Tank."""
    gate = GateMemory(
        memory,
        tenant,
        user,
        firm_id=firm_id,
        attorney_id=attorney_id,
        client_id=client_id,
        matter_id=matter_id,
    )
    memory_status = "ok"
    on = False
    worked: list = []
    try:
        on = await gate.enabled()
        worked = await gate.approved_worked_law() if on else []
        if on:
            hit = await gate.lookup(task_key, payload)
            if hit is not None:
                meta = hit.get("meta") or {}
                source = "correction" if hit.get("kind") == CORRECTION else "memory"
                prior = meta.get("receipt") if isinstance(meta.get("receipt"), dict) else {}
                nested = prior.get("would_have_cost") if isinstance(prior.get("would_have_cost"), dict) else {}
                would = meta.get("would_have_cost") if isinstance(meta.get("would_have_cost"), dict) else nested
                receipt = free_receipt(
                    hit.get("id"),
                    source,
                    would_have_cost={
                        "cost_usd": (would or prior).get("cost_usd") or 0.0,
                        "tokens_in": (would or prior).get("tokens_in") or 0,
                        "tokens_out": (would or prior).get("tokens_out") or 0,
                    },
                )
                return {
                    "result": copy.deepcopy(meta["result"]),
                    "receipt": receipt,
                    "answered_from_memory": True,
                    "memory": {
                        "hit": True,
                        "memory_id": hit.get("id"),
                        "enabled": True,
                        "worked_law": len(worked),
                        "status": memory_status,
                    },
                }
    except HTTPException:
        raise
    except Exception as exc:
        memory_status = "memory unavailable"
        on = False
        worked = []
        _ = type(exc).__name__

    result, receipt = await brain()
    would = {
        "cost_usd": receipt.get("cost_usd"),
        "tokens_in": receipt.get("tokens_in") or 0,
        "tokens_out": receipt.get("tokens_out") or 0,
    }
    receipt = {
        **receipt,
        "answered_from_memory": False,
        "would_have_cost": would,
        "memory_status": memory_status,
    }
    saved = None
    if on and memory_status == "ok":
        try:
            saved = await gate.remember_result(
                task_key, payload, result, {**receipt, "would_have_cost": would}
            )
        except HTTPException:
            raise
        except Exception:
            memory_status = "memory unavailable"
            receipt["memory_status"] = memory_status
    receipt = {**receipt, "memory_id": saved.get("id") if saved else None}
    return {
        "result": result,
        "receipt": receipt,
        "answered_from_memory": False,
        "memory": {
            "hit": False,
            "memory_id": receipt["memory_id"],
            "enabled": on,
            "worked_law": len(worked),
            "status": memory_status,
        },
    }


async def remembered_task(
    memory: Any,
    tenant: str,
    user: str,
    task_key: str,
    payload: Any,
    *,
    http: Any = None,
    tank: Any = None,
    firm_id: str | None = None,
    attorney_id: str | None = None,
    client_id: str | None = None,
    matter_id: str | None = None,
) -> dict:
    """Product entry: memory first (independent of brain switch), optional brain, Tank, remember."""
    if tank is None:
        raise ValueError("Tank client is required for Zenas paid routing")

    async def paid() -> tuple[Any, dict]:
        from agents.zenas.zenas_brain import brain_then_route

        if brain_enabled() and http is not None:
            routed = await brain_then_route(
                http,
                tank,
                tenant_id=str(tenant),
                task_type=task_key,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
        else:
            routed = await tank.route(
                str(tenant), task_key, [{"role": "user", "content": json.dumps(payload)}]
            )
            routed = {**routed, "brain": {"enabled": False}}
        receipt = tank_receipt(
            [
                {
                    "ledger_id": routed.get("ledger_id"),
                    "cost_usd": routed.get("cost_usd"),
                    "tokens_in": routed.get("tokens_in"),
                    "tokens_out": routed.get("tokens_out"),
                }
            ]
        )
        return routed.get("content"), {**receipt, "brain": routed.get("brain")}

    outcome = await run(
        memory,
        tenant,
        user,
        task_key,
        payload,
        paid,
        firm_id=firm_id,
        attorney_id=attorney_id,
        client_id=client_id,
        matter_id=matter_id,
    )
    return {
        **outcome,
        "receipt": {
            **outcome["receipt"],
            "memory_hit": outcome["memory"]["hit"],
            "answered_from_memory": bool(outcome.get("answered_from_memory")),
        },
        "brain": {"enabled": brain_enabled()},
    }


MEMORY_OFF_KIND = SETTING
MEMORY_OFF_SOURCE = "zenas:memory:switch"
MEMORY_OFF_TEXT = "memory_off=true"


def owned_memory_row(
    rows: list[dict],
    *,
    memory_id: str,
    tenant_id: str,
    user_id: str,
) -> dict | None:
    """Return the row only when tenant and meta user both match the signed-in actor."""
    wanted = str(memory_id)
    tenant = str(tenant_id)
    user = str(user_id)
    for row in rows:
        if str(row.get("id", "")) != wanted:
            continue
        if str(row.get("tenant_id", "")) != tenant:
            return None
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            return None
        owner = meta.get("user_id") or meta.get("user")
        if str(owner or "") != user:
            return None
        return row
    return None


def select_tenant_user_memories(
    rows: list[dict],
    *,
    tenant_id: str,
    user_id: str,
) -> list[dict]:
    """Keep only memories owned by this tenant and signed-in user."""
    tenant = str(tenant_id)
    user = str(user_id)
    kept: list[dict] = []
    for row in rows:
        if str(row.get("tenant_id", "")) != tenant:
            continue
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            continue
        owner = meta.get("user_id") or meta.get("user")
        if str(owner or "") != user:
            continue
        kept.append(row)
    return kept


def memory_off_from_rows(rows: list[dict], *, tenant_id: str, user_id: str) -> bool:
    """True when this signed-in user has switched memory off in this tenant."""
    for row in select_tenant_user_memories(rows, tenant_id=tenant_id, user_id=user_id):
        if row.get("kind") != MEMORY_OFF_KIND:
            continue
        meta = row.get("meta") or {}
        if meta.get("enabled") is False or MEMORY_OFF_TEXT in str(row.get("text", "")):
            return True
    return False


class FakeMemory:
    """In-process fake for offline gates 7+8 HTTP control tests. No network."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.remembers = 0
        self.forgets = 0
        self.recalls = 0

    async def remember(
        self,
        tenant_id: str,
        text: str,
        kind: str,
        source: str,
        *,
        meta: dict | None = None,
        **_: Any,
    ) -> dict:
        if kind not in REMEMBERS and kind != MEMORY_OFF_KIND:
            raise ValueError("unsupported Zenas memory kind")
        row = {
            "id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "text": text,
            "kind": kind,
            "source": source,
            "meta": dict(meta or {}),
        }
        self.rows.append(row)
        self.remembers += 1
        return {"id": row["id"], "seen": self.remembers}

    async def records(self, tenant_id: str, *, tier: str = "client", user_id: str | None = None) -> list[dict]:
        del tier
        # Simulate central Hermes user filter when user_id is provided.
        rows = [r for r in self.rows if r["tenant_id"] == str(tenant_id)]
        if user_id is not None:
            rows = [r for r in rows if (r.get("meta") or {}).get("user_id") == str(user_id) or (r.get("meta") or {}).get("user") == str(user_id) or r.get("kind") in TENANT_WIDE]
            return copy.deepcopy(rows)
        return copy.deepcopy(rows)

    async def recall(self, *args: Any, **kwargs: Any) -> list[dict]:
        raise AssertionError("exact repeats / controls must not use embedding recall")

    async def forget(self, memory_id: str, tenant_id: str) -> None:
        before = len(self.rows)
        self.rows = [
            r for r in self.rows
            if not (r["id"] == str(memory_id) and r["tenant_id"] == str(tenant_id))
        ]
        if len(self.rows) < before:
            self.forgets += 1
