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

from agents.zenas.core.engine import cite_check, keys_match, parse_json_object
from agents.zenas.core.release_mask import material_type_for_pool
from agents.zenas.core.selftest import box_jurisdiction, is_holdout
from agents.zenas.core.service import ModelAdapter

log = logging.getLogger("zenas.convert")

_PLATFORM = "00000000-0000-0000-0000-000000000001"
_DEFAULT_MAX = 200
_convert_lock = asyncio.Lock()
_convert_running: set[str] = set()

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


def _empty(*, ok: bool, region: str, reason: str = "") -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": ok,
        "region": region,
        "decided": 0,
        "converted": 0,
        "held_out": 0,
        "failed": 0,
        "failures": [],
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


def parse_worked(raw: str) -> dict[str, Any]:
    """Facts, question, elements, holding. The policy shape is accepted too."""
    data = parse_json_object(raw)
    facts = _fold(str(data.get("facts") or data.get("narrative") or ""))
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
    }


def _statements(case: dict[str, Any], parsed: dict[str, Any]) -> list[dict[str, str]]:
    citation = str(case.get("citation") or "")
    rows = [{"citation": citation, "quote": parsed["facts"]}]
    for item in parsed["citations"]:
        quote = _fold(item.get("quote") or "")
        if quote:
            rows.append({"citation": item.get("citation") or citation, "quote": quote})
    for element in parsed["elements"]:
        quote = _fold(element.get("quote") or "")
        if quote:
            rows.append({"citation": element.get("citation") or citation, "quote": quote})
    return rows


# An SA criminal caption: "S v …", or the DPP / NPA as a party.
_SA_CAPTION = re.compile(
    r"(?i)(?:^\s*s\s+v\s+\S|\bDPP\b|director of public prosecutions|\bNPA\b|national prosecuting authority)"
)


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


def _cite_reason(statements: list[dict[str, str]], passages: list[dict[str, Any]]) -> str:
    """A short reason. The judgment text is not included."""
    if not statements:
        return "cite check failed"
    for statement in statements:
        citation = str(statement.get("citation") or "")
        quote = _fold(str(statement.get("quote") or ""))
        hits = [item for item in passages if keys_match(str(item.get("citation") or ""), citation)]
        if not hits:
            return "citation did not resolve"
        if any(str(item.get("status") or "") == "superseded" for item in hits) and all(
            str(item.get("status") or "") == "superseded" for item in hits
        ):
            return "citation is superseded"
        if not quote or not any(quote in _fold(str(item.get("text") or "")) for item in hits):
            return "quote is not in the judgment"
    return "cite check failed"


async def _remember_failure(ladder: Any, *, region: str, pool_doc_id: str, reason: str) -> None:
    clean = " ".join(reason.split())[:240]
    log.warning("case convert failed for %s: %s", pool_doc_id, clean)
    await ladder.record_convert_failure(region=region, pool_doc_id=pool_doc_id, reason=clean)


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
    """Counts only: decided cases loaded, converted, held out, failed."""
    box = _box(region)
    if box is None or not _home_allows(pool, box):
        return _empty(ok=False, region=box or "", reason="wrong region")
    jurisdiction = box_jurisdiction(box)
    decided = await _list_cases(pool, jurisdiction)
    tally = await ladder.case_tally(region=box, jurisdiction=jurisdiction)
    failed = await ladder.convert_failure_count(region=box)
    reasons = await ladder.convert_failure_reasons(region=box)
    return {
        "ok": True,
        "region": box,
        "decided": len(decided),
        "converted": int(tally["converted"]),
        "held_out": int(tally["held_out"]),
        "failed": int(failed),
        "failures": reasons,
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
    for case in cases:
        if len(pending) >= cap:
            break
        existing = await ladder.worked_for_doc(region=box, pool_doc_id=case["document_id"])
        if existing is None:
            pending.append(case)
    adapter = ModelAdapter(model)
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
        if not text.strip() or not case["citation"].strip():
            await _remember_failure(ladder, region=box, pool_doc_id=case["document_id"], reason="cite check failed")
            continue
        prompt = _PROMPT_HEAD + case["citation"] + "\n\n" + text
        try:
            called = await adapter.complete(
                [{"role": "user", "content": prompt}],
                task_type="zenas.case_facts",
                provider="luna",
            )
        except Exception:
            await _remember_failure(ladder, region=box, pool_doc_id=case["document_id"], reason="model call failed")
            continue
        try:
            parsed = parse_worked(str(called.get("content") or ""))
        except (ValueError, TypeError):
            await _remember_failure(
                ladder, region=box, pool_doc_id=case["document_id"], reason="the model did not return case facts"
            )
            continue
        passages = [
            {
                "citation": case["citation"],
                "text": text,
                "doc_id": case["document_id"],
                "version_id": case["version_id"],
                "status": "in_force",
            }
        ]
        statements = _statements(case, parsed)
        check = cite_check(statements, passages)
        if not check.get("passed"):
            await _remember_failure(
                ladder, region=box, pool_doc_id=case["document_id"], reason=_cite_reason(statements, passages)
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
            }

    return _Route(), http
