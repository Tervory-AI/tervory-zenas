"""Held-out decided cases, and LegalBench on the US box only.

Twenty percent of decided cases, by a stable hash, are never reused and never
written as training pairs. A self-test sees only the facts the court stated.
US runs see only US cases. SA runs see only SA cases.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from typing import Any

from agents.zenas.core.engine import cite_check, code_finding, compose_memo, decide_without_model
from agents.zenas.core.legalbench import legalbench_scores
from agents.zenas.core.scope_rules import classify

_PLATFORM = "00000000-0000-0000-0000-000000000001"


def is_holdout(stable_id: str) -> bool:
    """One stable fifth. The same id is held out on every run."""
    digest = hashlib.sha256(stable_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 5 == 0


def box_jurisdiction(region: str) -> str:
    return "za" if region == "za" else "us"


def _facts_only(row: dict[str, Any]) -> dict[str, Any]:
    """The court's stated facts. Not the judgment, the findings, or the outcome."""
    pattern = row.get("fact_pattern") or {}
    features = dict(pattern.get("features") or {})
    facts = str(pattern.get("facts") or "")
    question = str(pattern.get("question") or "")
    return {"features": features, "facts": facts, "question": question}


def _predicted_outcome(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "unclear"
    states = [str(item.get("finding") or "") for item in findings]
    if all(item == "met" for item in states):
        return "met"
    if any(item == "not_met" for item in states):
        return "not_met"
    return "unclear"


def _court_outcome(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text in {"met", "not_met", "unclear"}:
        return text
    if text in {"enforced", "affirmed", "granted", "liable"}:
        return "met"
    if text in {"dismissed", "reversed", "denied"}:
        return "not_met"
    return text


_ERROR_REASONS = frozenset({"no rule card", "criminal law is refused", "the ladder did not run"})


def _error(case: dict[str, Any], reason: str) -> dict[str, Any]:
    """The ladder did not produce a score. The reason is a fixed phrase."""
    clean = reason if reason in _ERROR_REASONS else "the ladder did not run"
    return {
        "pool_doc_id": case.get("pool_doc_id"),
        "area": str(case.get("area") or ""),
        "jurisdiction": str(case.get("jurisdiction") or ""),
        "outcome_hit": False,
        "element_agreement": 0.0,
        "citations_valid": False,
        "cost_usd": 0.0,
        "error": True,
        "reason": clean,
    }


def _element_agreement(court: list[dict[str, Any]], ours: list[dict[str, Any]]) -> float:
    if not court:
        return 0.0
    by_id = {str(item.get("element_id") or ""): str(item.get("finding") or "") for item in ours}
    same = 0
    for item in court:
        if by_id.get(str(item.get("element_id") or "")) == str(item.get("finding") or ""):
            same += 1
    return 100.0 * same / len(court)


def _passages(card: dict[str, Any]) -> list[dict[str, Any]]:
    passages = []
    for element in card.get("elements") or []:
        quote = str(element.get("quote") or "")
        citation = str(element.get("citation") or "")
        if quote and citation:
            passages.append(
                {
                    "citation": citation,
                    "text": quote,
                    "doc_id": element.get("doc_id") or "",
                    "status": "in_force",
                }
            )
    return passages


async def _one(ladder: Any, case: dict[str, Any], *, region: str) -> dict[str, Any]:
    given = _facts_only(case)
    scope = classify(given["facts"] or given["question"], box_jurisdiction(region))
    if scope.get("criminal"):
        return _error(case, "criminal law is refused")
    card = None
    for card_id in case.get("rule_card_ids") or []:
        loaded = await ladder.get_rule_card(str(card_id), region=region, tenant_id=_PLATFORM)
        if loaded is not None and str(loaded.get("status") or "") != "stale":
            card = loaded
            break
    if card is None:
        return _error(case, "no rule card")
    # Reuse reads visible_worked, which already drops holdout rows, so this case
    # cannot be matched against itself. The code table decides the elements.
    try:
        return await _score(card, case, given)
    except Exception:
        return _error(case, "the ladder did not run")


async def _score(card: dict[str, Any], case: dict[str, Any], given: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    cost = 0.0
    for element in card.get("elements") or []:
        decided = code_finding(str(element.get("id") or ""), given["features"], card.get("decision_table") or [])
        if decided is None:
            found = decide_without_model(
                element,
                given["features"],
                card.get("decision_table") or [],
                [],
                tenant_id=_PLATFORM,
            )
        else:
            found = {
                "element_id": str(element.get("id") or ""),
                "finding": decided,
                "support": "",
                "missing": "",
                "rung": "code",
                "cost_usd": 0,
            }
        if found is None:
            found = {
                "element_id": str(element.get("id") or ""),
                "finding": "unclear",
                "support": "",
                "missing": "no code path",
                "rung": "code",
                "cost_usd": 0,
            }
        findings.append(found)
        cost += float(found.get("cost_usd") or 0)
    memo = compose_memo(card, findings)
    statements = [
        {"citation": element.get("citation"), "quote": element.get("quote")}
        for element in card.get("elements") or []
        if element.get("citation")
    ]
    checked = cite_check(statements, _passages(card)) if statements else {"passed": False}
    predicted = _predicted_outcome(findings)
    court = _court_outcome(str(case.get("outcome") or ""))
    return {
        "pool_doc_id": case.get("pool_doc_id"),
        "area": str(case.get("area") or ""),
        "jurisdiction": str(case.get("jurisdiction") or ""),
        "outcome_hit": predicted == court,
        "element_agreement": _element_agreement(list(case.get("element_findings") or []), findings),
        "citations_valid": bool(checked.get("passed")),
        "cost_usd": cost,
        "memo_citations": memo.get("citations") or [],
        "error": False,
        "reason": "",
    }


def _group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    errors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("error"):
            errors[str(row.get("reason") or "the ladder did not run")].append(row)
            continue
        buckets[(row["area"], row["jurisdiction"])].append(row)
    grouped = []
    for (area, jurisdiction), items in sorted(buckets.items()):
        count = len(items)
        grouped.append(
            {
                "area": area,
                "jurisdiction": jurisdiction,
                "cases": count,
                "outcome_agreement": round(100.0 * sum(1 for item in items if item["outcome_hit"]) / count, 2),
                "element_agreement": round(sum(item["element_agreement"] for item in items) / count, 2),
                "citations_valid": round(100.0 * sum(1 for item in items if item["citations_valid"]) / count, 2),
                "cost_per_case": round(sum(item["cost_usd"] for item in items) / count, 6),
                "case_ids": [str(item.get("pool_doc_id") or "") for item in items],
                "error": False,
                "reason": "",
            }
        )
    for reason, items in sorted(errors.items()):
        grouped.append(
            {
                "area": "",
                "jurisdiction": str(items[0].get("jurisdiction") or ""),
                "cases": len(items),
                "outcome_agreement": 0,
                "element_agreement": 0,
                "citations_valid": 0,
                "cost_per_case": 0,
                "case_ids": [str(item.get("pool_doc_id") or "") for item in items],
                "error": True,
                "reason": reason,
            }
        )
    return grouped


# One real case, copied from the saved Grancy judgment. The nightly traps are not this case.
_MADE_UP_LIBRARY = (
    {
        "name": "Grancy Property Limited and Another v Gihwala and Others",
        "citation": "[2026] ZACC 40",
        "quote": "Leave to appeal is refused.",
        "text": (
            "On appeal from the Supreme Court of Appeal (hearing an appeal from the High Court "
            "of South Africa, Western Cape Division, Cape Town): 1. Leave to appeal is refused."
        ),
    },
)


def made_up_questions(library: list[dict[str, str]] | None = None, *, region: str = "za") -> list[dict[str, str]]:
    """Fifty questions. Forty name cases that are not in the library."""
    documents = [item for item in (library or _MADE_UP_LIBRARY) if item.get("name") and item.get("citation")]
    place = "in South Africa" if str(region or "").strip().lower() == "za" else "in the United States"
    rows: list[dict[str, str]] = []
    codes = ("ZASCA", "ZAGPJHC", "ZAWCHC", "ZACC", "ZAFSHC")
    for index in range(40):
        name = f"Fictional Party {index} v Nobody {index}"
        citation = f"[2099] {codes[index % len(codes)]} {9000 + index}"
        rows.append(
            {
                "kind": "trap",
                "question": (
                    f"What did {name} {citation} decide in this commercial dispute {place}?"
                ),
                "name": name,
                "citation": citation,
            }
        )
    for index in range(10):
        real = documents[index % len(documents)]
        rows.append(
            {
                "kind": "real",
                "question": (
                    f"Question {index}: what did {real['name']} {real['citation']} hold "
                    f"in this commercial dispute {place}?"
                ),
                "name": real["name"],
                "citation": real["citation"],
                "quote": str(real.get("quote") or ""),
            }
        )
    return rows


def _verbatim_quote(text: str) -> str:
    """A sentence copied from the stored judgment, short enough to score, still the court's words.

    Skip judge bylines and sentences that personal-information masking would alter, because
    rule-card cleaning runs quotes through that mask before cite-check. Keep the historic
    20-character floor so short court holdings still become library quotes.
    """
    _skip = re.compile(
        r"(circuit judge|district judge|per curiam|justice[.:]?$|after stat\w* the facts)",
        re.I,
    )
    try:
        from agents.zenas.core.engine import _clean_piece
    except Exception:  # pragma: no cover
        _clean_piece = None  # type: ignore

    normalized = text.replace(chr(13), chr(10))
    candidates: list[str] = []
    for piece in normalized.split(chr(10)):
        line = piece.strip()
        if len(line) < 20 or _skip.search(line):
            continue
        end = line.find(". ")
        if end >= 20:
            sentence = line[: end + 1]
            if sentence in text and not _skip.search(sentence):
                candidates.append(sentence)
        if line.endswith(".") and 20 <= len(line) <= 400 and line in text:
            candidates.append(line)
        elif len(line) >= 40 and line[:240] in text:
            candidates.append(line[:240])
    for sentence in candidates:
        if _clean_piece is None:
            return sentence
        if _clean_piece(sentence, []) == sentence:
            return sentence
    # fall back: any substring that survives cleaning
    body = " ".join(normalized.split())
    for width in (120, 90, 70):
        for i in range(0, max(0, len(body) - width)):
            chunk = body[i : i + width]
            if _skip.search(chunk):
                continue
            if _clean_piece is not None and _clean_piece(chunk, []) != chunk:
                continue
            if chunk in text or chunk in body:
                return chunk
    return candidates[0] if candidates else ""


def _made_up_box(region: str) -> tuple[str, str, str, str]:
    """Home, box, jurisdiction, and provider for this box's made-up-case run."""
    if str(region or "").strip().lower() == "za":
        return "za", "za", "za", "za-local"
    return "us-east", "us-east", "us", "luna"


def _passages_for(documents: list[dict[str, str]], item: dict[str, str]) -> list[dict[str, str]]:
    """The case named in the question, first. A trap uses one real library case."""
    named = [document for document in documents if document["citation"] == item.get("citation")]
    chosen = named[:1] or documents[:1]
    return [
        {
            "citation": document["citation"],
            "text": document["text"],
            "title": document["name"],
            "name": document["name"],
            "status": "in_force",
        }
        for document in chosen
    ]


_LIBRARY_SQL = """
SELECT DISTINCT ON (document_id)
       citation, title, left(text, 1200) AS text
FROM zenas_pool.documents
WHERE kind = 'case'
  AND jurisdiction = $1
  AND effective_to IS NULL
  AND status IS DISTINCT FROM 'superseded'
  AND char_length(coalesce(citation, '')) > 0
  AND char_length(coalesce(title, '')) > 0
  AND char_length(coalesce(text, '')) >= 40
ORDER BY document_id, known_from DESC NULLS LAST, version_id DESC
""".strip()


def _row_text(row: Any, name: str) -> str:
    try:
        value = row[name]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(value or "")


async def _fetch_rows(conn: Any, sql: str, *args: Any) -> list[Any]:
    if conn is None:
        return []
    if hasattr(conn, "fetch"):
        return list(await conn.fetch(sql, *args))
    if hasattr(conn, "acquire"):
        async with conn.acquire() as held:
            return list(await held.fetch(sql, *args))
    return []


async def load_case_library(conn: Any, *, region: str) -> list[dict[str, str]]:
    """This box's current cases. A missing library leaves the made-up row not run."""
    jurisdiction = box_jurisdiction("za" if str(region or "").strip().lower() == "za" else "us-east")
    try:
        rows = await _fetch_rows(conn, _LIBRARY_SQL, jurisdiction)
    except Exception:
        return []
    documents: list[dict[str, str]] = []
    for row in rows:
        citation = " ".join(_row_text(row, "citation").split())
        name = " ".join(_row_text(row, "title").split())
        text = _row_text(row, "text")
        quote = _verbatim_quote(text)
        if not citation or not name or not quote or quote not in text:
            continue
        documents.append({"name": name, "citation": citation, "quote": quote, "text": text})
    return documents


def _tank_model() -> tuple[Any, Any]:
    """Tank on this box. The key is never logged. No key means the made-up row stays not run."""
    key = os.environ.get("TANK_KEY", "").strip()
    url = os.environ.get("TANK_URL", "").strip()
    if not key or not url:
        return None, None
    try:
        import httpx
        from chassis.tank import TankClient
    except ImportError:
        return None, None
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


def _library_documents(library: list[dict[str, str]] | None) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for item in library or []:
        name = " ".join(str(item.get("name") or "").split())
        citation = " ".join(str(item.get("citation") or "").split())
        quote = " ".join(str(item.get("quote") or "").split())
        text = str(item.get("text") or "")
        if name and citation and quote and quote in text:
            documents.append({"name": name, "citation": citation, "quote": quote, "text": text})
    return documents


def _not_run(questions: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "asked": 50,
        "passed": 0,
        "skills_may_be_approved": False,
        "path": "not run",
        "area": "Made-up cases: not run.",
        "questions": questions or [],
        "failures": [],
    }


async def run_made_up_test(
    ladder: Any = None,
    store: Any = None,
    model: Any = None,
    *,
    library: list[dict[str, str]] | None = None,
    tenant_id: str = _PLATFORM,
    region: str = "za",
) -> dict[str, Any]:
    """Score fifty questions on the real answer path for this box.

    Without this box's library and a model, the path did not run, and the row
    must not say 50 of 50. The model is Tank on this box, so the call lands on
    this box's ledger.
    """
    del store
    documents = _library_documents(library)
    if ladder is None or model is None or not documents:
        return _not_run()
    home, box, jurisdiction, provider = _made_up_box(region)
    from agents.zenas.core.service import answer_question

    questions = made_up_questions(documents, region=region)
    existing = await ladder.get_tenant(str(tenant_id))
    if existing is None:
        registered = await ladder.register_tenant(str(tenant_id), home)
        if not registered.get("ok"):
            return _not_run(questions)
    elif str(existing.get("home_region") or "") != home:
        return _not_run(questions)
    passed = 0
    failures: list[dict[str, str]] = []
    quotes = {document["citation"]: document["quote"] for document in documents}
    for item in questions:
        try:
            result = await answer_question(
                ladder,
                ladder,
                model,
                tenant_id=str(tenant_id),
                home_region=home,
                box_region=box,
                jurisdiction=jurisdiction,
                question=item["question"],
                passages=_passages_for(documents, item),
                features={"asked": "yes"},
                provider=provider,
            )
            answer = str((result.get("memo") or {}).get("text") or "") if result.get("ok") else str(result.get("reason") or "")
        except Exception:
            answer = ""
        if item["kind"] == "trap":
            ok = (
                "cannot find" in answer.lower()
                and "I could not verify" in answer
                and "I have left it out" in answer
            )
        else:
            quote = quotes.get(item["citation"]) or item.get("quote") or ""
            ok = "cannot find" not in answer.lower() and item["citation"] in answer and bool(quote) and quote in answer
        if ok:
            passed += 1
        elif len(failures) < 3:
            failures.append({"question": item["question"], "reason": answer[:500]})
    asked = len(questions)
    perfect = passed == asked == 50
    standing = "Zenas skills may be approved." if perfect else "Zenas skills may not be approved."
    return {
        "asked": asked,
        "passed": passed,
        "skills_may_be_approved": perfect,
        "path": "answer_question",
        "area": f"Made-up cases: {passed} of {asked}. path: answer_question. {standing}",
        "questions": questions,
        "failures": failures,
    }


def zenas_skills_may_be_approved(result: dict[str, Any] | None = None) -> bool:
    """True only when the real answer path scored every made-up question."""
    if not isinstance(result, dict) or result.get("path") != "answer_question":
        return False
    return bool(result.get("skills_may_be_approved"))


def _made_up_row_from(result: dict[str, Any], box: str, jurisdiction: str) -> dict[str, Any]:
    path = str(result.get("path") or "not run")
    ran = path == "answer_question"
    asked = int(result.get("asked") or 0) if ran else 0
    passed = int(result.get("passed") or 0) if ran else 0
    score = round(100.0 * passed / asked, 2) if ran and asked else 0
    approved = bool(result.get("skills_may_be_approved")) if ran else False
    area = str(result.get("area") or "Made-up cases: not run.")
    if not ran:
        area = "Made-up cases: not run."
    return {
        "region": box,
        "kind": "holdout",
        "area": area,
        "jurisdiction": jurisdiction,
        "cases": asked if ran else 0,
        "outcome_agreement": score,
        "element_agreement": score,
        "citations_valid": score,
        "cost_per_case": 0,
        "detail": {
            "case_ids": [],
            "made_up": True,
            "path": path if ran else "not run",
            "asked": asked,
            "passed": passed,
            "skills_may_be_approved": approved,
        },
    }


async def _made_up_row(
    box: str,
    jurisdiction: str,
    *,
    ladder: Any = None,
    library: list[dict[str, str]] | None = None,
    model: Any = None,
) -> dict[str, Any]:
    """One self-test row. No library or no model stays not run, never 50 of 50."""
    result = await run_made_up_test(ladder, ladder, model, library=library, region=box)
    return _made_up_row_from(result, box, jurisdiction)


async def run_selftest(
    ladder: Any,
    *,
    region: str,
    library: list[dict[str, str]] | None = None,
    model: Any = None,
) -> dict[str, Any]:
    """Score this box's held-out cases. The other region's cases are ignored."""
    box = "za" if region == "za" else "us-east"
    jurisdiction = box_jurisdiction(box)
    raw = await ladder.holdout_cases(region=box, jurisdiction=jurisdiction)
    cases = [
        row
        for row in raw
        if row.get("region") == box and row.get("jurisdiction") == jurisdiction and row.get("holdout")
    ]
    scored = [await _one(ladder, row, region=box) for row in cases]
    groups = _group(scored) if scored else []
    saved = []
    if not groups:
        row = await ladder.add_selftest(
            {
                "region": box,
                "kind": "holdout",
                "area": "",
                "jurisdiction": jurisdiction,
                "cases": 0,
                "outcome_agreement": 0,
                "element_agreement": 0,
                "citations_valid": 0,
                "cost_per_case": 0,
                "detail": {"case_ids": [], "baseline": False},
            }
        )
        saved.append(row)
    for group in groups:
        detail: dict[str, Any] = {"case_ids": group["case_ids"]}
        if group.get("error"):
            detail["error"] = True
            detail["reason"] = group["reason"]
        row = await ladder.add_selftest(
            {
                "region": box,
                "kind": "holdout",
                "area": group["area"],
                "jurisdiction": group["jurisdiction"],
                "cases": group["cases"],
                "outcome_agreement": group["outcome_agreement"],
                "element_agreement": group["element_agreement"],
                "citations_valid": group["citations_valid"],
                "cost_per_case": group["cost_per_case"],
                "detail": detail,
            }
        )
        saved.append(row)
    if box == "us-east":
        for item in legalbench_scores():
            saved.append(await ladder.add_selftest({"region": box, **item}))
    documents = library
    answer_model = model
    http = None
    if documents is None:
        documents = await load_case_library(getattr(ladder, "pool", None), region=box)
    if answer_model is None and documents:
        answer_model, http = _tank_model()
    try:
        saved.append(
            await ladder.add_selftest(
                await _made_up_row(box, jurisdiction, ladder=ladder, library=documents, model=answer_model)
            )
        )
    finally:
        if http is not None:
            await http.aclose()
    return {
        "ok": True,
        "region": box,
        "runs": [_public_run(row) for row in saved],
    }


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    """Scores only. No judgment text, no matter, no document body.

    A LegalBench row is a code baseline. The label is on the response so a
    reader does not take the percentage for a model score.
    """
    detail = row.get("detail") or {}
    kind = str(row.get("kind") or "")
    error = bool(detail.get("error"))
    reason = str(detail.get("reason") or "") if error else ""
    if error and reason not in _ERROR_REASONS:
        reason = "the ladder did not run"
    published: dict[str, Any] = {
        "id": row.get("id"),
        "region": row.get("region"),
        "kind": kind,
        "area": row.get("area") or "",
        "jurisdiction": row.get("jurisdiction") or "",
        "cases": int(row.get("cases") or 0),
        "outcome_agreement": float(row.get("outcome_agreement") or 0),
        "element_agreement": float(row.get("element_agreement") or 0),
        "citations_valid": float(row.get("citations_valid") or 0),
        "cost_per_case": float(row.get("cost_per_case") or 0),
        "case_ids": [str(item) for item in (detail.get("case_ids") or [])],
        "set": str(detail.get("set") or ""),
        "scored_by": "",
        "error": error,
        "reason": reason,
    }
    if error:
        published["label"] = "error"
    if kind == "legalbench":
        published["scored_by"] = "code baseline"
        published["label"] = "LegalBench code baseline"
        published["model"] = False
    return published


async def list_scores(ladder: Any, *, region: str) -> dict[str, Any]:
    box = "za" if region == "za" else "us-east"
    rows = await ladder.list_selftest(region=box)
    return {"ok": True, "region": box, "runs": [_public_run(row) for row in rows]}


async def run_scheduled(conn: Any, *, region: str) -> None:
    """Nightly, after the pool load. This box's library is scored through answer_question.

    Model calls go to Tank on this box. A failure here does not change the pool report.
    """
    try:
        import os

        os.nice(19)
    except OSError:
        pass
    from agents.zenas.core.ladder_store import PgLadder
    from agents.zenas.core.store import PgStore

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

    pool = conn if hasattr(conn, "acquire") else _Pool(conn)
    store = PgStore(pool)
    try:
        await store.apply_schema()
    except Exception:
        return
    ladder = PgLadder(pool)
    library = await load_case_library(conn, region=region)
    model, http = _tank_model()
    try:
        await run_selftest(ladder, region=region, library=library, model=model)
    finally:
        if http is not None:
            await http.aclose()
