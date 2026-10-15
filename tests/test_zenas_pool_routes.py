"""The Zenas room serves the feeds switchboard: /pool/status and a per-source load.

No database, no network: a memory store and fixtures only.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.zenas.pool.api import register_routes
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]


def _client(region: str | None) -> TestClient:
    store = MemoryStore()

    @asynccontextmanager
    async def open_store():
        yield store

    app = FastAPI()
    register_routes(app, open_store, lambda: region, live=False, snapshots={})
    return TestClient(app)


def test_pool_status_returns_200_with_every_source_for_the_box() -> None:
    for region in ("za", "us-east"):
        res = _client(region).get("/pool/status")
        assert res.status_code == 200, region
        body = res.json()
        assert body["region"] == region
        ids = [row["source_id"] for row in body["sources"]]
        assert ids == [s.source_id for s in sources_for_region(region)]
        for row in body["sources"]:
            assert row["region"] == region
            assert "documents" in row and "key_set" in row


def test_pool_status_refuses_an_unknown_box_region() -> None:
    assert _client("eu").get("/pool/status").status_code == 409
    assert _client(None).get("/pool/status").status_code == 409


def test_load_refuses_unknown_and_other_region_sources() -> None:
    client = _client("za")
    assert client.post("/pool/sources/no-such-source/load").status_code == 404
    us_source = sources_for_region("us-east")[0].source_id
    assert client.post(f"/pool/sources/{us_source}/load").status_code == 409


def test_the_zenas_room_registers_the_pool_routes() -> None:
    app_py = (ROOT / "agents" / "zenas" / "app.py").read_text(encoding="utf-8")
    assert "register_pool_routes(app, _pool_store, _box_region)" in app_py
