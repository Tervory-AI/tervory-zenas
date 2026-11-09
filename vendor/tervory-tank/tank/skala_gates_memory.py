"""Gates 7+8 memory for Skala: recall before Tank agent path, remember after.

Hermes via chassis MemoryClient only (POST /memories, POST /recall, GET /records,
DELETE /memories/{id}). Never Tank /v1/memory. Own agent key TANK_KEY_SKALA by
field name. Product passes verifiedActor.user_id only — no body user_id, no
product-side user filtering (t51). Requires the signed, user-scoped chassis
and Hermes contract merged in Tank #765; never falls back to an unscoped client.

Kinds: skala.skill_used, skala.version_worked, skala.failure,
skala.correction, skala.receipt, skala.task, skala.memory_control (personal).
No Skala brain: SKALA_BRAIN_ENABLED defaults false; Tank agent path for paid work.
"""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Awaitable, Callable

FORBIDDEN_SECRET_MARKERS = (
    "api_key", "apikey", "authorization", "bearer ", "password", "secret",
    "token=", "private_key",
)
MEMORY_CONTROLS = frozenset({"off", "view", "edit", "forget"})
CONTROL_KIND = "skala.memory_control"
CONTROL_SOURCE = "skala:memory_control"
TENANT_WIDE_KINDS = frozenset()  # Fixed core declares no shared Skala kinds.
FIXED_FAKE_PER_CALL = {"cost_usd": 0.05, "tokens_in": 10, "tokens_out": 3}
CACHE_ELIGIBLE_TASK_TYPES = frozenset({
    "tier0.answer", "tier0.summarize", "tier0.classify",
    "skill-run", "skill-cost-twice", "skill-retry",
})
MEMORY_VERIFIED_ACTOR_REQUIRED = (
    "Skala memory requires a verified signed-in tenant+user; "
    "body/query/header identity is not authority"
)
_verified_actor: ContextVar[dict[str, str] | None] = ContextVar(
    "skala_verified_memory_actor", default=None
)


class MemoryScopeError(ValueError):
    """Tenant/user scope refused."""


class MemoryControlError(ValueError):
    """Memory control refuses the requested action."""


class MemoryUnavailable(RuntimeError):
    """Memory service outage — answer without memory; never serve a stored repeat."""


def brain_enabled() -> bool:
    """Skala reuses Tank; this adapter never starts a separate brain."""
    return False


def set_verified_actor(*, tenant_id: str, user_id: str) -> None:
    tenant, user = _normalize_scope(tenant_id, user_id)
    _verified_actor.set({"tenant_id": tenant, "user_id": user})


def clear_verified_actor() -> None:
    _verified_actor.set(None)


def memory_actor_from_verified_proof() -> dict[str, str] | None:
    proof = _verified_actor.get()
    return dict(proof) if proof else None


def require_verified_memory_actor() -> dict[str, str]:
    proof = memory_actor_from_verified_proof()
    if not proof:
        raise MemoryScopeError(MEMORY_VERIFIED_ACTOR_REQUIRED)
    return proof


def _normalize_scope(tenant_id: Any, user_id: Any) -> tuple[str, str]:
    if tenant_id in (None, ""):
        raise MemoryScopeError("authenticated tenant_id required for Skala memory")
    tenant = str(tenant_id).strip()
    if not tenant:
        raise MemoryScopeError("authenticated tenant_id required for Skala memory")
    if user_id in (None, ""):
        raise MemoryScopeError("authenticated user_id required for Skala memory")
    user = str(user_id).strip()
    if not user:
        raise MemoryScopeError("authenticated user_id required for Skala memory")
    if user == tenant:
        raise MemoryScopeError("tenant-as-user identity is refused for Skala memory")
    return tenant, user


def require_authenticated_scope(*, tenant_id: Any, user_id: Any) -> tuple[str, str]:
    proof = require_verified_memory_actor()
    tenant, user = _normalize_scope(tenant_id, user_id)
    if tenant != proof["tenant_id"] or user != proof["user_id"]:
        raise MemoryScopeError(MEMORY_VERIFIED_ACTOR_REQUIRED)
    return tenant, user


def _text_has_secret(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in FORBIDDEN_SECRET_MARKERS)


def memory_kinds() -> frozenset[str]:
    return frozenset({
        "skala.skill_used", "skala.version_worked", "skala.failure",
        "skala.correction", "skala.receipt", "skala.task", CONTROL_KIND,
    })


def normalize_memory_control(value: Any) -> str:
    control = str(value or "edit").strip().lower()
    if control not in MEMORY_CONTROLS:
        raise ValueError(f"unsupported memory control: {control}")
    return control


def _control_allows_recall(control: str) -> bool:
    return control in {"view", "edit", "forget"}


def _control_allows_remember(control: str) -> bool:
    return control in {"edit", "forget"}


def _control_allows_forget(control: str) -> bool:
    return control == "forget"


def task_input_hash(task: dict[str, Any] | None) -> str:
    payload = task if isinstance(task, dict) else {}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def is_cache_eligible(task_key: str, task: dict[str, Any] | None = None) -> bool:
    task = task or {}
    task_type = str(task.get("task_type") or task_key or "")
    return task_type in CACHE_ELIGIBLE_TASK_TYPES or task_type.startswith(
        ("skill-", "tier0.", "skala.")
    )


def build_memory_text(
    *, skills_used: list[str] | None = None, versions_worked: list[str] | None = None,
    failures: list[str] | None = None, correction: str | None = None,
    receipt: str | None = None, summary: str | None = None, outcome: str | None = None,
) -> str:
    parts: list[str] = []
    if summary:
        parts.append(summary)
    if outcome:
        parts.append("outcome=" + str(outcome))
    if skills_used:
        parts.append("skills_used=" + ",".join(skills_used))
    if versions_worked:
        parts.append("versions_worked=" + ",".join(versions_worked))
    if failures:
        parts.append("failures=" + ",".join(failures))
    if correction:
        parts.append("correction=" + correction)
    if receipt:
        parts.append("receipt=" + receipt)
    text = "; ".join(parts)
    if not text.strip():
        raise ValueError("Skala memory refuses empty remember text")
    if _text_has_secret(text):
        raise ValueError("Skala memory refuses secret-bearing text")
    return text


def corrections_from_memories(rows: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for row in rows:
        text = str(row.get("text", row.get("claim", "")))
        correction = (row.get("meta") or {}).get("correction")
        if isinstance(correction, str) and correction.strip():
            found.append(correction.strip())
        elif "correction=" in text:
            found.append(text.split("correction=", 1)[1].split(";")[0].strip())
        elif row.get("kind") == "skala.correction" and text.strip():
            found.append(text.strip())
    return found


def memory_control_from_memories(rows: list[dict[str, Any]]) -> str | None:
    for row in reversed(rows):
        if row.get("kind") != CONTROL_KIND:
            continue
        meta = row.get("meta") or {}
        if isinstance(meta, dict) and meta.get("control") in MEMORY_CONTROLS:
            return str(meta["control"])
        text = str(row.get("text", ""))
        if "memory_control=" in text:
            value = text.split("memory_control=", 1)[1].split(";")[0].strip().lower()
            if value in MEMORY_CONTROLS:
                return value
    return None


def identical_reuse_from_memories(
    rows: list[dict[str, Any]], *, task_key: str, input_hash: str,
) -> dict[str, Any] | None:
    key, digest = str(task_key), str(input_hash)
    for row in reversed(rows):
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            continue
        if str(meta.get("task_key", "")) != key:
            continue
        if str(meta.get("input_hash", "")) != digest:
            continue
        if meta.get("invalidated"):
            continue
        reusable = meta.get("reusable_result")
        if isinstance(reusable, dict) and reusable:
            would = meta.get("would_have_cost") or {}
            return {
                "result": deepcopy(reusable),
                "memory_id": row.get("id"),
                "reuse": "identical_receipt",
                "input_hash": digest,
                "would_have_cost": {
                    "cost_usd": float(would.get("cost_usd") or 0.0),
                    "tokens_in": int(would.get("tokens_in") or 0),
                    "tokens_out": int(would.get("tokens_out") or 0),
                },
            }
    return None


def apply_corrections(task: dict[str, Any], corrections: list[str]) -> dict[str, Any]:
    result = deepcopy(task)
    if not corrections:
        return result
    applied = list(result.get("applied_corrections") or [])
    for item in corrections:
        if item and item not in applied:
            applied.append(item)
    result["applied_corrections"] = applied
    result["answered_from_memory"] = True
    if "messages" in task:
        result["content"] = corrections[-1]
    return result


async def _invalidate_receipts_for_task(memory: Any, *, tenant_id: str, user_id: str, task_key: str) -> None:
    for row in await _rows_for_tenant(memory, tenant_id, user_id):
        meta = dict(row.get("meta") or {})
        if meta.get("task_key") != task_key or not meta.get("reusable_result"):
            continue
        meta.update(invalidated=True, reusable_result=None)
        await _replace_record(memory, row, tenant_id=tenant_id, user_id=user_id,
                              claim=row["claim"], meta=meta)


async def _replace_record(memory: Any, row: dict, *, tenant_id: str, user_id: str,
                          claim: str, meta: dict) -> dict[str, Any]:
    # /records supplies claim/evidence, not the write/recall aliases text/source.
    # Hermes retains source as the first evidence item. Carry the whole record's
    # provenance and classification through the explicit, signed replacement.
    evidence = list(row["evidence"])
    return await _remember_scoped(
        memory, tenant_id, claim, row["kind"],
        evidence[0] if evidence else f"skala:edit:{row['id']}",
        user_id=user_id, meta=meta, record_id=str(row["id"]), replace=True,
        evidence=evidence, confidence=row.get("confidence", .5),
        last_verified=row.get("last_verified"),
    )


async def _remember_scoped(
    memory: Any, tenant_id: str, text: str, kind: str, source: str, *,
    user_id: str, meta: dict | None = None, **kwargs: Any,
) -> dict[str, Any]:
    meta = dict(meta or {})
    if kind not in TENANT_WIDE_KINDS:
        if not user_id:
            raise MemoryScopeError("personal Skala memory kind requires verified user_id")
        meta.setdefault("user_id", user_id)
    try:
        return await memory.remember(
            tenant_id, text, kind, source, user_id=user_id, meta=meta, **kwargs
        )
    except Exception as exc:
        _raise_memory_error(exc)


async def _recall_scoped(
    memory: Any, tenant_id: str, query: str, *, user_id: str,
    kind: str | None = None, limit: int | None = None,
) -> list[dict[str, Any]]:
    try:
        return await memory.recall(
            tenant_id, query, user_id=user_id, kind=kind, limit=limit
        )
    except Exception as exc:
        _raise_memory_error(exc)


def _raise_memory_error(exc: Exception) -> None:
    # Fixed chassis deliberately exposes only status, never service content.
    if isinstance(exc, (MemoryScopeError, TypeError, ValueError)):
        raise exc
    if str(exc) in {"hermes answered 401", "hermes answered 403"}:
        raise MemoryScopeError("Skala memory authentication refused") from exc
    raise MemoryUnavailable(f"memory unavailable: {type(exc).__name__}") from exc


async def resolve_memory_control(
    memory: Any, *, tenant_id: str, user_id: str, override: str | None = None,
) -> str:
    if override is not None:
        return normalize_memory_control(override)
    recalled = await _recall_scoped(
        memory, str(tenant_id), "memory_control", user_id=str(user_id),
        kind=CONTROL_KIND, limit=16,
    )
    found = memory_control_from_memories(recalled)
    return normalize_memory_control(found or "edit")


async def _rows_for_tenant(memory: Any, tenant: str, user_id: str) -> list[dict[str, Any]]:
    try:
        return list(await memory.records(tenant, user_id=user_id))
    except Exception as exc:
        _raise_memory_error(exc)


async def require_owned_record(
    memory: Any, *, memory_id: str, tenant_id: str, user_id: str,
) -> dict[str, Any]:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    if not str(memory_id).strip():
        raise MemoryScopeError("memory_id required for scoped memory control")
    rows = await _rows_for_tenant(memory, tenant, user)
    for row in rows:
        if str(row.get("id")) != str(memory_id):
            continue
        if str(row.get("tenant_id", "")) != tenant:
            continue
        meta = row.get("meta") or {}
        if not isinstance(meta, dict) or str(meta.get("user_id", "")) != user:
            continue
        return row
    raise MemoryScopeError(
        "refused: target memory tenant+user do not match signed-in caller"
    )


async def view_memories(
    memory: Any, *, tenant_id: str, user_id: str, task_key: str = "memory_control",
    memory_id: str | None = None,
) -> dict[str, Any]:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    control = await resolve_memory_control(memory, tenant_id=tenant, user_id=user)
    if control == "off":
        return {"memories": [], "memory_control": control}
    if memory_id is not None:
        owned = await require_owned_record(
            memory, memory_id=memory_id, tenant_id=tenant, user_id=user
        )
        return {"memories": [owned], "memory_control": control}
    recalled = await _rows_for_tenant(memory, tenant, user)
    return {"memories": recalled, "memory_control": control}


async def apply_memory_edit(
    memory: Any, *, memory_id: str, tenant_id: str, user_id: str, text: str,
    memory_control: str | None = None,
) -> dict[str, Any]:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    control = await resolve_memory_control(
        memory, tenant_id=tenant, user_id=user, override=memory_control
    )
    if not _control_allows_remember(control):
        raise MemoryControlError(f"memory control {control!r} refuses content edit")
    if not str(text).strip():
        raise ValueError("memory edit requires non-empty text")
    if _text_has_secret(text):
        raise ValueError("Skala memory refuses secret-bearing text")
    row = await require_owned_record(
        memory, memory_id=memory_id, tenant_id=tenant, user_id=user
    )
    await _invalidate_receipts_for_task(memory, tenant_id=tenant, user_id=user,
        task_key=str((row.get("meta") or {}).get("task_key", "")))
    meta = {**(row.get("meta") or {}), "user_id": user, "edited": True,
            "invalidated": True, "reusable_result": None, "correction": text.strip()}
    return await _replace_record(memory, row, tenant_id=tenant, user_id=user,
                                 claim=text.strip(), meta=meta)


async def apply_memory_forget(
    memory: Any, *, memory_id: str, tenant_id: str, user_id: str,
    memory_control: str = "forget",
) -> None:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    control = await resolve_memory_control(
        memory, tenant_id=tenant, user_id=user, override=memory_control
    )
    if not _control_allows_forget(control):
        raise MemoryControlError(f"memory control {control!r} refuses forget")
    row = await require_owned_record(
        memory, memory_id=memory_id, tenant_id=tenant, user_id=user
    )
    await _invalidate_receipts_for_task(memory, tenant_id=tenant, user_id=user,
        task_key=str((row.get("meta") or {}).get("task_key", "")))
    try:
        await memory.forget(memory_id, tenant, user_id=user)
    except Exception as exc:
        _raise_memory_error(exc)


async def set_memory_control(
    memory: Any, *, tenant_id: str, user_id: str, control: str,
    memory_id: str | None = None,
) -> dict[str, Any]:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    if memory_id is not None:
        await require_owned_record(
            memory, memory_id=memory_id, tenant_id=tenant, user_id=user
        )
    normalized = normalize_memory_control(control)
    text = build_memory_text(
        summary=f"Skala memory_control for user {user}",
        outcome=f"control:{normalized}",
        receipt=f"memory_control={normalized}",
        skills_used=["memory-control"],
    )
    return await _remember_scoped(
        memory, tenant, text, CONTROL_KIND, CONTROL_SOURCE,
        user_id=user,
        meta={"user_id": user, "control": normalized, "task_key": "memory_control"},
    )


class FakeMemory:
    """Offline stand-in implementing the PROPOSED chassis user_id contract (t51)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.recalls = 0
        self.remembers = 0
        self.forgets = 0
        self.fail_next = False

    async def remember(
        self, tenant_id: str, text: str, kind: str, source: str, *,
        user_id: str | None = None, meta: dict | None = None,
        record_id: str | None = None, evidence: list[str] | None = None,
        confidence: float = .5, last_verified: str | None = None, **_: Any,
    ) -> dict[str, Any]:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("hermes unreachable")
        if kind not in memory_kinds():
            raise ValueError("unsupported Skala memory kind")
        if _text_has_secret(text):
            raise ValueError("Skala memory refuses secret-bearing text")
        tenant = str(tenant_id)
        meta = dict(meta or {})
        if kind not in TENANT_WIDE_KINDS:
            if not user_id and not meta.get("user_id"):
                raise MemoryScopeError("personal Skala memory kind requires verified user_id")
            if user_id:
                meta["user_id"] = str(user_id)
        provenance = {"evidence": list(dict.fromkeys([source, *(evidence or [])])),
                      "confidence": confidence, "last_verified": last_verified}
        if record_id is not None:
            for row in self.rows:
                if row["id"] == str(record_id) and row["tenant_id"] == tenant:
                    row.update({"text": text, "kind": kind, "source": source, "meta": meta, **provenance})
                    self.remembers += 1
                    return {"id": row["id"], "seen": self.remembers, "updated": True}
            raise ValueError("remember update refused: memory not found for tenant")
        row = {
            "id": f"mem-{len(self.rows) + 1}",
            "tenant_id": tenant, "text": text, "kind": kind, "source": source, "meta": meta, **provenance,
        }
        self.rows.append(row)
        self.remembers += 1
        return {"id": row["id"], "seen": self.remembers}

    async def recall(
        self, tenant_id: str, query: str, *, user_id: str | None = None,
        kind: str | None = None, limit: int | None = None, **_: Any,
    ) -> list[dict[str, Any]]:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("hermes unreachable")
        self.recalls += 1
        tenant = str(tenant_id)
        matches: list[dict[str, Any]] = []
        for row in self.rows:
            if row["tenant_id"] != tenant:
                continue
            if kind is not None and row["kind"] != kind:
                continue
            meta = row.get("meta") or {}
            row_user = str(meta.get("user_id") or "")
            if row["kind"] in TENANT_WIDE_KINDS:
                pass
            elif not row_user:
                continue  # personal kind missing user_id refused
            elif not user_id or row_user != str(user_id):
                continue
            if (
                not query
                or query.lower() in row["text"].lower()
                or query == meta.get("task_key")
                or query == meta.get("control")
            ):
                matches.append(row)
        if limit is not None:
            matches = matches[:limit]
        return deepcopy(matches)

    async def forget(self, memory_id: str, tenant_id: str, *, user_id: str | None = None) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("hermes unreachable")
        tenant = str(tenant_id)
        before = len(self.rows)
        self.rows = [
            row for row in self.rows
            if not (row["id"] == str(memory_id) and row["tenant_id"] == tenant and (row.get("meta") or {}).get("user_id") == user_id)
        ]
        if len(self.rows) < before:
            self.forgets += 1

    async def records(
        self, tenant_id: str, *, user_id: str | None = None, tier: str = "client",
    ) -> list[dict[str, Any]]:
        del tier
        rows = await self.recall(str(tenant_id), "", user_id=user_id, limit=200)
        return [{**{k: v for k, v in row.items() if k not in {"text", "source"}},
                 "claim": row["text"]} for row in rows]


async def run_with_memory(
    memory: Any, *, tenant_id: str, user_id: str, task_key: str, task: dict[str, Any],
    paid_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    memory_control: str | None = None,
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Product → free/code → recall → correction|exact-repeat|Tank agent → remember.

    Memory service outage: paid path continues with receipt memory_unavailable;
    never serves a stored repeat from a failed memory read.
    """
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    digest = task_input_hash(task)
    cache_eligible = is_cache_eligible(task_key, task)
    receipt: dict[str, Any] = {
        "tenant_id": tenant, "user_id": user, "task_key": task_key,
        "input_hash": digest, "cache_eligible": cache_eligible,
        "memory_control": None, "steps": [],
        "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "paid_calls": 0,
        "answered_from_memory": False, "would_have_cost": None, "result": None,
        "memory_enabled": True, "brain_enabled": brain_enabled(),
        "memory_unavailable": False,
    }

    if code_free is not None:
        free = await code_free(task)
        receipt["steps"].append({"action": "code_skill", "hit": bool(free)})
        if free is not None:
            receipt["result"] = free.get("result", free)
            receipt["steps"].append({"action": "code_free_done"})
            return receipt

    try:
        control = await resolve_memory_control(
            memory, tenant_id=tenant, user_id=user, override=memory_control
        )
    except MemoryUnavailable:
        control = "edit"
        receipt["memory_unavailable"] = True
        receipt["steps"].append({"action": "memory_unavailable", "phase": "control"})
    receipt["memory_control"] = control
    receipt["memory_enabled"] = control != "off"

    if control == "off":
        receipt["steps"].append({"action": "memory_off"})
        paid = await paid_call(deepcopy(task))
        receipt["paid_calls"] = 1
        receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
        receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
        receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
        receipt["result"] = paid.get("result", paid)
        receipt["would_have_cost"] = {
            "cost_usd": receipt["cost_usd"], "tokens_in": receipt["tokens_in"],
            "tokens_out": receipt["tokens_out"],
        }
        receipt["steps"].append({"action": "tank_route", "status": "done"})
        return receipt

    if not _control_allows_recall(control):
        raise MemoryControlError(f"memory control {control!r} refuses recall")

    try:
        if receipt["memory_unavailable"]:
            raise MemoryUnavailable("memory control unavailable")
        scoped = await _recall_scoped(
            memory, tenant, task_key, user_id=user, kind=None, limit=32
        )
        receipt["steps"].append(
            {"action": "recall", "count": len(scoped), "memory_ids": [r["id"] for r in scoped]}
        )
    except MemoryUnavailable:
        receipt["memory_unavailable"] = True
        receipt["steps"].append({"action": "memory_unavailable", "phase": "recall"})
        paid = await paid_call(deepcopy(task))
        receipt["paid_calls"] = 1
        receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
        receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
        receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
        receipt["result"] = paid.get("result", paid)
        receipt["would_have_cost"] = {
            "cost_usd": receipt["cost_usd"], "tokens_in": receipt["tokens_in"],
            "tokens_out": receipt["tokens_out"],
        }
        receipt["steps"].append({"action": "tank_route", "status": "done"})
        return receipt

    corrections = corrections_from_memories(scoped)
    if corrections:
        await _invalidate_receipts_for_task(
            memory, tenant_id=tenant, user_id=user, task_key=task_key
        )
        result = apply_corrections(task, corrections)
        receipt["answered_from_memory"] = True
        receipt["result"] = result
        receipt["steps"].append({"action": "apply_correction", "corrections": corrections})
        return receipt

    identical = identical_reuse_from_memories(scoped, task_key=task_key, input_hash=digest) if cache_eligible else None
    if identical is not None:
        result = deepcopy(identical["result"])
        result["answered_from_memory"] = True
        result["reuse"] = "identical_receipt"
        receipt["answered_from_memory"] = True
        receipt["result"] = result
        receipt["would_have_cost"] = identical.get("would_have_cost")
        receipt["steps"].append({
            "action": "exact_repeat_from_memory",
            "memory_id": identical.get("memory_id"),
            "reuse": "identical_receipt",
            "input_hash": digest,
            "would_have_cost": identical.get("would_have_cost"),
        })
        return receipt

    paid = await paid_call(deepcopy(task))
    receipt["paid_calls"] = 1
    receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
    receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
    receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
    receipt["result"] = paid.get("result", paid)
    receipt["would_have_cost"] = {
        "cost_usd": receipt["cost_usd"], "tokens_in": receipt["tokens_in"],
        "tokens_out": receipt["tokens_out"],
    }
    receipt["steps"].append({"action": "tank_route", "status": "done"})

    if not _control_allows_remember(control):
        receipt["steps"].append({"action": "remember_skipped", "reason": f"control:{control}"})
        return receipt

    result_obj = receipt["result"] or {}
    skills_used = list(result_obj.get("skills_used") or [])
    versions_worked = list(result_obj.get("versions_worked") or [])
    failures = list(result_obj.get("failures") or [])
    correction = result_obj.get("correction")
    outcome = (
        result_obj.get("outcome") or result_obj.get("answer")
        or result_obj.get("summary") or f"completed:{task_key}"
    )
    if isinstance(outcome, (dict, list)):
        outcome = json.dumps(outcome, sort_keys=True, default=str)[:500]
    if not skills_used:
        skills_used = [str(result_obj.get("skill") or task.get("skill") or task_key)]
    text = build_memory_text(
        summary=f"Skala task {task_key}", outcome=str(outcome),
        skills_used=skills_used, versions_worked=versions_worked or None,
        failures=failures or None, correction=correction,
        receipt=(
            f"cost={receipt['cost_usd']};tokens_in={receipt['tokens_in']};"
            f"tokens_out={receipt['tokens_out']}"
        ),
    )
    kind = "skala.correction" if correction else "skala.receipt"
    if kind == "skala.correction":
        await _invalidate_receipts_for_task(
            memory, tenant_id=tenant, user_id=user, task_key=task_key
        )
    reusable = deepcopy(result_obj) if cache_eligible and not correction else None
    try:
        saved = await _remember_scoped(
            memory, tenant, text, kind, f"skala:task:{task_key}", user_id=user,
            meta={
                "user_id": user, "task_key": task_key, "input_hash": digest,
                "reusable_result": reusable, "invalidated": False,
                "approved": reusable is not None,
                "would_have_cost": receipt["would_have_cost"],
                "skills_used": skills_used, "versions_worked": versions_worked,
                "failures": failures,
            },
        )
        receipt["steps"].append(
            {"action": "remember", "memory_id": saved["id"], "source": f"skala:task:{task_key}"}
        )
    except MemoryUnavailable:
        receipt["memory_unavailable"] = True
        receipt["steps"].append({"action": "memory_unavailable", "phase": "remember"})
    return receipt
