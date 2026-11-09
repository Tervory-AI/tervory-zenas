"""Gates 7+8 memory bracket for Workspace: free steps before Tank agent path.

Uses the product's existing memory client (ctx.memory / MemoryClient) only —
Hermes via chassis.MemoryClient (agent key TANK_KEY_WORKSPACE) and
workspace/app/api/memory inspection. No second memory system. Never Tank /v1/memory. Workspace has no product brain; paid multi-step work goes through
Tank's existing agent path after free steps.

Free-step order (Base pattern): code skill → memory recall → correction →
identical reuse (same tenant+user+task_key+input hash) → cache-eligible mark →
only then the paid Tank agent route → remember + receipt.

Per-tenant+user memory_control: off | view | edit | forget (stored as
workspace.preference via the memory corridor). A global env flag alone is not
enough. Empty tenant/user identities are refused. Remembered fields are never
hardcoded empty.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Awaitable, Callable


FORBIDDEN_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer ",
    "password",
    "secret",
    "token=",
    "private_key",
)

MEMORY_CONTROLS = frozenset({"off", "view", "edit", "forget"})
_FINGERPRINT_KEYS = ("title", "request", "task_type", "messages")
CACHE_ELIGIBLE_TASK_TYPES = frozenset(
    {
        "tier0.answer",
        "workspace.summary",
        "workspace.status",
        "workspace.chat",
        "project-board-summary",
        "project-cost-twice",
        "project-retry-tone",
    }
)
FIXED_FAKE_PER_CALL = {
    "cost_usd": 0.05,
    "tokens_in": 10,
    "tokens_out": 3,
}
CONTROL_KIND = "workspace.preference"
# Explicit tenant-wide kinds only. Personal kinds always require user_id (t51).
try:
    from command_tools._workspace.memory_client import TENANT_WIDE_KINDS as TENANT_WIDE_KINDS
except ImportError:  # pragma: no cover - offline FakeMemory path
    TENANT_WIDE_KINDS = frozenset({"workspace.project"})
CONTROL_SOURCE = "workspace:memory_control"
ACTIVE_CONTROL: ContextVar[str | None] = ContextVar('workspace_memory_control', default=None)


class MemoryControlError(PermissionError):
    """Raised when a memory control refuses an operation."""


class MemoryScopeError(ValueError):
    """Raised when authenticated tenant/user scope is missing for memory use."""


class MemoryAuthenticationError(MemoryScopeError):
    def __init__(self, status: int):
        super().__init__('Memory authentication failed.')
        self.status_code = status


def refuse_memory_auth_failure(exc: BaseException, paid_receipt: dict | None = None) -> None:
    # The shipped chassis returns this fixed status-only error. Never expose
    # transport bodies or treat denied credentials as availability failures.
    if str(exc) in {'hermes answered 401', 'hermes answered 403'}:
        error = MemoryAuthenticationError(int(str(exc).rsplit(' ', 1)[1]))
        error.paid_receipt = paid_receipt
        raise error from exc


class MemoryOutageError(RuntimeError):
    """Memory client/service unreachable, timing out, or bracket modules missing.

    Callers may answer through the Tank agent path without memory, mark the
    receipt "memory unavailable", and must never serve identical_reuse or remember.
    If paid_receipt is set, the paid Tank call already happened — callers must
    return that receipt and never retry the paid step.
    """

    def __init__(self, message: str, *, paid_receipt: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.paid_receipt = paid_receipt


_SERVICE_UNAVAILABLE_MARKERS = (
    "hermes unreachable",
    "hermes answered 503",
    "hermes answered 502",
    "connection refused",
    "econnrefused",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
)


def is_memory_service_outage(exc: BaseException) -> bool:
    """Only genuine availability failures map to outage — not TypeError/KeyError/bugs."""
    if isinstance(exc, MemoryOutageError):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError, ConnectionResetError)):
        return True
    if isinstance(exc, OSError):
        # Connection-class OSErrors only (refused / reset / unreachable / timed out).
        msg = str(exc).lower()
        if any(m in msg for m in ("refused", "reset", "unreachable", "timed out", "timeout", "network")):
            return True
        return False
    # httpx / httpcore availability errors when those packages are present.
    type_name = type(exc).__module__ + "." + type(exc).__name__
    if type_name.startswith(("httpx.", "httpcore.")) and any(
        part in type_name.lower() for part in ("timeout", "network", "connect", "remoteprotocol")
    ):
        return True
    msg = str(exc).lower()
    if any(marker in msg for marker in _SERVICE_UNAVAILABLE_MARKERS):
        # Never treat programming bugs as outages even if message overlaps.
        if isinstance(exc, (TypeError, KeyError, AttributeError, AssertionError, NameError, SyntaxError)):
            return False
        if isinstance(exc, ValueError) and "hermes" not in msg and "unavailable" not in msg:
            return False
        return "hermes" in msg or "unavailable" in msg or "refused" in msg or "timeout" in msg
    return False


def _raise_if_memory_outage(exc: BaseException, *, context: str) -> None:
    refuse_memory_auth_failure(exc)
    if is_memory_service_outage(exc):
        if isinstance(exc, MemoryOutageError):
            raise exc
        raise MemoryOutageError(f"{context}: {exc}") from exc


def _text_has_secret(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in FORBIDDEN_SECRET_MARKERS)


def memory_kinds() -> frozenset[str]:
    return frozenset(
        {
            "workspace.conversation",
            "workspace.preference",
            "workspace.project",
            "workspace.file",
            "workspace.correction",
            "workspace.receipt",
            "workspace.task",
            "workspace.outcome",
            "workspace.summary",
        }
    )


def normalize_memory_control(value: Any) -> str:
    control = str(value or "edit").strip().lower()
    if control not in MEMORY_CONTROLS:
        raise ValueError(f"unsupported memory control: {control}")
    return control


def require_authenticated_scope(
    *,
    tenant_id: Any,
    user_id: Any,
) -> tuple[str, str]:
    """Refuse empty tenant or user. Also refuse synthetic tenant-as-user."""
    if tenant_id in (None, ""):
        raise MemoryScopeError("authenticated tenant_id required for Workspace memory")
    tenant = str(tenant_id).strip()
    if not tenant:
        raise MemoryScopeError("authenticated tenant_id required for Workspace memory")
    if user_id in (None, ""):
        raise MemoryScopeError("authenticated user_id required for Workspace memory")
    user = str(user_id).strip()
    if not user:
        raise MemoryScopeError("authenticated user_id required for Workspace memory")
    if user == tenant:
        raise MemoryScopeError("tenant-as-user identity is refused for Workspace memory")
    return tenant, user



def refuse_client_impersonation(
    *,
    session_tenant_id: Any,
    session_user_id: Any,
    claimed_tenant_id: Any = None,
    claimed_user_id: Any = None,
) -> tuple[str, str]:
    """Refuse when a client-supplied tenant/user disagrees with the signed-in session.

    The actor comes only from the session (currentTenant / authorized actor). Any
    tenant_id, user_id, header or body claim that disagrees is a scope refusal —
    never a memory outage.
    """
    tenant, user = require_authenticated_scope(
        tenant_id=session_tenant_id, user_id=session_user_id
    )
    claims: list[tuple[str, Any, str]] = []
    if claimed_tenant_id not in (None, ""):
        claims.append(("tenant_id", claimed_tenant_id, tenant))
    if claimed_user_id not in (None, ""):
        claims.append(("user_id", claimed_user_id, user))
    for label, claimed, expected in claims:
        if str(claimed).strip() != expected:
            raise MemoryScopeError(
                f"impersonation refused: {label} does not match signed-in caller"
            )
    return tenant, user


def require_record_owner(
    row: dict[str, Any] | None,
    *,
    tenant_id: str,
    user_id: str,
    action: str,
) -> dict[str, Any]:
    """Target record must have tenant AND user equal to the signed-in caller."""
    if not row:
        raise MemoryScopeError(f"{action} refused: memory not in caller tenant+user scope")
    if str(row.get("tenant_id", "")) != str(tenant_id):
        raise MemoryScopeError(f"{action} refused: memory tenant does not match caller")
    meta = row.get("meta") or {}
    if not isinstance(meta, dict) or str(meta.get("user_id", "")) != str(user_id):
        raise MemoryScopeError(f"{action} refused: memory user does not match caller")
    return row


async def load_owned_memory(
    memory: Any,
    *,
    memory_id: str,
    tenant_id: str,
    user_id: str,
    action: str,
) -> dict[str, Any]:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    rows: list[dict[str, Any]] = []
    if hasattr(memory, "records"):
        try:
            try:
                rows = await memory.records(tenant, user_id=user)
            except TypeError:
                rows = await memory.records(tenant)
        except Exception:
            # /records may be unavailable on legacy Hermes stores; ownership
            # still resolves via recall below. Do not map listing gaps to outage.
            rows = []
    if not rows and hasattr(memory, "recall"):
        try:
            # Hermes RecallIn.query min_length=1; "." is a non-semantic listing probe.
            rows = await memory.recall(tenant, ".", user_id=user, kind=None, limit=50)
        except MemoryOutageError:
            raise
        except Exception as exc:
            _raise_if_memory_outage(exc, context="memory recall unavailable")
            raise
    # Prefer exact id match across tenant listing, then enforce owner equality.
    match = next((row for row in rows if str(row.get("id")) == str(memory_id)), None)
    return require_record_owner(match, tenant_id=tenant, user_id=user, action=action)


async def apply_memory_view(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    task_key: str = "memory_control",
    memory_id: str | None = None,
    memory_control: str = "view",
) -> dict[str, Any]:
    """View memories for the signed-in caller only. Foreign memory_id is refused."""
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    control = normalize_memory_control(memory_control)
    live = await resolve_memory_control(memory, tenant_id=tenant, user_id=user)
    if control == "off" or live == "off":
        return {
            "tenant_id": tenant,
            "user_id": user,
            "memory_control": "off",
            "memories": [],
            "note": "memory_control=off",
        }
    if memory_id:
        owned = await load_owned_memory(
            memory, memory_id=memory_id, tenant_id=tenant, user_id=user, action="view"
        )
        return {
            "tenant_id": tenant,
            "user_id": user,
            "task_key": task_key,
            "memory_control": live,
            "memories": [owned],
        }
    try:
        recalled = await memory.recall(tenant, task_key, user_id=user, kind=None, limit=32)
    except MemoryOutageError:
        raise
    except Exception as exc:
        _raise_if_memory_outage(exc, context="memory recall unavailable")
        raise
    # Hermes/chassis scopes by user_id centrally (t51). No product-side user filter.
    scoped = list(recalled)
    return {
        "tenant_id": tenant,
        "user_id": user,
        "task_key": task_key,
        "memory_control": live,
        "memories": scoped,
    }


def resolve_authenticated_user_id(cfg: dict[str, Any] | None, tenant_id: Any) -> str | None:
    cfg = cfg or {}
    raw = cfg.get("user_id")
    if raw in (None, ""):
        raw = cfg.get("created_by")
    if raw in (None, ""):
        raw = cfg.get("requested_by")
    if raw in (None, ""):
        return None
    user = str(raw).strip()
    if not user or user == str(tenant_id):
        return None
    return user


def memory_enabled_env() -> bool:
    """Legacy global env flag. Live path prefers per-tenant+user memory_control."""
    raw = os.environ.get("WORKSPACE_MEMORY_ENABLED", "false").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


def _control_allows_recall(control: str) -> bool:
    return control in {"view", "edit", "forget"}


def _control_allows_remember(control: str) -> bool:
    return control in {"edit", "forget"}


def _control_allows_forget(control: str) -> bool:
    return control == "forget"


def build_memory_text(
    *,
    conversations: list[str] | None = None,
    preferences: list[str] | None = None,
    projects: list[str] | None = None,
    files: list[str] | None = None,
    correction: str | None = None,
    receipt: str | None = None,
    summary: str | None = None,
    outcome: str | None = None,
) -> str:
    parts: list[str] = []
    if summary:
        parts.append(summary)
    if outcome:
        parts.append("outcome=" + outcome)
    if conversations:
        parts.append("conversation=" + ",".join(conversations))
    if preferences:
        parts.append("preferences=" + ",".join(preferences))
    if projects:
        parts.append("projects=" + ",".join(projects))
    if files:
        parts.append("files=" + ",".join(files))
    if correction:
        parts.append("correction=" + correction)
    if receipt:
        parts.append("receipt=" + receipt)
    text = "; ".join(parts)
    if not text.strip():
        raise ValueError("Workspace memory refuses empty remember text")
    if _text_has_secret(text):
        raise ValueError("Workspace memory refuses secret-bearing text")
    return text


def assert_remember_fields_nonempty(fields: dict[str, Any]) -> dict[str, Any]:
    """Every field written by remember must carry a real value — never '' / [] / {} / None."""
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if value == "" or value == [] or value == {}:
            raise ValueError(f"Workspace memory refuses empty remembered field: {key}")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"Workspace memory refuses empty remembered field: {key}")
        if isinstance(value, (list, dict)) and len(value) == 0:
            raise ValueError(f"Workspace memory refuses empty remembered field: {key}")
        cleaned[key] = value
    if not cleaned:
        raise ValueError("Workspace memory refuses empty remember payload")
    return cleaned


def select_tenant_user_memories(
    rows: list[dict[str, Any]],
    *,
    tenant_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    tenant = str(tenant_id)
    user = str(user_id)
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


def task_input_fingerprint(task: dict[str, Any]) -> str:
    payload = {key: task.get(key) for key in _FINGERPRINT_KEYS}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_cache_eligible(task_key: str, task: dict[str, Any] | None = None) -> bool:
    task = task or {}
    task_type = str(task.get("task_type") or task_key or "")
    if task_type in CACHE_ELIGIBLE_TASK_TYPES:
        return True
    lowered = task_type.lower()
    if any(token in lowered for token in ("draft", "brief", "compose", "write")):
        return False
    return True


def corrections_from_memories(rows: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for row in rows:
        text = str(row.get("text", ""))
        meta = row.get('meta') or {}
        if meta.get('edited') and meta.get('correction'):
            found.append(str(meta['correction']))
            continue
        marker = "correction="
        if marker in text:
            found.append(text.split(marker, 1)[1].split(";")[0].strip())
            continue
        if row.get("kind") == "workspace.correction" and text.strip():
            found.append(text.strip())
    return found


def identical_reuse_from_memories(
    rows: list[dict[str, Any]],
    *,
    task_key: str,
    input_fingerprint: str,
) -> dict[str, Any] | None:
    key = str(task_key)
    fingerprint = str(input_fingerprint)
    for row in reversed(rows):
        if row.get("kind") not in {"workspace.receipt", "workspace.task", "workspace.outcome"}:
            continue
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            continue
        if str(meta.get("task_key", "")) != key:
            continue
        if str(meta.get("input_fingerprint", "")) != fingerprint:
            continue
        if meta.get("invalidated"):
            continue
        reusable = meta.get("reusable_result")
        if isinstance(reusable, dict) and reusable:
            return {
                "result": deepcopy(reusable),
                "memory_id": row.get("id"),
                "reuse": "identical_receipt",
                "would_have": {
                    "cost_usd": float(meta.get("cost_usd") or 0.0),
                    "tokens_in": int(meta.get("tokens_in") or 0),
                    "tokens_out": int(meta.get("tokens_out") or 0),
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
    # The latest explicit owner edit is the corrected answer, not metadata only.
    result["content"] = applied[-1]
    result["text"] = applied[-1]
    result["outcome"] = applied[-1]
    result["answered_from_memory"] = True
    return result


def memory_control_from_memories(rows: list[dict[str, Any]]) -> str | None:
    """Most recent per-tenant+user memory_control preference, if any."""
    # Hermes lists newest first; legacy in-process clients append oldest first.
    # Prefer the persisted creation time while keeping insertion order for rows
    # without timestamps. Authorization already restricted the candidate rows.
    ordered = sorted(rows, key=lambda row: str(row.get("created_at") or ""))
    for row in reversed(ordered):
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


async def resolve_memory_control(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    override: str | None = None,
) -> str:
    """Live per-tenant+user control via the memory corridor. Override wins when given."""
    if not memory_enabled_env():
        return "off"
    if override is not None:
        return normalize_memory_control(override)
    try:
        recalled = await memory.records(str(tenant_id), user_id=str(user_id))
    except MemoryOutageError:
        raise
    except Exception as exc:
        _raise_if_memory_outage(exc, context="memory control recall unavailable")
        raise
    # Central user scope already applied by recall(user_id=...).
    found = memory_control_from_memories(list(recalled))
    return normalize_memory_control(found or "edit")


async def set_memory_control(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    control: str,
    claimed_tenant_id: Any = None,
    claimed_user_id: Any = None,
) -> dict[str, Any]:
    """Persist per-tenant+user memory_control (view/edit/forget/off) via memory corridor.

    Only the signed-in caller scope is switched. Client-claimed tenant/user that
    disagree with the session are refused (nothing changes).
    """
    tenant, user = refuse_client_impersonation(
        session_tenant_id=tenant_id,
        session_user_id=user_id,
        claimed_tenant_id=claimed_tenant_id,
        claimed_user_id=claimed_user_id,
    )
    normalized = normalize_memory_control(control)
    text = build_memory_text(
        summary=f"Workspace memory_control for user {user}",
        preferences=[f"memory_control={normalized}"],
    )
    return await memory.remember(
        tenant,
        text,
        CONTROL_KIND,
        CONTROL_SOURCE,
        user_id=user,
        meta={"user_id": user, "control": normalized, "task_key": "memory_control"},
    )


class FakeMemory:
    """In-process fake for offline gates 7+8 tests. No network / Hermes."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.recalls = 0
        self.remembers = 0
        self.forgets = 0

    async def remember(
        self,
        tenant_id: str,
        text: str,
        kind: str,
        source: str,
        *,
        user_id: str | None = None,
        meta: dict | None = None,
        record_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if kind not in memory_kinds():
            raise ValueError("unsupported Workspace memory kind")
        if _text_has_secret(text):
            raise ValueError("Workspace memory refuses secret-bearing text")
        if not str(text).strip():
            raise ValueError("Workspace memory refuses empty remember text")
        meta = dict(meta or {})
        if kind in TENANT_WIDE_KINDS:
            meta.pop("user_id", None)
        else:
            uid = user_id or meta.get("user_id")
            if not uid or not str(uid).strip():
                raise ValueError("user_id required for personal memory kind")
            meta["user_id"] = str(uid).strip()
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
        self,
        tenant_id: str,
        query: str,
        *,
        user_id: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id required for recall")
        uid = str(user_id).strip()
        self.recalls += 1
        tenant = str(tenant_id)
        matches: list[dict[str, Any]] = []
        for row in self.rows:
            if row["tenant_id"] != tenant:
                continue
            if kind is not None and row["kind"] != kind:
                continue
            meta = row.get("meta") or {}
            owner = meta.get("user_id") if isinstance(meta, dict) else None
            if owner in (None, ""):
                if row.get("kind") not in TENANT_WIDE_KINDS:
                    continue
            elif str(owner) != uid:
                continue
            if (
                not query
                or query.lower() in row["text"].lower()
                or query == (row.get("meta") or {}).get("task_key")
                or query == (row.get("meta") or {}).get("control")
            ):
                matches.append(row)
        if limit is not None:
            matches = matches[:limit]
        return deepcopy(matches)

    async def forget(
        self, memory_id: str, tenant_id: str, *, user_id: str | None = None
    ) -> None:
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id required for forget")
        tenant = str(tenant_id)
        before = len(self.rows)
        self.rows = [
            row
            for row in self.rows
            if not (row["id"] == memory_id and row["tenant_id"] == tenant)
        ]
        if len(self.rows) < before:
            self.forgets += 1

    async def records(
        self, tenant_id: str, *, user_id: str | None = None, tier: str = "client"
    ) -> list[dict[str, Any]]:
        del tier
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id required for records listing")
        uid = str(user_id).strip()
        out: list[dict[str, Any]] = []
        for row in self.rows:
            if row["tenant_id"] != str(tenant_id):
                continue
            meta = row.get("meta") or {}
            owner = meta.get("user_id") if isinstance(meta, dict) else None
            if owner in (None, ""):
                if row.get("kind") in TENANT_WIDE_KINDS:
                    out.append(row)
            elif str(owner) == uid:
                out.append(row)
        return deepcopy(out)





async def apply_memory_edit(
    memory: Any,
    *,
    memory_id: str,
    tenant_id: str,
    user_id: str,
    text: str,
    memory_control: str | None = None,
) -> dict[str, Any]:
    """Change the content of one memory entry (edit means content, not only a mode)."""
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    control = await resolve_memory_control(
        memory, tenant_id=tenant, user_id=user, override=memory_control
    )
    if control not in {"edit", "forget"}:
        raise MemoryControlError(f"memory control {control!r} refuses content edit")
    if not str(text).strip():
        raise ValueError("memory edit requires non-empty text")
    if _text_has_secret(text):
        raise ValueError("Workspace memory refuses secret-bearing text")
    row = await load_owned_memory(
        memory, memory_id=memory_id, tenant_id=tenant, user_id=user, action="edit"
    )
    meta = dict(row.get("meta") or {})
    meta["user_id"] = user
    meta["edited"] = True
    # Content edits must not keep serving a stale reusable_result.
    if row.get("kind") in {"workspace.receipt", "workspace.task", "workspace.outcome"}:
        meta["invalidated"] = True
        meta["reusable_result"] = None
        meta['correction'] = str(text).strip()
    saved = await memory.remember(
        tenant,
        str(text).strip(),
        str(row.get("kind") or "workspace.outcome"),
        str(row.get("source") or f"workspace:edit:{memory_id}"),
        user_id=user,
        meta=meta,
        record_id=str(memory_id), replace=True,
    )
    return {"id": str(memory_id), "updated": True, "saved": saved, "memory_control": control}


async def apply_memory_forget(
    memory: Any,
    *,
    memory_id: str,
    tenant_id: str,
    user_id: str,
    memory_control: str = "forget",
) -> None:
    control = normalize_memory_control(memory_control)
    if not _control_allows_forget(control):
        raise MemoryControlError(f"memory control {control!r} refuses forget")
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    await load_owned_memory(
        memory, memory_id=memory_id, tenant_id=tenant, user_id=user, action="forget"
    )
    await memory.forget(memory_id, tenant, user_id=user)


async def persist_invalidate_identical_receipts(
    memory: Any,
    rows: list[dict[str, Any]],
    *,
    tenant_id: str,
    task_key: str,
    user_id: str | None = None,
) -> int:
    """Durably clear reusable_result through MemoryClient.remember(record_id=...).

    Recall returns deep copies; mutating those copies alone does not persist.
    """
    key = str(task_key)
    tenant = str(tenant_id)
    updated = 0
    for row in rows:
        meta = row.get("meta")
        if not isinstance(meta, dict):
            continue
        if str(meta.get("task_key", "")) != key:
            continue
        if row.get("kind") not in {"workspace.receipt", "workspace.task", "workspace.outcome"}:
            continue
        if meta.get("invalidated") and meta.get("reusable_result") in (None, {}):
            continue
        memory_id = row.get("id")
        if not memory_id:
            continue
        new_meta = {**meta, "invalidated": True, "reusable_result": None}
        text = str(row.get("text") or "").strip() or f"invalidated:{key}"
        owner = user_id or new_meta.get("user_id")
        await memory.remember(
            tenant,
            text,
            str(row.get("kind")),
            str(row.get("source") or f"workspace:task:{key}"),
            user_id=owner,
            meta=new_meta,
            record_id=str(memory_id), replace=True,
        )
        meta["invalidated"] = True
        meta["reusable_result"] = None
        updated += 1
    return updated


def _invalidate_identical_receipts(rows: list[dict[str, Any]], *, task_key: str) -> None:
    """Local copy marker only — prefer persist_invalidate_identical_receipts."""
    key = str(task_key)
    for row in rows:
        meta = row.get("meta")
        if not isinstance(meta, dict):
            continue
        if str(meta.get("task_key", "")) != key:
            continue
        if row.get("kind") in {"workspace.receipt", "workspace.task", "workspace.outcome"}:
            meta["invalidated"] = True
            meta["reusable_result"] = None


async def run_with_memory(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    task_key: str,
    task: dict[str, Any],
    paid_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    memory_control: str | None = None,
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Product request → free steps → Tank agent path → remember.

    Free steps: code skill, recall, correction, identical reuse. Paid Tank agent
    path runs only when free steps cannot answer. memory_control is per
    tenant+user (override or live preference); empty tenant/user refused.
    """
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    control = await resolve_memory_control(
        memory, tenant_id=tenant, user_id=user, override=memory_control
    )
    fingerprint = task_input_fingerprint(task)
    cache_eligible = is_cache_eligible(task_key, task)
    receipt: dict[str, Any] = {
        "tenant_id": tenant,
        "user_id": user,
        "task_key": task_key,
        "input_fingerprint": fingerprint,
        "cache_eligible": cache_eligible,
        "memory_control": control,
        "steps": [],
        "cost_usd": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "paid_calls": 0,
        "answered_from_memory": False,
        "result": None,
        "memory_enabled": control != "off",
    }

    # Free step: deterministic code skill (optional).
    original_paid = paid_call
    async def scoped_paid(task):
        token = ACTIVE_CONTROL.set(control)
        try:
            return await original_paid(task)
        finally:
            ACTIVE_CONTROL.reset(token)
    paid_call = scoped_paid

    if code_free is not None:
        free = await code_free(task)
        receipt["steps"].append({"action": "code_skill", "hit": bool(free)})
        if free is not None:
            receipt["result"] = free.get("result", free)
            receipt["answered_from_memory"] = False
            receipt["steps"].append({"action": "code_free_done"})
            return receipt

    if control == "off":
        receipt["steps"].append({"action": "memory_off"})
        paid = await paid_call(task)
        if paid.get('pending'):
            receipt['result'] = paid.get('result', paid)
            receipt['pending'] = True
            receipt['steps'].append({'action': 'task_queued'})
            return receipt
        receipt["paid_calls"] = 1
        receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
        receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
        receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
        receipt["result"] = paid.get("result", paid)
        receipt["steps"].append({"action": "tank_agent", "status": "done"})
        return receipt

    if not _control_allows_recall(control):
        raise MemoryControlError(f"memory control {control!r} refuses recall")

    try:
        recalled = await memory.recall(tenant, task_key, user_id=user, kind=None, limit=32)
    except MemoryOutageError:
        raise
    except Exception as exc:
        _raise_if_memory_outage(exc, context="memory recall unavailable")
        raise
    # Hermes/chassis scopes by user_id centrally (t51). No product-side user filter.
    scoped = list(recalled)
    corrections = corrections_from_memories(scoped)
    receipt["steps"].append(
        {"action": "recall", "count": len(scoped), "memory_ids": [r["id"] for r in scoped]}
    )

    if corrections:
        try:
            await persist_invalidate_identical_receipts(
                memory, scoped, tenant_id=tenant, task_key=task_key, user_id=user
            )
        except MemoryOutageError:
            raise
        except Exception as exc:
            _raise_if_memory_outage(exc, context="memory invalidation unavailable")
            raise
        result = apply_corrections(task, corrections)
        receipt["answered_from_memory"] = True
        receipt["result"] = result
        receipt["steps"].append({"action": "apply_correction", "corrections": corrections})
        return receipt

    identical = (
        identical_reuse_from_memories(
            scoped, task_key=task_key, input_fingerprint=fingerprint
        )
        if cache_eligible
        else None
    )
    if identical is not None:
        result = deepcopy(identical["result"])
        result["answered_from_memory"] = True
        result["reuse"] = "identical_receipt"
        receipt["answered_from_memory"] = True
        receipt["result"] = result
        # Would-have cost/tokens from the prior paid receipt (never invent a new charge).
        prior = identical.get("would_have") or {}
        receipt["would_have_cost_usd"] = float(prior.get("cost_usd") or 0.0)
        receipt["would_have_tokens_in"] = int(prior.get("tokens_in") or 0)
        receipt["would_have_tokens_out"] = int(prior.get("tokens_out") or 0)
        receipt["steps"].append(
            {
                "action": "identical_reuse",
                "memory_id": identical.get("memory_id"),
                "reuse": "identical_receipt",
                "would_have_cost_usd": receipt["would_have_cost_usd"],
            }
        )
        return receipt

    if cache_eligible:
        receipt["steps"].append({"action": "cache_eligible", "task_type": task.get("task_type") or task_key})

    paid = await paid_call(task)
    if paid.get('pending'):
        receipt['result'] = paid.get('result', paid)
        receipt['pending'] = True
        receipt['steps'].append({'action': 'task_queued'})
        return receipt
    receipt["paid_calls"] = 1
    receipt["cost_usd"] = float(paid.get("cost_usd") or 0)
    receipt["tokens_in"] = int(paid.get("tokens_in") or 0)
    receipt["tokens_out"] = int(paid.get("tokens_out") or 0)
    receipt["result"] = paid.get("result", paid)
    receipt["steps"].append({"action": "tank_agent", "status": "done"})

    if not _control_allows_remember(control):
        receipt["steps"].append({"action": "remember_skipped", "reason": f"control:{control}"})
        return receipt

    result_obj = receipt["result"] or {}
    conversations = list(result_obj.get("conversations") or result_obj.get("conversation") or [])
    preferences = list(result_obj.get("preferences") or [])
    projects = list(result_obj.get("projects") or [])
    files = list(result_obj.get("files") or [])
    correction = result_obj.get("correction")
    outcome = result_obj.get("outcome") or result_obj.get("answer") or result_obj.get("content")
    if isinstance(outcome, (dict, list)):
        outcome = json.dumps(outcome, sort_keys=True, default=str)[:500]
    fields = assert_remember_fields_nonempty(
        {
            "summary": f"Workspace task {task_key}",
            "outcome": str(outcome) if outcome not in (None, "") else f"completed:{task_key}",
            "conversations": conversations or None,
            "preferences": preferences or None,
            "projects": projects or None,
            "files": files or None,
            "correction": correction,
            "receipt": (
                f"cost={receipt['cost_usd']};tokens_in={receipt['tokens_in']};"
                f"tokens_out={receipt['tokens_out']}"
            ),
        }
    )
    text = build_memory_text(
        summary=fields.get("summary"),
        outcome=fields.get("outcome"),
        conversations=fields.get("conversations"),
        preferences=fields.get("preferences"),
        projects=fields.get("projects"),
        files=fields.get("files"),
        correction=fields.get("correction"),
        receipt=fields.get("receipt"),
    )
    kind = "workspace.correction" if correction else "workspace.receipt"
    reusable = None if correction else deepcopy(result_obj)
    if correction:
        try:
            await persist_invalidate_identical_receipts(
                memory, scoped, tenant_id=tenant, task_key=task_key, user_id=user
            )
        except Exception as exc:
            # Paid call already completed — never raise outage (handler would re-pay).
            refuse_memory_auth_failure(exc, receipt)
            if is_memory_service_outage(exc):
                reason = str(exc)
                receipt["memory_unavailable"] = True
                receipt["memory_status"] = "memory unavailable"
                receipt["answered_from_memory"] = False
                receipt["steps"].append(
                    {"action": "memory_unavailable", "reason": reason, "after": "paid"}
                )
                return receipt
            raise
    try:
        saved = await memory.remember(
            tenant,
            text,
            kind,
            f"workspace:task:{task_key}",
            user_id=user,
            meta={
                "user_id": user,
                "task_key": task_key,
                "input_fingerprint": fingerprint,
                "cache_eligible": cache_eligible,
                "reusable_result": reusable,
                "invalidated": False,
                "cost_usd": float(receipt.get("cost_usd") or 0.0),
                "tokens_in": int(receipt.get("tokens_in") or 0),
                "tokens_out": int(receipt.get("tokens_out") or 0),
            },
        )
    except Exception as exc:
        # Paid call already happened — never raise outage (handler would re-pay).
        refuse_memory_auth_failure(exc, receipt)
        if is_memory_service_outage(exc):
            reason = str(exc)
            receipt["memory_unavailable"] = True
            receipt["memory_status"] = "memory unavailable"
            receipt["answered_from_memory"] = False
            receipt["steps"].append(
                {"action": "memory_unavailable", "reason": reason, "after": "paid"}
            )
            return receipt
        raise
    receipt["steps"].append(
        {"action": "remember", "memory_id": saved["id"], "source": f"workspace:task:{task_key}"}
    )
    return receipt


async def product_to_tank_agent_memory(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    task_key: str,
    task: dict[str, Any],
    tank_agent_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    memory_control: str | None = None,
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Product → free steps → Tank agent path → remember. No new brain."""
    return await run_with_memory(
        memory,
        tenant_id=tenant_id,
        user_id=user_id,
        task_key=task_key,
        task=task,
        paid_call=tank_agent_call,
        memory_control=memory_control,
        code_free=code_free,
    )


# Back-compat alias used by earlier gates 7+8 tests.
memory_enabled = memory_enabled_env
