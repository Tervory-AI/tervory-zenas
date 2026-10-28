import json
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

import pytest

from agents.zenas.pool.adapters import laws_africa_api as api
from agents.zenas.pool.adapters.laws_africa_html import parse_expression
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.region import RegionError

RECORDINGS = Path(__file__).with_name("recordings")


@pytest.mark.asyncio
async def test_bearer_and_pacing_without_page_size(clock_and_token):
    seen = []

    async def get(url, headers):
        seen.append((url, headers))
        return (RECORDINGS / "works-1.json").read_bytes()

    client = api.LawsAfricaClient(region="za", http_get=get)
    result = await client.json(api.WORKS)
    assert result["next"] == api.WORKS + "?page=2"
    assert seen == [(api.WORKS, {"Authorization": "Bearer LAWS_AFRICA_API_TOKEN",
        "Accept": "application/json", "User-Agent": api.bearer_headers("", "")["User-Agent"]})]
    assert clock_and_token == [1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [429, 500, 503, 599])
async def test_backoff_honours_retry_after_without_capping_it(clock_and_token, code):
    calls = 0

    async def get(url, headers):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(url, code, "", {"Retry-After": "601"}, None)
        return b"{}"

    assert await api.LawsAfricaClient(region="za", http_get=get).json(api.WORKS) == {}
    assert calls == 2
    assert clock_and_token == [1.0, 601.0, 1.0]


@pytest.mark.asyncio
async def test_bad_token_uses_existing_not_configured_exception(clock_and_token):
    async def get(url, headers):
        raise HTTPError(url, 401, "private response body must not be shown", {}, None)

    with pytest.raises(FetchNotConfigured, match="^HTTP 401 from api.laws.africa$"):
        await api.LawsAfricaClient(region="za", http_get=get).json(api.WORKS)


def test_us_region_refuses_before_reading_token(monkeypatch):
    def forbidden():
        pytest.fail("US must not read the token")

    monkeypatch.setattr(api, "laws_africa_token", forbidden)
    with pytest.raises(RegionError):
        api.LawsAfricaClient(region="us-east")


@pytest.mark.parametrize("url", ["http://api.laws.africa/v3/akn/za/.json", "https://other.org/v3/akn/za/.json",
    "https://api.laws.africa/v3/akn/za-cpt/.json", "https://api.laws.africa/v3/akn/za/.json?token=", "https://user@api.laws.africa/v3/akn/za/.json"])
def test_untrusted_paging_and_expression_urls_are_refused(url):
    with pytest.raises((ValueError, FetchNotConfigured)):
        api.api_url(url)


def test_current_html_keeps_section_link_version_date_and_publisher():
    work = json.loads((RECORDINGS / "works-1.json").read_text())["results"][0]
    expression = api.expressions(work, on=date(2026, 10, 28))[0]
    source = next(row for row in CATALOGUE if row.source_id == "companies-act")
    payload = {"work": work, "expression": expression, "toc": json.loads((RECORDINGS / "toc.json").read_text()),
               "html": (RECORDINGS / "section.html").read_text()}
    document, = parse_expression(payload, source)
    assert document.citation == "Act 71 of 2008, s 66"
    assert document.effective_from == date(2024, 1, 1)
    assert document.date == date(2008, 1, 1)
    assert document.body == "Laws.Africa" and "Laws.Africa" in document.licence
    assert document.url == api.representation(expression["url"], ".html") + "#sec_66"
    assert "direction of its board" in document.text


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_transport", [False, True])
async def test_real_http_transport_disables_redirects_before_sending_bearer_elsewhere(monkeypatch, clock_and_token, explicit_transport):
    from agents.zenas.pool import http

    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            raise HTTPError(request.full_url, 302, "", {"Location": "https://other.org/"}, None)

    def opener(handler):
        assert isinstance(handler, http._NoRedirect)
        assert handler.redirect_request(None, None, 302, "", {}, "https://other.org/") is None
        return Opener()

    monkeypatch.setattr(http.urllib.request, "build_opener", opener)
    with pytest.raises(FetchNotConfigured, match="^HTTP 302 from api.laws.africa$"):
        await api.LawsAfricaClient(region="za", http_get=http.urllib_get if explicit_transport else None).json(api.WORKS)
    assert len(requests) == 1 and requests[0].full_url == api.WORKS


def test_only_za_compose_forwards_the_token_setting():
    import yaml

    root = Path(__file__).resolve().parents[2]
    za = yaml.safe_load((root / "agents/zenas/docker-compose.za.yml").read_text())
    us = yaml.safe_load((root / "agents/zenas/docker-compose.yml").read_text())
    assert za["services"]["agent-zenas"]["environment"]["LAWS_AFRICA_API_TOKEN"] == "${LAWS_AFRICA_API_TOKEN:-}"
    assert za["services"]["agent-zenas"]["environment"]["TANK_REGION"] == "za"
    assert all("LAWS_AFRICA_API_TOKEN" not in service.get("environment", {}) for service in us["services"].values())
