"""Job 9 review: first-night records, actual chassis publisher, and LegiScan replay."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from test_alerts import setup_watch, tick, FR, TENANT, OTHER, SAVED
from agents.zenas.core.regulation_sources import Sources, legiscan_request
from agents.zenas.pool.licence import FetchNotConfigured


@pytest.mark.asyncio
async def test_first_night_bus_once_and_recovery(catcher, monkeypatch, caplog):
    from chassis import events
    import importlib.util

    # Load the actual publisher separately from the suite's default no-network stub.
    spec = importlib.util.spec_from_file_location(
        "review_events", Path(events.__file__)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from event_bus import FACTS

    monkeypatch.setenv("TANK_KEY", "TANK_KEY_ZENAS")
    monkeypatch.setenv("TANK_REGION", "us-east")
    accepted = []
    down = True

    def bus(request):
        if down:
            return httpx.Response(503)
        payload = json.loads(request.content)
        assert request.headers["X-Tervory-Tenant"] == TENANT
        assert payload["event"] == "zenas.regulation.changed"
        FACTS[payload["event"]].model_validate(payload["facts"])
        accepted.append(payload)
        return httpx.Response(200, json={"published": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(bus)) as http:

        async def publish(*args):
            return await module.publish(*args, http=http)

        monkeypatch.setattr(events, "publish", publish)
        store, watch, source, tank = await setup_watch()
        source.rows = [FR[0], FR[1]]
        args = dict(transport=catcher.transport, recipient="watcher@example.test")
        first = await tick(store, source, tank, 1, **args)
        assert first["changes"] == first["sent"] == 1 and not accepted
        assert "Event publication unavailable" in caplog.text
        down = False
        module._retry_after = 0
        await tick(store, source, tank, 1, **args)
        await tick(store, source, tank, 2, **args)
    assert len(accepted) == len(catcher.messages) == 1
    (row,) = await store.changes(TENANT)
    assert row["event_sent"]
    assert accepted[0]["source_id"] == module.source_id(row["id"])
    assert accepted[0]["facts"] == dict(
        source="federal-register",
        document_id=row["document"]["id"],
        date="2026-10-08",
        link=FR[0]["html_url"],
    )
    assert await store.changes(OTHER) == []
    import os

    if os.environ.get("ZENAS_ALERT_EVIDENCE_DIR"):
        path = Path(os.environ["ZENAS_ALERT_EVIDENCE_DIR"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "bus-review.json").write_text(
            json.dumps(
                {
                    "tenant_id": TENANT,
                    "change_id": row["id"],
                    "events": accepted,
                    "emails": len(catcher.messages),
                    "transport": "offline HTTP bus boundary",
                },
                indent=2,
            )
            + "\n"
        )


def test_bus_accepts_both_shapes_and_no_client_text():
    from event_bus import FACTS

    validate = FACTS["zenas.regulation.changed"].model_validate
    validate(dict(document_id="a" * 64, version_id="b" * 64))
    facts = dict(
        source="federal-register",
        document_id="public-document",
        date="2026-10-08",
        link=FR[0]["html_url"],
    )
    assert validate(facts).model_dump(mode="json") == facts
    with pytest.raises(ValidationError):
        validate({**facts, "summary": "client text"})
    with pytest.raises(ValidationError):
        validate({**facts, "link": "https://untrusted.example/"})


@pytest.mark.asyncio
async def test_older_publication_is_baseline_even_if_discovered_later():
    store, watch, http, tank = await setup_watch()
    await tick(store, http, tank, 0)
    http.rows = [FR[2]]
    assert (await tick(store, http, tank, 2))["changes"] == 0
    assert not await store.changes(TENANT)


@pytest.mark.asyncio
async def test_legiscan_saved_search_paging_and_bills(monkeypatch):
    monkeypatch.setenv("LEGISCAN_API_KEY", "LEGISCAN_API_KEY")
    saved = json.loads((SAVED / "saved/legiscan/search.json").read_text())[
        "searchresult"
    ]
    bills = json.loads((SAVED / "saved/legiscan/bills.json").read_text())
    calls = []

    async def api(params):
        calls.append(params)
        if params["op"] == "getSearch":
            index = str(params["page"] - 1)
            return {
                "status": "OK",
                "searchresult": {"summary": {"count": 2}, index: saved[index]},
            }
        return bills[str(params["id"])]

    rows = await Sources("us-east", legiscan_get=api).fetch(
        dict(source="legiscan", topic="consumer", options={"state": "CA"})
    )
    assert [p["op"] for p in calls] == ["getSearch", "getSearch", "getBill", "getBill"]
    assert len(rows) == 2 and all(
        r["kind"] == "bill" and r["link"].startswith("https://legiscan.com/")
        for r in rows
    )
    assert rows[0]["date"] == "2024-03-02"

    async def bad(params):
        return {"status": "ERROR"}

    with pytest.raises(FetchNotConfigured):
        await Sources("us-east", legiscan_get=bad).fetch(
            dict(source="legiscan", topic="consumer", options={"state": "CA"})
        )


@pytest.mark.asyncio
async def test_legiscan_key_parameter_and_error_redaction(monkeypatch):
    import urllib.request
    from urllib.parse import urlsplit, parse_qs

    monkeypatch.setenv("LEGISCAN_API_KEY", "LEGISCAN_API_KEY")
    calls = []

    class Opener:
        def open(self, request, timeout):
            params = parse_qs(urlsplit(request.full_url).query)
            assert params["key"] == ["LEGISCAN_API_KEY"]
            assert params["op"] == ["getSearch"]
            calls.append(1)
            raise RuntimeError(request.full_url)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(FetchNotConfigured) as error:
        await legiscan_request({"op": "getSearch"})
    assert str(error.value) == "LEGISCAN_API_KEY" and "https://" not in str(error.value)
    assert calls == [1]


@pytest.mark.asyncio
async def test_legiscan_records_once_and_outage_keeps_snapshot(monkeypatch):
    from datetime import datetime, timezone, timedelta
    from agents.zenas.core.regulations import add_watch, run_daily
    from agents.zenas.core.regulation_store import MemoryWatches
    from test_alerts import TankStub

    monkeypatch.setenv("LEGISCAN_API_KEY", "LEGISCAN_API_KEY")
    search = json.loads((SAVED / "saved/legiscan/search.json").read_text())
    bills = json.loads((SAVED / "saved/legiscan/bills.json").read_text())
    failed = False

    async def api(params):
        if failed:
            raise OSError("offline outage")
        return search if params["op"] == "getSearch" else bills[str(params["id"])]

    sent = []

    class Mail:
        async def send(self, letter):
            sent.append(letter)

    store = MemoryWatches()
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await add_watch(
        store,
        tenant_id=TENANT,
        region="us-east",
        source="legiscan",
        topic="consumer",
        options={"state": "CA"},
        now=created,
    )
    source = Sources("us-east", legiscan_get=api)
    args = dict(region="us-east", transport=Mail(), recipient="watcher@example.test")
    for day in (100, 101):
        await run_daily(
            store, source, TankStub(), now=created + timedelta(days=day), **args
        )
    assert len(await store.changes(TENANT)) == len(sent) == 2
    failed = True
    result = await run_daily(
        store, source, TankStub(), now=created + timedelta(days=102), **args
    )
    assert (
        result["unavailable"] == 1
        and len(await store.changes(TENANT)) == len(sent) == 2
    )
