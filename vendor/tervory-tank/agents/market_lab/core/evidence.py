"""Tenant-owned immutable evidence and deterministic, individually cited facts."""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime
from typing import Any
from decimal import Decimal

from feeds.catalog import sources
from feeds.keys import bind_key, missing_key
from feeds.geo import area_label, mentioned_states, state_for_area
from feeds.presentation import observation_label, unit_label, period_label, event_label, notice_link


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def save_check(ledger: Any, tenant_id: str, result: dict, *, at: datetime) -> dict:
    owner = uuid.UUID(str(tenant_id))
    facts = result["points"] + result["events"]
    document_id = str(uuid.uuid4()) if facts else None
    check = {"id": str(uuid.uuid4()), "tenant_id": str(owner), "source_id": result["source_id"],
             "checked_at": at.isoformat(), "rows": len(facts), "note": result["note"],
             "fixture": result["fixture"], "document_id": document_id}
    content = {key: value for key, value in result.items() if key != "fixture"}
    content.update(id=document_id, fetched_at=at.isoformat())
    if hasattr(ledger, "conn"):
        async with ledger.conn.transaction():
            if document_id:
                await ledger.conn.execute("""
                    INSERT INTO agent_market_lab.feed_documents
                    (id, tenant_id, source_id, fetched_at, fixture, content)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """, uuid.UUID(document_id), owner, result["source_id"], at, result["fixture"], json.dumps(content))
            await ledger.conn.execute("""
                INSERT INTO agent_market_lab.feed_checks
                (id, tenant_id, source_id, checked_at, fixture, rows, note, document_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, uuid.UUID(check["id"]), owner, result["source_id"], at, result["fixture"],
                len(facts), result["note"], uuid.UUID(document_id) if document_id else None)
    else:
        ledger.feed_checks = getattr(ledger, "feed_checks", []) + [check]
        if document_id:
            ledger.feed_documents = getattr(ledger, "feed_documents", []) + [{**content, "tenant_id": str(owner), "fixture": result["fixture"]}]
    return check


async def latest_checks(ledger: Any, tenant_id: str, *, fixtures: bool = False) -> list[dict]:
    owner = uuid.UUID(str(tenant_id))
    if hasattr(ledger, "conn"):
        rows = await ledger.conn.fetch("""
            SELECT DISTINCT ON (source_id) source_id, checked_at, rows, note, document_id
            FROM agent_market_lab.feed_checks WHERE tenant_id = $1 AND fixture = $2
            ORDER BY source_id, checked_at DESC, id DESC
        """, owner, fixtures)
        return [{**dict(row), "checked_at": row["checked_at"].isoformat(),
                 "document_id": str(row["document_id"]) if row["document_id"] else None} for row in rows]
    by_source = {}
    for row in getattr(ledger, "feed_checks", []):
        if row["tenant_id"] == str(owner) and row["fixture"] == fixtures:
            by_source[row["source_id"]] = dict(row)
    return list(by_source.values())


async def document(ledger: Any, tenant_id: str, document_id: str, *, fixtures: bool = False) -> dict | None:
    owner, ident = uuid.UUID(str(tenant_id)), uuid.UUID(document_id)
    if hasattr(ledger, "conn"):
        value = await ledger.conn.fetchval("""
            SELECT content FROM agent_market_lab.feed_documents
            WHERE tenant_id = $1 AND id = $2 AND fixture = $3
        """, owner, ident, fixtures)
        return _decode(value) if value else None
    return next((dict(row) for row in getattr(ledger, "feed_documents", [])
                 if row["tenant_id"] == str(owner) and row["id"] == str(ident) and row["fixture"] == fixtures), None)


def point_fact(doc: dict, index: int, point: dict) -> dict:
    """The existing source-row wording, shared by answers and saved briefs."""
    value = number_label(point["value"])
    shown_value = point.get("display_value") or f'{value} {unit_label(point["value"], point["unit"])}'
    text = f'{observation_label(point, doc.get("presentation"))}: {shown_value} ({period_label(point["date"], point.get("frequency", ""))}).'
    return {"text": text, "href": f'/market-lab/sources/{doc["id"]}?row={index}#point-{index}', "name": doc["name"]}


async def usable_points(ledger: Any, tenant_id: str, doc: dict) -> list[tuple[int, dict]]:
    ids = list({point["batch_id"] for point in doc["points"]})
    if not ids:
        return []
    if hasattr(ledger, "conn"):
        rows = await ledger.conn.fetch("""
            SELECT id FROM agent_market_lab.batches
            WHERE tenant_id = $1 AND id = ANY($2::text[]) AND status = 'pooled'
        """, uuid.UUID(str(tenant_id)), ids)
        allowed = {row["id"] for row in rows}
    else:
        allowed = {key for key, value in ledger.pool.batches.items()
                   if value["tenant_id"] == str(tenant_id) and value["status"] == "pooled"}
    return [(index, point) for index, point in enumerate(doc["points"]) if point["batch_id"] in allowed]


def current_revisions(points: list[dict]) -> dict[tuple[str, str], int]:
    """Collection order preserves versions, including a return to an old value."""
    return {(point["series_id"], point["date"]): index for index, point in enumerate(points)}


def presented_document(doc: dict, engine: str | None) -> dict:
    # Refresh wording for earlier immutable documents without changing their data.
    if engine:
        spec = next((source for source in sources(engine) if source.id == doc["source_id"]), None)
        if spec:
            return {**doc, "name": spec.name, "presentation": spec.config.get("presentation", {})}
    return doc


DOCUMENT_PAGE_SIZE = 50


def number_label(value: Any) -> str:
    formatted = format(Decimal(str(value)), ",f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


async def document_page(ledger: Any, tenant_id: str, document_id: str, offset: int = 0, *, row: int | None = None, row_kind: str | None = None, fixtures: bool = False, engine: str | None = None) -> dict | None:
    from core.documents import source_page
    uploaded = await source_page(ledger, tenant_id, document_id, offset, row)
    if uploaded is not None:
        return uploaded
    doc = await document(ledger, tenant_id, document_id, fixtures=fixtures)
    if doc is None:
        return None
    if doc.get("kind") == "warning_evidence":
        return {**doc, "offset": 0, "page_size": DOCUMENT_PAGE_SIZE, "point_count": 0, "event_count": 0}
    doc = presented_document(doc, engine)
    active = {index for index, _ in await usable_points(ledger, tenant_id, doc)}
    current = current_revisions(doc["points"])
    counts = Counter((p["series_id"], p["date"]) for p in doc["points"])
    # Sort a view of the immutable rows; citations keep their original indexes.
    points = sorted(enumerate(doc["points"]), key=lambda item: (item[1]["date"], item[0]), reverse=True)
    events = sorted(enumerate(doc["events"]), key=lambda item: (item[1]["date"], item[0]), reverse=True)
    if row is not None:
        selected = events if row_kind == "event" else points if row_kind == "point" else points or events
        offset = next((position for position, (index, _) in enumerate(selected) if index == row), 0)
    last_offset = max(0, (max(len(points), len(events)) - 1) // DOCUMENT_PAGE_SIZE * DOCUMENT_PAGE_SIZE)
    offset = min(max(0, offset // DOCUMENT_PAGE_SIZE * DOCUMENT_PAGE_SIZE), last_offset)
    def shown(index: int, point: dict) -> dict:
        key = (point["series_id"], point["date"])
        label = "Current figure" if current[key] == index else "Earlier figure"
        return {**point, "index": index, "display_value": point.get("display_value") or number_label(point["value"]),
                "geo_name": area_label(point["geo"], point["geo_level"], point["title"]),
                "display_title": observation_label(point, doc.get("presentation")),
                "date_label": period_label(point["date"], point.get("frequency", "")),
                "unit_label": unit_label(point["value"], point["unit"]), "withdrawn": index not in active,
                "revision_label": (f'{label} ({point["revision"]})' if point["revision"] else label) if counts[key] > 1 else ""}
    return {**doc, "offset": offset, "page_size": DOCUMENT_PAGE_SIZE, "point_count": len(points), "event_count": len(events),
            "points": [shown(index, point) for index, point in points[offset:offset + DOCUMENT_PAGE_SIZE]],
            "events": [{**event, "index": index, "display_title": event_label(event, doc.get("presentation")),
                        "date_label": period_label(event["date"]), **notice_link(event, doc.get("presentation"))}
                       for index, event in events[offset:offset + DOCUMENT_PAGE_SIZE]]}


async def overview(ledger: Any, tenant_id: str, engine: str | None = None, *, fixtures: bool = False) -> dict:
    if not engine:
        raise ValueError("A feed plan is required")
    checks = {row["source_id"]: row for row in await latest_checks(ledger, tenant_id, fixtures=fixtures)}
    feeds = []
    from core.feed_links import source_for, subscriptions
    configured = sources(engine) + [source_for(row) for row in await subscriptions(ledger, tenant_id)]
    for source in configured:
        spec = bind_key(source)
        check = checks.get(spec.id, {})
        rows = int(check.get("rows", 0))
        note = check.get("note") or ("Off, needs key" if missing_key(spec) else "Off, not fetched yet")
        if rows:
            note = check.get("note") or ""
        feeds.append({"id": spec.id, "name": spec.name, "kind": "copy" if spec.allow_bulk else "linked",
                      "url": spec.url, "cadence": spec.cadence,
                      "rows": rows, "state": "on" if spec.allow_bulk and rows > 0 else "off",
                      "note": note, "checked_at": check.get("checked_at"), "document_id": check.get("document_id")})
    return {"feeds": feeds, "on_count": sum(feed["state"] == "on" for feed in feeds)}


def _words(text: str) -> set[str]:
    # Function words cannot establish a topic. Keep meaningful qualifiers (such
    # as an unknown place, object or date), so they must match the stored fact.
    stop = set("""a an the and or but is are was were be been being am do does did
        has have had can could may might should would will shall what which who
        whom whose why when where how show tell give find get me my our your
        their its about for from to with without within at in on of by as into
        over under between than then also there here this that these those it
        they them he she i we you any all some each every many much please
        market latest current state states county counties figure figures count
        counts monthly weekly annual data stored saved""".split())
    words = {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in stop and (len(word) > 1 or word.isdigit())}
    return {word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word for word in words}


def _match_score(terms: set[str], text: str, source_words: set[str], place: str, period: str, frequency: str = "", units: str = "") -> int:
    words = _words(text)
    readable_period = period_label(period, frequency)
    if frequency != "annual":
        try:
            readable_period += " " + datetime.fromisoformat(period[:10]).strftime("%B")
        except ValueError:
            pass
    context = _words(place + " " + readable_period)
    topics = {word for word in words | source_words if word not in context and not word.isdigit()}
    context |= _words(units)
    # Every meaningful qualifier must be supported by this observation, and
    # there must be a topic beyond a date, place, or conversational filler.
    if not terms or not terms <= words | source_words | context or not terms & topics:
        return 0
    return 3 * len(terms & words) + len(terms & source_words)


async def gated_facts(ledger: Any, tenant_id: str, facts: list[dict], *, fixtures: bool = False, now: datetime | None = None) -> list[dict]:
    """Final assembly boundary for stored answers and supplied brief passages.

    Authoritative prediction IDs and warning document links determine the cohort;
    a caller cannot open it by putting a different cohort on a proposed fact.
    Unknown warning/prediction references fail closed. Ordinary feed facts remain.
    """
    import inspect
    from core.predictions import gate

    loaded = None
    documents = {}
    accepted = []
    for fact in facts:
        href = str(fact.get("href") or fact.get("url") or "")
        match = re.search(r"/market-lab/sources/([0-9a-fA-F-]{36})(?:[?#/]|$)", href)
        doc_id = match.group(1) if match else None
        if doc_id not in documents:
            documents[doc_id] = await document(ledger, tenant_id, doc_id, fixtures=fixtures) if doc_id else None
        if doc_id and documents[doc_id] is None:
            from core.documents import records
            uploads = await records(ledger, tenant_id, doc_id)
            documents[doc_id] = uploads[0] if uploads else None
        if doc_id and documents[doc_id] is None:
            continue  # Missing, deleted or foreign tenant source cannot be cited.
        doc = documents[doc_id] or {}
        predictive = (doc.get("kind") in {"warning_evidence", "prediction", "warning"}
                      or fact.get("kind") in {"prediction", "warning"}
                      or any(fact.get(key) is not None for key in ("prediction_id", "warning_id")))
        if not predictive:
            accepted.append(fact)
            continue
        if loaded is None:
            rows = ledger.predictions_for(tenant_id)
            rows = await rows if inspect.isawaitable(rows) else rows
            rows = [row for row in rows if str(row["tenant_id"]) == str(tenant_id)]
            opened = {(row["kind"], row["feed"], row["horizon_seconds"]) for row in gate(rows, tenant_id, now=now)["cohorts"] if row["open"]}
            loaded = rows, opened
        rows, opened = loaded
        candidates = []
        for row in rows:
            if row.get("outcome") is not None:
                continue
            sources = [source for source in row.get("sources") or [] if isinstance(source, dict)]
            prediction_match = fact.get("prediction_id") is not None and str(row["id"]) == str(fact["prediction_id"])
            warning_match = fact.get("warning_id") is not None and any(str(source.get("warning_id")) == str(fact["warning_id"]) for source in sources)
            if prediction_match or warning_match:
                candidates.append(row)
            elif doc_id and any(doc_id in str(source.get("evidence", "")) for source in sources):
                candidates.append(row)
        if candidates and all((row.get("kind"), row.get("feed"), row.get("horizon_seconds")) in opened for row in candidates):
            accepted.append(fact)
    return accepted


async def answer(ledger: Any, tenant_id: str, question: str, *, fixtures: bool = False, engine: str | None = None) -> dict:
    terms = _words(question)
    states = mentioned_states(question)
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", question))
    # A comparison baseline in a measurement's title is not its reporting year.
    comparison_years = set(re.findall(r"(?:compared (?:with|to)|base year|relative to)\s+((?:19|20)\d{2})\b", question, re.I))
    years -= comparison_years
    ranked = []
    documents = []
    for check in await latest_checks(ledger, tenant_id, fixtures=fixtures):
        if check["document_id"] and check["rows"]:
            doc = await document(ledger, tenant_id, check["document_id"], fixtures=fixtures)
            if doc:
                documents.append(presented_document(doc, engine))
    # The caller's catalogue may distinguish a question about notices from
    # measurements mentioned in it (for example a bill about a measurement).
    focused = [doc for doc in documents if any(re.search(r"\b" + re.escape(topic) + r"\b", question, re.I)
               for topic in doc.get("presentation", {}).get("answer_topics", []))]
    if focused:
        documents = focused
    counties = set()
    for doc in documents:
        for point in doc["points"]:
            if point["geo_level"] != "county" or point["geo"] == "unmatched":
                continue
            name = area_label(point["geo"], "county", point["title"]).split(",")[0]
            name = re.sub(r" (County|Parish|Borough|Census Area|Municipality)$", "", name, flags=re.I)
            if name and re.search(r"\b" + re.escape(name) + r"\b", question, re.I):
                counties.add(point["geo"])
    for doc in documents:
        source_words = _words(doc["name"] + " " + " ".join(doc.get("presentation", {}).get("answer_topics", [])))
        latest = {}
        current = current_revisions(doc["points"])
        for index, point in await usable_points(ledger, tenant_id, doc):
            if current[(point["series_id"], point["date"])] != index:
                continue
            if counties and point["geo"] not in counties:
                continue
            covered_states = doc.get("presentation", {}).get("geo_states", {}).get(point["geo"], [])
            if states and state_for_area(point["geo"]) not in states and not states.intersection(covered_states):
                continue
            if years and point["date"][:4] not in years:
                continue
            key = point["series_id"]
            if key not in latest or point["date"] >= latest[key][1]["date"]:
                latest[key] = (index, point)
        for index, point in latest.values():
            score = _match_score(terms, observation_label(point, doc.get("presentation")),
                                 source_words, area_label(point["geo"], point["geo_level"], point["title"]) + " " + point["geo"], point["date"], point.get("frequency", ""), unit_label(point["value"], point["unit"]))
            if not score:
                continue
            ranked.append((score, point["date"], point_fact(doc, index, point)))
        for index, event in enumerate(doc["events"]):
            if counties and event["geo"] not in counties:
                continue
            if states and state_for_area(event["geo"]) not in states:
                continue
            if years and event["date"][:4] not in years:
                continue
            if event["kind"] == "changed":
                continue
            score = _match_score(terms, f'{event_label(event, doc.get("presentation"))} {event["body"]}',
                                 source_words, area_label(event["geo"], "state", event["title"]) + " " + event["geo"], event["date"])
            if score:
                ranked.append((score, event["date"], {"text": f'{event_label(event, doc.get("presentation"))} ({period_label(event["date"])}).', "href": f'/market-lab/sources/{doc["id"]}?row={index}&kind=event#event-{index}', "name": doc["name"]}))
    from core.documents import matched
    ranked.extend(await matched(ledger, tenant_id, question))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    best = ranked[0][0] if ranked else 0
    facts = await gated_facts(ledger, tenant_id, [row[2] for row in ranked if row[0] == best][:5], fixtures=fixtures)
    return {"content": "Here is what the saved sources show. This information does not explain why something happened or predict what comes next." if facts else "No stored data matches this question", "facts": facts, "sources": [{"name": fact["name"], "href": fact["href"]} for fact in facts]}
