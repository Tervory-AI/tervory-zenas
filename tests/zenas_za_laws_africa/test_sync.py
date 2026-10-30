import json
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

import httpx
import pytest

from agents.zenas.pool.adapters import laws_africa_api as api
from agents.zenas.pool.api import create_app, finish_loads
from agents.zenas.pool.bulk import load_wave
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.laws_africa import MINIMUM
from agents.zenas.pool.nightly import run_nightly
from agents.zenas.pool.store import MemoryStore

RECORDINGS = Path(__file__).with_name("recordings")
ON = date(2026, 10, 28)
SOURCE = next(source for source in CATALOGUE if source.source_id == "laws-africa")


class Replay:
    def __init__(self):
        self.pages = [json.loads((RECORDINGS / f"works-{n}.json").read_text()) for n in (1, 2)]
        self.calls = []
        self.failure = None
        self.failures_left = 0
        self.html = (RECORDINGS / "section.html").read_bytes()
        self.on_content = None

    @property
    def works(self):
        return [row for page in self.pages for row in page["results"]]

    async def get(self, url, headers):
        assert headers["Authorization"] == "Bearer LAWS_AFRICA_API_TOKEN"
        assert "LAWS_AFRICA_API_TOKEN" not in url
        self.calls.append(url)
        if url == self.failure and self.failures_left:
            self.failures_left -= 1
            raise HTTPError(url, 429, "not persisted", {"Retry-After": "3"}, None)
        if url == api.WORKS:
            return json.dumps(self.pages[0]).encode()
        if url == api.WORKS + "?page=2":
            return json.dumps(self.pages[1]).encode()
        if url.endswith("/toc.json"):
            return (RECORDINGS / "toc.json").read_bytes()
        if ".html?" in url:
            if self.on_content:
                await self.on_content(url)
            return self.html
        for work in self.works:
            if url == api.API + work["frbr_uri"].lstrip("/") + ".json":
                return json.dumps(work).encode()
        raise AssertionError("unexpected request: " + url)


def row(report, source_id="laws-africa"):
    return next(item for item in report["sources"] if item["source_id"] == source_id)


@pytest.mark.asyncio
async def test_full_listing_keeps_minimum_loads_regulation_and_excludes_municipal(clock_and_token):
    replay, store = Replay(), MemoryStore()
    report = await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    assert row(report)["last_status"] == "ok", report
    assert row(report)["documents"] == 12
    assert all(row(report, source_id)["documents"] == 1 for source_id in MINIMUM.values())
    assert api.WORKS + "?page=2" in replay.calls
    assert not any("page_size" in url or "za-cpt" in url for url in replay.calls)
    assert any(doc.kind == "regulation" for doc in store.docs.values())
    assert all(doc.body == "Laws.Africa" and "Laws.Africa" in doc.licence for doc in store.docs.values())
    assert await store.get_checkpoint("laws-africa") == ""
    assert clock_and_token == [1.0] * len(replay.calls)

    replay.calls.clear()
    second = await run_nightly(store, region="za", only=["laws-africa"], live=True, http_get=replay.get, on=ON)
    assert row(second)["last_status"] == "ok"
    assert replay.calls == [api.WORKS, api.WORKS + "?page=2"]
    assert await store.version_count() == 12


@pytest.mark.asyncio
async def test_nightly_fetches_only_changed_expressions_and_preserves_dates(clock_and_token):
    replay, store = Replay(), MemoryStore()
    await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    work = replay.pages[0]["results"][0]
    expression = {"language": "eng", "url": api.API + work["frbr_uri"].lstrip("/") + "/eng@2026-10-29"}
    work["points_in_time"].append({"date": "2026-10-29", "expressions": [expression]})
    replay.calls.clear()
    changed = await run_nightly(store, region="za", only=["laws-africa"], live=True, http_get=replay.get, on=date(2026, 10, 30))
    assert row(changed)["last_status"] == "ok"
    assert [url for url in replay.calls if url != api.WORKS and "?page=2" not in url] == [
        api.representation(expression["url"], ".html"), api.representation(expression["url"], "/toc.json")]
    versions = sorted((doc for doc in store.docs.values() if doc.source_id == "companies-act"), key=lambda doc: doc.effective_from)
    assert [doc.effective_from for doc in versions] == [date(2024, 1, 1), date(2026, 10, 29)]
    assert versions[0].effective_to == date(2026, 10, 29)
    assert versions[1].effective_to is None
    # Same section words in a new consolidation still retain the new edition date/link.
    assert versions[0].text == versions[1].text
    assert "2026-10-29" in versions[1].url
    earlier = await store.find_by_citation("Act 71 of 2008, s 66", jurisdiction="za", as_at=ON)
    assert earlier and earlier[0].version_id == versions[0].version_id


@pytest.mark.asyncio
async def test_persistent_cursor_resumes_after_rate_limit_without_refetching_saved_content(clock_and_token):
    replay, store = Replay(), MemoryStore()
    failed = replay.pages[0]["results"][1]["points_in_time"][0]["expressions"][0]["url"]
    replay.failure, replay.failures_left = api.representation(failed, ".html"), 6
    report = await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    assert row(report)["last_status"] == "rate_limited"
    assert row(report, "companies-act")["documents"] == 1
    assert json.loads(await store.get_checkpoint("laws-africa"))["pending"][0]["frbr_uri"] == "/akn/za/act/2008/68"
    replay.calls.clear()
    # New process/client, same durable store state, no in-memory adapter cursor.
    report = await run_nightly(store, region="za", only=["laws-africa"], live=True, http_get=replay.get, on=ON)
    assert row(report)["last_status"] == "ok"
    assert row(report)["documents"] == 12
    assert api.WORKS not in replay.calls
    assert not any("/2008/71/" in url for url in replay.calls)
    assert "Bearer" not in str(store.checkpoints)


@pytest.mark.asyncio
async def test_missing_minimum_is_requested_from_details_and_not_silently_omitted(clock_and_token):
    replay, store = Replay(), MemoryStore()
    missing = replay.pages[0]["results"].pop(0)
    details_url = api.API + missing["frbr_uri"].lstrip("/") + ".json"
    original = replay.get

    async def get(url, headers):
        if url == details_url:
            replay.calls.append(url)
            return json.dumps(missing).encode()
        return await original(url, headers)

    report = await load_wave(store, region="za", only=[SOURCE], live=True, http_get=get, on=ON)
    assert row(report)["last_status"] == "ok"
    assert details_url in replay.calls
    assert row(report, "companies-act")["documents"] == 1


@pytest.mark.asyncio
async def test_actual_load_and_status_routes_show_progress_and_missing_key(monkeypatch, clock_and_token):
    replay, store = Replay(), MemoryStore()
    app = create_app(store, region="za", http_get=replay.get, live=True)
    during = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
        async def check_progress(_url):
            status = (await client.get("/pool/status")).json()
            during.append(row(status)["documents"])

        replay.on_content = check_progress
        start = await client.post("/pool/sources/laws-africa/load")
        assert start.status_code == 200
        assert start.json() == {"ok": True, "status": "running"}
        await finish_loads()
        assert row((await client.get("/pool/status")).json())["documents"] == 12
        assert any(0 < count < 12 for count in during)
        monkeypatch.delenv("LAWS_AFRICA_API_TOKEN")
        assert row((await client.get("/pool/status")).json())["last_status"] == "not_configured"
        replay.calls.clear()
        await client.post("/pool/sources/laws-africa/load")
        await finish_loads()
        assert replay.calls == []


@pytest.mark.asyncio
async def test_saflii_remains_off_even_when_load_is_requested(clock_and_token):
    calls = []

    async def get(url, headers):
        calls.append(url)
        raise AssertionError("SAFLII must not be fetched")

    source = next(item for item in CATALOGUE if item.source_id == "saflii")
    report = await load_wave(MemoryStore(), region="za", only=[source], live=True, http_get=get, on=ON)
    assert not source.enabled
    assert row(report, "saflii")["last_status"] == "skipped"
    assert calls == []


@pytest.mark.asyncio
async def test_bad_token_through_load_route_does_not_log_response_or_create_checkpoint(clock_and_token):
    async def get(url, headers):
        raise HTTPError(url, 403, "LAWS_AFRICA_API_TOKEN must never be echoed as a value", {}, None)

    store = MemoryStore()
    app = create_app(store, region="za", http_get=get, live=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
        await client.post("/pool/sources/laws-africa/load")
        await finish_loads()
        result = row((await client.get("/pool/status")).json())
    assert result["last_status"] == "not_configured"
    assert result["detail"] == api.CONTENT_API_403
    assert store.docs == {} and store.checkpoints == {}


@pytest.mark.asyncio
async def test_changed_expression_url_at_same_date_is_refetched(clock_and_token):
    replay, store = Replay(), MemoryStore()
    await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    expression = replay.pages[0]["results"][0]["points_in_time"][0]["expressions"][0]
    expression["url"] += ".json"
    replay.calls.clear()
    report = await run_nightly(store, region="za", only=["laws-africa"], live=True, http_get=replay.get, on=ON)
    assert row(report)["last_status"] == "ok"
    assert sum(".html?" in url for url in replay.calls) == 1
    assert await store.version_count("companies-act") == 2


@pytest.mark.asyncio
async def test_partial_language_failure_is_not_left_running_and_resumes(clock_and_token):
    replay, store = Replay(), MemoryStore()
    point = replay.pages[0]["results"][0]["points_in_time"][0]
    point["expressions"].append({"language": "zul", "url": point["expressions"][0]["url"].replace("/eng@", "/zul@")})
    replay.failure = api.representation(point["expressions"][1]["url"], ".html")
    replay.failures_left = 6
    report = await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    assert row(report, "companies-act")["last_status"] == "rate_limited"
    replay.calls.clear()
    report = await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
    assert row(report)["last_status"] == "ok"
    assert not any("/2008/71/eng@" in url for url in replay.calls)
    assert await store.instrument_count("companies-act") == 2


@pytest.mark.asyncio
async def test_paging_cycle_and_foreign_next_url_do_not_reach_another_host(clock_and_token):
    for next_url in (api.WORKS, "https://other.org/page/2"):
        replay, store = Replay(), MemoryStore()
        replay.pages[0]["next"] = next_url
        report = await load_wave(store, region="za", only=[SOURCE], live=True, http_get=replay.get, on=ON)
        assert row(report)["last_status"] == "error"
        assert all(url.startswith(api.API) for url in replay.calls)
