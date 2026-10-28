"""The /v1/zenas proxy stays on this server and stores nothing for the other home."""
from __future__ import annotations

import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
os.environ.setdefault("LITELLM_MASTER_KEY", "test")
os.environ.setdefault("TANK_API_KEY", "test")

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main

SECRET = "laws-africa-secret-value"


def _request(method: str, path: str, body: bytes = b"", query: bytes = b"") -> Request:
    sent = {"done": False}

    async def receive() -> dict:
        if sent["done"]:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    return Request(scope, receive)


class _Room:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def request(self, method: str, url: str, **kwargs: object) -> object:
        self.calls.append(url)

        class _Response:
            content = self.payload
            status_code = 200
            headers = {"content-type": "application/json"}

        return _Response()


def test_upstream_is_only_the_local_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    assert main.zenas_upstream("matters") == "http://agent-zenas:8000/matters"
    assert main.zenas_upstream("matters/11111111-1111-1111-1111-111111111111/documents").endswith("/documents")
    monkeypatch.setenv("ZENAS_AGENT_URL", "https://za.tervory.com")
    with pytest.raises(HTTPException) as caught:
        main.zenas_upstream("matters")
    assert caught.value.status_code == 403
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://10.1.2.3:8000")
    with pytest.raises(HTTPException) as caught:
        main.zenas_upstream("pool/status")
    assert caught.value.status_code == 403
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    assert main.zenas_upstream("tenants") == "http://agent-zenas:8000/tenants"
    assert main.zenas_upstream("selftest") == "http://agent-zenas:8000/selftest"
    assert main.zenas_upstream("review-switch") == "http://agent-zenas:8000/review-switch"
    assert main.zenas_upstream("cases/convert") == "http://agent-zenas:8000/cases/convert"
    assert main.zenas_upstream("pool/sources/companies-act/load").endswith("/pool/sources/companies-act/load")
    with pytest.raises(HTTPException) as caught:
        main.zenas_upstream("self-test")
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        main.zenas_upstream("review_switch")
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        main.zenas_upstream("../postgres")
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        main.zenas_upstream("pool/load")
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        main.zenas_upstream("pool/sources/not a source/load")
    assert caught.value.status_code == 404


def test_feed_payload_keeps_the_key_name_and_drops_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", SECRET)
    cleaned = main.annotate_key_set(
        main.public_feed(
            {
                "sources": [
                    {
                        "source_id": "laws-africa",
                        "key_name": "LAWS_AFRICA_API_TOKEN",
                        "api_key": SECRET,
                        "token": SECRET,
                        "documents": 4,
                    }
                ]
            }
        )
    )
    encoded = json.dumps(cleaned)
    assert SECRET not in encoded
    assert cleaned["sources"][0]["key_name"] == "LAWS_AFRICA_API_TOKEN"
    assert cleaned["sources"][0]["key_set"] is True
    assert "api_key" not in cleaned["sources"][0]


def test_feed_passes_load_status_and_strips_keyed_urls() -> None:
    secret = "super-secret-value-do-not-show"
    tail = "TAIL-MUST-NOT-CROSS"
    long_detail = ("HTTP 401 from courtlistener.com " * 20) + tail
    cleaned = main.public_feed(
        {
            "sources": [
                {
                    "source_id": "courtlistener",
                    "key_name": "COURTLISTENER_API_TOKEN",
                    "api_key": secret,
                    "token": secret,
                    "last_status": "not_configured",
                    "detail": "COURTLISTENER_API_TOKEN is not set",
                    "terms_url": f"https://www.courtlistener.com/api/rest/v4/?api_key={secret}",
                },
                {
                    "source_id": "govinfo",
                    "last_status": "error",
                    "detail": f"fetch failed https://api.govinfo.gov/docs?api_key={secret} after the call",
                    "errors": [f"https://api.govinfo.gov/packages?token={secret}"],
                },
                {
                    "source_id": "saflii",
                    "last_status": "error",
                    "detail": long_detail,
                },
            ]
        }
    )
    encoded = json.dumps(cleaned)
    assert secret not in encoded
    assert "api_key=" not in encoded
    assert "token=" not in encoded
    assert "sk-" not in encoded
    court = cleaned["sources"][0]
    assert court["last_status"] == "not_configured"
    assert court["detail"] == "COURTLISTENER_API_TOKEN is not set"
    assert court["key_name"] == "COURTLISTENER_API_TOKEN"
    assert "api_key" not in court
    assert "token" not in court
    assert secret not in court["terms_url"]
    gov = cleaned["sources"][1]
    assert gov["last_status"] == "error"
    assert gov["detail"] == "fetch failed after the call"
    assert gov["errors"] == [""]
    saflii = cleaned["sources"][2]
    assert saflii["last_status"] == "error"
    assert len(saflii["detail"]) <= 240
    assert saflii["detail"].startswith("HTTP 401 from courtlistener.com")
    assert tail not in saflii["detail"]
    leaked = main.public_feed(
        {"sources": [{"last_status": "error", "detail": "upstream said sk-abcd1234 bad"}]}
    )
    assert "sk-" not in leaked["sources"][0]["detail"]
    assert "upstream said" in leaked["sources"][0]["detail"]
    assert "bad" in leaked["sources"][0]["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["", " ", "eu", "us"])
async def test_proxy_refuses_when_the_box_region_is_missing(
    monkeypatch: pytest.MonkeyPatch, region: str
) -> None:
    if region == "":
        monkeypatch.delenv("TANK_REGION", raising=False)
    else:
        monkeypatch.setenv("TANK_REGION", region)
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    room = _Room(b"{}")
    monkeypatch.setattr(main, "_http", room)
    body = json.dumps(
        {"tenant_id": "11111111-1111-1111-1111-111111111111", "home_region": "us-east"}
    ).encode()
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy("matters", _request("POST", "/v1/zenas/matters", body))
    assert caught.value.status_code == 409
    assert caught.value.detail == "TANK_REGION must be us-east or za"
    assert room.calls == []


@pytest.mark.asyncio
async def test_proxy_refuses_the_other_home_and_does_not_call_the_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    room = _Room(b"{}")
    monkeypatch.setattr(main, "_http", room)
    body = json.dumps({"tenant_id": "11111111-1111-1111-1111-111111111111", "home_region": "za", "title": "NDA"}).encode()
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy("matters", _request("POST", "/v1/zenas/matters", body))
    assert caught.value.detail == main.ZA_HOME_MESSAGE
    assert room.calls == []


@pytest.mark.asyncio
async def test_proxy_forwards_only_inside_the_region_and_strips_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", SECRET)
    room = _Room(
        json.dumps(
            {"sources": [{"source_id": "laws-africa", "key_name": "LAWS_AFRICA_API_TOKEN", "api_key": SECRET, "documents": 2}]}
        ).encode()
    )
    monkeypatch.setattr(main, "_http", room)
    response = await main.zenas_proxy(
        "pool/status",
        _request("GET", "/v1/zenas/pool/status", query=b"home_region=za"),
    )
    assert room.calls == ["http://agent-zenas:8000/pool/status"]
    assert SECRET.encode() not in response.body
    payload = json.loads(response.body)
    assert payload["sources"][0]["key_set"] is True


@pytest.mark.asyncio
async def test_proxy_passes_load_status_and_strips_keyed_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "super-secret-value-do-not-show"
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", secret)
    room = _Room(
        json.dumps(
            {
                "region": "us-east",
                "sources": [
                    {
                        "source_id": "courtlistener",
                        "key_name": "COURTLISTENER_API_TOKEN",
                        "api_key": secret,
                        "last_status": "not_configured",
                        "detail": "COURTLISTENER_API_TOKEN is not set",
                        "terms_url": f"https://www.courtlistener.com/api/?api_key={secret}",
                    }
                ],
            }
        ).encode()
    )
    monkeypatch.setattr(main, "_http", room)
    response = await main.zenas_proxy(
        "pool/status",
        _request("GET", "/v1/zenas/pool/status", query=b"home_region=us-east"),
    )
    assert secret.encode() not in response.body
    assert b"api_key=" not in response.body
    payload = json.loads(response.body)
    row = payload["sources"][0]
    assert row["last_status"] == "not_configured"
    assert row["detail"] == "COURTLISTENER_API_TOKEN is not set"
    assert row["key_name"] == "COURTLISTENER_API_TOKEN"
    assert row["key_set"] is True
    assert "api_key" not in row


@pytest.mark.asyncio
async def test_proxy_forwards_tenants_only_for_this_regions_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    room = _Room(b'{"ok": true, "created": true, "home_region": "us-east"}')
    monkeypatch.setattr(main, "_http", room)
    body = json.dumps(
        {"tenant_id": "11111111-1111-1111-1111-111111111111", "home_region": "us-east"}
    ).encode()
    response = await main.zenas_proxy("tenants", _request("POST", "/v1/zenas/tenants", body))
    assert response.status_code == 200
    assert room.calls == ["http://agent-zenas:8000/tenants"]
    room.calls.clear()
    foreign = json.dumps(
        {"tenant_id": "11111111-1111-1111-1111-111111111111", "home_region": "za"}
    ).encode()
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy("tenants", _request("POST", "/v1/zenas/tenants", foreign))
    assert caught.value.status_code == 403
    assert caught.value.detail == main.ZA_HOME_MESSAGE
    assert room.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "selftest", b""),
        ("POST", "selftest", b""),
        ("GET", "review-switch", b""),
        ("POST", "review-switch", b'{"home_region":"za","review":"off"}'),
        ("GET", "cases/convert", b""),
        ("POST", "cases/convert", b'{"home_region":"za"}'),
    ],
)
async def test_selftest_and_review_switch_reach_the_room(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: bytes
) -> None:
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    room = _Room(b'{"ok": true, "review": "off", "runs": []}')
    monkeypatch.setattr(main, "_http", room)
    response = await main.zenas_proxy(path, _request(method, f"/v1/zenas/{path}", body, b"home_region=za"))
    assert response.status_code == 200
    assert room.calls == [f"http://agent-zenas:8000/{path}"]


@pytest.mark.asyncio
async def test_a_source_load_is_forwarded_and_its_key_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    room = _Room(
        json.dumps(
            {"ok": True, "source": {"source_id": "companies-act", "key_name": "LAWS_AFRICA_API_TOKEN", "api_key": SECRET}}
        ).encode()
    )
    monkeypatch.setattr(main, "_http", room)
    response = await main.zenas_proxy(
        "pool/sources/companies-act/load",
        _request("POST", "/v1/zenas/pool/sources/companies-act/load", body=b'{"home_region":"za"}'),
    )
    assert room.calls == ["http://agent-zenas:8000/pool/sources/companies-act/load"]
    assert SECRET.encode() not in response.body


class _Explode:
    def acquire(self) -> None:
        raise AssertionError("this region must not touch the database")


class _Provider:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("provider was called")


@pytest.mark.asyncio
@pytest.mark.parametrize("box", ["us-east", "za"])
@pytest.mark.parametrize("mode", ["missing", "empty-body", "empty-query", "unknown", "blank-body-with-local-query"])
async def test_proxy_refuses_a_bad_home_before_any_call(monkeypatch: pytest.MonkeyPatch, box: str, mode: str) -> None:
    monkeypatch.setenv("TANK_REGION", box)
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    monkeypatch.setattr(main, "pool", lambda: _Explode())
    monkeypatch.setattr(main.httpx, "AsyncClient", _Provider)
    room = _Room(b"{}")
    monkeypatch.setattr(main, "_http", room)
    body = b""
    query = b""
    if mode == "empty-body":
        body = b'{"home_region":""}'
    elif mode == "empty-query":
        query = b"home_region="
    elif mode == "unknown":
        body = b'{"home_region":"eu"}'
    elif mode == "blank-body-with-local-query":
        body = json.dumps({"home_region": "", "title": "NDA"}).encode()
        query = f"home_region={box}".encode()
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy("matters", _request("POST", "/v1/zenas/matters", body, query))
    assert caught.value.status_code == 403
    assert caught.value.detail == main.HOME_REQUIRED
    assert room.calls == []


@pytest.mark.asyncio
async def test_a_foreign_room_url_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://us-east.tervory.com")
    room = _Room(b"{}")
    monkeypatch.setattr(main, "_http", room)
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy("health", _request("GET", "/v1/zenas/health", query=b"home_region=za"))
    assert caught.value.status_code == 403
    assert caught.value.detail == "Zenas stays in this region"
    assert room.calls == []


@pytest.mark.asyncio
async def test_published_moot_reader_reaches_only_the_local_us_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANK_REGION", "us-east")
    monkeypatch.setenv("ZENAS_AGENT_URL", "http://agent-zenas:8000")
    room = _Room(b'{"matches": []}')
    monkeypatch.setattr(main, "_http", room)
    response = await main.zenas_proxy(
        "moot/published", _request("GET", "/v1/zenas/moot/published", query=b"home_region=us-east"),
    )
    assert response.status_code == 200
    assert json.loads(response.body) == {"matches": []}
    assert room.calls == ["http://agent-zenas:8000/moot/published"]
    room.calls.clear()
    with pytest.raises(HTTPException) as caught:
        await main.zenas_proxy(
            "moot/published", _request("GET", "/v1/zenas/moot/published", query=b"home_region=za"),
        )
    assert caught.value.status_code == 403
    assert room.calls == []
