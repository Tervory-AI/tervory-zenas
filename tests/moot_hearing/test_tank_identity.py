import json
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1/moot_unused_test")
os.environ.setdefault("LITELLM_MASTER_KEY", "offline-test-value")
os.environ.setdefault("TANK_API_KEY", "offline-test-value")

import main


def request(headers, *, home="us-east"):
    body = json.dumps({"request_id": str(uuid4()), "revision": 0, "payload": {}}).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/v1/zenas/moot/hearings",
        "query_string": ("home_region=" + home).encode(), "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}, receive)


class HTTP:
    def __init__(self): self.calls = []
    async def request(self, method, target, **kwargs):
        self.calls.append((method, target, kwargs))
        return httpx.Response(200, json={"ok": True})


@pytest.mark.asyncio
async def test_tank_derives_founder_and_discards_forged_capability(monkeypatch):
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("TANK_API_KEY", "workspace-disposable-test-key")
    monkeypatch.setenv("TANK_KEY_ZENAS", "room-disposable-test-key")
    monkeypatch.setenv("TERVORY_ADMIN_USER_IDS", "actual-founder")
    transport = HTTP(); monkeypatch.setattr(main, "_http", transport)
    headers = {"X-Tank-Key": "workspace-disposable-test-key", "X-Moot-Actor": "student",
        "X-Moot-Tenant": str(uuid4()), "X-Moot-Founder": "1"}
    await main.zenas_proxy("moot/hearings", request(headers))
    forwarded = transport.calls[-1][2]["headers"]
    assert forwarded["X-Moot-Founder"] == "0"
    assert forwarded["X-Tank-Key"] == "room-disposable-test-key"
    await main.zenas_proxy("moot/hearings", request({**headers, "X-Moot-Actor": "actual-founder"}))
    assert transport.calls[-1][2]["headers"]["X-Moot-Founder"] == "1"
    for mutation in ({"X-Tank-Key": "other-agent-key"}, {"X-Moot-Actor": ""}, {"X-Moot-Tenant": "not-a-tenant"}):
        with pytest.raises(HTTPException) as caught:
            await main.zenas_proxy("moot/hearings", request({**headers, **mutation}))
        assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy("moot/hearings", request(headers, home="za"))
    assert caught.value.status_code == 403
