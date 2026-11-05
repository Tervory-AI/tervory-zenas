"""Series, gaps, surges, and predictions. No industry words and no database.

A skill package loads this file. FRED points are marked stored=False. The
ledger refuses them. Listing counts come from the feeds package.
"""

from __future__ import annotations

import csv
import io
import statistics
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


def series_fetch(payload: dict[str, Any]) -> dict[str, Any]:
    adapter = str(payload.get("adapter") or "")
    fetched_at = str(payload.get("fetched_at") or _now())
    if adapter == "fred":
        return _fred(payload, fetched_at)
    if adapter == "csv":
        return _table(payload, fetched_at, _csv_rows(str(payload.get("text") or "")))
    if adapter == "xlsx":
        return _table(payload, fetched_at, _xlsx_rows(payload))
    if adapter == "listing":
        return _listing(payload, fetched_at)
    raise ValueError(f"unknown adapter {adapter}")


def surge_detect(payload: dict[str, Any]) -> dict[str, Any]:
    points = _points(payload.get("points") or [])
    window = int(payload.get("window") or 6)
    z_min = float(payload.get("z_threshold") if payload.get("z_threshold") is not None else 2)
    pct_min = float(payload.get("pct_threshold") if payload.get("pct_threshold") is not None else 0.25)
    events = _surge_events(points, window, z_min, pct_min)
    return {"events": events}


def gap_score(payload: dict[str, Any]) -> dict[str, Any]:
    areas = list(payload.get("areas") or [])
    histories = [_area_history(area) for area in areas]
    dates = sorted({item[0] for history in histories for item in history})
    gap_by_area: dict[str, list[dict[str, Any]]] = {str(area.get("area")): [] for area in areas}
    latest: dict[str, dict[str, Any]] = {}
    for stamp in dates:
        snapshot = _snapshot(areas, histories, stamp)
        if len(snapshot) < 1:
            continue
        scored = _score_snapshot(snapshot)
        for row in scored:
            gap_by_area[row["area"]].append({"date": stamp, "value": row["gap"]})
            latest[row["area"]] = row
    ranked = sorted(latest.values(), key=lambda row: (-row["gap"], row["area"]))
    for index, row in enumerate(ranked, start=1):
        trend = _trend(gap_by_area[row["area"]])
        row["trend"] = trend
        row["rank"] = index
        row["recommendation"] = recommend(row["gap"], row["saturation"], trend.get("m3"))
        row["segment"] = row.get("segment") or "all"
    hot = max(ranked, key=lambda row: (row["supply_idx"], row["area"])) if ranked else None
    return {
        "ranked": ranked,
        "top_area": None if not ranked else ranked[0]["area"],
        "top_rank": None if not ranked else ranked[0]["rank"],
        "top_recommendation": None if not ranked else ranked[0]["recommendation"],
        "hot_area": None if hot is None else hot["area"],
        "hot_recommendation": None if hot is None else hot["recommendation"],
    }


def shift_detect(payload: dict[str, Any]) -> dict[str, Any]:
    """Flags a sharp gap change by running surge detection on the gap series."""
    window = int(payload.get("window") or 6)
    z_min = float(payload.get("z_threshold") if payload.get("z_threshold") is not None else 2)
    pct_min = float(payload.get("pct_threshold") if payload.get("pct_threshold") is not None else 0.25)
    found: list[dict[str, Any]] = []
    for area in payload.get("areas") or []:
        events = surge_detect(
            {
                "points": area.get("gap") or area.get("points") or [],
                "window": window,
                "z_threshold": z_min,
                "pct_threshold": pct_min,
            }
        )["events"]
        for event in events:
            kind = "bottleneck" if event["kind"] == "surge" else "saturating"
            found.append(
                {
                    "area": area.get("area"),
                    "segment": area.get("segment") or "all",
                    "date": event["date"],
                    "kind": kind,
                    "size": event["size"],
                }
            )
    return {"events": found}


def research_prompt(question: str) -> str:
    return (
        "Answer in short claims. For each claim use three lines: "
        "CLAIM, URL, DATE. Every claim needs a source URL and a date. "
        "Question: " + question.strip()
    )


def research_answer(payload: dict[str, Any]) -> dict[str, Any]:
    question = str(payload.get("question") or "").strip()
    citations = _citations(payload)
    claims = [item["claim"] for item in citations]
    if claims:
        answer = " ".join(claims)
    elif question:
        answer = ""
    else:
        answer = ""
    return {"question": question, "answer": answer, "citations": citations}


def recommend(gap: float, saturation: float, trend_m3: float | None) -> str:
    if gap <= 0 or saturation >= 0.7:
        return "reduce"
    if gap > 0 and saturation < 0.45 and (trend_m3 is None or trend_m3 >= 0):
        return "increase"
    return "hold"


def make_prediction(
    *,
    tenant_id: str,
    statement: str,
    metric_series_id: str,
    comparator: str,
    target: float,
    resolve_by: datetime,
    made_at: datetime,
    rationale: str,
    sources: list[dict[str, Any]] | None = None,
    prediction_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": prediction_id or _new_id(),
        "tenant_id": str(tenant_id),
        "made_at": _stamp(made_at),
        "statement": statement,
        "metric_series_id": metric_series_id,
        "comparator": comparator,
        "target": float(target),
        "resolve_by": _stamp(resolve_by),
        "rationale": rationale,
        "sources": list(sources or []),
        "outcome": None,
        "resolved_at": None,
        "score": None,
    }


def resolve_prediction(
    prediction: dict[str, Any],
    points: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any] | None:
    """Return a new row. The prediction that was made is not changed."""
    if prediction.get("outcome"):
        return None
    resolve_by = _parse_stamp(str(prediction["resolve_by"]))
    clock = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if clock < resolve_by:
        return None
    actual = _value_at(points, resolve_by.date())
    if actual is None:
        hit = False
    else:
        hit = _compare(actual, str(prediction["comparator"]), float(prediction["target"]))
    sources = list(prediction.get("sources") or [])
    sources.append({"resolves": prediction["id"]})
    return {
        "id": _new_id(),
        "tenant_id": prediction["tenant_id"],
        "made_at": prediction["made_at"],
        "statement": prediction["statement"],
        "metric_series_id": prediction["metric_series_id"],
        "comparator": prediction["comparator"],
        "target": prediction["target"],
        "resolve_by": prediction["resolve_by"],
        "rationale": prediction["rationale"],
        "sources": sources,
        "outcome": "hit" if hit else "miss",
        "resolved_at": _stamp(clock),
        "score": 1.0 if hit else 0.0,
    }


def correct_prediction(prediction: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A correction is a new row. The earlier row stays as it was made."""
    nxt = dict(prediction)
    nxt.update(changes)
    sources = list(prediction.get("sources") or [])
    sources.append({"corrects": prediction["id"]})
    nxt["sources"] = sources
    nxt["id"] = _new_id()
    nxt["outcome"] = None
    nxt["resolved_at"] = None
    nxt["score"] = None
    return nxt


def opportunity_rows(ranked: list[dict[str, Any]], products: list[dict[str, Any]], signals: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for row in ranked:
        if row["gap"] <= 0:
            continue
        signal = signals.get(row["area"]) or {}
        for product in products:
            if not signal.get(str(product.get("when"))):
                continue
            fit = float(product.get("fit") or 0)
            size = float(signal.get("size") or row.get("size") or 0)
            reach = float(row["reachability"])
            score = size * reach * max(float(row["gap"]), 0.0) * fit
            found.append(
                {
                    "area": row["area"],
                    "segment": row["segment"],
                    "product": product.get("id"),
                    "fit": fit,
                    "size": size,
                    "reachability": reach,
                    "gap": row["gap"],
                    "score": round(score, 4),
                    "evidence": {
                        "demand_idx": row["demand_idx"],
                        "supply_idx": row["supply_idx"],
                        "saturation": row["saturation"],
                        "recommendation": row["recommendation"],
                    },
                }
            )
    found.sort(key=lambda item: (-item["score"], str(item["area"]), str(item["product"])))
    for index, item in enumerate(found, start=1):
        item["rank"] = index
    return found


def areas_from_series(
    series_rows: list[dict[str, Any]],
    point_rows: list[dict[str, Any]],
    bindings: dict[str, list[dict[str, str]]],
    extras: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Turn stored series into gap inputs. The engine supplies the prefixes."""
    by_series: dict[str, list[dict[str, Any]]] = {}
    for point in point_rows:
        by_series.setdefault(str(point["series_id"]), []).append(point)
    buckets: dict[str, dict[str, Any]] = {}
    for row in series_rows:
        role = _role_of(row, bindings)
        if role is None:
            continue
        area = _area_of(str(row.get("ref") or ""), bindings.get(role) or [])
        if not area:
            continue
        bucket = buckets.setdefault(area, {"area": area, "segment": "all"})
        points = [
            {"date": str(item["date"])[:10], "value": _num(item["value"])}
            for item in by_series.get(str(row["id"]), [])
        ]
        if role in {"demand", "supply"}:
            bucket[role] = _merge_points(list(bucket.get(role) or []), points)
        elif points:
            bucket[role if role != "population" else "population"] = points[-1]["value"]
            if role == "competitors":
                bucket["competitors"] = points[-1]["value"]
    for area, extra in (extras or {}).items():
        bucket = buckets.setdefault(area, {"area": area, "segment": "all"})
        bucket.update(extra)
        bucket["area"] = area
    return [buckets[key] for key in sorted(buckets)]


def prediction_for_recommendation(
    row: dict[str, Any],
    *,
    tenant_id: str,
    made_at: datetime,
    targets: dict[str, float] | None = None,
) -> dict[str, Any]:
    word = str(row["recommendation"])
    target = float((targets or {}).get(word, {"increase": 5, "hold": 3, "reduce": 1}[word]))
    statement = f"{row['area']} / {row['segment']}: response rate above {target:g}% in 90 days"
    return make_prediction(
        tenant_id=tenant_id,
        statement=statement,
        metric_series_id=f"response:{row['area']}:{row['segment']}",
        comparator="gt",
        target=target,
        resolve_by=made_at + timedelta(days=90),
        made_at=made_at,
        rationale=f"{word} marketing",
        sources=[
            {
                "demand_idx": row["demand_idx"],
                "supply_idx": row["supply_idx"],
                "gap": row["gap"],
                "saturation": row["saturation"],
                "reachability": row["reachability"],
                "trend": row["trend"],
            }
        ],
    )


def _fred(payload: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    observations = payload.get("observations")
    if observations is None:
        observations = body.get("observations") or []
    points: list[dict[str, Any]] = []
    for item in observations:
        raw = item.get("value")
        if raw is None or str(raw).strip() in {"", "."}:
            continue
        points.append({"date": _date(item.get("date")), "value": _num(raw)})
    points.sort(key=lambda item: item["date"])
    series_id = str(payload.get("series_id") or payload.get("ref") or "")
    return {
        "series_id": series_id,
        "points": points,
        "source": "fred",
        "fetched_at": fetched_at,
        "stored": False,
    }


def _table(payload: dict[str, Any], fetched_at: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    date_col = str(payload.get("date_column") or "date")
    value_col = str(payload.get("value_column") or "value")
    points = [{"date": _date(row[date_col]), "value": _num(row[value_col])} for row in rows if row.get(date_col)]
    points.sort(key=lambda item: item["date"])
    return {
        "series_id": str(payload.get("series_id") or ""),
        "points": points,
        "source": str(payload.get("source") or payload.get("adapter") or "upload"),
        "fetched_at": fetched_at,
        "stored": True,
    }


def _csv_rows(text: str) -> list[dict[str, str]]:
    sample = text.lstrip("\ufeff")
    if not sample.strip():
        return []
    dialect = csv.Sniffer().sniff(sample.splitlines()[0], delimiters=",\t")
    return [dict(row) for row in csv.DictReader(io.StringIO(sample), dialect=dialect)]


def _xlsx_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("xlsx")
    if isinstance(raw, str):
        import base64

        blob = base64.b64decode(raw)
    elif isinstance(raw, (bytes, bytearray)):
        blob = bytes(raw)
    else:
        raise ValueError("xlsx upload is missing")
    return _sheet_rows(blob)


def _sheet_rows(blob: bytes) -> list[dict[str, str]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(blob)) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
        sheet_name = "xl/worksheets/sheet1.xml"
        root = ElementTree.fromstring(book.read(sheet_name))
        grid: list[list[str]] = []
        for row in root.findall("m:sheetData/m:row", ns):
            values: list[str] = []
            for cell in row.findall("m:c", ns):
                kind = cell.attrib.get("t")
                if kind == "inlineStr":
                    text = "".join(node.text or "" for node in cell.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                elif kind == "s":
                    node = cell.find("m:v", ns)
                    text = shared[int(node.text or "0")] if node is not None and node.text else ""
                else:
                    node = cell.find("m:v", ns)
                    text = "" if node is None or node.text is None else node.text
                values.append(text)
            if any(values):
                grid.append(values)
    if not grid:
        return []
    header = grid[0]
    return [dict(zip(header, row)) for row in grid[1:]]


def _listing(payload: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    """Count one series through the feeds package. This does not reimplement it."""
    root = _feeds_root()
    if root is None:
        raise RuntimeError("feeds package is not in this process")
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from feeds.runner import run

    fixtures = str(payload.get("fixtures") or "")
    repo = _repo_root()
    if not fixtures:
        if repo is None:
            raise RuntimeError("listing fetch needs fixtures or a live runner")
        fixtures = str(repo / "tests" / "fixtures" / "feeds")
    elif not Path(fixtures).is_absolute() and repo is not None:
        fixtures = str(repo / fixtures)
    import sqlite3
    import tempfile

    source_id = str(payload.get("source_id") or "listing_counts")
    ref = str(payload.get("ref") or "")
    engine = str(payload.get("engine") or "")
    if not engine:
        raise ValueError("engine is required")
    with tempfile.TemporaryDirectory() as folder:
        target = str(Path(folder) / "feeds.sqlite")
        run(
            "sqlite:///" + target,
            engine=engine,
            source=source_id,
            backfill=True,
            fixtures=fixtures,
            live=False,
        )
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT p.date, p.value
                FROM points p
                JOIN series s ON s.id = p.series_id
                WHERE s.source_id = ? AND s.ref = ?
                ORDER BY p.date, p.revision
                """,
                (source_id, ref),
            ).fetchall()
        finally:
            conn.close()
    points = [{"date": str(row["date"])[:10], "value": _num(row["value"])} for row in rows]
    return {
        "series_id": f"{source_id}:{ref}",
        "points": points,
        "source": source_id,
        "fetched_at": fetched_at,
        "stored": True,
    }


def fred_is_link_only(engine: str) -> bool:
    """True when the feeds licence gate refuses a FRED copy."""
    root = _feeds_root()
    if root is None:
        raise RuntimeError("feeds package is not in this process")
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from feeds.catalog import sources
    from feeds.licence import LicenceBlocked, require_copy

    specs = [item for item in sources(engine) if item.adapter == "link" and "fred" in item.id]
    if not specs:
        raise RuntimeError("no linked fred source")
    for spec in specs:
        try:
            require_copy(spec)
        except LicenceBlocked:
            continue
        return False
    return True


def _role_of(row: dict[str, Any], bindings: dict[str, list[dict[str, str]]]) -> str | None:
    source = str(row.get("source") or "")
    ref = str(row.get("ref") or "")
    for role, rules in bindings.items():
        for rule in rules:
            if rule.get("source") and rule["source"] != source:
                continue
            prefix = str(rule.get("prefix") or "")
            if prefix and not ref.startswith(prefix):
                continue
            return role
    return None


def _area_of(ref: str, rules: list[dict[str, str]]) -> str:
    for rule in rules:
        prefix = str(rule.get("prefix") or "")
        if prefix and ref.startswith(prefix):
            return ref[len(prefix) :]
    return ""


def _merge_points(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    for item in left + right:
        totals[str(item["date"])] = totals.get(str(item["date"]), 0.0) + float(item["value"])
    return [{"date": stamp, "value": totals[stamp]} for stamp in sorted(totals)]


def _feeds_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "feeds" / "runner.py").is_file():
            return parent
        nested = parent / "agents" / "market_lab"
        if (nested / "feeds" / "runner.py").is_file():
            return nested
    return None


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "tests" / "fixtures" / "feeds").is_dir():
            return parent
    return None


def _points(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = [{"date": _date(row.get("date")), "value": _num(row.get("value"))} for row in rows]
    points.sort(key=lambda item: item["date"])
    return points


def _surge_events(
    points: list[dict[str, Any]],
    window: int,
    z_min: float,
    pct_min: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if window < 2:
        raise ValueError("window must be at least 2")
    for index in range(window, len(points)):
        history = [float(item["value"]) for item in points[index - window : index]]
        value = float(points[index]["value"])
        previous = history[-1]
        mean = statistics.fmean(history)
        spread = statistics.pstdev(history)
        z = None if spread == 0 else (value - mean) / spread
        pct = None if previous == 0 else (value - previous) / abs(previous)
        z_hit = z is not None and abs(z) >= z_min
        pct_hit = pct is not None and abs(pct) >= pct_min
        if not z_hit and not pct_hit:
            continue
        direction = value - previous
        if z is not None:
            size = round(z, 4)
        elif pct is not None:
            size = round(pct, 4)
        else:
            size = 0.0
        events.append(
            {
                "date": points[index]["date"],
                "kind": "surge" if direction >= 0 else "drop",
                "size": size,
                "z": None if z is None else round(z, 4),
                "pct": None if pct is None else round(pct, 4),
            }
        )
    return events


def _area_history(area: dict[str, Any]) -> list[tuple[str, float, float]]:
    demand = {item["date"]: item["value"] for item in _points(area.get("demand") or [])}
    supply = {item["date"]: item["value"] for item in _points(area.get("supply") or [])}
    stamps = sorted(set(demand) & set(supply))
    return [(stamp, float(demand[stamp]), float(supply[stamp])) for stamp in stamps]


def _snapshot(
    areas: list[dict[str, Any]],
    histories: list[list[tuple[str, float, float]]],
    stamp: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for area, history in zip(areas, histories):
        match = [item for item in history if item[0] == stamp]
        if not match:
            continue
        _, demand, supply = match[0]
        population = area.get("population")
        per_capita = population not in (None, 0, "0")
        supply_level = supply / float(population) if per_capita else supply
        rows.append(
            {
                "area": str(area.get("area")),
                "segment": str(area.get("segment") or "all"),
                "demand": demand,
                "supply_level": supply_level,
                "per_capita": bool(per_capita),
                "competitors": float(area.get("competitors") or 0),
                "reachability": _reach(area),
                "size": float(area.get("size") or demand),
                "fit": float(area.get("fit") or 0),
            }
        )
    return rows


def _score_snapshot(snapshot: list[dict[str, Any]]) -> list[dict[str, Any]]:
    demand_max = max(item["demand"] for item in snapshot)
    supply_max = max(item["supply_level"] for item in snapshot)
    rival_max = max(item["competitors"] for item in snapshot)
    scored: list[dict[str, Any]] = []
    for item in snapshot:
        demand_idx = 0.0 if demand_max == 0 else 100.0 * item["demand"] / demand_max
        supply_idx = 0.0 if supply_max == 0 else 100.0 * item["supply_level"] / supply_max
        density_idx = 0.0 if supply_max == 0 else item["supply_level"] / supply_max
        rival_idx = 0.0 if rival_max == 0 else item["competitors"] / rival_max
        saturation = 0.5 * density_idx + 0.5 * rival_idx
        scored.append(
            {
                "area": item["area"],
                "segment": item["segment"],
                "demand_idx": round(demand_idx, 4),
                "supply_idx": round(supply_idx, 4),
                "gap": round(demand_idx - supply_idx, 4),
                "saturation": round(saturation, 4),
                "reachability": round(item["reachability"], 4),
                "per_capita": item["per_capita"],
                "size": item["size"],
                "fit": item["fit"],
            }
        )
    return scored


def _reach(area: dict[str, Any]) -> float:
    if area.get("reachability") is not None:
        return min(1.0, max(0.0, float(area["reachability"])))
    targets = float(area.get("targets") or 0)
    if targets <= 0:
        return 0.0
    return min(1.0, max(0.0, float(area.get("reachable") or 0) / targets))


def _trend(series: list[dict[str, Any]]) -> dict[str, float | None]:
    if not series:
        return {"m3": None, "m6": None, "m12": None}
    ordered = sorted(series, key=lambda item: item["date"])
    latest = ordered[-1]
    end = date.fromisoformat(latest["date"])
    current = float(latest["value"])

    def change(months: int) -> float | None:
        target = _shift_months(end, -months)
        prior = [item for item in ordered if date.fromisoformat(item["date"]) <= target]
        if not prior:
            return None
        return round(current - float(prior[-1]["value"]), 4)

    return {"m3": change(3), "m6": change(6), "m12": change(12)}


def _shift_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))


def _citations(payload: dict[str, Any]) -> list[dict[str, str]]:
    """A citation is a passage we were given. A model line is not a source."""
    if payload.get("passages") is None:
        return []
    return [_one_citation(item) for item in payload.get("passages") or [] if _one_citation(item)]


def _one_citation(item: dict[str, Any]) -> dict[str, str] | None:
    claim = str(item.get("claim") or "").strip()
    url = str(item.get("url") or "").strip()
    stamp = str(item.get("date") or "").strip()
    if not claim or not url.startswith("http") or not stamp:
        return None
    return {"claim": claim, "url": url, "date": _date(stamp)}


def _value_at(points: list[dict[str, Any]], day: date) -> float | None:
    parsed = _points(points)
    prior = [item for item in parsed if date.fromisoformat(item["date"]) <= day]
    if not prior:
        return None
    return float(prior[-1]["value"])


def _compare(actual: float, comparator: str, target: float) -> bool:
    if comparator in {"gt", ">"}:
        return actual > target
    if comparator in {"gte", ">="}:
        return actual >= target
    if comparator in {"lt", "<"}:
        return actual < target
    if comparator in {"lte", "<="}:
        return actual <= target
    if comparator in {"eq", "="}:
        return actual == target
    raise ValueError(f"unknown comparator {comparator}")


def _date(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) == 7 and text[4] == "-":
        return text + "-01"
    raise ValueError("bad date")


def _num(value: object) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("empty number")
    text = str(value).strip().replace(",", "")
    if text in {"", "."}:
        raise ValueError("empty number")
    number = float(text)
    if number.is_integer():
        return float(int(number))
    return number


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stamp(value: datetime) -> str:
    clock = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_stamp(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())
