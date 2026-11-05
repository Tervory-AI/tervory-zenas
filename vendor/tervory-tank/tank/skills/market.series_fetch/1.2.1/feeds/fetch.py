"""Live fetches. One source at a time. Keys stay in the environment."""

from __future__ import annotations

import hashlib
import io
import json
from calendar import monthrange
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from feeds.adapters import (
    ADAPTERS,
    _emit_tally,
    _ingest_census_population,
    ingest_alerts,
    ingest_census_trade,
    ingest_climate,
    ingest_legiscan,
    ingest_state_month_pdf,
    tally_listing,
)
from feeds.geo import STATE_FIPS
from feeds.licence import require_copy
from feeds.live import (
    PoliteClient,
    census_population_urls,
    census_trade_urls,
    complete_listing_link,
    ecfr_structure_url,
    eia_diesel_url,
    federal_register_url,
    legiscan_urls,
    ncei_url,
    nws_alerts_url,
    require_env,
)
from feeds.models import SourceSpec
from feeds.reader import iter_dicts
from feeds.store import Store, utc_now


class MemoryReader:
    def __init__(self, text: str) -> None:
        self.text = text

    def read_text(self, relative: str) -> str:
        return self.text

    def iter_rows(self, relative: str, *, header_marker: str | None = None):
        return iter_dicts(io.StringIO(self.text), header_marker=header_marker)


def watch_page(source: SourceSpec, client: PoliteClient, store: Store) -> int:
    """Store a content hash and the link. Never the page text."""
    body = client.get(source.url)
    digest = hashlib.sha256(body).hexdigest()
    if store.latest_event_body(source.id, "changed") == digest:
        return 0
    stored = store.put_event(
        source.id,
        "changed",
        "US",
        utc_now()[:10],
        "changed",
        digest,
        source.url,
    )
    return int(stored)


def fetch_source(
    source: SourceSpec,
    client: PoliteClient,
    store: Store,
    *,
    backfill: bool,
) -> int:
    require_copy(source)
    handler = _HANDLERS.get(source.adapter)
    if handler is None:
        raise RuntimeError(f"no live fetch for {source.adapter}")
    return handler(source, client, store, backfill=backfill)


def _live_listing(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    columns = source.config["columns"]
    months = _listing_months(source, client, backfill=backfill)
    if not months:
        return 0
    write_from = 0 if backfill or len(months) == 1 else len(months) - 1
    inserted = 0
    prior: dict[str, set[str]] | None = None
    marker = str(columns["id_fields"][0])
    for index, (stamp, url) in enumerate(months):
        tally = tally_listing(
            iter_dicts(client.iter_lines(url), header_marker=marker),
            columns,
            {},
        )
        if index >= write_from:
            inserted += _emit_tally(source, store, tally, f"{stamp}-01", stamp, prior)
        prior = tally.ids_by_state
    return inserted


def _listing_months(
    source: SourceSpec,
    client: PoliteClient,
    *,
    backfill: bool,
) -> list[tuple[str, str]]:
    template = str(source.config.get("month_page") or "")
    if "{year}" not in template or "{month}" not in template:
        raise RuntimeError("no month page template")
    today = datetime.now(timezone.utc).date()
    if backfill:
        found: list[tuple[str, str]] = []
        year = int(source.config.get("start_year") or today.year)
        month = 1
        while (year, month) <= (today.year, today.month):
            link = _one_listing(client, template, year, month)
            if link is not None:
                found.append(link)
            year, month = _shift(year, month, 1)
        return found
    found = []
    year, month = today.year, today.month
    for _ in range(36):
        link = _one_listing(client, template, year, month)
        if link is not None:
            found.append(link)
            if len(found) == 2:
                break
        year, month = _shift(year, month, -1)
    found.reverse()
    return found


def _one_listing(
    client: PoliteClient,
    template: str,
    year: int,
    month: int,
) -> tuple[str, str] | None:
    page = template.format(year=year, month=month)
    try:
        return complete_listing_link(client.get_text(page), page)
    except RuntimeError as exc:
        if "HTTP 404 " in str(exc):
            return None
        raise


def _live_typed(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    if source.url.lower().split("?", 1)[0].endswith(".pdf"):
        return ingest_state_month_pdf(source, store, client.get(source.url))
    return _live_table(source, client, store, backfill=backfill)


def _live_population(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    key = require_env("CENSUS_API_KEY")
    raw_years = source.config.get("years") or (range(int(source.config.get("start_year", 2009)), datetime.now(timezone.utc).year - 1) if backfill else [datetime.now(timezone.utc).year - 2])
    years = [int(year) for year in raw_years]
    inserted = 0
    for label, url in census_population_urls(key, years):
        year = label.split("-")[1]
        inserted += _ingest_census_population(
            source, store, client.get_text(url), label, year=year,
        )
    return inserted


def _live_trade(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    key = require_env("CENSUS_API_KEY")
    today = datetime.now(timezone.utc).date()
    months = [str(item) for item in source.config.get("months") or []]
    if not months:
        if backfill:
            first = str(source.config.get("start_month") or "2013-01")
            count = (today.year - int(first[:4])) * 12 + today.month - int(first[5:7])
            months = _recent_months(count, today)
        else:
            months = _recent_months(1, today)
    codes = [str(code) for code in source.config["codes"]]
    inserted = 0
    for flow in ("import", "export"):
        for label, url in census_trade_urls(key, months, codes, flow):
            inserted += ingest_census_trade(
                source, store, json.loads(client.get_text(url)), flow, label,
            )
    return inserted


def _live_legiscan(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    key = require_env("LEGISCAN_API_KEY")
    topics = [str(topic) for topic in source.config.get("topics") or []]
    cap = 10000 if backfill else 1
    inserted = 0
    for _label, url in legiscan_urls(key, topics):
        for page in range(1, cap + 1):
            page_url = url if page == 1 else f"{url}&page={page}"
            payload = json.loads(client.get_text(page_url))
            inserted += ingest_legiscan(source, store, payload)
            summary = (payload.get("searchresult") or {}).get("summary") or {}
            try:
                total = int(summary.get("page_total") or 1)
            except (TypeError, ValueError):
                total = 1
            if page >= total:
                break
    return inserted


def _live_register(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    url: str | None = federal_register_url([str(item) for item in source.config.get("agencies") or []])
    cap = 10000 if backfill else 1
    inserted = 0
    seen = 0
    visited = set()
    while url and seen < cap:
        from urllib.parse import urlsplit
        if url in visited or urlsplit(url).hostname != "www.federalregister.gov":
            raise RuntimeError("invalid source pagination")
        visited.add(url)
        payload = json.loads(client.get_text(url))
        inserted += _json_adapter("federal_register", source, store, payload, f"page-{seen}.json")
        url = payload.get("next_page_url") or None
        seen += 1
    return inserted


def _live_ecfr(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    import re
    title = str(source.config.get("title_number") or "49")
    dates = [datetime.now(timezone.utc).date().isoformat()]
    if backfill:
        versions = json.loads(client.get_text(f"https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json"))
        dates = sorted({str(item.get("date", "")) for item in versions.get("content_versions", [])
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(item.get("date", "")))})
        if not dates:
            raise RuntimeError("Source did not supply version dates")
    inserted = 0
    for date in dates:
        url = ecfr_structure_url(title).replace("/current/", f"/{date}/")
        payload = json.loads(client.get_text(url))
        payload["date"] = date
        inserted += _json_adapter("ecfr", source, store, payload, f"structure-{date}.json")
    return inserted


def _live_eia(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    key = require_env("EIA_API_KEY")
    series = source.config["series"]
    url = eia_diesel_url(key, str(series["product"]), str(series["duoarea"]))
    payload = json.loads(client.get_text(url))
    return _json_adapter("eia", source, store, payload, "live.json")


def _live_world_bank(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    from feeds.published import commodity_rows, published_tables
    config = source.config
    tables = published_tables(client, config["download_page"], link_terms=("historical", "monthly"))
    inserted = 0
    for spec, date, value in commodity_rows(tables, config["columns"]):
        series = store.ensure_series(source.id, spec["ref"], spec["title"], spec["unit"], "national", "World", "monthly")
        inserted += store.put_point(series, date, value, date)
    return inserted


def _live_alerts(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    from urllib.parse import urlsplit
    url = "https://api.weather.gov/alerts?status=actual&limit=500" if backfill else nws_alerts_url()
    visited = set()
    inserted = 0
    while url:
        if url in visited or urlsplit(url).hostname != "api.weather.gov":
            raise RuntimeError("invalid source pagination")
        visited.add(url)
        payload = json.loads(client.get_text(url, headers={"Accept": "application/geo+json"}))
        inserted += ingest_alerts(source, store, payload)
        url = (payload.get("pagination") or {}).get("next") if backfill else None
    return inserted


def _live_climate(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    token = require_env("NOAA_TOKEN")
    datatypes = [str(item) for item in source.config.get("datatypes") or ["TAVG", "PRCP"]]
    today = datetime.now(timezone.utc).date()
    if backfill:
        start = str(source.config.get("start") or "2016-01-01")
        windows = _year_chunks(start, today.isoformat())
    else:
        year, month = _shift(today.year, today.month, -1)
        windows = [(_day(year, month, 1), _day(year, month, monthrange(year, month)[1]))]
    inserted = 0
    headers = {"token": token}
    for postal, fips in STATE_FIPS.items():
        for datatype in datatypes:
            for start, end in windows:
                url = ncei_url(datatype, fips, start, end)
                # A page boundary must not turn one state's monthly mean into
                # several conflicting partial means.
                results = [row for page in _climate_pages(client, url, headers) for row in page.get("results", [])]
                inserted += ingest_climate(
                    source, store, {"results": results}, state=postal, revision=f"{start}:{end}",
                )
    return inserted


def _climate_pages(
    client: PoliteClient,
    url: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    offset = 1
    for _ in range(10000):
        page_url = url if offset == 1 else f"{url}&offset={offset}"
        payload = json.loads(client.get_text(page_url, headers=headers))
        pages.append(payload)
        meta = (payload.get("metadata") or {}).get("resultset") or {}
        try:
            count = int(meta.get("count") or 0)
            limit = int(meta.get("limit") or 0)
            current = int(meta.get("offset") or offset)
        except (TypeError, ValueError):
            break
        if limit <= 0 or current + limit - 1 >= count:
            break
        offset = current + limit
    return pages


def _live_table(source: SourceSpec, client: PoliteClient, store: Store, *, backfill: bool) -> int:
    if source.config.get("headings"):
        from feeds.published import published_tables, table_rows
        tables = published_tables(client, str(source.config.get("file_url") or source.url),
                                  link_terms=tuple(source.config.get("download_terms", [])))
        inserted = 0
        for stamp, geo, value in table_rows(tables, source.config):
            ref = str(source.config.get("ref") or geo)
            series = store.ensure_series(source.id, ref, str(source.config["title"]).format(geo=geo, year=stamp[:4]),
                                         source.config["unit"], source.config.get("geo_level", "national"),
                                         geo, source.config.get("frequency", "monthly"))
            inserted += store.put_point(series, stamp, value, stamp)
        if inserted == 0:
            raise RuntimeError("No matching published table was found")
        return inserted
    target = str(source.config.get("file_url") or source.url)
    text = client.get_text(target)
    head = text.lstrip()[:300].lower()
    if "<html" in head or head.startswith("<!doctype"):
        raise RuntimeError("response is not a table")
    spec = replace(source, config={**source.config, "files": ["live.csv"]})
    adapter = ADAPTERS[source.adapter]
    return adapter(spec, MemoryReader(text), store, backfill=True)


def _json_adapter(name: str, source: SourceSpec, store: Store, payload: object, filename: str) -> int:
    spec = replace(source, config={**source.config, "files": [filename]})
    return ADAPTERS[name](spec, MemoryReader(json.dumps(payload)), store, backfill=True)


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return year, month


def _day(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _recent_months(count: int, today: Any) -> list[str]:
    months: list[str] = []
    year, month = today.year, today.month
    for _ in range(count):
        year, month = _shift(year, month, -1)
        months.append(f"{year:04d}-{month:02d}")
    months.reverse()
    return months


def _year_chunks(start: str, end: str) -> list[tuple[str, str]]:
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    chunks: list[tuple[str, str]] = []
    year, month = sy, sm
    while (year, month) <= (ey, em):
        end_year, end_month = _shift(year, month, (9 * 12) - 1)
        if (end_year, end_month) > (ey, em):
            end_year, end_month = ey, em
        chunks.append((
            _day(year, month, 1),
            _day(end_year, end_month, monthrange(end_year, end_month)[1]),
        ))
        year, month = _shift(end_year, end_month, 1)
    return chunks


_HANDLERS = {
    "listing": _live_listing,
    "typed_counts": _live_typed,
    "census_population": _live_population,
    "census_trade": _live_trade,
    "legiscan": _live_legiscan,
    "federal_register": _live_register,
    "ecfr": _live_ecfr,
    "eia": _live_eia,
    "world_bank": _live_world_bank,
    "noaa_alerts": _live_alerts,
    "noaa_climate": _live_climate,
    "port_csv": _live_table,
    "annual_csv": _live_table,
    "month_index": _live_table,
}
