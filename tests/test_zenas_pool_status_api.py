"""Feeds switchboard: status for this box, and a load of one source."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.api import create_app, finish_loads
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.nightly import packaged_snapshots
from agents.zenas.pool.region import RegionError
from agents.zenas.pool.store import MemoryStore

MARKER = "marker-" + ("b" * 16)


def _open(region: str, *, snapshots=None, http_get=None, live=None):
    store = MemoryStore()
    app = create_app(
        store,
        region=region,
        snapshots=snapshots,
        http_get=http_get,
        live=live,
    )
    return TestClient(app), store


def _client(region: str, *, snapshots=None, http_get=None, live=None) -> TestClient:
    client, _store = _open(region, snapshots=snapshots, http_get=http_get, live=live)
    return client


def _nothing_stored(store: MemoryStore) -> None:
    assert store.sources == {}
    assert store.docs == {}
    assert store.chunks == {}
    assert store.links == {}
    assert store.runs == {}
    assert store.cites == {}
    assert store.checkpoints == {}
    assert asyncio.run(store.version_count()) == 0


@pytest.mark.parametrize("region", [None, "", "eu"])
def test_create_app_refuses_a_missing_or_unknown_region(region):
    store = MemoryStore()
    with pytest.raises(RegionError):
        create_app(store, region=region)
    assert store.home_region in (None, "")
    _nothing_stored(store)


def test_status_lists_this_region_and_never_the_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LAWS_AFRICA_API_TOKEN", MARKER)
    client = _client("za")
    response = client.get("/pool/status")
    assert response.status_code == 200
    body = response.json()
    assert body["region"] == "za"
    ids = {row["source_id"] for row in body["sources"]}
    assert ids == {source.source_id for source in sources_for_region("za")}
    assert "courtlistener" not in ids
    encoded = json.dumps(body)
    assert MARKER not in encoded
    assert "Bearer" not in encoded
    popia = next(row for row in body["sources"] if row["source_id"] == "popia")
    assert popia["key_name"] == "LAWS_AFRICA_API_TOKEN"
    assert popia["key_required"] is True
    assert popia["key_set"] is True
    assert popia["documents"] == 0
    assert popia["last_load"] is None
    assert popia["errors"] == []
    saflii = next(row for row in body["sources"] if row["source_id"] == "saflii")
    assert saflii["key_name"] == ""
    assert saflii["key_set"] is True


def test_status_key_set_is_false_until_the_variable_is_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAWS_AFRICA_API_TOKEN", raising=False)
    client = _client("za")
    body = client.get("/pool/status").json()
    popia = next(row for row in body["sources"] if row["source_id"] == "popia")
    assert popia["key_set"] is False
    assert MARKER not in json.dumps(body)


@pytest.mark.asyncio
async def test_load_one_za_source_from_a_snapshot():
    store = MemoryStore()
    app = create_app(store, region="za", snapshots=packaged_snapshots("za"), live=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post("/pool/sources/companies-act/load")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "status": "running"}
        await finish_loads()
        status = (await client.get("/pool/status")).json()
    assert status["region"] == "za"
    companies = next(row for row in status["sources"] if row["source_id"] == "companies-act")
    assert companies["documents"] == 1
    assert companies["last_status"] == "ok"
    assert companies["errors"] == []
    assert companies["key_name"] == "LAWS_AFRICA_API_TOKEN"
    popia = next(row for row in status["sources"] if row["source_id"] == "popia")
    assert popia["documents"] == 0


def test_a_us_source_on_a_za_app_is_refused_with_no_fetch_and_no_write():
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"US-TEXT"

    client, store = _open("za", http_get=get, live=True)
    response = client.post("/pool/sources/courtlistener/load")
    assert response.status_code == 409
    assert "courtlistener" in response.json()["detail"]
    assert seen == []
    _nothing_stored(store)
    assert asyncio.run(store.get_source("courtlistener")) is None
    status = client.get("/pool/status").json()
    assert status["region"] == "za"
    assert status["sources"]
    assert all(row["region"] == "za" for row in status["sources"])
    assert "courtlistener" not in {row["source_id"] for row in status["sources"]}


def test_a_za_source_on_a_us_app_is_refused_with_no_fetch_and_no_write():
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"ZA-TEXT"

    client, store = _open("us-east", http_get=get, live=True)
    response = client.post("/pool/sources/saflii/load")
    assert response.status_code == 409
    assert seen == []
    _nothing_stored(store)
    assert asyncio.run(store.get_source("saflii")) is None
    listed = client.get("/pool/status").json()
    assert listed["region"] == "us-east"
    assert listed["sources"]
    assert "saflii" not in {row["source_id"] for row in listed["sources"]}
    assert "courtlistener" in {row["source_id"] for row in listed["sources"]}


@pytest.mark.asyncio
async def test_missing_key_does_not_call_the_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LAWS_AFRICA_API_TOKEN", raising=False)
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        return b"<xml></xml>"

    app = create_app(MemoryStore(), region="za", http_get=get, live=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post("/pool/sources/lra/load")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "status": "running"}
        await finish_loads()
        status = (await client.get("/pool/status")).json()
    row = next(item for item in status["sources"] if item["source_id"] == "lra")
    assert row["last_status"] == "not_configured"
    assert row["key_set"] is False
    assert row["documents"] == 0
    assert seen == []
    assert MARKER not in response.text


def test_unknown_source_is_not_found():
    client = _client("za")
    response = client.post("/pool/sources/not-a-source/load")
    assert response.status_code == 404
