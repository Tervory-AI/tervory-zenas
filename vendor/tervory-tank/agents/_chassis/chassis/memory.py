"""Memory client. Calls Hermes with this agent's own key.

Memory is private to one agent and one tenant. Hermes chooses the collection
from the key. This client never names a collection, and it never logs the key.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class MemoryClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        agent: str | None = None,
        http: httpx.AsyncClient | None = None,
        *, user_id: str | None = None,
    ) -> None:
        configured = base_url if base_url is not None else os.environ.get("HERMES_URL", "")
        self._base = (configured or "http://hermes:8000").rstrip("/")
        self._api_key = api_key if api_key is not None else os.environ.get("TANK_KEY", "")
        self._agent = agent if agent is not None else os.environ.get("AGENT_NAME", "")
        self._http = http
        self._user_id = user_id

    def __repr__(self) -> str:
        return f"MemoryClient(agent={self._agent!r})"

    async def remember(self, tenant_id: str, text: str, kind: str, source: str, *,
                       user_id: str | None = None,
                       evidence: list[str] | None = None, confidence: float = .5,
                       record_id: str | None = None, row_id: str | None = None,
                       last_verified: str | None = None, meta: dict | None = None,
                       index: bool = True, tenant_wide: bool | None = None, replace: bool = False) -> dict[str, Any]:
        """Write memory. Personal rows always stamp meta.user_id; tenant-wide omit it.

        user_id must come from the verified signed-in actor, never a request body.
        """
        from uuid import UUID
        if user_id is None and not tenant_wide:
            user_id = self._user_id
        identity = {"id": str(UUID(record_id))} if record_id is not None else {}
        # Existing service heartbeats have no human actor. Hermes still validates
        # the agent-specific type declaration; this is not an authorization flag.
        if tenant_wide is None:
            tenant_wide = kind == "heartbeat" and user_id is None
        payload_meta = dict(meta or {})
        if tenant_wide:
            if user_id is not None or payload_meta.get("user_id") is not None:
                raise ValueError("tenant-wide records carry no user_id")
            payload_meta.pop("user_id", None)
        else:
            uid = (user_id or "").strip()
            if not uid:
                raise ValueError("user_id required for personal memory")
            payload_meta["user_id"] = uid
        body = {
            **identity,
            "tenant_id": str(tenant_id),
            "text": text,
            "kind": kind,
            "source": source,
            "evidence": evidence or [],
            "confidence": confidence,
            "meta": payload_meta,
            **({"id": row_id} if row_id else {}),
            **({"last_verified": last_verified} if last_verified else {}),
            **({"index": False} if not index else {}),
        }
        if user_id and not tenant_wide:
            body["user_id"] = str(user_id).strip()
        # Edits are explicit; deterministic record_id/row_id writes retain
        # their existing idempotent-create/replay contract.
        if replace and not record_id:
            raise ValueError("record_id required for edit")
        method, path = ("PATCH", "/memories/" + str(UUID(record_id))) if replace else ("POST", "/memories")
        data = await self._send(method, path, body)
        if not isinstance(data, dict):
            raise RuntimeError("hermes answer was not an object")
        return data

    async def recall(
        self,
        tenant_id: str,
        query: str,
        *,
        user_id: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
        tier: str = 'client',
    ) -> list[dict[str, Any]]:
        user_id = self._user_id if user_id is None else user_id
        if tier not in ('client', 'platform'):
            raise ValueError('Reference corpora use their registered read-only endpoint')
        body: dict[str, Any] = {"tenant_id": str(tenant_id), "query": query}
        if tier != 'client':
            body['tier'] = tier
        if kind:
            body["kind"] = kind
        if limit is not None:
            body["limit"] = limit
        if user_id is not None and str(user_id).strip():
            body["user_id"] = str(user_id).strip()
        data = await self._send("POST", "/recall", body)
        if not isinstance(data, dict) or not isinstance(data.get("memories"), list):
            raise RuntimeError("hermes answer was not an object")
        memories: list[dict[str, Any]] = []
        for item in data["memories"]:
            if not isinstance(item, dict):
                raise RuntimeError("hermes answer was not an object")
            memories.append(item)
        return memories

    async def records(self, tenant_id: str, *, user_id: str | None = None, tier: str = "client") -> list[dict[str, Any]]:
        """SQL listing only; never performs recall or an embedding search."""
        from uuid import UUID
        from urllib.parse import urlencode
        user_id = self._user_id if user_id is None else user_id
        if tier not in {"client", "platform"}:
            raise ValueError("invalid memory tier")
        rows = []
        for offset in range(0, 20000, 200):
            params = {"tenant_id": str(UUID(tenant_id)), "tier": tier, "limit": 200, "offset": offset}
            if user_id is not None and str(user_id).strip():
                params["user_id"] = str(user_id).strip()
            data = await self._send("GET", "/records?" + urlencode(params))
            if not isinstance(data, dict) or not isinstance(data.get("memories"), list) or any(not isinstance(row, dict) for row in data["memories"]):
                raise RuntimeError("hermes answer was not an object")
            page = data["memories"]
            rows.extend(page)
            if len(page) < 200:
                return rows
        raise RuntimeError("Memory record list exceeds the limit")

    async def forget(self, memory_id: str, tenant_id: str, *, user_id: str | None = None) -> None:
        from uuid import UUID
        from urllib.parse import urlencode
        user_id = self._user_id if user_id is None else user_id
        params = {"tenant_id": str(UUID(tenant_id))}
        if user_id is not None:
            params["user_id"] = user_id
        await self._send("DELETE", "/memories/" + str(UUID(memory_id)) + "?" + urlencode(params))

    async def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self._api_key:
            raise RuntimeError("tank key is not set")
        if "/v1/memory" in self._base:
            raise RuntimeError("MemoryClient must call Hermes directly; Tank /v1/memory is banned")
        import json
        from urllib.parse import parse_qs, urlsplit
        try:
            from chassis.user_assertion import bind_personal_headers
        except ImportError:
            from command_tools._user_assertion import bind_personal_headers

        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode() if body is not None else b""
        tenant, user = "", ""
        if body is not None:
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            tenant = str(body.get("tenant_id") or "")
            user = body.get("user_id") or meta.get("user_id") or ""
        else:
            query = parse_qs(urlsplit(self._base + path).query)
            tenant = (query.get("tenant_id") or [""])[0]
            user = (query.get("user_id") or [""])[0]
        headers = {"X-Tank-Key": self._api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if tenant and user:
            target = httpx.URL(self._base + path).raw_path.decode("ascii")
            headers.update(bind_personal_headers(self._api_key, method, target,
                tenant=tenant, user=user, body=raw))

        async def _do(http: httpx.AsyncClient) -> httpx.Response:
            if body is not None:
                return await http.request(
                    method, self._base + path, content=raw, headers=headers
                )
            return await http.request(method, self._base + path, headers=headers)

        try:
            if self._http is not None:
                response = await _do(self._http)
            else:
                async with httpx.AsyncClient(timeout=30) as http:
                    response = await _do(http)
        except httpx.HTTPError:
            raise RuntimeError("hermes unreachable") from None
        if response.status_code >= 400:
            raise RuntimeError(f"hermes answered {response.status_code}")
        if not response.content:
            return {}
        data = response.json()
        return data
