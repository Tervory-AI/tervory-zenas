"""Boot Rig against a real Postgres and load every sample catalogue.

The in-memory store accepts a date string. asyncpg does not. This test is the
startup path that failed in production: apply schema.sql, import the sheets,
including market dates, then search.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CATALOGUES = ROOT / "tests" / "fixtures" / "rig_catalogues"


def _admin_url() -> str:
    for name in ("RIG_TEST_DATABASE_URL", "POOL_SCHEMA_DATABASE_URL"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    if os.environ.get("GITHUB_ACTIONS"):
        pytest.fail("POOL_SCHEMA_DATABASE_URL is not set")
    pytest.skip("POOL_SCHEMA_DATABASE_URL is not set")
    return ""


def _database_url(admin: str, name: str) -> str:
    parts = urlsplit(admin)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))


async def _create_database(admin: str, name: str) -> None:
    conn = await asyncpg.connect(admin)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _drop_database(admin: str, name: str) -> None:
    conn = await asyncpg.connect(admin)
    try:
        await conn.fetch(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


def _load_app():
    rig = str(ROOT / "agents" / "rig")
    chassis = str(ROOT / "agents" / "_chassis")
    for path in (chassis, rig):
        if path not in sys.path:
            sys.path.insert(0, path)
    module_name = "rig_live_app"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "agents" / "rig" / "app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _search(client: TestClient, key: str, origin: str, query: str) -> dict:
    response = client.get(
        "/v1/rig/search",
        params={"q": query},
        headers={"X-Rig-Key": key, "Origin": origin},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_rig_startup_ignores_folders_then_explicit_test_import_on_postgres(tmp_path: Path, monkeypatch) -> None:
    admin = _admin_url()
    name = "rig_boot_" + uuid4().hex[:12]
    url = _database_url(admin, name)
    asyncio.run(_create_database(admin, name))
    saved = {key: os.environ.get(key) for key in ("DATABASE_URL", "DATA_DIR", "CATALOGUE_DIR", "AGENT_NAME")}
    os.environ["DATABASE_URL"] = url
    os.environ["DATA_DIR"] = str(tmp_path)
    offline = tmp_path / "offline-catalogues"
    shutil.copytree(CATALOGUES, offline)
    os.environ["CATALOGUE_DIR"] = str(offline)
    os.environ["AGENT_NAME"] = "rig"
    try:
        app_module = _load_app()
        with TestClient(app_module.app) as client:
            # A configured disk folder is ignored by the actual startup.
            async def seed_test_catalogues():
                from rig_fixture_loader import load_catalogue
                async with app_module.app.state.pool.acquire() as conn:
                    assert await conn.fetchval('SELECT count(*) FROM agent_rig.products') == 0
                for folder in sorted(offline.iterdir()):
                    if folder.is_dir():
                        await load_catalogue(app_module.app.state.rig, folder)
            client.portal.call(seed_test_catalogues)
            monkeypatch.delenv("RIG_TRUSTED_PROXY_CIDRS", raising=False)
            assert client.get("/health").status_code == 503
            monkeypatch.setenv("RIG_TRUSTED_PROXY_CIDRS", "0.0.0.0/0")
            assert client.get("/health").status_code == 503
            monkeypatch.setenv("RIG_TRUSTED_PROXY_CIDRS", "10.20.0.0/24")
            health = client.get("/health")
            assert health.status_code == 200
            assert health.json()["ok"] is True
            home = client.get("/")
            assert home.status_code == 200
            assert "rk_pub_archon" not in home.text
            assert "rk_pub_examplebrand" not in home.text
            assert "What are you building?" in home.text
            assert "live distributor keys are off" not in home.text
            assert "Demo · keys off" not in home.text
            assert 'alt="Tervory"' in home.text
            classic = client.get("/cn-classic")
            assert classic.status_code == 200
            assert "rk_pub_customnation" not in classic.text
            assert "Custom Nation" not in classic.text
            custom = _search(client, "rk_pub_customnation", "https://rig.tervory.com", "LumaWorks C2")
            assert custom["exact_match"] is True
            compact = next(item for item in custom["results"] if item["id"] == "STI-C2-9")
            assert compact["price"]["amount"] == 58999
            assert compact["attributes"] == {"size": "compact"}
            example = _search(client, "rk_pub_examplebrand", "https://rig.tervory.com", "Example Rifle")
            assert example["results"][0]["id"] == "EXAMPLE-RIFLE-01"
            second = _search(client, "rk_pub_secondtenant", "https://second.example", "Desk Lamp")
            assert second["results"][0]["id"] == "SECOND-LAMP-01"
        asyncio.run(_assert_imported(url))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        asyncio.run(_drop_database(admin, name))


async def _assert_imported(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        products = await conn.fetchval("SELECT count(*) FROM agent_rig.products")
        # customnation 18 + example 3 + second 2 + archon 15
        assert products == 18 + 3 + 2 + 15
        seen = await conn.fetch("SELECT seen_at, price FROM agent_rig.market_prices")
        # customnation market.csv 32 rows + archon market.csv 53 rows
        assert len(seen) == 32 + 53
        dates: dict[str, int] = {}
        for row in seen:
            assert isinstance(row["seen_at"], datetime)
            assert row["price"] is not None
            day = row["seen_at"].date().isoformat()
            dates[day] = dates.get(day, 0) + 1
        # Each catalogue keeps the date its market.csv carries.
        assert dates == {"2026-03-01": 32, "2026-10-18": 53}
        kind = await conn.fetchval(
            "SELECT jsonb_typeof(attributes) FROM agent_rig.products WHERE sku = $1",
            "STI-C2-9",
        )
        assert kind == "object"
    finally:
        await conn.close()
