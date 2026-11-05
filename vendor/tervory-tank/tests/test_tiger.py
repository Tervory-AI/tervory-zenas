"""C8 Tiger: price skills, watch history, and the browser-pool session rules.

Fixtures only. No database and no network.
"""

from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import skala
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "_chassis"))
sys.path.insert(0, str(ROOT / "agents" / "tiger"))
sys.path.insert(0, str(ROOT / "tiger"))

import pool as pool_mod  # noqa: E402
import watch as watch_mod  # noqa: E402
from pool import (  # noqa: E402
    IDLE_S,
    SessionKeyError,
    blocks_resource,
    idle_expired,
    is_public_url,
    render_block,
    route_continues,
    session_file,
)
from watch import (  # noqa: E402
    SCHEMA,
    BadWatch,
    DomainLimiter,
    MemoryStore,
    alerts_for,
    check_url,
    dispatch_tenant,
    price_moved,
    robots_allows,
    run_check,
    session_for_url,
    watch_is_due,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
EXTRACT = ROOT / "tank" / "skills" / "tiger.price_extract" / "1.0.0"
OCR = ROOT / "tank" / "skills" / "tiger.price_ocr" / "1.0.0"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EXTRACT_MOD = _load("tiger_price_extract", EXTRACT / "run.py")
OCR_MOD = _load("tiger_price_ocr", OCR / "run.py")


def _page(price: str, availability: str) -> str:
    return f"""
    <html><head><script type="application/ld+json">
    {{"@type":"Product","name":"Sample item","offers":{{"@type":"Offer","price":"{price}",
    "priceCurrency":"USD","availability":"https://schema.org/{availability}",
    "url":"https://example.test/item"}}}}
    </script></head><body><h1>Sample item</h1></body></html>
    """


def _watch(tenant: str = TENANT_A, every: str = "1h", threshold: str = "0") -> dict:
    return {
        "id": "watch-1",
        "tenant_id": tenant,
        "url": "https://example.test/item",
        "label": "sample",
        "engine": "core",
        "every": every,
        "enabled": True,
        "threshold": threshold,
    }


def test_session_files_stay_inside_one_tenant(tmp_path: Path) -> None:
    session = session_for_url("https://example.test/item")
    first = session_file(tmp_path, "tiger", TENANT_A, session)
    second = session_file(tmp_path, "tiger", TENANT_B, session)
    assert first != second
    assert first.parent != second.parent
    assert first.name == second.name
    assert "sessions" in first.parts
    with pytest.raises(SessionKeyError):
        session_file(tmp_path, "tiger", "..", session)
    with pytest.raises(SessionKeyError):
        session_file(tmp_path, "tiger", TENANT_A, "a/b")


def test_images_and_fonts_stay_blocked_unless_screenshot() -> None:
    assert blocks_resource("image", False)
    assert blocks_resource("font", False)
    assert blocks_resource("media", False)
    assert not blocks_resource("document", False)
    assert not blocks_resource("image", True)
    assert IDLE_S == 600
    assert idle_expired(0, 600)
    assert not idle_expired(0, 599)


def test_browser_does_not_hide_or_solve_checks() -> None:
    text = (ROOT / "tiger" / "app.py").read_text(encoding="utf-8")
    assert "TervoryTiger" in text
    assert "does not click" in text
    assert "route_continues" in text
    assert "render_block" in text
    assert "png_base64" in text
    assert "storage_state" in text
    assert "idle_expired" in text
    for banned in ("2captcha", "playwright-stealth", "puppeteer-extra"):
        assert banned not in text
    dockerfile = (ROOT / "tiger" / "Dockerfile").read_text(encoding="utf-8")
    assert "tesseract-ocr" in dockerfile
    assert "pool.py" in dockerfile
    assert "ocr.py" in dockerfile


def test_price_extract_evals_cover_the_required_pages() -> None:
    fixtures = sorted(path.name for path in (EXTRACT / "evals" / "fixtures").glob("*.html"))
    assert len(fixtures) >= 8
    cases = (EXTRACT / "evals" / "cases.jsonl").read_text(encoding="utf-8")
    for name in ("sale price", "out of stock", "multiple offers", "no price"):
        assert name in cases
    sale = EXTRACT_MOD.extract((EXTRACT / "evals" / "fixtures" / "sale.html").read_text(encoding="utf-8"), "")
    assert sale["items"][0]["price"] == "9.99"
    assert sale["items"][0]["price"] != "19.99"
    pattern = EXTRACT_MOD.extract((EXTRACT / "evals" / "fixtures" / "pattern.html").read_text(encoding="utf-8"), "")
    assert pattern["items"][0]["price"] == "4.50"
    aggregate = EXTRACT_MOD.extract((EXTRACT / "evals" / "fixtures" / "aggregate.html").read_text(encoding="utf-8"), "")
    assert aggregate["items"][0]["price"] == "5.00"


def test_ocr_confidence_is_lower_than_html_and_uses_the_tiger_box() -> None:
    html = EXTRACT_MOD.extract("<html></html>", "")
    assert OCR_MOD._CONFIDENCE < min(EXTRACT_MOD.CONFIDENCE.values())

    class Response:
        status_code = 200

        def json(self) -> dict:
            return {"text": "Shown as $4.25"}

    class Http:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def post(self, url: str, json: dict | None = None, timeout: float | None = None):
            self.urls.append(url)
            return Response()

    http = Http()

    class Ctx:
        env = {"TIGER_URL": "http://tiger:3000"}

        def __init__(self) -> None:
            self.http = http

    async def check() -> None:
        out = await OCR_MOD.run({"png_base64": "abc", "url": "https://example.test/pic"}, Ctx())
        assert out["method"] == "ocr"
        assert out["items"][0]["price"] == "4.25"
        assert out["confidence"] < 0.5
        assert http.urls == ["http://tiger:3000/ocr"]
        assert html["confidence"] == 0

    asyncio.run(check())


def test_saved_skill_cases_pass_offline() -> None:
    packages = skala.load_packages(ROOT / "tank" / "skills")
    failures = []
    for key in (("tiger.price_extract", "1.0.0"), ("tiger.price_ocr", "1.0.0")):
        pkg = packages[key]
        module = EXTRACT_MOD if key[0].endswith("extract") else OCR_MOD

        async def once(package=pkg, run=module.run) -> None:
            for case in package.cases:
                output = await run(case["input"], None)
                ok, why = skala.score_case(case["expected"], output, case["tolerance"])
                if not ok:
                    failures.append(f"{package.ref} {case.get('name')}: {why}")

        asyncio.run(once())
    assert not failures, "\n".join(failures)


def test_price_move_uses_a_strict_threshold() -> None:
    assert price_moved("20.00", "10.00", "0.5") is False
    assert price_moved("20.00", "9.00", "0.5") is True
    assert price_moved("20.00", "20.00", "0") is False
    assert price_moved("20.00", "19.99", "0") is True
    previous = {"method": "jsonld", "price": "20.00", "availability": "InStock"}
    current = {"method": "jsonld", "price": "10.00", "availability": "OutOfStock"}
    kinds = {row["kind"] for row in alerts_for(previous, current, "0")}
    assert kinds == {"price", "stock"}
    assert alerts_for(None, current, "0") == []
    blocked = {"method": "blocked", "price": None, "availability": None}
    assert alerts_for(previous, blocked, "0") == []


def test_one_request_per_domain_every_ten_seconds() -> None:
    slept: list[float] = []
    now = {"t": 0.0}

    def clock() -> float:
        return now["t"]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now["t"] += seconds

    limiter = DomainLimiter(10, clock=clock, sleep=sleep)

    async def check() -> None:
        assert await limiter.wait("example.test") == 0
        assert await limiter.wait("example.test") == 10
        assert await limiter.wait("other.test") == 0

    asyncio.run(check())
    assert slept == [10]


def test_robots_and_schedule() -> None:
    assert robots_allows("User-agent: *\nDisallow: /\n", "https://example.test/item") is False
    assert robots_allows("User-agent: *\nDisallow: /private\n", "https://example.test/item") is True
    body = "User-agent: TervoryTiger\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert robots_allows(body, "https://example.test/item") is False
    assert watch_is_due("1h", NOW, None) is True
    assert watch_is_due("1h", NOW, NOW - timedelta(minutes=30)) is False
    assert watch_is_due("1h", NOW, NOW - timedelta(hours=2)) is True
    with pytest.raises(BadWatch):
        check_url("file:///tmp/x")


# Names the fake resolver knows. Anything else is a lookup failure, never the network.
_RESOLVED = {
    "example.com": ["93.184.216.34"],
    "public.example": ["10.0.0.1"],
    "split.example": ["93.184.216.34", "10.0.0.1"],
}

_REFUSED = [
    "http://tank:8000",
    "http://litellm:4000/key/info",
    "http://localhost",
    "http://127.0.0.1",
    "http://10.0.0.5",
    "http://169.254.169.254",
    "http://[::1]",
    "http://[::ffff:10.0.0.1]",
    "https://public.example/item",
]


def _fake_getaddrinfo(host, _port, *_args, **_kwargs):
    key = host.lower().rstrip(".")
    if key in _RESOLVED:
        ips = _RESOLVED[key]
    else:
        try:
            ipaddress.ip_address(key)
        except ValueError as exc:
            raise socket.gaierror(8, "not found") from exc
        ips = [key]
    rows = []
    for ip in ips:
        if ipaddress.ip_address(ip.split("%", 1)[0]).version == 4:
            rows.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)))
        else:
            rows.append((socket.AF_INET6, socket.SOCK_STREAM, 17, "", (ip, 0, 0, 0)))
    return rows


def _patch_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pool_mod.socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(watch_mod.socket, "getaddrinfo", _fake_getaddrinfo)


@pytest.mark.parametrize("url", _REFUSED)
def test_private_and_internal_urls_are_refused(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _patch_dns(monkeypatch)
    assert is_public_url(url) is False
    assert watch_mod.is_public_url(url) is False
    assert render_block(url) == {"error": "url not allowed"}
    assert route_continues(url, "document", False) is False
    with pytest.raises(BadWatch, match="url not allowed"):
        check_url(url)


def test_public_url_is_allowed_and_a_split_answer_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dns(monkeypatch)
    assert is_public_url("https://example.com") is True
    assert watch_mod.is_public_url("https://example.com/path") is True
    assert check_url("  https://example.com/path  ") == "https://example.com/path"
    assert render_block("https://example.com") is None
    assert route_continues("https://example.com/app.js", "script", False) is True
    assert route_continues("https://example.com/photo.jpg", "image", False) is False
    assert route_continues("https://example.com/photo.jpg", "image", True) is True
    assert is_public_url("https://split.example") is False
    assert watch_mod.is_public_url("https://split.example") is False


def test_bad_watch_url_is_http_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dns(monkeypatch)
    agent = _load("tiger_agent_app", ROOT / "agents" / "tiger" / "app.py")
    body = agent.WatchIn(tenant_id=TENANT_A, url="http://127.0.0.1", every="1h")
    with pytest.raises(agent.HTTPException) as caught:
        agent._fields(body, partial=False)
    assert caught.value.status_code == 400
    assert caught.value.detail == "url not allowed"
    other = agent.WatchIn(tenant_id=TENANT_A, url="https://example.com", every="nope")
    with pytest.raises(agent.HTTPException) as schedule:
        agent._fields(other, partial=False)
    assert schedule.value.status_code == 422


def test_two_tenants_get_separate_rows_and_a_price_change_alerts() -> None:
    store = MemoryStore()
    seen: list[tuple] = []

    async def render(url: str, session: str, agent: str, tenant: str) -> dict:
        seen.append((tenant, str(session_file("/data", agent, tenant, session))))
        price = "20.00" if len([item for item in seen if item[0] == tenant]) == 1 else "10.00"
        stock = "InStock" if price == "20.00" else "OutOfStock"
        return {"ok": True, "challenge": False, "html": _page(price, stock), "png_base64": None}

    async def extract(html: str, url: str) -> dict:
        return EXTRACT_MOD.extract(html, url)

    async def ocr(png: str, url: str) -> dict:
        return {"items": [], "method": "ocr", "confidence": 0.15}

    async def fetch_text(url: str) -> str:
        return ""

    limiter = DomainLimiter(0)

    async def check() -> None:
        for tenant in (TENANT_A, TENANT_B):
            await store.create_watch(_watch(tenant))
            first = await run_check(
                store, tenant, _watch(tenant), "tiger", render, extract, ocr, fetch_text, limiter,
            )
            assert first["status"] == "observed"
            assert first["price"] == "20.00"
            assert first["alerts"] == []
            second = await run_check(
                store, tenant, _watch(tenant), "tiger", render, extract, ocr, fetch_text, limiter,
            )
            assert second["price"] == "10.00"
            assert {row["kind"] for row in second["alerts"]} == {"price", "stock"}
        assert len(await store.list_observations(TENANT_A, "https://example.test/item")) == 2
        assert len(await store.list_observations(TENANT_B, "https://example.test/item")) == 2
        assert {row["tenant_id"] for row in store.observations} == {TENANT_A, TENANT_B}
        paths = {item[1] for item in seen}
        assert len(paths) == 2
        assert all(item[0] in item[1] for item in seen)

    asyncio.run(check())


def test_blocked_pages_do_not_invent_a_price_or_fetch_robots() -> None:
    store = MemoryStore()
    rendered = {"n": 0}

    async def render(url: str, session: str, agent: str, tenant: str) -> dict:
        rendered["n"] += 1
        return {"ok": False, "challenge": True}

    async def extract(html: str, url: str) -> dict:
        raise AssertionError("a blocked page is not read for a price")

    async def ocr(png: str, url: str) -> dict:
        raise AssertionError("a blocked page is not read for a price")

    async def allow(url: str) -> str:
        raise AssertionError("Public reads must not fetch robots.txt")

    async def deny(url: str) -> str:
        raise AssertionError("Public reads must not fetch robots.txt")

    async def check() -> None:
        blocked = await run_check(
            store, TENANT_A, _watch(), "tiger", render, extract, ocr, allow, DomainLimiter(0),
        )
        assert blocked["status"] == "blocked"
        assert blocked["observation"]["method"] == "blocked"
        assert blocked["observation"]["evidence"] == "blocked"
        assert blocked["alerts"] == []
        robots = await run_check(
            store, TENANT_A, _watch(), "tiger", render, extract, ocr, deny, DomainLimiter(0),
        )
        assert robots["status"] == "blocked"
        assert robots["observation"]["method"] == "blocked"
        assert rendered["n"] == 2

    asyncio.run(check())


def test_dispatch_follows_the_watchlist() -> None:
    store = MemoryStore()

    async def check() -> None:
        await store.create_watch(_watch(every="1h"))
        await store.add_observation({
            "tenant_id": TENANT_A,
            "url": "https://example.test/item",
            "ts": NOW - timedelta(minutes=20),
            "price": "20.00",
            "currency": "USD",
            "availability": "InStock",
            "method": "jsonld",
            "evidence": "{}",
        })
        quiet = await dispatch_tenant(store, TENANT_A, NOW)
        assert quiet["queued"] == []
        due = await dispatch_tenant(store, TENANT_A, NOW + timedelta(hours=2))
        assert due["queued"] == ["https://example.test/item"]
        again = await dispatch_tenant(store, TENANT_A, NOW + timedelta(hours=2))
        assert again["queued"] == []
        assert store.schedules == [{"tenant_id": TENANT_A, "kind": "dispatch", "cron": "* * * * *"}]

    asyncio.run(check())


def test_queries_are_tenant_scoped_and_the_room_is_wired() -> None:
    import watch as watch_mod

    statements = [getattr(watch_mod, name) for name in dir(watch_mod) if name.startswith("SQL_")]
    assert len(statements) >= 8
    for statement in statements:
        assert "tenant_id" in statement
    assert SCHEMA == "agent_tiger"

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    for name in ("tank", "postgres", "hermes", "tiger"):
        assert "tervory-gw-tiger" in services[name]["networks"]
    for name in ("workspace", "redis", "qdrant", "litellm", "searxng", "agent-roles"):
        assert "tervory-gw-tiger" not in (services[name].get("networks") or [])
    assert "tervory-gw-template" not in services["tiger"]["networks"]
    assert services["tank"]["environment"]["TANK_KEY_TIGER"] == "${TANK_KEY_TIGER:-}"
    assert services["hermes"]["environment"]["TANK_KEY_TIGER"] == "${TANK_KEY_TIGER:-}"
    assert "AGENT_DB_PASSWORD_TIGER" in services["agent-roles"]["environment"]
    assert compose["networks"]["tervory-gw-tiger"]["external"] is True
    assert compose["networks"]["tervory-gw-tiger"]["name"] == "tervory-gw-tiger"
    assert "tiger_data" in compose["volumes"]

    box = yaml.safe_load((ROOT / "agents" / "tiger" / "docker-compose.yml").read_text(encoding="utf-8"))
    service = box["services"]["agent-tiger"]
    assert box["networks"]["gw"]["name"] == "tervory-gw-tiger"
    assert service["networks"] == ["gw"]
    assert service["mem_limit"] == "512m"
    assert service["cpus"] in (0.5, "0.5")
    assert service["environment"]["DATABASE_URL"].startswith("postgresql://agent_tiger:")
    assert "POSTGRES_PASSWORD" not in service["environment"]
    assert "POSTGRES_USER" not in service["environment"]
    assert service["environment"]["TANK_KEY"] == "${TANK_KEY_TIGER:-}"

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TANK_KEY_TIGER=" in env
    assert "AGENT_DB_PASSWORD_TIGER=" in env

    script = (ROOT / "agents" / "check_isolation.sh").read_text(encoding="utf-8")
    assert 'if [ "$host" = "tiger" ] && [ "$tiger_corridor" -eq 1 ]' in script
    assert '[ "$net" = "tervory-gw-tiger" ] && has_part "$short" tiger' in script
    assert "for host in workspace redis qdrant litellm searxng tiger agent-other; do" in script

    agent = _load("tiger_agent_app", ROOT / "agents" / "tiger" / "app.py")
    assert agent.app.state.agent == "tiger"
    assert set(agent.app.state.handlers) == {"ping", "check", "dispatch", "rig_discover", "competitor_match"}
    assert agent.app.state.job_timeouts["competitor_match"] == 45
    assert any(row["kind"] == "ping" for row in agent.app.state.schedules)
    assert any(row["kind"] == "rig_discover" and row["cron"] == "* * * * *"
               and row["tenant_id"] == agent.HEARTBEAT_TENANT for row in agent.app.state.schedules)
    image = (ROOT / "agents" / "tiger" / "Dockerfile").read_text(encoding="utf-8")
    assert "watch.py" in image
    assert "rig_watch.py" in image
    assert "app.py" in image
