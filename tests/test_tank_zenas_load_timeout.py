"""Load now: Tank waits long enough for a slow source load in the Zenas room.

Network mocked: the Zenas room is a fake. No database.
"""
from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
os.environ.setdefault("LITELLM_MASTER_KEY", "test")
os.environ.setdefault("TANK_API_KEY", "test")

import pytest
from starlette.requests import Request

import main


def _request(method: str, path: str, body: bytes = b"") -> Request:
    sent = {"done": False}

    async def receive() -> dict:
        if sent["done"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    return Request(scope, receive)


class _Room:
    def __init__(self) -> None:
        self.timeouts: list[object] = []

    async def request(self, method: str, url: str, **kwargs: object) -> object:
        self.timeouts.append(kwargs.get("timeout"))

        class _Response:
            content = json.dumps({"ok": True}).encode()
            status_code = 200
            headers = {"content-type": "application/json"}

        return _Response()


@pytest.fixture
def room(monkeypatch: pytest.MonkeyPatch) -> _Room:
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    monkeypatch.delenv("ZENAS_LOAD_TIMEOUT", raising=False)
    fake = _Room()
    monkeypatch.setattr(main, "_http", fake)
    return fake


def test_source_load_gets_the_long_timeout(room: _Room) -> None:
    body = json.dumps({"home_region": "za"}).encode()
    resp = asyncio.run(main.zenas_proxy("pool/sources/concourt/load", _request("POST", "/v1/zenas/pool/sources/concourt/load", body)))
    assert resp.status_code == 200
    assert room.timeouts == [main.ZENAS_LOAD_TIMEOUT_DEFAULT]
    assert main.ZENAS_LOAD_TIMEOUT_DEFAULT > 60


@pytest.mark.parametrize("path", ["pool/status", "tenants", "pool/sources/concourt", "pool/sources/load"])
def test_other_calls_keep_sixty_seconds(path: str) -> None:
    assert main.zenas_timeout(path) == 60


def test_load_timeout_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENAS_LOAD_TIMEOUT", "300")
    assert main.zenas_timeout("pool/sources/sca/load") == 300
    monkeypatch.setenv("ZENAS_LOAD_TIMEOUT", "nonsense")
    assert main.zenas_timeout("/pool/sources/sca/load/") == main.ZENAS_LOAD_TIMEOUT_DEFAULT
