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
    ) -> None:
        configured = base_url if base_url is not None else os.environ.get("HERMES_URL", "")
        self._base = (configured or "http://hermes:8000").rstrip("/")
        self._api_key = api_key if api_key is not None else os.environ.get("TANK_KEY", "")
        self._agent = agent if agent is not None else os.environ.get("AGENT_NAME", "")
        self._http = http

    def __repr__(self) -> str:
        return f"MemoryClient(agent={self._agent!r})"

    async def remember(self, tenant_id: str, text: str, kind: str, source: str, *,
                       evidence: list[str] | None = None, confidence: float = .5,
                       record_id: str | None = None, row_id: str | None = None,
                       last_verified: str | None = None, meta: dict | None = None,
                       index: bool = True) -> dict[str, Any]:
        from uuid import UUID
        identity = {"id": str(UUID(record_id))} if record_id is not None else {}
        data = await self._send(
            "POST",
            "/memories",
            {
                **identity,
                "tenant_id": str(tenant_id),
                "text": text,
                "kind": kind,
                "source": source,
                "evidence": evidence or [],
                "confidence": confidence,
                **({"id": row_id} if row_id else {}),
                **({"last_verified": last_verified} if last_verified else {}),
                **({"meta": meta} if meta is not None else {}),
                **({"index": False} if not index else {}),
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError("hermes answer was not an object")
        return data

    async def recall(
        self,
        tenant_id: str,
        query: str,
        *,
        kind: str | None = None,
        limit: int | None = None,
        tier: str = 'client',
    ) -> list[dict[str, Any]]:
        if tier not in ('client', 'platform'):
            raise ValueError('Reference corpora use their registered read-only endpoint')
        body: dict[str, Any] = {"tenant_id": str(tenant_id), "query": query}
        if tier != 'client':
            body['tier'] = tier
        if kind:
            body["kind"] = kind
        if limit is not None:
            body["limit"] = limit
        data = await self._send("POST", "/recall", body)
        if not isinstance(data, dict) or not isinstance(data.get("memories"), list):
            raise RuntimeError("hermes answer was not an object")
        memories: list[dict[str, Any]] = []
        for item in data["memories"]:
            if not isinstance(item, dict):
                raise RuntimeError("hermes answer was not an object")
            memories.append(item)
        return memories

    async def records(self, tenant_id: str, *, tier: str = "client") -> list[dict[str, Any]]:
        """SQL listing only; never performs recall or an embedding search."""
        from uuid import UUID
        from urllib.parse import urlencode
        if tier not in {"client", "platform"}:
            raise ValueError("invalid memory tier")
        rows = []
        for offset in range(0, 20000, 200):
            data = await self._send("GET", "/records?" + urlencode({"tenant_id": str(UUID(tenant_id)), "tier": tier,
                                                                    "limit": 200, "offset": offset}))
            if not isinstance(data, dict) or not isinstance(data.get("memories"), list) or any(not isinstance(row, dict) for row in data["memories"]):
                raise RuntimeError("hermes answer was not an object")
            page = data["memories"]
            rows.extend(page)
            if len(page) < 200:
                return rows
        raise RuntimeError("Memory record list exceeds the limit")

    async def forget(self, memory_id: str, tenant_id: str) -> None:
        from uuid import UUID
        await self._send("DELETE", "/memories/" + str(UUID(memory_id)) + '?tenant_id=' + str(UUID(tenant_id)))

    async def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self._api_key:
            raise RuntimeError("tank key is not set")
        headers = {"X-Tank-Key": self._api_key}
        try:
            if self._http is not None:
                response = await self._http.request(method, self._base + path, json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=30) as http:
                    response = await http.request(method, self._base + path, json=body, headers=headers)
        except httpx.HTTPError:
            raise RuntimeError("hermes unreachable") from None
        if response.status_code >= 400:
            raise RuntimeError(f"hermes answered {response.status_code}")
        if not response.content:
            return {}
        data = response.json()
        return data
