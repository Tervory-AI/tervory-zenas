"""Public, region-local candidate labels; no client rows or model calls."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from agents.zenas.core.convert import criminal_case, fact_passages
from agents.zenas.core.engine import quote_in_source
from agents.zenas.core.release_mask import COURT_SOURCES
from agents.zenas.core.scope_rules import classify
from agents.zenas.pool.catalogue import CATALOGUE

JSON_MAX_BYTES = 60 * 1024
ROW_MAX_BYTES = 64 * 1024
RESPONSE_MAX_BYTES = 4500000
PAGE_SIZE = 50


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _bounded(candidate: dict[str, Any]) -> dict[str, Any] | None:
    try:
        if any(len(_encoded(candidate[key])) > JSON_MAX_BYTES for key in ("input", "expected")):
            return None
        if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode("utf-8")) > ROW_MAX_BYTES:
            return None
    except (TypeError, ValueError, RecursionError):
        return None
    return candidate


def _references(facts: str, judgment: str) -> list[dict[str, Any]]:
    headings = list(re.finditer(r"(?m)^\s*(?:\[(\d+)\]|(\d+)\.)\s+", judgment))
    references = []
    for passage in fact_passages(facts):
        numbers = []
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(judgment)
            paragraph = judgment[heading.end():end].strip()
            if paragraph and (quote_in_source(passage, paragraph) or quote_in_source(paragraph, passage)):
                numbers.append(int(heading.group(1) or heading.group(2)))
        words = passage.split()
        references.append({
            "paragraphs": numbers,
            "first_words": " ".join(words[:8]), "last_words": " ".join(words[-8:]),
        })
    return references


def _month(value: Any) -> str | None:
    """Calendar month of a date or timestamp, as YYYY-MM."""
    if value is None or isinstance(value, bool):
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value).strip()
    if len(text) < 7 or text[4] != "-" or not (text[:4].isdigit() and text[5:7].isdigit()):
        return None
    if not 1 <= int(text[5:7]) <= 12:
        return None
    return text[:7]


def _family(name: str, synthetic: bool) -> str:
    return name.replace(":", "-synthetic:", 1) if synthetic else name


def _first_month(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        month = _month(row.get(key))
        if month:
            return month
    return None


def _object(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def case_candidate(row: dict[str, Any], region: str) -> dict[str, Any] | None:
    jurisdiction = "za" if region == "za" else "us"
    if (
        region not in {"za", "us-east"}
        or row.get("region") != region or row.get("jurisdiction") != jurisdiction
        or row.get("tenant_id") is not None or row.get("source") != "case"
        or row.get("material_type") != "public_record"
        or row.get("source_id") not in COURT_SOURCES or criminal_case(row)
    ):
        return None
    pattern = _object(row.get("fact_pattern") or {})
    facts = str(pattern.get("facts") or pattern.get("narrative") or "")
    judgment = str(row.get("text") or "")
    if not facts or not judgment or classify(facts, jurisdiction).get("criminal"):
        return None
    if not all(quote_in_source(part, judgment) for part in fact_passages(facts)):
        return None
    findings = _object(row.get("element_findings") or [])
    outcome = str(row.get("outcome") or "")
    if not findings or not outcome:
        return None
    held = bool(row.get("holdout"))
    month = _first_month(row, ("as_at", "created_at", "known_from", "doc_date"))
    if month is None:
        return None
    return _bounded({
        "skill_id": "zenas.case_facts",
        "input": {
            "pool_doc_id": str(row["pool_doc_id"]), "pool_version_id": str(row["pool_version_id"]),
            "judgment": facts, "paragraph_references": _references(facts, judgment),
            "home_region": region, "holdout": held,
        },
        "expected": {
            "features": pattern.get("features") or {}, "narrative": facts,
            "element_findings": findings, "outcome": outcome,
        },
        "label_source": f"{'held_out' if held else 'converted'}:{row['source_id']}:{row['pool_version_id']}",
        "label_family": _family(f"converted:{row['source_id']}:{month}", row.get("synthetic") is True),
        "region": region,
        "public": True,
        "holdout": held,
    })


def statute_candidate(row: dict[str, Any], region: str) -> dict[str, Any] | None:
    jurisdiction = "za" if region == "za" else "us"
    sources = {item.source_id for item in CATALOGUE if item.region == region}
    if (
        region not in {"za", "us-east"}
        or row.get("jurisdiction") != jurisdiction or row.get("source_id") not in sources
        or row.get("kind") != "statute" or not row.get("text") or not row.get("citation")
        or classify(str(row.get("title") or "") + " " + str(row["text"]), jurisdiction).get("criminal")
    ):
        return None
    month = _first_month(row, ("known_from", "captured_at", "effective_from", "doc_date"))
    if month is None:
        return None
    passage = {
        "doc_id": str(row["document_id"]), "version_id": str(row["version_id"]),
        "citation": row["citation"], "heading": row.get("heading") or "", "text": row["text"],
        "jurisdiction": jurisdiction, "kind": "statute", "status": "effective",
        "path": row.get("path") or "", "level": "",
    }
    return _bounded({
        "skill_id": "zenas.retrieve",
        "input": {"citation": row["citation"], "jurisdiction": jurisdiction, "corpus": [passage]},
        "expected": {"count": 1, "citations": [row["citation"]], "passages": [passage]},
        "label_source": f"statute_section:{row['source_id']}:{row['version_id']}:{row.get('path') or ''}",
        "label_family": _family(f"statute_section:{row['source_id']}:{month}", row.get("synthetic") is True),
        "region": region,
        "public": True,
        "holdout": False,
    })


async def candidates(pool: Any, *, region: str, limit: int = 200) -> list[dict[str, Any]]:
    if region not in {"za", "us-east"}:
        raise ValueError("wrong region")
    limit = max(0, min(limit, 200))
    if not limit:
        return []
    jurisdiction = "za" if region == "za" else "us"
    sources = [item.source_id for item in CATALOGUE if item.region == region]
    rotation = datetime.now(timezone.utc).date().isoformat()
    result: list[dict[str, Any]] = []
    size = 2
    async with pool.acquire() as conn:
        async def worked_rows():
            offset = 0
            while True:
                rows = await conn.fetch(
            """
            SELECT w.*, d.text, d.source_id, d.title, d.docket_number, d.nature_of_suit,
                   d.doc_date, d.known_from
            FROM agent_zenas.worked_matters w
            JOIN zenas_pool.documents d ON d.version_id = w.pool_version_id
              AND d.document_id = w.pool_doc_id AND d.jurisdiction = w.jurisdiction
            WHERE w.region = $1 AND w.jurisdiction = $2 AND w.tenant_id IS NULL
              AND w.source = 'case' AND w.material_type = 'public_record'
              AND d.kind = 'case' AND d.source_id = ANY($3::text[])
              AND d.effective_to IS NULL AND d.status IS DISTINCT FROM 'superseded'
            ORDER BY md5(w.id::text || $4), w.id LIMIT $5 OFFSET $6
            """,
                    region, jurisdiction, sorted(COURT_SOURCES), rotation, PAGE_SIZE, offset,
                )
                for row in rows:
                    yield row
                if len(rows) < PAGE_SIZE:
                    return
                offset += len(rows)

        async def statute_rows():
            offset = 0
            while True:
                rows = await conn.fetch(
            """
            SELECT c.*, d.source_id, d.kind, d.title,
                   d.doc_date, d.effective_from, d.known_from, d.captured_at
            FROM zenas_pool.chunks c JOIN zenas_pool.documents d ON d.version_id = c.version_id
            WHERE c.jurisdiction = $1 AND d.jurisdiction = $1
              AND d.source_id = ANY($2::text[]) AND d.kind = 'statute'
              AND d.effective_to IS NULL AND d.status IS DISTINCT FROM 'superseded'
            ORDER BY md5(c.chunk_id || $3), c.chunk_id LIMIT $4 OFFSET $5
            """,
                    jurisdiction, sources, rotation, PAGE_SIZE, offset,
                )
                for row in rows:
                    yield row
                if len(rows) < PAGE_SIZE:
                    return
                offset += len(rows)

        async def filtered(rows, build):
            async for row in rows:
                try:
                    candidate = build(dict(row), region)
                except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
                    continue
                if candidate is not None:
                    yield candidate

        streams = [filtered(worked_rows(), case_candidate), filtered(statute_rows(), statute_candidate)]
        while streams and len(result) < limit:
            for stream in list(streams):
                try:
                    candidate = await stream.__anext__()
                except StopAsyncIteration:
                    streams.remove(stream)
                    continue
                added = len(_encoded(candidate)) + bool(result)
                if size + added > RESPONSE_MAX_BYTES:
                    return result
                result.append(candidate)
                size += added
                if len(result) == limit:
                    break
    return result
