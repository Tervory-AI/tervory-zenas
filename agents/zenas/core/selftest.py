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
                "case": f"trap-{index:02d}",
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
                "case": f"real-{index:02d}",
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


# Where the court's own words start: paragraph [1], or "1. The ..." in older judgments.
_FIRST_PARAGRAPH = (
    re.compile(r"(?m)^[ \t]*\[[ \t]*1[ \t]*\]"),
    re.compile(r"(?m)^[ \t]*1\.?[ \t]+[A-Z][a-z]+[ \t]+[a-z]"),
)
# Header lines: court names, captions, case numbers, dates, parties and their roles.
_HEADER_START = re.compile(
    r"^\W*(?:coram|before|heard|delivered|decided|date|dates|case\s*(?:no|number)|cac\s+case|ct\s+case|"
    r"neutral\s+citation|in\s+the\s+(?:matter|appeal|high|supreme|labour|land|tax|competition|constitutional)|"
    r"judgment|order|summary|reportable|of\s+interest|revised|signature|counsel|attorneys?|for\s+the\s+"
    r"(?:applicant|appellant|respondent|plaintiff|defendant|state)|held\s+at|republic\s+of|reasons|"
    r"this\s+judgment\s+(?:was|has\s+been)\s+handed|heard\s+on|judges?\b)",
    re.I,
)
_PARTY_ROLE = re.compile(
    r"\b(?:applicants?|appellants?|respondents?|plaintiffs?|defendants?|excipients?|intervenors?|"
    r"amicus\s+curiae|(?:acquiring|target)\s+firms?)\s*$",
    re.I,
)
_DATE_ONLY = re.compile(r"^\W*\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s+\d{4}\W*$")
_CASE_NUMBER = re.compile(r"\b(?:[A-Z]{1,6}\s?)?\d{1,6}\s?/\s?(?:[A-Z]{2,5}\s?/\s?)?[A-Za-z]{0,3}\s?\d{2,4}\b")
_PARAGRAPH_MARK = re.compile(r"^(?:\[\s*\d+\s*\]|\(?\d{1,3}[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.])\s+(?=[\[(\"\u201cA-Z0-9])")
# "Notice No. 42", "para. 7", "s. 49C": a full stop that does not end the sentence.
_ABBREVIATION_END = re.compile(r"(?:^|[\s(])(?:No|Nos|no|nos|para|paras|s|ss|v|vs|Mr|Mrs|Ms|Dr|Prof|cf|e\.g|i\.e|Reg|Art)\.$")
_BYLINE = re.compile(r"(circuit judge|district judge|per curiam|justice[.:]?$|after stat\w* the facts)", re.I)
# "KRIEGLER J:" and "MOJAPELO AJA (Ponnan JA concurring):" open a judgment; they are not the court's reasoning.
_JUDGE_OPENING = re.compile(r"^[A-Z][A-Z'\u2019 -]+\s(?:A?J|A?JA|JP|DJP|AJP|CJ|DCJ|P)\b")


def _header_line(line: str) -> bool:
    """A line from a court header or caption, never a quote."""
    text = " ".join(line.split())
    if not text:
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) >= 3 and sum(ch.isupper() for ch in letters) >= 0.7 * len(letters):
        return True
    if _HEADER_START.search(text) or _PARTY_ROLE.search(text) or _DATE_ONLY.match(text):
        return True
    if re.fullmatch(r"[\W\d_]+", text):
        return True
    words = text.split()
    if len(words) < 4 and not text.endswith((".", ",", ";", ":")):
        return True
    if len(words) < 10 and _CASE_NUMBER.search(text) and not text.endswith("."):
        return True
    return False


def _judgment_body(text: str) -> str:
    """The judgment from its first numbered paragraph on; the whole text when unnumbered."""
    body = (text or "").replace(chr(13), chr(10))
    starts = [found.start() for pattern in _FIRST_PARAGRAPH for found in [pattern.search(body)] if found]
    return body[min(starts):] if starts else body


def library_passage(text: str, *, limit: int = 4000) -> str:
    """The text Zenas reads for a library case: the judgment body, whitespace folded, header left out."""
    return " ".join(_judgment_body(text).split())[:limit]


def _sentence_ok(sentence: str) -> bool:
    words = sentence.split()
    if not 5 <= len(words) <= 60 or not 25 <= len(sentence) <= 320:
        return False
    if not sentence.endswith(".") or re.search(r"(?:[;,]\s\(?[a-z]|\s\(?\d{1,2})[.)]$", sentence) or not (sentence[0].isupper() or sentence[0] in "\"\u201c("):
        return False
    letters = [ch for ch in sentence if ch.isalpha()]
    if not letters or sum(ch.islower() for ch in letters) < 0.6 * len(letters):
        return False
    return not (_BYLINE.search(sentence) or _JUDGE_OPENING.match(sentence) or _HEADER_START.search(sentence) or _PARTY_ROLE.search(sentence.rstrip(".")))


def _sentences(joined: str) -> list[str]:
    """Split on full stops, but not after "No." or "para." and the like."""
    out: list[str] = []
    for piece in _SENTENCE_END.split(joined):
        if out and _ABBREVIATION_END.search(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def _verbatim_quote(text: str, *, limit: int = 4000) -> str:
    """A whole sentence of the court's own reasoning, word for word from the stored judgment.

    The quote comes from the judgment body (paragraph [1] on), never from the court
    header, the caption, case numbers, dates, or party and role lines. A sentence is
    taken only from consecutive non-header lines, so it is a whitespace-folded copy
    of the judgment, and it must sit in ``library_passage(text)``. Sentences that
    personal-information masking would alter are skipped, because rule-card
    cleaning runs quotes through that mask before cite-check. No sentence: "",
    and that case is left out of the library rather than scored on a header.
    """
    try:
        from agents.zenas.core.engine import _clean_piece
    except Exception:  # pragma: no cover
        _clean_piece = None  # type: ignore

    passage = library_passage(text, limit=limit)
    blocks: list[list[str]] = [[]]
    for line in _judgment_body(text).split(chr(10)):
        if _header_line(line):
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line)
    for block in blocks:
        joined = " ".join(" ".join(block).split())
        for raw in _sentences(joined):
            sentence = _PARAGRAPH_MARK.sub("", raw.strip()).strip()
            if not _sentence_ok(sentence) or sentence not in passage:
                continue
            if _clean_piece is not None and _clean_piece(sentence, []) != sentence:
                continue
            return sentence
    return ""


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
       citation, title, left(text, 12000) AS text
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
        raw = _row_text(row, "text")
        quote = _verbatim_quote(raw)
        text = library_passage(raw)
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
        "misses": [],
        "failures": [],
    }


# Why a made-up case missed. Fixed phrases, so a run can be grouped and compared.
MISS_REASONS = (
    "error: the answer path raised",
    "refused: the route refused the call",
    "trap: answered a case that does not exist",
    "trap: refusal is missing 'I could not verify'",
    "trap: refusal is missing 'I have left it out'",
    "real: said it cannot find a library case",
    "real: the answer does not give the citation",
    "real: the answer does not quote the court's sentence",
    "real: no quote to check",
)
_MISS_ANSWER_CHARS = 300


# A quote shorter than this is not the court's words but a phrase. Same floor as _sentence_ok.
_QUOTE_MIN_WORDS = 5
_QUOTE_MIN_CHARS = 25


def _fold(value: Any) -> str:
    return " ".join(str(value or "").split())


def supported_quotes(result: dict[str, Any] | None, citation: str, judgment_text: str) -> list[str]:
    """Quotes in this answer's rule card that the named judgment says word for word.

    A quote counts only when its element cites this case, the quote sits VERBATIM
    (whitespace folded, nothing else) in the stored, normalised judgment text, and
    cite-check passes it against that judgment alone. A quote shown masked counts
    only when the exact unmasked words it was built from are in that judgment. A paraphrase, or a sentence
    from a different judgment, is never returned.
    """
    from agents.zenas.core.engine import cite_check

    text = _fold(judgment_text)
    wanted = _fold(citation)
    card = (result or {}).get("rule_card") if isinstance(result, dict) else None
    elements = (card or {}).get("elements") if isinstance(card, dict) else None
    if not text or not wanted or not isinstance(elements, list):
        return []
    passage = [{"citation": wanted, "text": text, "status": "in_force"}]
    out: list[str] = []
    for element in elements:
        if not isinstance(element, dict) or _fold(element.get("citation")) != wanted:
            continue
        quote = _fold(element.get("quote"))
        sha = str(element.get("quote_sha") or "")
        if len(quote.split()) < _QUOTE_MIN_WORDS or len(quote) < _QUOTE_MIN_CHARS:
            continue
        # A masked quote (a date shown as [Date]) counts only through its fingerprint:
        # the exact unmasked words must be in this judgment (engine.masked_quote_in_source).
        if quote not in text and not sha:
            continue
        if not cite_check([{"citation": wanted, "quote": quote, "quote_sha": sha}], passage).get("passed"):
            continue
        out.append(quote)
    return out


def _quotes_the_judgment(answer: str, quote: str, supported: list[str]) -> bool:
    """The answer quotes the court: the library sentence, or any verbatim, cite-checked quote from it."""
    folded = _fold(answer)
    if quote and (quote in answer or _fold(quote) in folded):
        return True
    return any(item in folded for item in supported)


def _miss_reason(
    item: dict[str, str],
    answer: str,
    *,
    ok_result: bool,
    raised: str,
    quote: str,
    supported: list[str] | None = None,
) -> str:
    """The first check this answer failed, as one of MISS_REASONS. Empty when it passed.

    A real case passes on a quote that is verbatim anywhere in the named judgment
    and supports the answer (see supported_quotes), not only on its first sentence.
    """
    if raised:
        return MISS_REASONS[0]
    if item["kind"] == "trap":
        if "cannot find" not in answer.lower():
            return MISS_REASONS[1] if not ok_result else MISS_REASONS[2]
        if "I could not verify" not in answer:
            return MISS_REASONS[3]
        if "I have left it out" not in answer:
            return MISS_REASONS[4]
        return ""
    if not ok_result:
        return MISS_REASONS[1]
    if "cannot find" in answer.lower():
        return MISS_REASONS[5]
    if item["citation"] not in answer:
        return MISS_REASONS[6]
    if not quote:
        return MISS_REASONS[8]
    if not _quotes_the_judgment(answer, quote, supported or []):
        return MISS_REASONS[7]
    return ""


def _miss(item: dict[str, str], reason: str, answer: str, *, raised: str, refusal: str, quote: str) -> dict[str, str]:
    """One missed case as it is stored in the run detail."""
    out = {
        "case": str(item.get("case") or ""),
        "kind": str(item.get("kind") or ""),
        "citation": str(item.get("citation") or ""),
        "reason": reason,
        "question": str(item.get("question") or ""),
        "answer": " ".join(answer.split())[:_MISS_ANSWER_CHARS],
    }
    if raised:
        out["error"] = raised
    if refusal:
        out["refusal"] = " ".join(refusal.split())[:200]
    if quote and item.get("kind") == "real":
        out["expected_quote"] = quote
    return out


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
    misses: list[dict[str, str]] = []
    quotes = {document["citation"]: document["quote"] for document in documents}
    texts = {document["citation"]: document["text"] for document in documents}
    for item in questions:
        raised = ""
        refusal = ""
        ok_result = False
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
            ok_result = bool(result.get("ok"))
            answer = str((result.get("memo") or {}).get("text") or "") if ok_result else str(result.get("reason") or "")
            if not ok_result:
                refusal = answer
        except Exception as exc:  # noqa: BLE001 - one case does not stop the run; the class is kept
            answer = ""
            raised = type(exc).__name__
            result = {}
        quote = (quotes.get(item["citation"]) or item.get("quote") or "") if item["kind"] == "real" else ""
        supported = (
            supported_quotes(result, item["citation"], texts.get(item["citation"], ""))
            if item["kind"] == "real" and ok_result
            else []
        )
        reason = _miss_reason(item, answer, ok_result=ok_result, raised=raised, quote=quote, supported=supported)
        if not reason:
            passed += 1
        else:
            misses.append(_miss(item, reason, answer, raised=raised, refusal=refusal, quote=quote))
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
        "misses": misses,
        # The first three, in the shape older readers expect.
        "failures": [{"question": miss["question"], "reason": miss["answer"]} for miss in misses[:3]],
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
            "misses": list(result.get("misses") or []) if ran else [],
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
    if detail.get("made_up"):
        published["passed"] = int(detail.get("passed") or 0)
        published["misses"] = _public_misses(detail.get("misses"))
    if error:
        published["label"] = "error"
    if kind == "legalbench":
        published["scored_by"] = "code baseline"
        published["label"] = "LegalBench code baseline"
        published["model"] = False
    return published


def _public_misses(raw: Any) -> list[dict[str, str]]:
    """Each missed made-up case: id, kind, citation and reason.

    The stored detail also keeps the question, the start of the answer and the
    expected quote. Those are not published: this response carries no judgment text.
    """
    out: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        miss = {key: str(item.get(key) or "") for key in ("case", "kind", "citation", "reason")}
        if miss["reason"] not in MISS_REASONS:
            miss["reason"] = MISS_REASONS[0] if item.get("error") else "unknown"
        for key in ("refusal", "error"):
            if item.get(key):
                miss[key] = " ".join(str(item[key]).split())[:200]
        out.append(miss)
    return out


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
