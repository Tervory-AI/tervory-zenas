"""Gates 7+8 memory bracket for Opli: free steps first, then Tank agent path.

Uses the product's existing memory client (ctx.memory / MemoryClient) only.
Tenant and user scope never cross. Secrets are refused. Corrections and prior
identical receipts recalled from memory are applied before any paid Tank route.
Opli has no product brain; paid multi-step work goes through Tank's existing
agent path (code-turn / opli.code).

Opli remembers per repo: the repo map, conventions, commands that work, past
fixes, and decisions. View/edit/forget and memory-off reuse the existing
chassis MemoryClient → Hermes /memories|/recall|/records. User scoping is chassis+Hermes (unapplied tank-patches/t51-central-user-memory.patch); Opli only passes the signed-in actor user_id.

Exact uncorrected repeats (same tenant + user + task_key + input hash) return
the stored outcome from memory at $0 with zero tokens and no paid call, before
any Tank agent path. Safe Opli repeats are also marked cache-eligible for
Tank's semantic cache (tank-patches/gates-7-8.patch, never applied here).
"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from typing import Any, Awaitable, Callable


FORBIDDEN_SECRET_MARKERS = (
    "api_key", "apikey", "authorization", "bearer ", "password", "secret",
    "token=", "private_key",
)

MEMORY_CONTROLS = frozenset({"off", "view", "edit", "forget"})
CONTROL_KIND = "opli.memory_control"
CONTROL_SOURCE = "opli:memory_control"

# Kinds Opli marks tenant-wide (no per-user wall). All other kinds are personal
# and require the verified signed-in actor user_id on remember.
TENANT_WIDE_KINDS = frozenset({"opli.repo_map", "opli.convention"})

# Live Hermes surface — chassis MemoryClient only (never the founder-only Tank memory corridor).
OPLI_AGENT_KEY_ENV = "TANK_KEY_OPLI"
HERMES_URL_ENV = "HERMES_URL"
WORKSPACE_KEY_ENV = "TANK_API_KEY"
AGENT_HEADER = "X-Tank-Key"
DEFAULT_HERMES_BASE_URL = "http://hermes:8000"
OPLI_AGENT_NAME = "opli"
KIND_RE = __import__("re").compile(r"^[a-z][a-z0-9_.]{0,79}$")


class MemoryUnavailableError(RuntimeError):
    """Hermes outage or unreachable — product must answer without memory."""


def production_memory(*, http=None, api_key=None):
    """Live memory: chassis MemoryClient → Hermes http://hermes:8000.

    Uses Opli's own agent key env TANK_KEY_OPLI (name only). Never TANK_API_KEY.
    """
    try:
        from chassis.memory import MemoryClient
    except ModuleNotFoundError:
        from command_tools._shared_memory import MemoryClient

    key = api_key if api_key is not None else os.environ.get(OPLI_AGENT_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"Opli memory requires {OPLI_AGENT_KEY_ENV} (agent key); "
            f"never use {WORKSPACE_KEY_ENV} for memory writes"
        )
    workspace = os.environ.get(WORKSPACE_KEY_ENV, "").strip()
    if workspace and key == workspace:
        raise RuntimeError(
            f"Opli memory refuses {WORKSPACE_KEY_ENV} as the agent key"
        )
    base = (os.environ.get(HERMES_URL_ENV, "") or DEFAULT_HERMES_BASE_URL).rstrip("/")
    return MemoryClient(base_url=base, api_key=key, agent=OPLI_AGENT_NAME, http=http)


def is_memory_outage(exc: Exception) -> bool:
    text = str(exc).lower()
    if isinstance(exc, MemoryUnavailableError):
        return True
    markers = (
        "hermes answered 503",
        "memory store unreachable",
        "connection refused",
        "connect error",
        "timed out",
        "timeout",
        "unreachable",
    )
    return any(m in text for m in markers)


CACHE_ELIGIBLE_TASK_TYPES = frozenset({
    "tier0.answer", "opli.summary", "opli.status", "repo-map-summary",
    "repo-cost-twice", "repo-retry-lint", "command.code",
})


class MemoryScopeError(ValueError):
    """Tenant/user scope refused."""


class MemoryControlError(ValueError):
    """Memory control refuses the requested action."""


def _text_has_secret(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in FORBIDDEN_SECRET_MARKERS)


def memory_enabled() -> bool:
    """Legacy global env flag. Live path prefers per-tenant+user memory_control."""
    raw = os.environ.get("OPLI_MEMORY_ENABLED", "false").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def brain_enabled() -> bool:
    """Opli has no product brain; Tank agent routing covers multi-step work."""
    return False


def memory_kinds() -> frozenset[str]:
    return frozenset({
        "opli.repo_map", "opli.convention", "opli.command", "opli.fix",
        "opli.decision", "opli.correction", "opli.receipt", "opli.task",
        CONTROL_KIND,
    })


def normalize_memory_control(value: Any) -> str:
    control = str(value or "edit").strip().lower()
    if control not in MEMORY_CONTROLS:
        raise ValueError(f"unsupported memory control: {control}")
    return control


def require_authenticated_scope(*, tenant_id: Any, user_id: Any) -> tuple[str, str]:
    if tenant_id in (None, ""):
        raise MemoryScopeError("authenticated tenant_id required for Opli memory")
    tenant = str(tenant_id).strip()
    if not tenant:
        raise MemoryScopeError("authenticated tenant_id required for Opli memory")
    if user_id in (None, ""):
        raise MemoryScopeError("authenticated user_id required for Opli memory")
    user = str(user_id).strip()
    if not user:
        raise MemoryScopeError("authenticated user_id required for Opli memory")
    if user == tenant:
        raise MemoryScopeError("tenant-as-user identity is refused for Opli memory")
    return tenant, user



def _call_supports(fn: Any, name: str) -> bool:
    import inspect
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


async def _remember(memory: Any, tenant_id: str, text: str, kind: str, source: str, *,
                    user_id: str | None = None, meta: dict | None = None, **kwargs: Any) -> dict[str, Any]:
    """Pass verified actor user_id into the memory client; no product-side filtering."""
    meta_out = dict(meta or {})
    if kind not in TENANT_WIDE_KINDS:
        if not (user_id and str(user_id).strip()):
            raise MemoryScopeError(f"personal memory kind {kind!r} requires signed-in user_id")
        meta_out["user_id"] = str(user_id).strip()
    elif user_id and str(user_id).strip():
        meta_out.setdefault("user_id", str(user_id).strip())
    kw = dict(kwargs)
    kw["meta"] = meta_out
    if _call_supports(memory.remember, "user_id"):
        kw["user_id"] = user_id
    try:
        return await memory.remember(tenant_id, text, kind, source, **kw)
    except RuntimeError as exc:
        _refuse_memory_auth(exc)
        raise


async def _recall(memory: Any, tenant_id: str, query: str, *, user_id: str | None = None,
                  kind: str | None = None, limit: int | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    """Pass verified actor user_id; trust chassis/Hermes to scope. No product filter."""
    kw = dict(kwargs)
    if kind is not None:
        kw["kind"] = kind
    if limit is not None:
        kw["limit"] = limit
    if _call_supports(memory.recall, "user_id"):
        kw["user_id"] = user_id
    try:
        return await memory.recall(tenant_id, query, **kw)
    except RuntimeError as exc:
        _refuse_memory_auth(exc)
        raise


def _refuse_memory_auth(error: Exception) -> None:
    if str(error) in {"hermes answered 401", "hermes answered 403"}:
        raise MemoryScopeError("Memory identity could not be verified") from None


def _control_allows_recall(control: str) -> bool:
    return control in {"view", "edit", "forget"}


def _control_allows_remember(control: str) -> bool:
    return control in {"edit", "forget"}


def _control_allows_forget(control: str) -> bool:
    return control == "forget"


def task_input_hash(task: dict[str, Any] | None) -> str:
    payload = task if isinstance(task, dict) else {}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_cache_eligible(task_key: str, task: dict[str, Any] | None = None) -> bool:
    task = task or {}
    task_type = str(task.get("task_type") or task_key or "")
    return task_type in CACHE_ELIGIBLE_TASK_TYPES or any(
        task_type.startswith(prefix) for prefix in ("repo-", "opli.", "command.code")
    )


def assert_remember_fields_nonempty(fields: dict[str, Any]) -> dict[str, Any]:
    """Every field written by remember must carry a real value — never '' / [] / {} / None."""
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if value == "" or value == [] or value == {}:
            raise ValueError(f"Opli memory refuses empty remembered field: {key}")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"Opli memory refuses empty remembered field: {key}")
        if isinstance(value, (list, dict)) and len(value) == 0:
            raise ValueError(f"Opli memory refuses empty remembered field: {key}")
        cleaned[key] = value
    if not cleaned:
        raise ValueError("Opli memory refuses empty remember payload")
    return cleaned


def build_memory_text(
    *,
    repo_map: list[str] | None = None,
    conventions: list[str] | None = None,
    commands: list[str] | None = None,
    fixes: list[str] | None = None,
    decisions: list[str] | None = None,
    correction: str | None = None,
    receipt: str | None = None,
    summary: str | None = None,
    repo: str | None = None,
    outcome: str | None = None,
) -> str:
    parts: list[str] = []
    if summary:
        parts.append(summary)
    if outcome:
        parts.append("outcome=" + outcome)
    if repo:
        parts.append("repo=" + repo)
    if repo_map:
        parts.append("repo_map=" + ",".join(repo_map))
    if conventions:
        parts.append("conventions=" + ",".join(conventions))
    if commands:
        parts.append("commands=" + ",".join(commands))
    if fixes:
        parts.append("fixes=" + ",".join(fixes))
    if decisions:
        parts.append("decisions=" + ",".join(decisions))
    if correction:
        parts.append("correction=" + correction)
    if receipt:
        parts.append("receipt=" + receipt)
    text = "; ".join(parts)
    if not text.strip():
        raise ValueError("Opli memory refuses empty remember text")
    if _text_has_secret(text):
        raise ValueError("Opli memory refuses secret-bearing text")
    return text


def select_tenant_user_memories(
    rows: list[dict[str, Any]], *, tenant_id: str, user_id: str,
) -> list[dict[str, Any]]:
    tenant, user = str(tenant_id), str(user_id)
    kept: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("tenant_id", "")) != tenant:
            continue
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            continue
        if str(meta.get("user_id", "")) != user:
            continue
        kept.append(row)
    return kept


def corrections_from_memories(rows: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for row in rows:
        text = str(row.get("text", ""))
        marker = "correction="
        if marker in text:
            found.append(text.split(marker, 1)[1].split(";")[0].strip())
            continue
        if row.get("kind") == "opli.correction" and text.strip():
            found.append(text.strip())
    return found


def memory_control_from_memories(rows: list[dict[str, Any]]) -> str | None:
    ordered = sorted(enumerate(rows), key=lambda item: (
        str(item[1].get("updated_at") or item[1].get("created_at") or ""), item[0]), reverse=True)
    for _, row in ordered:
        if row.get("kind") != CONTROL_KIND:
            continue
        meta = row.get("meta") or {}
        if isinstance(meta, dict) and meta.get("control") in MEMORY_CONTROLS:
            return str(meta["control"])
        text = str(row.get("text", ""))
        marker = "memory_control="
        if marker in text:
            value = text.split(marker, 1)[1].split(";")[0].strip().lower()
            if value in MEMORY_CONTROLS:
                return value
    return None


def identical_reuse_from_memories(
    rows: list[dict[str, Any]], *, task_key: str, input_hash: str,
) -> dict[str, Any] | None:
    key, digest = str(task_key), str(input_hash)
    for row in reversed(rows):
        if row.get("kind") != "opli.receipt":
            continue
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
            return {
                "result": deepcopy(reusable),
                "memory_id": row.get("id"),
                "reuse": "identical_receipt",
                "input_hash": digest,
                "meta": dict(meta),
                "would_have_cost_usd": meta.get("would_have_cost_usd"),
                "would_have_tokens_in": meta.get("would_have_tokens_in"),
                "would_have_tokens_out": meta.get("would_have_tokens_out"),
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
    result["outcome"] = "\n".join(applied)
    return result


async def resolve_memory_control(
    memory: Any, *, tenant_id: str, user_id: str, override: str | None = None,
) -> str:
    if not memory_enabled():
        return "off"
    if override is not None:
        return normalize_memory_control(override)
    try:
        recalled = await _recall(
            memory, str(tenant_id), "memory_control",
            user_id=str(user_id), kind=CONTROL_KIND, limit=16,
        )
    except Exception as exc:
        if not is_memory_outage(exc):
            raise
        raise MemoryUnavailableError("Memory is unavailable") from None
    found = memory_control_from_memories(recalled)
    return normalize_memory_control(found or "edit")


async def require_owned_record(
    memory: Any, *, memory_id: str, tenant_id: str, user_id: str,
) -> dict[str, Any]:
    """Refuse unless the target record's tenant AND user match the signed-in caller.

    Identity must already be the authenticated actor scope — never a body/header
    user selector. On mismatch: raise MemoryScopeError and change nothing.
    """
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    if not str(memory_id).strip():
        raise MemoryScopeError("memory_id required for scoped memory control")
    rows: list = []
    # Never read FakeMemory.rows wholesale — that discloses other users' personal rows.
    if hasattr(memory, "records"):
        try:
            rows = list(await memory.records(tenant, user_id=user))
        except TypeError:
            # Legacy records() without user_id kw: do not call unscoped; fall back.
            rows = []
        except Exception as exc:
            _refuse_memory_auth(exc)
            raise
    if not rows and hasattr(memory, "recall"):
        if not _call_supports(memory.recall, "user_id"):
            raise MemoryScopeError("The current Tank memory client is required")
        try:
            rows = list(await memory.recall(tenant, str(memory_id), user_id=user, kind=None, limit=50))
        except Exception as exc:
            _refuse_memory_auth(exc)
            raise
    match = None
    for row in rows or []:
        if str(row.get("id")) != str(memory_id):
            continue
        if str(row.get("tenant_id", "")) != tenant:
            continue
        meta = row.get("meta") or {}
        if not isinstance(meta, dict) or str(meta.get("user_id", "")) != user:
            continue
        match = row
        break
    if match is None:
        raise MemoryScopeError(
            "refused: target memory tenant+user do not match signed-in caller"
        )
    return match



async def set_memory_control(
    memory: Any, *, tenant_id: str, user_id: str, control: str,
    memory_id: str | None = None,
) -> dict[str, Any]:
    """SWITCH-OFF / control: signed-in actor scope only.

    When memory_id is provided (target record), tenant+user on that record must
    match the authenticated actor or the call is refused with no writes.
    """
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    if memory_id is not None:
        await require_owned_record(
            memory, memory_id=memory_id, tenant_id=tenant, user_id=user
        )
    normalized = normalize_memory_control(control)
    text = build_memory_text(
        summary=f"Opli memory_control for user {user}",
        decisions=[f"memory_control={normalized}"],
        outcome=f"control:{normalized}",
        receipt=f"control={normalized}",
    )
    return await _remember(
        memory, tenant, text, CONTROL_KIND, CONTROL_SOURCE,
        user_id=user,
        meta={"user_id": user, "control": normalized, "task_key": "memory_control"},
    )




async def view_memories(
    memory: Any, *, tenant_id: str, user_id: str, task_key: str = "memory_control",
    memory_id: str | None = None,
) -> dict[str, Any]:
    """VIEW control: signed-in actor scope only. Optional memory_id must be owned."""
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    control = await resolve_memory_control(memory, tenant_id=tenant, user_id=user)
    if memory_id is not None:
        # Explicit target: refuse unless tenant+user match signed-in caller.
        owned = await require_owned_record(
            memory, memory_id=memory_id, tenant_id=tenant, user_id=user
        )
        return {"memories": [] if control == "off" else [owned], "memory_control": control}
    if control == "off":
        return {"memories": [], "memory_control": control}
    recalled = await _recall(
        memory, tenant, task_key, user_id=user, kind=None, limit=50,
    )
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
        raise ValueError("Opli memory refuses secret-bearing text")
    row = await require_owned_record(
        memory, memory_id=memory_id, tenant_id=tenant, user_id=user
    )
    return await _remember(
        memory, tenant, text.strip(), str(row.get("kind") or "opli.task"),
        str(row.get("source") or f"opli:edit:{memory_id}"),
        user_id=user,
        meta={**(row.get("meta") or {}), "user_id": user, "edited": True,
              **({"invalidated": True, "reusable_result": None} if row.get("kind") == "opli.receipt" else {})},
        record_id=str(memory_id), replace=True,
    )


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
    await require_owned_record(
        memory, memory_id=memory_id, tenant_id=tenant, user_id=user
    )
    if not _call_supports(memory.forget, "user_id"):
        raise MemoryScopeError("The current Tank memory client is required")
    await memory.forget(memory_id, tenant, user_id=user)


def _invalidate_receipts_for_task(
    rows: list[dict[str, Any]], *, tenant_id: str, user_id: str, task_key: str,
) -> None:
    tenant, user, key = str(tenant_id), str(user_id), str(task_key)
    for row in rows:
        if str(row.get("tenant_id", "")) != tenant or row.get("kind") != "opli.receipt":
            continue
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            continue
        if str(meta.get("user_id", "")) != user or str(meta.get("task_key", "")) != key:
            continue
        meta["invalidated"] = True
        row["meta"] = meta


class FakeMemory:
    """Offline unit-test double only. Live path must use production_memory() → chassis MemoryClient."""

    """In-process fake for offline gates 7+8 tests. No network / Hermes."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.recalls = 0
        self.remembers = 0
        self.forgets = 0

    async def remember(
        self, tenant_id: str, text: str, kind: str, source: str, *,
        user_id: str | None = None, meta: dict | None = None,
        record_id: str | None = None, **_: Any,
    ) -> dict[str, Any]:
        if kind not in memory_kinds():
            raise ValueError("unsupported Opli memory kind")
        if _text_has_secret(text):
            raise ValueError("Opli memory refuses secret-bearing text")
        if not str(text).strip():
            raise ValueError("Opli memory refuses empty remember text")
        meta = dict(meta or {})
        if kind not in TENANT_WIDE_KINDS:
            uid = str(user_id or meta.get("user_id") or "").strip()
            if not uid:
                raise ValueError(f"personal memory kind {kind!r} requires user_id")
            meta["user_id"] = uid
        elif user_id:
            meta.setdefault("user_id", str(user_id).strip())
        tenant = str(tenant_id)
        if record_id is not None:
            for row in self.rows:
                if row["id"] == str(record_id) and row["tenant_id"] == tenant:
                    row["text"] = text
                    row["kind"] = kind
                    row["source"] = source
                    row["meta"] = dict(meta or {})
                    self.remembers += 1
                    return {"id": row["id"], "seen": self.remembers, "updated": True}
            raise ValueError("remember update refused: memory not found for tenant")
        row = {
            "id": f"mem-{len(self.rows) + 1}",
            "tenant_id": tenant,
            "text": text,
            "kind": kind,
            "source": source,
            "meta": dict(meta or {}),
        }
        self.rows.append(row)
        self.remembers += 1
        return {"id": row["id"], "seen": self.remembers}

    async def recall(
        self, tenant_id: str, query: str, *, kind: str | None = None,
        limit: int | None = None, user_id: str | None = None, **_: Any,
    ) -> list[dict[str, Any]]:
        """Simulates proposed Hermes user SQL filter for offline unit tests only."""
        self.recalls += 1
        tenant = str(tenant_id)
        matches = []
        for row in self.rows:
            if row["tenant_id"] != tenant:
                continue
            if kind is not None and row["kind"] != kind:
                continue
            if query and not (
                query.lower() in row["text"].lower()
                or query == (row.get("meta") or {}).get("task_key")
                or query == (row.get("meta") or {}).get("control")
            ):
                continue
            meta = row.get("meta") or {}
            row_uid = str(meta.get("user_id") or "") if isinstance(meta, dict) else ""
            if user_id:
                # Proposed Hermes: personal rows only for that user; tenant-wide kinds for all.
                if row["kind"] in TENANT_WIDE_KINDS:
                    if row_uid and row_uid != str(user_id):
                        continue
                else:
                    if row_uid != str(user_id):
                        continue
            matches.append(row)
        if limit is not None:
            matches = matches[:limit]
        return deepcopy(matches)

    async def forget(self, memory_id: str, tenant_id: str, *, user_id: str | None = None) -> None:
        tenant = str(tenant_id)
        before = len(self.rows)
        self.rows = [row for row in self.rows if not (row["id"] == memory_id and row["tenant_id"] == tenant and (user_id is None or row["meta"].get("user_id") == user_id))]
        if len(self.rows) < before:
            self.forgets += 1

    async def records(self, tenant_id: str, *, user_id: str | None = None, tier: str = "client") -> list[dict[str, Any]]:
        """List must match recall user isolation (t51). Omitted user_id is fail-closed.

        Personal rows only for the verified user. Declared tenant-wide kinds are
        visible to every signed-in user. Without a user_id, only unowned
        tenant-wide kinds are returned — never another user's personal rows.
        """
        del tier
        tenant = str(tenant_id)
        requester = str(user_id).strip() if user_id is not None and str(user_id).strip() else None
        matches = []
        for row in self.rows:
            if row["tenant_id"] != tenant:
                continue
            meta = row.get("meta") or {}
            row_uid = str(meta.get("user_id") or "") if isinstance(meta, dict) else ""
            if requester is None:
                if row["kind"] in TENANT_WIDE_KINDS and not row_uid:
                    matches.append(row)
                continue
            if row["kind"] in TENANT_WIDE_KINDS:
                if row_uid and row_uid != requester:
                    continue
            elif row_uid != requester:
                continue
            matches.append(row)
        return deepcopy(matches)


async def run_with_memory(
    memory: Any, *, tenant_id: str, user_id: str, task_key: str, task: dict[str, Any],
    paid_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    memory_control: str | None = None,
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Product request → free steps → Tank agent path → remember.

    Free steps: code skill, recall, correction, identical reuse. Paid Tank agent
    path runs only when free steps cannot answer.
    """
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    unavailable = False
    try:
        control = await resolve_memory_control(
            memory, tenant_id=tenant, user_id=user, override=memory_control
        )
    except MemoryUnavailableError:
        control, unavailable = "off", True
    digest = task_input_hash(task)
    cache_eligible = is_cache_eligible(task_key, task)
    receipt: dict[str, Any] = {
        "tenant_id": tenant, "user_id": user, "task_key": task_key,
        "input_hash": digest, "cache_eligible": cache_eligible,
        "memory_control": control, "steps": [],
        "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "paid_calls": 0,
        "answered_from_memory": False, "result": None,
        "memory_enabled": control != "off", "brain_enabled": brain_enabled(),
        "memory_unavailable": unavailable,
    }

    # Free step: deterministic code skill (optional) before any paid Tank call.
    if code_free is not None:
        free = await code_free(task)
        receipt["steps"].append({"action": "code_skill", "hit": bool(free)})
        if free is not None:
            receipt["result"] = free.get("result", free)
            receipt["steps"].append({"action": "code_free_done"})
            return receipt

    if control == "off":
        if unavailable:
            receipt["steps"].append({"action": "memory_unavailable"})
        receipt["steps"].append({"action": "memory_off"})
        paid = await paid_call(task)
        receipt["paid_calls"] = int(paid.get("paid_calls", 1))
        receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
        receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
        receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
        receipt["result"] = paid.get("result", paid)
        receipt["steps"].append({"action": "tank_route", "status": "done"})
        return receipt

    if not _control_allows_recall(control):
        raise MemoryControlError(f"memory control {control!r} refuses recall")

    # Free step: memory recall (before brain / Tank agent path).
    try:
        recalled = await _recall(
            memory, tenant, task_key, user_id=user, kind=None, limit=32,
        )
    except Exception as exc:
        if not is_memory_outage(exc):
            raise
        # Outage: answer without memory; never serve a stored repeat.
        receipt["memory_unavailable"] = True
        receipt["steps"].append({"action": "memory_unavailable"})
        paid = await paid_call(task)
        receipt["paid_calls"] = int(paid.get("paid_calls", 1))
        receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
        receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
        receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
        receipt["result"] = paid.get("result", paid)
        receipt["steps"].append({"action": "tank_route", "status": "done", "memory": "unavailable"})
        return receipt
    relevant = [row for row in recalled if (row.get("meta") or {}).get("task_key") == task_key]
    corrections = corrections_from_memories(relevant)
    receipt["steps"].append(
        {"action": "recall", "count": len(recalled), "memory_ids": [r["id"] for r in recalled]}
    )

    if corrections:
        if hasattr(memory, "rows"):
            _invalidate_receipts_for_task(
                memory.rows, tenant_id=tenant, user_id=user, task_key=task_key
            )
        result = apply_corrections(task, corrections)
        receipt["answered_from_memory"] = True
        receipt["result"] = result
        receipt["steps"].append({"action": "apply_correction", "corrections": corrections})
        return receipt

    identical = (
        identical_reuse_from_memories(recalled, task_key=task_key, input_hash=digest)
        if cache_eligible else None
    )
    if identical is not None:
        result = deepcopy(identical["result"])
        result["answered_from_memory"] = True
        result["reuse"] = "identical_receipt"
        receipt["answered_from_memory"] = True
        receipt["result"] = result
        # Exact repeat: $0 paid; receipt carries would-have-cost from the stored run.
        would = identical.get("would_have_cost_usd")
        if would is None:
            would = float((identical.get("meta") or {}).get("would_have_cost_usd") or 0)
        receipt["would_have_cost_usd"] = float(would or 0)
        receipt["would_have_tokens_in"] = int(
            identical.get("would_have_tokens_in")
            or (identical.get("meta") or {}).get("would_have_tokens_in")
            or 0
        )
        receipt["cost_usd"] = 0.0
        receipt["tokens_in"] = 0
        receipt["tokens_out"] = 0
        receipt["paid_calls"] = 0
        receipt["steps"].append({
            "action": "identical_reuse",
            "memory_id": identical.get("memory_id"),
            "reuse": "identical_receipt",
            "input_hash": digest,
            "would_have_cost_usd": receipt["would_have_cost_usd"],
        })
        return receipt

    paid = await paid_call(task)
    receipt["paid_calls"] = int(paid.get("paid_calls", 1))
    receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
    receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
    receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
    receipt["result"] = paid.get("result", paid)
    receipt["steps"].append({"action": "tank_route", "status": "done"})

    if not _control_allows_remember(control):
        receipt["steps"].append({"action": "remember_skipped", "reason": f"control:{control}"})
        return receipt

    result_obj = receipt["result"] or {}
    repo = result_obj.get("repo") or task.get("repo")
    repo_map = list(result_obj.get("repo_map") or [])
    conventions = list(result_obj.get("conventions") or [])
    commands = list(result_obj.get("commands") or [])
    fixes = list(result_obj.get("fixes") or [])
    decisions = list(result_obj.get("decisions") or [])
    correction = result_obj.get("correction")
    outcome = (
        result_obj.get("outcome") or result_obj.get("answer") or result_obj.get("content")
        or result_obj.get("summary")
    )
    if isinstance(outcome, (dict, list)):
        outcome = json.dumps(outcome, sort_keys=True, default=str)[:500]
    if outcome in (None, ""):
        outcome = f"completed:{task_key}"
    fields = assert_remember_fields_nonempty({
        "summary": f"Opli task {task_key}",
        "outcome": str(outcome),
        "repo": repo or None,
        "repo_map": repo_map or None,
        "conventions": conventions or None,
        "commands": commands or None,
        "fixes": fixes or None,
        "decisions": decisions or None,
        "correction": correction,
        "receipt": (
            f"cost={receipt['cost_usd']};tokens_in={receipt['tokens_in']};"
            f"tokens_out={receipt['tokens_out']}"
        ),
    })
    text = build_memory_text(
        summary=fields.get("summary"), outcome=fields.get("outcome"),
        repo=fields.get("repo"), repo_map=fields.get("repo_map"),
        conventions=fields.get("conventions"), commands=fields.get("commands"),
        fixes=fields.get("fixes"), decisions=fields.get("decisions"),
        correction=fields.get("correction"), receipt=fields.get("receipt"),
    )
    kind = "opli.correction" if correction else "opli.receipt"
    if kind == "opli.correction" and hasattr(memory, "rows"):
        _invalidate_receipts_for_task(
            memory.rows, tenant_id=tenant, user_id=user, task_key=task_key
        )
    raw = result_obj.get("raw") or {}
    reusable = None if correction or not cache_eligible or raw.get("requests") or raw.get("cards") else deepcopy(result_obj)
    try:
        saved = await _remember(
            memory, tenant, text, kind, f"opli:task:{task_key}",
            user_id=user,
            meta={
                "user_id": user, "task_key": task_key, "repo": fields.get("repo"),
                "input_hash": digest, "reusable_result": reusable, "invalidated": False,
                "would_have_cost_usd": receipt["cost_usd"],
                "would_have_tokens_in": receipt["tokens_in"],
                "would_have_tokens_out": receipt["tokens_out"],
            },
        )
    except Exception as exc:
        if not is_memory_outage(exc):
            raise
        receipt["memory_unavailable"] = True
        receipt["steps"].append({"action": "remember_unavailable"})
        return receipt
    receipt["steps"].append(
        {"action": "remember", "memory_id": saved["id"], "source": f"opli:task:{task_key}"}
    )
    return receipt


async def product_to_tank_agent_memory(
    memory: Any, *, tenant_id: str, user_id: str, task_key: str, task: dict[str, Any],
    tank_agent_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    memory_control: str | None = None,
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Product corridor into Tank's existing agent path with free steps first."""
    return await run_with_memory(
        memory, tenant_id=tenant_id, user_id=user_id, task_key=task_key, task=task,
        paid_call=tank_agent_call, memory_control=memory_control, code_free=code_free,
    )
