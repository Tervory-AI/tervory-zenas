"""Browser client. C8 fills this in with Tiger's browser pool."""

from __future__ import annotations

from typing import Any


class BrowserClient:
    async def render(self, url: str, session: str) -> dict[str, Any]:
        raise NotImplementedError("browser pool arrives in C8")
