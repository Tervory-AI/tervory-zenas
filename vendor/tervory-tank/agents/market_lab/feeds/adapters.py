"""Parsers for the baseline feeds. Each one refuses to copy without a licence."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from decimal import Decimal
from datetime import date
from pathlib import Path
from typing import Any

from feeds.licence import require_copy
from feeds.models import SourceSpec
from feeds.reader import FixtureReader
from feeds.store import Store


def _files(source: SourceSpec, backfill: bool) -> list[Any]:
    files = list(source.config.get("files") or [])
    if not backfill and files:
        return files[-1:]
    return files


def _path_of(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    return str(entry["path"])


def _revision_of(entry: Any) -> str:
    return Path(_path_of(entry)).stem


def _parse_table(text: str) -> list[dict[str, str]]:
    sample = text.lstrip("\ufeff")
    delimiter = "\t" if "\t" in sample.splitlines()[0] else ","
    return list(csv.DictReader(io.StringIO(sample), delimiter=delimiter))


def _row_id(row: dict[str, str], columns: dict[str, Any]) -> str:
    return "-".join(row[name].strip() for name in columns["id_fields"])


def _crosswalk(reader: FixtureReader, relative: str | None) -> dict[tuple[str, str], tuple[str, str]]:
    if not relative:
        return {}
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for row in _parse_table(reader.read_text(relative)):
        zip_code = row["zip"].strip()[:5]
        found[(row["state"].strip(), zip_code)] = (row["county_fips"].strip(), row["county_name"].strip())
    return found


def run_listing_clean(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    """Month files in order. One month is streamed, then only its id set is kept."""
    require_copy(source)
    columns = source.config["columns"]
    crosswalk = _crosswalk(reader, source.config.get("crosswalk"))
    files = list(source.config.get("files") or [])
    if not files:
        return 0
    start = 0 if backfill else max(len(files) - 1, 0)
    inserted = 0
    prior_by_state: dict[str, set[str]] | None = None
    marker = str(columns["id_fields"][0])
    for index, entry in enumerate(files):
        revision = _revision_of(entry)
        date = _month_date(revision)
        tally = tally_listing(
            reader.iter_rows(_path_of(entry), header_marker=marker),
            columns,
            crosswalk,
        )
        if index >= start:
            inserted += _emit_tally(source, store, tally, date, revision, prior_by_state)
        prior_by_state = tally.ids_by_state
    return inserted


def tally_listing(
    rows: Any,
    columns: dict[str, Any],
    crosswalk: dict[tuple[str, str], tuple[str, str]],
) -> "ListingTally":
    """One pass. The row iterator is not stored."""
    by_state: dict[str, int] = defaultdict(int)
    by_county: dict[tuple[str, str], int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    ids_by_state: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        state = row[columns["state"]].strip()
        zip_code = row[columns["zip"]].strip()[:5]
        type_code = row[columns["type"]].strip()
        by_state[state] += 1
        by_type[type_code] += 1
        county = crosswalk.get((state, zip_code))
        key = ("unmatched", "unmatched") if county is None else county
        by_county[key] += 1
        ids_by_state[state].add(_row_id(row, columns))
    return ListingTally(by_state, by_county, by_type, ids_by_state)


class ListingTally:
    def __init__(
        self,
        by_state: dict[str, int],
        by_county: dict[tuple[str, str], int],
        by_type: dict[str, int],
        ids_by_state: dict[str, set[str]],
    ) -> None:
        self.by_state = by_state
        self.by_county = by_county
        self.by_type = by_type
        self.ids_by_state = ids_by_state


def _emit_tally(
    source: SourceSpec,
    store: Store,
    tally: ListingTally,
    date: str,
    revision: str,
    prior_by_state: dict[str, set[str]] | None,
) -> int:
    inserted = 0
    for state, count in sorted(tally.by_state.items()):
        inserted += _point(store, source, f"count:state:{state}", f"Active listings in {state}", "listings", "state", state, "monthly", date, count, revision)
    for (fips, name), count in sorted(tally.by_county.items()):
        inserted += _point(store, source, f"count:county:{fips}", f"Active listings in {name}", "listings", "county", fips, "monthly", date, count, revision)
    for type_code, count in sorted(tally.by_type.items()):
        inserted += _point(store, source, f"count:type:{type_code}", f"Active listings of type {type_code}", "listings", "national", "US", "monthly", date, count, revision)
    if prior_by_state is not None:
        states = sorted(set(tally.ids_by_state) | set(prior_by_state))
        for state in states:
            now = tally.ids_by_state.get(state, set())
            then = prior_by_state.get(state, set())
            inserted += _point(store, source, f"new:state:{state}", f"New listings in {state}", "listings", "state", state, "monthly", date, len(now - then), revision)
            inserted += _point(store, source, f"dropped:state:{state}", f"Dropped listings in {state}", "listings", "state", state, "monthly", date, len(then - now), revision)
    return inserted


def run_typed_counts(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        revision = _revision_of(entry)
        for row in _parse_table(reader.read_text(_path_of(entry))):
            geo = row[source.config["geo_column"]].strip()
            kind = row[source.config["type_column"]].strip()
            date = _month_date(row[source.config["date_column"]].strip())
            ref = f"{kind}:{geo}"
            title = str(source.config["title"]).format(type=kind, geo=geo)
            inserted += _point(
                store, source, ref, title,
                str(source.config["unit"]),
                str(source.config["geo_level"]),
                geo,
                str(source.config["frequency"]),
                date,
                row[source.config["value_column"]],
                revision,
            )
    return inserted


def run_census_population(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        inserted += _ingest_census_population(source, store, reader.read_text(_path_of(entry)), _revision_of(entry))
    return inserted


def _ingest_census_population(
    source: SourceSpec,
    store: Store,
    text: str,
    revision: str,
    *,
    year: str | None = None,
) -> int:
    table = json.loads(text)
    header = table[0]
    inserted = 0
    date = f"{year or source.config.get('year', '2023')}-01-01"
    for raw in table[1:]:
        row = dict(zip(header, raw))
        name = row["NAME"]
        value = row["B01003_001E"]
        if "county" in row:
            geo = row["state"] + row["county"]
            level = "county"
            ref = f"population:county:{geo}"
        else:
            geo = row["state"]
            level = "state"
            ref = f"population:state:{geo}"
        inserted += _point(store, source, ref, f"Population, {name}", "persons", level, geo, "annual", date, value, revision)
    return inserted


def run_census_trade(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    entries = list(source.config.get("files") or [])
    if not backfill and entries:
        entries = entries[-1:]
    for entry in entries:
        flow = entry["flow"] if isinstance(entry, dict) else source.config.get("flow", "import")
        inserted += ingest_census_trade(
            source, store, json.loads(reader.read_text(_path_of(entry))), str(flow), _revision_of(entry),
        )
    return inserted


def ingest_census_trade(
    source: SourceSpec,
    store: Store,
    table: list[Any],
    flow: str,
    revision: str,
) -> int:
    codes = {str(code) for code in source.config["codes"]}
    header = table[0]
    value_column = "GEN_VAL_MO" if "GEN_VAL_MO" in header else "ALL_VAL_MO"
    inserted = 0
    for raw in table[1:]:
        row = dict(zip(header, raw))
        code = str(row.get("I_COMMODITY") or row.get("E_COMMODITY") or "")
        if code not in codes:
            continue
        partner = row["CTY_NAME"]
        port = row["DIST_NAME"]
        date = _month_date(row["time"])
        ref = f"{flow}:{code}:{partner}:{port}"
        title = f"{flow} {code} from {partner} via {port}" if flow == "import" else f"{flow} {code} to {partner} via {port}"
        inserted += _point(store, source, ref, title, "dollars", "port", port, "monthly", date, row[value_column], revision)
    return inserted


def run_legiscan(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        inserted += ingest_legiscan(source, store, json.loads(reader.read_text(_path_of(entry))))
    return inserted


def ingest_legiscan(source: SourceSpec, store: Store, payload: dict[str, Any]) -> int:
    """getSearch rows use last_action and last_action_date.

    Saved fixtures that predate those fields are still filtered by topic.
    A live search already applied the topic, so those rows are kept.
    """
    topics = [str(topic) for topic in source.config.get("topics") or []]
    inserted = 0
    result = payload.get("searchresult") or {}
    for key, item in result.items():
        if key == "summary" or not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        real = "last_action" in item or "last_action_date" in item
        if not real and topics:
            haystack = f"{title} {item.get('keywords') or ''}".lower()
            if not any(topic.lower() in haystack for topic in topics):
                continue
        status = str(item.get("last_action") or item.get("status") or item.get("status_desc") or "status")
        number = str(item.get("bill_number") or item.get("bill_id") or "bill")
        state = str(item.get("state") or "US")
        date = str(item.get("last_action_date") or item.get("status_date") or "1970-01-01")[:10]
        url = str(item.get("url") or item.get("text_url") or source.url)
        inserted += int(store.put_event(
            source.id, "bill_status", state, date, f"{number} {status}", title, url,
        ))
    return inserted


def run_federal_register(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    wanted = set(source.config["agencies"])
    inserted = 0
    for entry in _files(source, backfill):
        payload = json.loads(reader.read_text(_path_of(entry)))
        for doc in payload.get("results") or []:
            slugs = {str(agency.get("slug")) for agency in doc.get("agencies") or []}
            if wanted and slugs.isdisjoint(wanted):
                continue
            inserted += int(store.put_event(
                source.id,
                "rule",
                "US",
                str(doc["publication_date"])[:10],
                str(doc["title"]),
                str(doc.get("abstract") or ""),
                str(doc.get("html_url") or source.url),
            ))
    return inserted


def run_ecfr(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    parts = {str(part) for part in source.config["parts"]}
    title = str(source.config["title_number"])
    inserted = 0
    for entry in _files(source, backfill):
        payload = json.loads(reader.read_text(_path_of(entry)))
        date = str(payload.get("date") or "1970-01-01")[:10]
        for node in _walk(payload):
            identifier = str(node.get("identifier") or "")
            if node.get("type") == "part" and identifier in parts:
                url = f"https://www.ecfr.gov/current/title-{title}/part-{identifier}"
                body = str(node.get("label_description") or node.get("label") or "")
                inserted += int(store.put_event(
                    source.id, "regulation", "US", date, f"{title} CFR {identifier}", body, url,
                ))
    return inserted


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for child in node.get("children") or []:
            found.extend(_walk(child))
    return found


def run_eia(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    series = source.config["series"]
    inserted = 0
    for entry in _files(source, backfill):
        payload = json.loads(reader.read_text(_path_of(entry)))
        data = payload.get("response", payload).get("data", [])
        for row in data:
            inserted += _point(
                store, source, str(series["id"]), str(series["title"]), str(series["unit"]),
                "national", "US", "weekly", str(row["period"])[:10], row["value"], _revision_of(entry),
            )
    return inserted


def run_world_bank(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    wanted = set(source.config["indicators"])
    inserted = 0
    for entry in _files(source, backfill):
        payload = json.loads(reader.read_text(_path_of(entry)))
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else payload
        for row in rows:
            if not isinstance(row, dict):
                continue
            indicator = row.get("indicator") or {}
            code = str(indicator.get("id") or "")
            if code not in wanted or row.get("value") is None:
                continue
            date = _world_bank_date(str(row.get("date")))
            metadata = next((item for item in source.config.get("columns", {}).values() if item["ref"] == code), None)
            if not metadata:
                raise ValueError("A commodity needs its published unit")
            title = str(metadata["title"])
            inserted += _point(
                store, source, code, title, str(metadata["unit"]), "national", "WLD", "monthly", date, row["value"], _revision_of(entry),
            )
    return inserted


def run_noaa_alerts(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        inserted += ingest_alerts(source, store, json.loads(reader.read_text(_path_of(entry))))
    return inserted


def ingest_alerts(source: SourceSpec, store: Store, payload: dict[str, Any]) -> int:
    inserted = 0
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        states = alert_states(props)
        sent = str(props.get("sent") or "")[:10]
        headline = str(props.get("headline") or props.get("event") or "Alert")
        url = str(props.get("id") or props.get("@id") or source.url)
        body = str(props.get("event") or "")
        for geo in states:
            inserted += int(store.put_event(source.id, "severe_weather", geo, sent, headline, body, url))
    return inserted


def alert_states(props: dict[str, Any]) -> list[str]:
    """Land zones become postal codes. Marine zones are ignored."""
    found: list[str] = []
    saw_zone = False
    geocode = props.get("geocode") or {}
    ugc = geocode.get("UGC") or []
    if isinstance(ugc, str):
        ugc = [ugc]
    if ugc:
        saw_zone = True
    for code in ugc:
        state = land_state(str(code))
        if state is not None:
            found.append(state)
    zones = props.get("affectedZones") or []
    if zones:
        saw_zone = True
    for zone_url in zones:
        tail = str(zone_url).rstrip("/").rsplit("/", 1)[-1]
        state = land_state(tail)
        if state is not None:
            found.append(state)
    if not saw_zone:
        return ["US"]
    unique: list[str] = []
    for state in found:
        if state not in unique:
            unique.append(state)
    return unique


def land_state(code: str) -> str | None:
    from feeds.geo import US_POSTAL_SET

    text = code.strip().upper()
    if len(text) < 6 or text[2] not in {"C", "Z"} or not text[3:].isdigit():
        return None
    prefix = text[:2]
    if prefix not in US_POSTAL_SET:
        return None
    return prefix


def run_noaa_climate(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        inserted += ingest_climate(
            source,
            store,
            json.loads(reader.read_text(_path_of(entry))),
            state=None,
            revision=_revision_of(entry),
        )
    return inserted


def ingest_climate(
    source: SourceSpec,
    store: Store,
    payload: dict[str, Any],
    *,
    state: str | None,
    revision: str,
) -> int:
    results = [row for row in (payload.get("results") or []) if isinstance(row, dict)]
    if any("station" in row for row in results):
        return _ingest_gsom(source, store, results, state=state, revision=revision)
    inserted = 0
    for row in results:
        row_state = str(row["state"])
        datatype = str(row["datatype"])
        unit = _climate_unit(datatype)
        inserted += _point(
            store, source, f"{datatype}:{row_state}", f"{datatype} in {row_state}", unit,
            "state", row_state, "monthly", str(row["date"])[:10], row["value"], revision,
        )
    return inserted


def _ingest_gsom(
    source: SourceSpec,
    store: Store,
    results: list[dict[str, Any]],
    *,
    state: str | None,
    revision: str,
) -> int:
    """Real GSOM rows are station observations. Average them to one state value."""
    if not state:
        raise RuntimeError("state is required for a GSOM climate response")
    grouped: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for row in results:
        if row.get("value") is None or row.get("value") == "":
            continue
        datatype = str(row.get("datatype") or "")
        date = str(row.get("date") or "")[:10]
        if not datatype or len(date) < 10:
            continue
        grouped[(datatype, date)].append(Decimal(str(row["value"])))
    inserted = 0
    for (datatype, date), values in sorted(grouped.items()):
        average = sum(values, Decimal(0)) / Decimal(len(values))
        inserted += _point(
            store, source, f"{datatype}:{state}", f"{datatype} in {state}", _climate_unit(datatype),
            "state", state, "monthly", date, average, revision,
        )
    return inserted


def _climate_unit(datatype: str) -> str:
    if datatype == "TAVG":
        return "degrees F"
    if datatype == "PRCP":
        return "inches"
    return "value"


def run_port_csv(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        for row in _parse_table(reader.read_text(_path_of(entry))):
            date = _month_date(row[source.config["month_column"]])
            inserted += _point(
                store, source, "teu", str(source.config["title"]), "TEU", "port",
                str(source.config["geo"]), "monthly", date, row[source.config["value_column"]], _revision_of(entry),
            )
    return inserted


def run_annual_csv(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        for row in _parse_table(reader.read_text(_path_of(entry))):
            geo = row[source.config["geo_column"]].strip()
            year = row[source.config["year_column"]].strip()
            date = f"{year}-01-01"
            title = str(source.config["title"]).format(geo=geo, year=year)
            inserted += _point(
                store, source, f"holders:{geo}", title, str(source.config["unit"]),
                "state", geo, "annual", date, row[source.config["value_column"]], _revision_of(entry),
            )
    return inserted


def run_month_index(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    require_copy(source)
    inserted = 0
    for entry in _files(source, backfill):
        for row in _parse_table(reader.read_text(_path_of(entry))):
            date = _month_date(row["month"])
            inserted += _point(
                store, source, str(source.config["ref"]), str(source.config["title"]),
                str(source.config["unit"]), "national", "US", "monthly", date, row["value"], _revision_of(entry),
            )
    return inserted


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_YEAR = re.compile(r"Year\s*(\d{4})", re.IGNORECASE)
_SKIP_COLUMN = {"grandtotal", "totals", "total"}
_SKIP_ROW = {"total", "totals", "grandtotal"}


def parse_state_month_pdf(data: bytes) -> list[tuple[str, int, str, str]]:
    """Published month-by-state tables. Returns year, month, postal code, value."""
    import pdfplumber

    from feeds.geo import STATE_BY_NAME, normalize_label

    found: list[tuple[str, int, str, str]] = []
    year: str | None = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            years = _YEAR.findall(text)
            if years:
                year = years[0]
            if year is None:
                continue
            for table in page.extract_tables() or []:
                found.extend(_rows_from_state_month_table(table, year, STATE_BY_NAME, normalize_label))
    return found


def _rows_from_state_month_table(
    table: list[list[Any]],
    year: str,
    names: dict[str, str],
    normalize: Any,
) -> list[tuple[str, int, str, str]]:
    header_index = None
    month_at: dict[int, int] = {}
    state_at = 0
    for index, row in enumerate(table):
        labels = [normalize(str(cell or "")) for cell in row]
        months = {position: _MONTHS[label] for position, label in enumerate(labels) if label in _MONTHS}
        if months and any(label.startswith("state") for label in labels):
            header_index = index
            month_at = months
            for position, label in enumerate(labels):
                if label.startswith("state"):
                    state_at = position
                    break
            break
    if header_index is None:
        return []
    found: list[tuple[str, int, str, str]] = []
    for row in table[header_index + 1 :]:
        if state_at >= len(row):
            continue
        label = normalize(str(row[state_at] or ""))
        if not label or label in _SKIP_ROW:
            continue
        postal = names.get(label)
        if postal is None:
            continue
        for position, month in month_at.items():
            if position >= len(row):
                continue
            header = normalize(str(table[header_index][position] or ""))
            if header in _SKIP_COLUMN:
                continue
            value = _plain_number(row[position])
            if value is None:
                continue
            found.append((year, month, postal, value))
    return found


def ingest_state_month_pdf(source: SourceSpec, store: Store, data: bytes) -> int:
    require_copy(source)
    kind = str(source.config.get("pdf_type") or "total")
    title_template = str(source.config.get("pdf_title") or "{geo}")
    unit = str(source.config.get("unit") or "count")
    inserted = 0
    for year, month, postal, value in parse_state_month_pdf(data):
        date = f"{year}-{month:02d}-01"
        title = title_template.format(geo=postal, type=kind)
        inserted += _point(
            store, source, f"{kind}:{postal}", title, unit,
            "state", postal, "monthly", date, value, year,
        )
    return inserted


def _plain_number(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text in {"-", "–", "—"}:
        return None
    try:
        Decimal(text)
    except Exception:
        return None
    return text


def _point(
    store: Store,
    source: SourceSpec,
    ref: str,
    title: str,
    unit: str,
    geo_level: str,
    geo: str,
    frequency: str,
    date: str,
    value: object,
    revision: str,
) -> int:
    series_id = store.ensure_series(source.id, ref, title, unit, geo_level, geo, frequency)
    return int(store.put_point(series_id, date, value, revision))


def _month_date(text: str) -> str:
    raw = text.strip()
    if len(raw) == 7 and raw[4] == "-":
        return f"{raw}-01"
    return raw[:10]


def _world_bank_date(text: str) -> str:
    if "M" in text:
        year, month = text.split("M", 1)
        return f"{year}-{int(month):02d}-01"
    if len(text) == 4 and text.isdigit():
        return f"{text}-01-01"
    return _month_date(text)


def run_socrata_monthly(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    """Monthly category counts from a licensed official Socrata dataset."""
    require_copy(source)
    inserted = 0
    config = source.config
    for entry in _files(source, backfill):
        rows = json.loads(reader.read_text(_path_of(entry)))
        if not isinstance(rows, list):
            raise ValueError("expected Socrata rows")
        for row in rows:
            month = _MONTHS[row[config["month_column"]].lower()]
            stamp = date(int(row[config["year_column"]]), month, 1).isoformat()
            category = row[config["type_column"]]
            title = config["titles"][category]
            value = Decimal(row[config["value_column"]])
            if not value.is_finite() or value < 0:
                raise ValueError("invalid monthly count")
            inserted += _point(store, source, category, title, config["unit"],
                               config["geo_level"], config["geo"], "monthly", stamp,
                               value, "published")
    return inserted


def run_sec_submissions(source: SourceSpec, reader: FixtureReader, store: Store, *, backfill: bool) -> int:
    """SEC's recent submissions window, one recorded response per filer.

    Older archive files are deliberately not followed: this source describes
    recent filings, not a complete company history. No filing HTML is read.
    """
    require_copy(source)
    inserted = 0
    allowed = {int(cik) for cik in source.config["ciks"]}
    for entry in source.config.get("files", []):
        payload = json.loads(reader.read_text(_path_of(entry)))
        cik = int(payload["cik"])
        name = payload["name"].strip()
        if cik not in allowed or not name:
            raise ValueError("unexpected SEC filer")
        recent = payload["filings"]["recent"]
        fields = ("accessionNumber", "filingDate", "form", "primaryDocDescription")
        columns = [recent[field] for field in fields]
        if not all(isinstance(column, list) for column in columns) or len({len(column) for column in columns}) != 1:
            raise ValueError("incomplete SEC filing columns")
        for accession, filed, form, description in zip(*columns):
            if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession) or not form.strip():
                raise ValueError("invalid SEC filing identity")
            if date.fromisoformat(filed).isoformat() != filed:
                raise ValueError("invalid SEC filing date")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.html"
            inserted += int(store.put_event(source.id, "filing", "US", filed,
                                            f"{name} — {form}", description, url))
    return inserted


from feeds.keyless import run_comtrade, run_rss, run_searxng


ADAPTERS = {
    "rss": run_rss,
    "comtrade_preview": run_comtrade,
    "searxng": run_searxng,
    "socrata_monthly": run_socrata_monthly,
    "sec_submissions": run_sec_submissions,
    "listing": run_listing_clean,
    "typed_counts": run_typed_counts,
    "census_population": run_census_population,
    "census_trade": run_census_trade,
    "legiscan": run_legiscan,
    "federal_register": run_federal_register,
    "ecfr": run_ecfr,
    "eia": run_eia,
    "world_bank": run_world_bank,
    "noaa_alerts": run_noaa_alerts,
    "noaa_climate": run_noaa_climate,
    "port_csv": run_port_csv,
    "annual_csv": run_annual_csv,
    "month_index": run_month_index,
}
