"""Turn loaded decided cases into worked matters, one region at a time.

US cases are read only when this box is Boston. SA cases are read only when
this box is the ZA box. A worked matter is stored only after zenas.cite_check
passes. The same stable hash as Z7 marks one case in five holdout.

zenas.case_facts is the batch rung in Tank's policy. This job does not set a
priority of its own, so that batch rung stays. The nightly run also yields
with os.nice.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from agents.zenas.core.engine import (
    CHEAP_CALL_USD,
    citation_hits,
    cite_check,
    fold_for_quote,
    parse_json_object,
    quote_in_source,
)
from agents.zenas.core.release_mask import material_type_for_pool
from agents.zenas.core.pointers import PointerError, judgment_text, resolve_answer
from agents.zenas.core.selftest import box_jurisdiction, is_holdout

log = logging.getLogger("zenas.convert")

_PLATFORM = "00000000-0000-0000-0000-000000000001"
_DEFAULT_MAX = 200
_EXCERPT = 1500
_QUOTE_BIT = 120
# A joined facts quote is several passages, not a handful of words.
_MIN_PASSAGE_WORDS = 8
_MAX_PASSAGES = 8
_MIN_FACTS_CHARS = 200
_PASSAGE_WORDS = _MIN_PASSAGE_WORDS
_convert_lock = asyncio.Lock()
_convert_running: set[str] = set()
# Models join verbatim passages with '...', '…', or '. . .', spaces or not.
# Three or more dots are one joiner, so a sentence period plus '...' still splits.
_PASSAGE_JOIN = re.compile(r"\s*(?:\u2026|\.{3,}|(?:\.\s+){2}\.)\s*")

_LIST_SQL = """
SELECT DISTINCT ON (document_id)
       document_id, version_id, jurisdiction, citation, source_id, title,
       docket_number, nature_of_suit
FROM zenas_pool.documents
WHERE kind = 'case'
  AND jurisdiction = $1
  AND effective_to IS NULL
  AND status IS DISTINCT FROM 'superseded'
ORDER BY document_id, version_id DESC
""".strip()

_TEXT_SQL = """
SELECT text
FROM zenas_pool.documents
WHERE version_id = $1 AND jurisdiction = $2 AND kind = 'case'
""".strip()

_PROMPT_HEAD = (
    "Read this judgment as published. Copy the facts in the court's own words, "
    "as a continuous passage from the judgment. Name the question, the elements, "
    "and the holding. Cite only this case. Every quote must appear in the judgment "
    "word for word. Reply with JSON only:\n"
    '{"facts":"","question":"","elements":[{"element_id":"","finding":"met|not_met|unclear","quote":""}],'
    '"holding":"","citations":[{"citation":"","quote":""}]}\n\n'
    "Citation: "
)


# A criminal docket: "1:20-cr-00123", "18-1234-cr", "No. 19-CR-55", "Crim. No. 4".
_CRIMINAL_DOCKET = re.compile(r"(?i)(?:^|[^a-z])(?:cr|crim)(?:[^a-z]|$)|criminal")


def criminal_record(docket_number: str, nature_of_suit: str) -> bool:
    """True when the stored docket number or nature of suit marks a criminal matter."""
    nature = (nature_of_suit or "").lower()
    if "criminal" in nature:
        return True
    return bool(_CRIMINAL_DOCKET.search(docket_number or ""))


def _row_text(row: Any, name: str) -> str:
    try:
        value = row[name]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(value or "")


def convert_limit() -> int:
    """At most this many cases in one run. ZENAS_CONVERT_MAX, default 200."""
    raw = os.environ.get("ZENAS_CONVERT_MAX", "").strip()
    if not raw:
        return _DEFAULT_MAX
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX
    if value < 0:
        return _DEFAULT_MAX
    return value


def _box(region: str) -> str | None:
    raw = (region or "").strip().lower()
    if raw == "za":
        return "za"
    if raw in {"us-east", "us"}:
        return "us-east"
    return None


def _fold(text: str) -> str:
    return " ".join((text or "").split())


def _joined_parts(facts: str) -> list[str] | None:
    """The pieces of a joined facts quote. None when the facts are one passage."""
    text = facts or ""
    if not _PASSAGE_JOIN.search(text):
        return None
    parts = [part.strip() for part in _PASSAGE_JOIN.split(text) if part.strip()]
    if len(parts) <= 1:
        return None
    return parts


def facts_too_short(facts: str) -> bool:
    """True when joined facts are scraps, too brief, or split into too many pieces.

    Each passage needs 8 words, the facts need 200 characters, and there may
    be at most 8 passages. One continuous passage is not judged here.
    """
    parts = _joined_parts(facts)
    if parts is None:
        return False
    if len(parts) > _MAX_PASSAGES:
        return True
    if len(facts or "") < _MIN_FACTS_CHARS:
        return True
    return any(len(fold_for_quote(part).split()) < _MIN_PASSAGE_WORDS for part in parts)


def fact_passages(facts: str) -> list[str]:
    """One verbatim passage, or several joined the way a model writes an ellipsis.

    A join counts only when every piece is long enough to be a passage.
    Shorter pieces stay one string, so a paraphrase split into words fails.
    """
    text = facts or ""
    parts = _joined_parts(text)
    if parts is None:
        stripped = [part.strip() for part in _PASSAGE_JOIN.split(text) if part.strip()]
        if len(stripped) == 1 and _PASSAGE_JOIN.search(text):
            return stripped
        return [text] if text.strip() else []
    if any(len(fold_for_quote(part).split()) < _PASSAGE_WORDS for part in parts):
        return [text]
    return parts


def answer_cut_off(finish_reason: str) -> bool:
    """True when the model stopped because the answer budget ran out."""
    token = re.sub(r"[^a-z0-9]", "", (finish_reason or "").lower())
    if token in {"length", "maxtokens", "maxoutputtokens"}:
        return True
    return "maxtoken" in token


def cutoff_label(finish_reason: str) -> str:
    """'answer cut off' plus the finish reason the model reported."""
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "", (finish_reason or "").strip())[:40]
    if not safe:
        safe = "length"
    return f"answer cut off ({safe})"


def _public_excerpt(answer: str) -> str:
    """The model's own answer, capped. Not the judgment, and not client text."""
    return (answer or "")[:_EXCERPT]


def _answer_view(result: Any) -> dict[str, Any]:
    """Content plus the finish reason and token counts, when the call returned them."""
    if isinstance(result, str):
        return {
            "content": result,
            "cost_usd": CHEAP_CALL_USD,
            "model": "",
            "finish_reason": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }
    if not isinstance(result, dict):
        return {
            "content": str(result or ""),
            "cost_usd": CHEAP_CALL_USD,
            "model": "",
            "finish_reason": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }
    cost = result.get("cost_usd")
    if cost is None:
        cost = CHEAP_CALL_USD
    return {
        "content": str(result.get("content") or ""),
        "cost_usd": float(cost),
        "model": str(result.get("model") or ""),
        "finish_reason": str(result.get("finish_reason") or ""),
        "tokens_in": int(result.get("tokens_in") or 0),
        "tokens_out": int(result.get("tokens_out") or 0),
    }


async def _ask_model(model: Any, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Call the model and keep the finish reason and token counts.

    The on-demand model object drops those fields. When it holds a Tank
    client, ask Tank directly so the founder can see why a public case failed.
    """
    tank = getattr(model, "tank", None)
    tenant_id = getattr(model, "tenant_id", None)
    if tank is not None and tenant_id:
        data = await tank.route(str(tenant_id), "zenas.case_facts", messages)
        return _answer_view(data)
    if hasattr(model, "complete"):
        result = await model.complete(messages, task_type="zenas.case_facts", provider="luna")
    else:
        result = await model.model(messages, task_type="zenas.case_facts")
    return _answer_view(result)


def _empty(*, ok: bool, region: str, reason: str = "") -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": ok,
        "region": region,
        "decided": 0,
        "converted": 0,
        "held_out": 0,
        "failed": 0,
        "failures": [],
        "cases": [],
    }
    if reason:
        report["reason"] = reason
    return report


def _home_allows(pool: Any, box: str) -> bool:
    """A store bound to the other region is not read."""
    home = getattr(pool, "home_region", None)
    if home is None:
        return True
    return home == box


def _sql_conn(pool: Any) -> Any | None:
    conn = getattr(pool, "_conn", None)
    if conn is not None and hasattr(conn, "fetch"):
        return conn
    return None


async def _list_cases(pool: Any, jurisdiction: str) -> list[dict[str, Any]]:
    """Open decided cases for this jurisdiction. The text stays unread."""
    conn = _sql_conn(pool)
    if conn is not None:
        rows = await conn.fetch(_LIST_SQL, jurisdiction)
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if str(row["jurisdiction"]) != jurisdiction:
                continue
            doc_id = str(row["document_id"])
            if doc_id in seen:
                continue
            seen.add(doc_id)
            found.append(
                {
                    "document_id": doc_id,
                    "version_id": str(row["version_id"] or ""),
                    "jurisdiction": jurisdiction,
                    "citation": str(row["citation"] or ""),
                    "source_id": str(row["source_id"] or ""),
                    "title": str(row["title"] or ""),
                    "docket_number": _row_text(row, "docket_number"),
                    "nature_of_suit": _row_text(row, "nature_of_suit"),
                }
            )
        return found
    docs = getattr(pool, "docs", None)
    if not isinstance(docs, dict):
        return []
    found = []
    seen = set()
    for doc in docs.values():
        if getattr(doc, "kind", None) != "case":
            continue
        if getattr(doc, "jurisdiction", None) != jurisdiction:
            continue
        if getattr(doc, "effective_to", None) is not None:
            continue
        if str(getattr(doc, "status", "") or "") == "superseded":
            continue
        doc_id = str(getattr(doc, "id", "") or "")
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        found.append(
            {
                "document_id": doc_id,
                "version_id": str(getattr(doc, "version_id", "") or ""),
                "jurisdiction": jurisdiction,
                "citation": str(getattr(doc, "citation", "") or ""),
                "source_id": str(getattr(doc, "source_id", "") or ""),
                "title": str(getattr(doc, "title", "") or ""),
                "docket_number": str(getattr(doc, "docket_number", "") or ""),
                "nature_of_suit": str(getattr(doc, "nature_of_suit", "") or ""),
                "doc": doc,
            }
        )
    found.sort(key=lambda item: item["document_id"])
    return found


async def _judgment(pool: Any, case: dict[str, Any]) -> str:
    """The text of this one case. Called only for a case this region is converting."""
    doc = case.get("doc")
    if doc is not None:
        return str(doc.text or "")
    conn = _sql_conn(pool)
    if conn is None:
        return ""
    row = await conn.fetchrow(_TEXT_SQL, case["version_id"], case["jurisdiction"])
    if row is None:
        return ""
    return str(row["text"] or "")


def parse_worked(raw: str, text: str | None = None) -> dict[str, Any]:
    """Facts, question, elements, holding. The policy shape is accepted too."""
    data = parse_json_object(raw)
    if text is not None:
        data = resolve_answer(data, text)
    facts = str(data.get("facts") or "").strip() if "facts_pointer" in data else _fold(str(data.get("facts") or data.get("narrative") or ""))
    question = _fold(str(data.get("question") or ""))
    holding = _fold(str(data.get("holding") or data.get("outcome") or ""))
    raw_elements = data.get("elements")
    if not isinstance(raw_elements, list):
        raw_elements = data.get("element_findings") if isinstance(data.get("element_findings"), list) else []
    elements: list[dict[str, Any]] = []
    for index, item in enumerate(raw_elements):
        if not isinstance(item, dict):
            continue
        finding = str(item.get("finding") or "unclear")
        if finding not in {"met", "not_met", "unclear"}:
            finding = "unclear"
        elements.append(
            {
                "element_id": str(item.get("element_id") or item.get("id") or f"e{index + 1}"),
                "finding": finding,
                "reasoning": str(item.get("reasoning") or ""),
                "basis": [str(key) for key in (item.get("basis") or []) if str(key)],
                "quote": str(item.get("quote") or ""),
                "citation": str(item.get("citation") or ""),
            }
        )
    citations: list[dict[str, str]] = []
    for item in data.get("citations") or []:
        if isinstance(item, dict):
            citations.append(
                {"citation": str(item.get("citation") or ""), "quote": str(item.get("quote") or "")}
            )
    features = data.get("features") if isinstance(data.get("features"), dict) else {}
    if not facts or not holding:
        raise ValueError("the model did not return case facts")
    return {
        "facts": facts,
        "question": question,
        "elements": elements,
        "holding": holding,
        "citations": citations,
        "features": features,
        "pointed": "facts_pointer" in data,
        "has_pointers": "facts_pointer" in data or any(
            isinstance(item, dict) and "quote_pointer" in item
            for field in ("elements", "element_findings", "citations")
            for item in (data.get(field) or [])
        ),
    }


def _statements(case: dict[str, Any], parsed: dict[str, Any]) -> list[dict[str, str]]:
    citation = str(case.get("citation") or "")
    rows = [
        {"citation": citation, "quote": part, "part": "facts"}
        for part in fact_passages(str(parsed.get("facts") or ""))
    ]
    for index, item in enumerate(parsed["citations"], start=1):
        quote = _fold(item.get("quote") or "")
        if quote:
            rows.append(
                {
                    "citation": item.get("citation") or citation,
                    "quote": quote,
                    "part": f"citation {index}",
                }
            )
    for index, element in enumerate(parsed["elements"], start=1):
        quote = _fold(element.get("quote") or "")
        if quote:
            rows.append(
                {
                    "citation": element.get("citation") or citation,
                    "quote": quote,
                    "part": f"element {index}",
                }
            )
    return rows


# An SA criminal caption: "S v …", or the DPP / NPA as a party.
_SA_CAPTION = re.compile(
    r"(?i)(?:^\s*s\s+v\s+\S|\bDPP\b|director of public prosecutions|\bNPA\b|national prosecuting authority)"
)
# A Competition Commission or Tribunal merger clearance, not the reasons.
_MERGER_LETTER = re.compile(
    r"merger clearance certificate|conditional merger approval|merger approval letter|"
    r"approves the merger in terms of section\s+16",
    re.I,
)
_COMPETITION_BODY = re.compile(r"competition (commission|tribunal)", re.I)
_REASONS_TITLE = re.compile(r"\breasons\b|\bjudgment\b", re.I)


def criminal_case(case: dict[str, Any]) -> bool:
    """The one criminal check. It reads only what the source recorded.

    A US case is refused when its stored docket_number is a criminal ("cr")
    docket or its stored nature_of_suit is criminal; its caption is not read.
    An SA case is refused when its stored title is an "S v …" caption or names
    the DPP or NPA. The judgment text is never read. An empty field is
    unknown, so a case with no type converts.
    """
    if str(case.get("jurisdiction") or "") == "za":
        return _SA_CAPTION.search(str(case.get("title") or "")) is not None
    return criminal_record(str(case.get("docket_number") or ""), str(case.get("nature_of_suit") or ""))


def merger_approval_letter(case: dict[str, Any], text: str) -> bool:
    """A Competition Commission or Tribunal merger clearance, not a judgment.

    The Appeal Court and a reasons document stay in. The letter is skipped
    before a model is called.
    """
    source = str(case.get("source_id") or "").lower()
    if source == "cac":
        return False
    title = str(case.get("title") or "")
    if _REASONS_TITLE.search(title):
        return False
    opening = ""
    for line in (text or "").splitlines():
        if line.strip():
            opening = line.strip()
            break
    if re.match(r"(reasons|judgment)\b", opening, re.I):
        return False
    blob = "\n".join((title, str(case.get("citation") or ""), text or ""))
    competition = source in {"comptrib", "compcom"} or _COMPETITION_BODY.search(blob) is not None
    if not competition:
        return False
    return _MERGER_LETTER.search(blob) is not None


def _quote_bit(quote: str) -> str:
    """The first 120 characters of the quote that failed. Not the judgment."""
    return (quote or "")[:_QUOTE_BIT]


def _cite_failure(statements: list[dict[str, str]], passages: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Reason, which part failed, and the start of that quote.

    The case's own citation resolves to its own row. Another citation the
    answer adds still has to resolve. The judgment text is not returned.
    """
    if not statements:
        return "cite check failed", "", ""
    for statement in statements:
        citation = str(statement.get("citation") or "")
        quote = str(statement.get("quote") or "")
        part = str(statement.get("part") or "")
        hits = citation_hits(citation, passages)
        if not hits:
            return "citation did not resolve", part, _quote_bit(quote)
        if any(str(item.get("status") or "") == "superseded" for item in hits) and all(
            str(item.get("status") or "") == "superseded" for item in hits
        ):
            return "citation is superseded", part, _quote_bit(quote)
        if not any(quote_in_source(quote, str(item.get("text") or "")) for item in hits):
            return "quote is not in the judgment", part, _quote_bit(quote)
    return "cite check failed", "", ""


async def _store_failure_debug(
    ladder: Any,
    *,
    region: str,
    pool_doc_id: str,
    answer: str,
    finish_reason: str,
    tokens_in: int,
    tokens_out: int,
    quote_part: str = "",
    quote_excerpt: str = "",
) -> None:
    """Store this attempt only. The judgment is not stored."""
    excerpt = _public_excerpt(answer)
    finish = (finish_reason or "")[:80]
    incoming = int(tokens_in or 0)
    outgoing = int(tokens_out or 0)
    part = " ".join((quote_part or "").split())[:40]
    bit = (quote_excerpt or "")[:_QUOTE_BIT]
    rows = getattr(ladder, "failures", None)
    if isinstance(rows, list):
        kept = False
        fresh: list[dict[str, Any]] = []
        for row in rows:
            if row.get("region") == region and row.get("pool_doc_id") == pool_doc_id:
                if kept:
                    continue
                row["answer_excerpt"] = excerpt
                row["finish_reason"] = finish
                row["tokens_in"] = incoming
                row["tokens_out"] = outgoing
                row["quote_part"] = part
                row["quote_excerpt"] = bit
                kept = True
            fresh.append(row)
        rows[:] = fresh
        return
    pool = getattr(ladder, "pool", None)
    if pool is None:
        return
    update_sql = """
    UPDATE agent_zenas.convert_failures
    SET answer_excerpt = $3, finish_reason = $4, tokens_in = $5, tokens_out = $6,
        quote_part = $7, quote_excerpt = $8
    WHERE region = $1 AND pool_doc_id = $2
    """.strip()
    # One row per case. An older attempt's reason and answer do not remain.
    drop_sql = """
    DELETE FROM agent_zenas.convert_failures AS older
    WHERE older.region = $1 AND older.pool_doc_id = $2
      AND EXISTS (
        SELECT 1 FROM agent_zenas.convert_failures AS newer
        WHERE newer.region = older.region
          AND newer.pool_doc_id = older.pool_doc_id
          AND (
            newer.created_at > older.created_at
            OR (newer.created_at = older.created_at AND newer.id > older.id)
          )
      )
    """.strip()
    try:
        async with pool.acquire() as conn:
            await conn.execute(drop_sql, region, pool_doc_id)
            await conn.execute(
                update_sql, region, pool_doc_id, excerpt, finish, incoming, outgoing, part, bit
            )
    except Exception:
        log.warning("convert debug was not stored")


async def _failure_details(ladder: Any, region: str) -> list[dict[str, Any]]:
    """One row per failed public case. No judgment text and no client text."""
    rows = getattr(ladder, "failures", None)
    if isinstance(rows, list):
        found = [row for row in rows if row.get("region") == region]
    else:
        pool = getattr(ladder, "pool", None)
        if pool is None:
            return []
        sql = """
        SELECT DISTINCT ON (pool_doc_id)
               pool_doc_id, reason,
               COALESCE(answer_excerpt, '') AS answer_excerpt,
               COALESCE(finish_reason, '') AS finish_reason,
               COALESCE(tokens_in, 0) AS tokens_in,
               COALESCE(tokens_out, 0) AS tokens_out,
               COALESCE(quote_part, '') AS quote_part,
               COALESCE(quote_excerpt, '') AS quote_excerpt
        FROM agent_zenas.convert_failures
        WHERE region = $1
        ORDER BY pool_doc_id, created_at DESC, id DESC
        """.strip()
        try:
            async with pool.acquire() as conn:
                fetched = await conn.fetch(sql, region)
        except Exception:
            log.warning("convert failures could not be read")
            return []
        found = [dict(row) for row in fetched]
    details = []
    for row in found:
        details.append(
            {
                "id": str(row.get("pool_doc_id") or ""),
                "reason": str(row.get("reason") or ""),
                "answer": _public_excerpt(str(row.get("answer_excerpt") or "")),
                "finish_reason": str(row.get("finish_reason") or ""),
                "tokens_in": int(row.get("tokens_in") or 0),
                "tokens_out": int(row.get("tokens_out") or 0),
                "quote_part": str(row.get("quote_part") or ""),
                "quote_excerpt": _quote_bit(str(row.get("quote_excerpt") or "")),
            }
        )
    return details


async def _remember_failure(
    ladder: Any,
    *,
    region: str,
    pool_doc_id: str,
    reason: str,
    answer: str = "",
    finish_reason: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    quote_part: str = "",
    quote_excerpt: str = "",
) -> None:
    clean = " ".join(reason.split())[:240]
    log.warning("case convert failed for %s: %s", pool_doc_id, clean)
    await ladder.record_convert_failure(region=region, pool_doc_id=pool_doc_id, reason=clean)
    await _store_failure_debug(
        ladder,
        region=region,
        pool_doc_id=pool_doc_id,
        answer=answer,
        finish_reason=finish_reason,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        quote_part=quote_part,
        quote_excerpt=quote_excerpt,
    )


def convert_running(region: str) -> bool:
    """True while a background conversion for this region has not finished."""
    box = _box(region)
    return box is not None and box in _convert_running


async def begin_convert(region: str, runner: Any) -> dict[str, Any]:
    """Start runner in the background and return at once.

    The status is running, or already running when this region is already going.
    """
    box = _box(region)
    if box is None:
        return {"ok": False, "region": "", "running": False, "status": ""}
    async with _convert_lock:
        if box in _convert_running:
            return {"ok": True, "region": box, "running": True, "status": "already running"}
        _convert_running.add(box)

    async def _go() -> None:
        try:
            await runner()
        except Exception:
            log.warning("convert failed")
        finally:
            async with _convert_lock:
                _convert_running.discard(box)

    try:
        asyncio.create_task(_go())
    except Exception:
        async with _convert_lock:
            _convert_running.discard(box)
        raise
    return {"ok": True, "region": box, "running": True, "status": "running"}


async def conversion_report(pool: Any, ladder: Any, *, region: str) -> dict[str, Any]:
    """Counts, plus the model's answer for each failed public case."""
    box = _box(region)
    if box is None or not _home_allows(pool, box):
        return _empty(ok=False, region=box or "", reason="wrong region")
    jurisdiction = box_jurisdiction(box)
    decided = await _list_cases(pool, jurisdiction)
    tally = await ladder.case_tally(region=box, jurisdiction=jurisdiction)
    failed = await ladder.convert_failure_count(region=box)
    reasons = await ladder.convert_failure_reasons(region=box)
    citations = {case["document_id"]: str(case.get("citation") or "") for case in decided}
    cases = []
    for row in await _failure_details(ladder, box):
        cases.append(
            {
                "id": row["id"],
                "citation": citations.get(row["id"], ""),
                "reason": row["reason"],
                "answer": row["answer"],
                "finish_reason": row["finish_reason"],
                "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"],
                "quote_part": row["quote_part"],
                "quote_excerpt": row["quote_excerpt"],
            }
        )
    cases.sort(key=lambda item: (item["citation"], item["id"]))
    return {
        "ok": True,
        "region": box,
        "decided": len(decided),
        "converted": int(tally["converted"]),
        "held_out": int(tally["held_out"]),
        "failed": int(failed),
        "failures": reasons,
        "cases": cases,
    }


async def convert_cases(
    pool: Any,
    ladder: Any,
    model: Any,
    *,
    region: str,
) -> dict[str, Any]:
    """Convert up to ZENAS_CONVERT_MAX decided cases that have no worked matter yet."""
    box = _box(region)
    if box is None or not _home_allows(pool, box):
        return _empty(ok=False, region=box or "", reason="wrong region")
    jurisdiction = box_jurisdiction(box)
    if model is None:
        log.warning("convert skipped: no model is configured")
        report = await conversion_report(pool, ladder, region=box)
        report["ok"] = False
        report["reason"] = "no model is configured"
        return report
    cap = convert_limit()
    cases = await _list_cases(pool, jurisdiction)
    pending: list[dict[str, Any]] = []
    repaired = 0
    for case in cases:
        if len(pending) >= cap:
            break
        existing = await ladder.worked_for_doc(region=box, pool_doc_id=case["document_id"])
        if existing is None:
            pending.append(case)
        elif not existing.get("rule_card_ids") and repaired < cap and not criminal_case(case):
            await _link_converted_card(pool, ladder, model, existing, region=box)
            repaired += 1
    for case in pending:
        if case["jurisdiction"] != jurisdiction:
            continue
        material = material_type_for_pool(case["source_id"])
        if material != "public_record":
            await _remember_failure(
                ladder, region=box, pool_doc_id=case["document_id"], reason="material type is unknown"
            )
            continue
        if criminal_case(case):
            await _remember_failure(ladder, region=box, pool_doc_id=case["document_id"], reason="criminal law is refused")
            continue
        text = await _judgment(pool, case)
        if merger_approval_letter(case, text):
            await _remember_failure(
                ladder, region=box, pool_doc_id=case["document_id"], reason="not a judgment"
            )
            continue
        if not text.strip() or not case["citation"].strip():
            await _remember_failure(ladder, region=box, pool_doc_id=case["document_id"], reason="cite check failed")
            continue
        prompt = _PROMPT_HEAD + case["citation"] + "\n\n" + text
        try:
            called = await _ask_model(model, [{"role": "user", "content": prompt}])
        except Exception:
            await _remember_failure(ladder, region=box, pool_doc_id=case["document_id"], reason="model call failed")
            continue
        content = str(called.get("content") or "")
        finish = str(called.get("finish_reason") or "")
        tokens_in = int(called.get("tokens_in") or 0)
        tokens_out = int(called.get("tokens_out") or 0)
        if answer_cut_off(finish):
            await _remember_failure(
                ladder,
                region=box,
                pool_doc_id=case["document_id"],
                reason=cutoff_label(finish),
                answer=content,
                finish_reason=finish,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            continue
        try:
            resolved_text = judgment_text(text)
            parsed = parse_worked(content, resolved_text)
        except PointerError:
            await _remember_failure(
                ladder, region=box, pool_doc_id=case["document_id"],
                reason="pointer did not resolve", answer=content,
                finish_reason=finish, tokens_in=tokens_in, tokens_out=tokens_out,
            )
            continue
        except (ValueError, TypeError):
            await _remember_failure(
                ladder,
                region=box,
                pool_doc_id=case["document_id"],
                reason="the model did not return case facts",
                answer=content,
                finish_reason=finish,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            continue
        if (parsed["pointed"] and len(parsed["facts"]) < _MIN_FACTS_CHARS) or facts_too_short(parsed["facts"]):
            await _remember_failure(
                ladder,
                region=box,
                pool_doc_id=case["document_id"],
                reason="facts too short",
                answer=content,
                finish_reason=finish,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            continue
        passages = [
            {
                "citation": case["citation"],
                "text": resolved_text if parsed["has_pointers"] else fold_for_quote(text),
                "doc_id": case["document_id"],
                "version_id": case["version_id"],
                "status": "in_force",
            }
        ]
        statements = _statements(case, parsed)
        check = cite_check(statements, passages)
        if not check.get("passed"):
            reason, part, bit = _cite_failure(statements, passages)
            await _remember_failure(
                ladder,
                region=box,
                pool_doc_id=case["document_id"],
                reason=reason,
                answer=content,
                finish_reason=finish,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                quote_part=part,
                quote_excerpt=bit,
            )
            continue
        held = is_holdout(case["document_id"])
        worked = await ladder.add_worked(
            {
                "region": box,
                "tenant_id": None,
                "source": "case",
                "material_type": material,
                "jurisdiction": jurisdiction,
                "area": "",
                "rule_card_ids": [],
                "fact_pattern": {
                    "features": parsed["features"],
                    "narrative": parsed["facts"],
                    "facts": parsed["facts"],
                    "question": parsed["question"],
                    "citation": case["citation"],
                },
                "element_findings": [
                    {
                        "element_id": item["element_id"],
                        "finding": item["finding"],
                        "reasoning": item["reasoning"],
                        "basis": item["basis"],
                    }
                    for item in parsed["elements"]
                ],
                "outcome": parsed["holding"],
                "lawyer_edits": "",
                "as_at": None,
                "pool_doc_id": case["document_id"],
                "pool_version_id": case["version_id"],
                "holdout": held,
            }
        )
        await ladder.clear_convert_failure(region=box, pool_doc_id=case["document_id"])
        await _link_converted_card(pool, ladder, model, worked, region=box)
        if not held:
            await ladder.add_pair(
                {
                    "region": box,
                    "tenant_id": None,
                    "source": "case",
                    "worked_id": worked["id"],
                    "request": case["citation"],
                    "findings": worked["element_findings"],
                    "outcome": parsed["holding"],
                    "memo": parsed["facts"],
                    "lawyer_edits": "",
                    "as_at": None,
                }
            )
        cost = float(called.get("cost_usd") or 0)
        await ladder.add_run(
            {
                "region": box,
                "tenant_id": _PLATFORM,
                "kind": "case_facts",
                "question_key": case["document_id"],
                "cost_usd": cost,
                "modeled": 1,
            }
        )
    return await conversion_report(pool, ladder, region=box)


async def _link_converted_card(pool: Any, ladder: Any, model: Any, worked: dict[str, Any], *, region: str) -> None:
    from agents.zenas.core.converted_cards import link_card

    try:
        await link_card(pool, ladder, model, worked, region=region)
    except Exception:
        log.warning("converted rule card could not be built", exc_info=True)


async def run_scheduled(conn: Any, *, region: str) -> None:
    """Nightly, after the pool load. A failure here does not change the pool report."""
    try:
        os.nice(19)
    except OSError:
        pass
    from agents.zenas.core.ladder_store import PgLadder
    from agents.zenas.core.store import PgStore
    from agents.zenas.pool.store import PostgresStore

    class _Hold:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def __aenter__(self) -> Any:
            return self.inner

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    class _Pool:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def acquire(self) -> _Hold:
            return _Hold(self.inner)

    raw = conn
    pool = conn if hasattr(conn, "acquire") else _Pool(conn)
    store = PgStore(pool)
    try:
        await store.apply_schema()
    except Exception:
        return
    ladder = PgLadder(pool)
    law = PostgresStore(raw)
    model, http = _night_model()
    try:
        await convert_cases(law, ladder, model, region=region)
    finally:
        if http is not None:
            await http.aclose()


def _night_model() -> tuple[Any, Any]:
    """Tank when this process has a key. The key is never logged or returned."""
    key = os.environ.get("TANK_KEY", "").strip()
    url = os.environ.get("TANK_URL", "").strip()
    if not key or not url:
        return None, None
    import httpx
    from chassis.tank import TankClient

    http = httpx.AsyncClient(timeout=120)
    client = TankClient(url, key, "zenas", http)

    class _Route:
        async def complete(self, messages: list[dict[str, str]], *, task_type: str, provider: str) -> dict[str, Any]:
            del provider
            data = await client.route(_PLATFORM, task_type, messages)
            return {
                "content": str(data.get("content") or ""),
                "cost_usd": float(data.get("cost_usd") or 0),
                "model": str(data.get("model") or ""),
                "finish_reason": str(data.get("finish_reason") or ""),
                "tokens_in": int(data.get("tokens_in") or 0),
                "tokens_out": int(data.get("tokens_out") or 0),
            }

    return _Route(), http
