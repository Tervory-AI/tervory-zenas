"""The legal ladder for one Zenas box. Writes stay in the tenant's home region.

Public statutes and judgments may be read from this box's pool whatever their
jurisdiction. Client facts, released matters, training pairs, tenant-tied rule
cards, and memory are stored here only when this box is the tenant's home.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from agents.zenas.core.engine import (
    CHEAP_CALL_USD,
    IDENTIFIER_REFUSED,
    TOP_CALL_USD,
    card_from_proposal,
    cite_check,
    compose_memo,
    decide_without_model,
    law_only_problem,
    normalize_box,
    parse_case_facts,
    parse_json_object,
    pii_detect,
    prepare_prompt,
    home_cross_check,
    question_key,
    rank_worked,
    region_refusal,
    retrieve_passages,
    sanitize_match,
    sharing_mode,
    summarize,
    support_line,
    wording_keeps_law,
)
from agents.zenas.core.docx_redline import docx_bytes
from agents.zenas.core.drafting import TEMPLATES, assemble_draft, select_prior
from agents.zenas.core.matters import consider
from agents.zenas.core.review import review_positions
from agents.zenas.core.review_switch import card_servable, review_required
from agents.zenas.core.scope_rules import NOT_YET, REFUSED, classify, ruled
from agents.zenas.core.precedent import draft_label, drop_unverified_cases, rules_are_draft
from agents.zenas.core.selftest import is_holdout
from agents.zenas.core.citation_sources import cited_sources, public_source_url

# Whole passages, best first. A passage is never sliced to fit.
_PASSAGE_BUDGET = 8000
_LEGAL_NOTICE = "Zenas provides legal information, not legal advice."


def _with_legal_notice(text: str) -> str:
    text = text.rstrip()
    return text if text.endswith(_LEGAL_NOTICE) else f"{text}\n\n{_LEGAL_NOTICE}"

_RULE_PROMPT = """Build a rule card from these passages only. Reply with JSON:
{{"elements":[{{"id":"","test":"","citation":"","quote":""}}],"exceptions":[],"defences":[],"remedies":[],"decision_table":[]}}
Every quote must be copied from a passage. Do not add law. Do not include client facts.

Question: {question}

Passages:
{passages}
"""

_APPLY_PROMPT = """Decide one element. Reply with JSON only:
{{"finding":"met|not_met|unclear","support":"","missing":""}}
Use only the facts and the test. Do not add law.

Test: {test}
Citation: {citation}
Facts: {facts}
"""

_SMOOTH_PROMPT = """Smooth the wording of this memo. Do not add law, citations, or facts.
Keep every citation that is already there.

{text}
"""


def passages_for_model(passages: list[dict[str, Any]], *, budget: int = _PASSAGE_BUDGET) -> tuple[list[dict[str, Any]], int]:
    """As many whole passages as fit, in the order given. The first is kept even when it is long."""
    kept: list[dict[str, Any]] = []
    for index, passage in enumerate(passages):
        trial = kept + [passage]
        if kept and len(json.dumps(trial)) > budget:
            return kept, len(passages) - index
        kept.append(passage)
        if len(json.dumps(kept)) > budget:
            return kept, len(passages) - index - 1
    return kept, 0


def rule_card_prompt(question: str, passages: list[dict[str, Any]]) -> str:
    kept, left_out = passages_for_model(passages)
    body = json.dumps(kept)
    if left_out == 1:
        body += "\n1 passage was left out. Each passage above is whole."
    elif left_out:
        body += f"\n{left_out} passages were left out. Each passage above is whole."
    return _RULE_PROMPT.format(question=question, passages=body)


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": False, "reason": reason}
    body.update(extra)
    return body


def _box(box_region: str) -> str:
    return normalize_box(box_region)


async def _use_home(store: Any, *, tenant_id: str, home_region: str, box_region: str) -> tuple[str, dict[str, Any] | None]:
    """The tenant record holds the home region. The request only confirms it."""
    row = await store.get_tenant(str(tenant_id))
    stored = None if row is None else str(row.get("home_region") or "")
    problem = home_cross_check(stored or None, home_region)
    if problem:
        return "", _refuse(problem)
    refusal = region_refusal(stored, box_region)
    if refusal:
        return "", _refuse(refusal)
    return stored, None


async def _gate(tank: Any, *, tenant_id: str, text: str, jurisdiction: str | None) -> dict[str, Any]:
    if tank is None:
        decision = classify(text or "", jurisdiction if jurisdiction in ("us", "za", "both") else None)
        stopped = bool(decision.get("criminal") or not decision.get("in_scope"))
        return {"stopped": stopped, "scope": decision}
    return await consider(
        tank,
        tenant_id=str(tenant_id),
        text=text or "",
        jurisdiction=jurisdiction,
    )


class ModelAdapter:
    def __init__(self, model: Any) -> None:
        self.model = model
        self.calls = 0
        self.sent: list[Any] = []

    async def complete(self, messages: list[dict[str, str]], *, task_type: str, provider: str) -> dict[str, Any]:
        self.calls += 1
        self.sent.append({"task_type": task_type, "provider": provider, "messages": messages})
        if hasattr(self.model, "complete"):
            result = await self.model.complete(messages, task_type=task_type, provider=provider)
        else:
            result = await self.model.model(messages, task_type=task_type)
        if isinstance(result, str):
            cost = TOP_CALL_USD if task_type == "zenas.rule_card" else CHEAP_CALL_USD
            return {"content": result, "cost_usd": cost, "model": provider or "luna"}
        content = str(result.get("content") or "")
        cost = result.get("cost_usd")
        if cost is None:
            cost = TOP_CALL_USD if task_type == "zenas.rule_card" else CHEAP_CALL_USD
        return {
            "content": content,
            "cost_usd": float(cost),
            "model": str(result.get("model") or provider or "luna"),
        }


async def _masked_call(
    model: ModelAdapter | None,
    text: str,
    *,
    home_region: str,
    provider: str,
    task_type: str,
) -> dict[str, Any]:
    prepared = prepare_prompt(text, home_region=home_region, provider=provider)
    if not prepared["ok"]:
        return _refuse(prepared["reason"] or IDENTIFIER_REFUSED)
    if model is None:
        return _refuse("no model is configured")
    result = await model.complete(
        [{"role": "user", "content": prepared["text"]}],
        task_type=task_type,
        provider=provider,
    )
    return {"ok": True, "result": result, "masked": prepared["masked"]}


def _public_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "region": card.get("region"),
        "jurisdiction": card.get("jurisdiction"),
        "question": card.get("question"),
        "elements": card.get("elements") or [],
        "exceptions": card.get("exceptions") or [],
        "defences": card.get("defences") or [],
        "remedies": card.get("remedies") or [],
        "sources": card.get("sources") or [],
        "as_at": card.get("as_at"),
        "status": "active" if card.get("active") and card.get("status") == "draft" else card.get("status"),
        "active": bool(card.get("active")),
        "approved_by": card.get("approved_by"),
        "reviewer_kind": card.get("reviewer_kind") or "",
        "model_used": card.get("model_used"),
        "cost_usd": float(card.get("cost_usd") or 0),
        "decision_table": card.get("decision_table") or [],
        "edit_count": int(card.get("edit_count") or 0),
    }


async def retrieve(
    ladder: Any,
    tank: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    jurisdiction: str,
    query: str = "",
    citation: str = "",
    as_at: str | None = None,
    level: str | None = None,
    kind: str | None = None,
    corpus: list[dict[str, Any]] | None = None,
    pool: Any = None,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    gate = await _gate(tank, tenant_id=tenant_id, text=query or citation, jurisdiction=jurisdiction)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    if corpus is not None:
        passages = retrieve_passages(
            corpus,
            query=query,
            citation=citation,
            jurisdiction=jurisdiction,
            as_at=as_at,
            level=level,
            kind=kind,
        )
    else:
        passages = await _passages_from_pool(
            pool,
            query=query,
            citation=citation,
            jurisdiction=jurisdiction,
            as_at=as_at,
            level=level,
            kind=kind,
        )
    return {"ok": True, "passages": passages}


_SEARCH_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "under",
        "was",
        "were",
        "with",
        "a",
        "does",
        "do",
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "can",
        "could",
        "should",
        "would",
        "may",
        "might",
        "this",
        "these",
        "those",
        "about",
        "into",
        "over",
        "than",
        "then",
        "its",
        "our",
        "your",
        "their",
        "united",
        "states",
    }
)


def _search_query(query: str) -> str:
    """Drop English stop words so Ask questions can hit MemoryStore and FTS alike."""
    raw = re.findall(r"[a-z0-9§.]+", query.lower())
    tokens = [tok for tok in raw if len(tok) > 1 and tok not in _SEARCH_STOP]
    return " ".join(tokens) if tokens else query.strip()


async def _passages_from_pool(
    pool: Any,
    *,
    query: str,
    citation: str,
    jurisdiction: str,
    as_at: str | None,
    level: str | None,
    kind: str | None,
) -> list[dict[str, Any]]:
    if pool is None:
        return []
    from datetime import date

    day = date.fromisoformat(as_at) if as_at else None
    law = jurisdiction if jurisdiction in ("us", "za") else "us"
    chunks: list[Any] = []
    if citation.strip() and hasattr(pool, "find_by_citation"):
        chunks.extend(await pool.find_by_citation(citation, jurisdiction=law, as_at=day))
    search = _search_query(query)
    if search and hasattr(pool, "search_text"):
        chunks.extend(
            await pool.search_text(search, jurisdiction=law, as_at=day, level=level, kind=kind, limit=8)
        )
    passages = []
    seen: set[str] = set()
    metadata = await pool.source_metadata(chunks, jurisdiction=law) if hasattr(pool, "source_metadata") else {}
    for chunk in chunks:
        key = getattr(chunk, "chunk_id", "") or str(chunk)
        if key in seen:
            continue
        seen.add(key)
        passages.append(
            {
                "doc_id": chunk.document_id,
                "version_id": chunk.version_id,
                "citation": chunk.citation,
                "heading": chunk.heading,
                "text": chunk.text,
                "path": chunk.path,
                "jurisdiction": chunk.jurisdiction,
                "kind": kind or "",
                "level": level or "",
                "status": "effective",
                **metadata.get((chunk.document_id, chunk.version_id), {}),
            }
        )
    return passages


async def check_citations(
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    statements: list[dict[str, Any]],
    passages: list[dict[str, Any]],
    tank: Any = None,
    jurisdiction: str | None = None,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    text = " ".join(str(item.get("quote") or "") for item in statements)
    gate = await _gate(tank, tenant_id="", text=text, jurisdiction=jurisdiction)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    result = cite_check(statements, passages)
    return {"ok": True, **result}


async def build_rule_card(
    ladder: Any,
    tank: Any,
    model: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    jurisdiction: str,
    question: str,
    passages: list[dict[str, Any]],
    as_at: str | None = None,
    client_facts: str = "",
    provider: str = "luna",
    scope_text: str = "",
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    gate = await _gate(tank, tenant_id=tenant_id, text=scope_text or question, jurisdiction=jurisdiction)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    box = _box(box_region)
    key = question_key(question)
    existing = await ladder.find_approved(region=box, jurisdiction=jurisdiction, key=key, tenant_id=tenant_id)
    if existing is not None:
        await ladder.add_run(
            {
                "region": box,
                "tenant_id": tenant_id,
                "kind": "rule_card",
                "question_key": key,
                "cost_usd": 0,
                "elements": len(existing.get("elements") or []),
            }
        )
        return {"ok": True, "rule_card": _public_card(existing), "reused": True, "cost_usd": 0, "model_calls": 0}
    adapter = ModelAdapter(model) if model is not None else None
    prompt = rule_card_prompt(question, passages)
    called = await _masked_call(
        adapter,
        prompt,
        home_region=home_region,
        provider=provider,
        task_type="zenas.rule_card",
    )
    if not called["ok"]:
        return called
    try:
        proposal = parse_json_object(called["result"]["content"])
    except (ValueError, json.JSONDecodeError):
        return _refuse("the model did not return a rule card")
    built = card_from_proposal(proposal, passages, question=question, secrets=[client_facts] if client_facts else [])
    problem = law_only_problem(built, [client_facts] if client_facts else [])
    if problem:
        return _refuse(problem)
    cost = float(called["result"]["cost_usd"])
    row = await ladder.add_rule_card(
        {
            "region": box,
            "tenant_id": None,
            "jurisdiction": jurisdiction,
            "question": built["question"],
            "elements": built["elements"],
            "exceptions": built["exceptions"],
            "defences": built["defences"],
            "remedies": built["remedies"],
            "sources": built["sources"],
            "as_at": as_at,
            "status": "draft",
            "active": bool(built["cite"]["passed"]) and not await review_required(ladder, region=box),
            "model_used": called["result"]["model"],
            "cost_usd": cost,
            "decision_table": built["decision_table"],
        }
    )
    await ladder.add_run(
        {
            "region": box,
            "tenant_id": tenant_id,
            "kind": "rule_card",
            "question_key": key,
            "cost_usd": cost,
            "elements": len(built["elements"]),
            "modeled": 1,
        }
    )
    return {
        "ok": True,
        "rule_card": _public_card(row),
        "reused": False,
        "cost_usd": cost,
        "approvable": built["cite"]["passed"],
        "cite": built["cite"],
        "model_calls": 1,
    }


async def approve_rule_card(
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    card_id: str,
    reviewer: str,
    admission_no: str,
    jurisdiction: str,
    passages: list[dict[str, Any]],
    changes: str = "",
    reviewer_kind: str = "reviewer",
) -> dict[str, Any]:
    del jurisdiction
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    from agents.zenas.core.matters import sign_off_identity

    identity = sign_off_identity(reviewer_kind=reviewer_kind, admission_no=admission_no)
    if not reviewer or identity is None:
        return _refuse("approval needs a reviewer and an admission number")
    kind, number = identity
    card = await ladder.get_rule_card(card_id, region=_box(box_region), tenant_id=tenant_id)
    if card is None:
        return _refuse("no such rule card")
    if card.get("status") == "stale":
        return _refuse("this rule card is stale")
    statements = [
        {"citation": element.get("citation"), "quote": element.get("quote")}
        for element in card.get("elements") or []
    ]
    checked = cite_check(statements, passages)
    if not checked["passed"]:
        return _refuse("a citation did not resolve to the pool", cite=checked)
    edit_count = int(card.get("edit_count") or 0)
    if changes.strip():
        edit_count += 1
    saved = await ladder.set_status(
        card_id,
        region=_box(box_region),
        status="lawyer_approved",
        approved_by=reviewer,
        admission_no=number,
        reviewer_kind=kind,
        edit_count=edit_count,
    )
    return {"ok": True, "rule_card": _public_card(saved or card), "cite": checked}


async def mark_stale(
    ladder: Any,
    *,
    box_region: str,
    doc_id: str,
    version_id: str | None = None,
    event: str = "version",
) -> dict[str, Any]:
    del version_id, event
    if not doc_id:
        return _refuse("a source change needs a document id")
    cards = await ladder.cards_using(region=_box(box_region), doc_id=doc_id)
    stale_ids = []
    for card in cards:
        if card.get("status") == "stale":
            stale_ids.append(card["id"])
            continue
        await ladder.set_status(card["id"], region=_box(box_region), status="stale")
        stale_ids.append(card["id"])
    return {"ok": True, "stale_ids": stale_ids}


async def read_rule_card(
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    card_id: str,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    card = await ladder.get_rule_card(card_id, region=_box(box_region), tenant_id=tenant_id)
    if card is None:
        return _refuse("no such rule card")
    return {"ok": True, "rule_card": _public_card(card)}


# Tank refuses a body that is not this wrapper plus the pool row. Keep the two equal.
FACTS_PROMPT_PREFIX = (
    "Read this judgment as published. Reply with JSON only: "
    '{"features":{},"narrative":"","element_findings":[{"element_id":"","finding":"met|not_met|unclear","reasoning":"","basis":[]}],"outcome":""}\n\n'
)
# Same sentence as Tank's IDENTIFIER_REFUSAL. A body judgment is not a pool read.
BODY_TEXT_REFUSED = "A personal identifier cannot be sent to a provider outside South Africa."


def judgment_from_pool_row(
    body_judgment: str,
    body_source_id: str,
    row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use the pool row. The body's judgment and source_id are not the source.

    A judgment in the body that is not the stored text is refused. An empty
    body uses the row. A missing court row does not fall back to the body.
    """
    del body_source_id
    text = str((row or {}).get("text") or "")
    source = str((row or {}).get("source_id") or "")
    from agents.zenas.core.release_mask import material_type_for_pool

    supplied = str(body_judgment or "").strip()
    if material_type_for_pool(source) != "public_record" or not text.strip():
        if supplied:
            return {"ok": False, "reason": BODY_TEXT_REFUSED}
        return {"ok": False, "shared": False, "reason": "material type is unknown"}
    if supplied and supplied != text.strip():
        return {"ok": False, "reason": BODY_TEXT_REFUSED}
    return {
        "ok": True,
        "judgment": text,
        "source_id": source,
        "citation": str((row or {}).get("citation") or ""),
    }


async def extract_case_facts(
    ladder: Any,
    model: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    jurisdiction: str,
    pool_doc_id: str,
    pool_version_id: str = "",
    citation: str = "",
    source_id: str = "",
    judgment: str,
    rule_card_ids: list[str] | None = None,
    as_at: str | None = None,
    provider: str = "luna",
    tank: Any = None,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    from agents.zenas.core.release_mask import log_withheld, material_type_for_pool

    material = material_type_for_pool(source_id)
    if material != "public_record":
        log_withheld("material type is unknown")
        return {"ok": False, "shared": False, "reason": "material type is unknown"}
    kind = ruled(judgment)
    if kind == "criminal":
        return _refuse(REFUSED, scope=classify(judgment, jurisdiction if jurisdiction in ("us", "za", "both") else None))
    if kind == "not_yet":
        scope = classify(judgment, jurisdiction if jurisdiction in ("us", "za", "both") else None)
        return _refuse(str(scope.get("reason") or "Not offered yet."), scope=scope)
    box = _box(box_region)
    existing = await ladder.worked_for_doc(region=box, pool_doc_id=pool_doc_id)
    if existing is not None:
        return {"ok": True, "worked_matter": _public_case(existing), "cached": True, "cost_usd": 0, "model_calls": 0}
    if model is None:
        return _refuse("no model is configured")
    adapter = ModelAdapter(model)
    prompt = FACTS_PROMPT_PREFIX + judgment
    result = await adapter.complete(
        [{"role": "user", "content": prompt}],
        task_type="zenas.case_facts",
        provider=provider,
    )
    called = {"ok": True, "result": result}
    try:
        parsed = parse_case_facts(called["result"]["content"])
    except (ValueError, json.JSONDecodeError):
        return _refuse("the model did not return case facts")
    cost = float(called["result"]["cost_usd"])
    published = judgment
    held = is_holdout(pool_doc_id)
    worked = await ladder.add_worked(
        {
            "region": box,
            "tenant_id": None,
            "source": "case",
            "material_type": material,
            "jurisdiction": jurisdiction,
            "area": "",
            "rule_card_ids": rule_card_ids or [],
            "fact_pattern": {
                "features": parsed["features"],
                "narrative": published,
                "facts": str(parsed.get("narrative") or ""),
            },
            "element_findings": parsed["element_findings"],
            "outcome": parsed["outcome"],
            "lawyer_edits": "",
            "as_at": as_at,
            "pool_doc_id": pool_doc_id,
            "pool_version_id": pool_version_id,
            "holdout": held,
        }
    )
    if held:
        return {
            "ok": True,
            "worked_matter": _public_case(worked),
            "training_pair_id": None,
            "holdout": True,
            "cached": False,
            "cost_usd": cost,
            "model_calls": 1,
        }
    pair = await ladder.add_pair(
        {
            "region": box,
            "tenant_id": None,
            "source": "case",
            "worked_id": worked["id"],
            "request": citation or pool_doc_id,
            "findings": parsed["element_findings"],
            "outcome": parsed["outcome"],
            "memo": published,
            "lawyer_edits": "",
            "as_at": as_at,
        }
    )
    await ladder.add_run(
        {
            "region": box,
            "tenant_id": tenant_id,
            "kind": "case_facts",
            "question_key": pool_doc_id,
            "cost_usd": cost,
            "modeled": 1,
        }
    )
    return {
        "ok": True,
        "worked_matter": _public_case(worked),
        "training_pair_id": pair["id"],
        "cached": False,
        "cost_usd": cost,
        "model_calls": 1,
    }


def _public_case(row: dict[str, Any]) -> dict[str, Any]:
    pattern = row.get("fact_pattern") or {}
    return {
        "id": row.get("id"),
        "region": row.get("region"),
        "source": row.get("source"),
        "material_type": row.get("material_type"),
        "jurisdiction": row.get("jurisdiction"),
        "rule_card_ids": row.get("rule_card_ids") or [],
        "fact_pattern": {
            "features": pattern.get("features") or {},
            "narrative": pattern.get("narrative") or "",
            "facts": pattern.get("facts") or "",
        },
        "holdout": bool(row.get("holdout")),
        "element_findings": row.get("element_findings") or [],
        "outcome": row.get("outcome") or "",
        "pool_doc_id": row.get("pool_doc_id"),
        "as_at": row.get("as_at"),
    }


async def match_matters(
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    jurisdiction: str,
    rule_card_ids: list[str],
    features: dict[str, Any],
    narrative: str = "",
    question: str = "",
    sharing: str | None = None,
    tank: Any = None,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    gate = await _gate(
        tank,
        tenant_id=tenant_id,
        text=question or narrative,
        jurisdiction=jurisdiction,
    )
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    mode = sharing_mode(sharing)
    rows = await ladder.visible_worked(region=_box(box_region), tenant_id=tenant_id, sharing=mode)
    ranked = rank_worked(rows, rule_card_ids=rule_card_ids, features=features, narrative=narrative)
    return {"ok": True, "matches": [sanitize_match(item, tenant_id=tenant_id) for item in ranked]}


async def _ranked(
    ladder: Any,
    *,
    tenant_id: str,
    box_region: str,
    rule_card_ids: list[str],
    features: dict[str, Any],
    narrative: str,
    sharing: str | None,
) -> list[dict[str, Any]]:
    rows = await ladder.visible_worked(
        region=_box(box_region),
        tenant_id=tenant_id,
        sharing=sharing_mode(sharing),
    )
    return rank_worked(rows, rule_card_ids=rule_card_ids, features=features, narrative=narrative)


async def apply_law(
    ladder: Any,
    model: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    card_id: str,
    features: dict[str, Any],
    narrative: str = "",
    question: str = "",
    sharing: str | None = None,
    provider: str = "luna",
    tank: Any = None,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    card_for_gate = await ladder.get_rule_card(card_id, region=_box(box_region), tenant_id=tenant_id)
    gate_text = question or (str(card_for_gate.get("question") or "") if card_for_gate else "") or narrative
    gate_law = card_for_gate.get("jurisdiction") if card_for_gate else None
    gate = await _gate(tank, tenant_id=tenant_id, text=gate_text, jurisdiction=gate_law)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    card = await ladder.get_rule_card(card_id, region=_box(box_region), tenant_id=tenant_id)
    if card is None:
        return _refuse("no such rule card")
    if not card_servable(card, review_on=await review_required(ladder, region=_box(box_region))):
        return _refuse("this rule card is not approved")
    ranked = await _ranked(
        ladder,
        tenant_id=tenant_id,
        box_region=box_region,
        rule_card_ids=[card_id],
        features=features,
        narrative=narrative,
        sharing=sharing,
    )
    adapter = ModelAdapter(model) if model is not None else None
    elements: list[dict[str, Any]] = []
    cost = 0.0
    reused = coded = modeled = 0
    for element in card.get("elements") or []:
        element_id = str(element.get("id") or "")
        found = decide_without_model(
            element,
            features,
            card.get("decision_table") or [],
            ranked,
            tenant_id=tenant_id,
        )
        if found is not None:
            elements.append(found)
            if found["rung"] == "reuse":
                reused += 1
            else:
                coded += 1
            continue
        prompt = _APPLY_PROMPT.format(
            test=element.get("test") or "",
            citation=element.get("citation") or "",
            facts=json.dumps(features),
        )
        called = await _masked_call(
            adapter,
            prompt,
            home_region=home_region,
            provider=provider,
            task_type="zenas.apply",
        )
        if not called["ok"]:
            return called
        try:
            parsed = parse_json_object(called["result"]["content"])
        except (ValueError, json.JSONDecodeError):
            parsed = {"finding": "unclear", "missing": "the model did not decide"}
        finding = str(parsed.get("finding") or "unclear")
        if finding not in ("met", "not_met", "unclear"):
            finding = "unclear"
        spent = float(called["result"]["cost_usd"])
        cost += spent
        modeled += 1
        elements.append(
            {
                "element_id": element_id,
                "finding": finding,
                "support": support_line(features, []) or str(parsed.get("support") or ""),
                "missing": str(parsed.get("missing") or ""),
                "rung": "model",
                "cost_usd": spent,
                "cited_matter_id": None,
                "reasoning": "",
            }
        )
    await ladder.add_run(
        {
            "region": _box(box_region),
            "tenant_id": tenant_id,
            "kind": "apply",
            "question_key": question_key(str(card.get("question") or "")),
            "cost_usd": cost,
            "reused": reused,
            "coded": coded,
            "modeled": modeled,
            "elements": len(elements),
        }
    )
    return {
        "ok": True,
        "elements": elements,
        "cost_usd": cost,
        "reused": reused,
        "coded": coded,
        "modeled": modeled,
        "model_calls": modeled,
    }


async def write_memo(
    ladder: Any,
    model: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    card_id: str,
    features: dict[str, Any],
    narrative: str = "",
    question: str = "",
    sharing: str | None = None,
    provider: str = "luna",
    tank: Any = None,
    smooth: bool = True,
) -> dict[str, Any]:
    applied = await apply_law(
        ladder,
        model,
        tenant_id=tenant_id,
        home_region=home_region,
        box_region=box_region,
        card_id=card_id,
        features=features,
        narrative=narrative,
        question=question,
        sharing=sharing,
        provider=provider,
        tank=tank,
    )
    if not applied["ok"]:
        return applied
    card = await ladder.get_rule_card(card_id, region=_box(box_region), tenant_id=tenant_id)
    if card is None:
        return _refuse("no such rule card")
    memo = compose_memo(card, applied["elements"])
    cost = float(applied["cost_usd"])
    model_calls = int(applied.get("model_calls") or 0)
    smoothed = False
    if smooth and model is not None:
        adapter = ModelAdapter(model)
        called = await _masked_call(
            adapter,
            _SMOOTH_PROMPT.format(text=memo["text"]),
            home_region=home_region,
            provider=provider,
            task_type="zenas.compose",
        )
        if not called["ok"]:
            return called
        proposal = called["result"]["content"]
        spent = float(called["result"]["cost_usd"])
        cost += spent
        model_calls += 1
        if wording_keeps_law(memo["text"], proposal, memo["citations"]):
            memo["text"] = proposal.strip()
            smoothed = True
    memo["text"] = _with_legal_notice(memo["text"])
    return {
        "ok": True,
        "memo": memo,
        "elements": applied["elements"],
        "cost_usd": cost,
        "smoothed": smoothed,
        "model_calls": model_calls,
        "reused": applied["reused"],
        "coded": applied["coded"],
        "modeled": applied["modeled"],
    }


async def plain_language(
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    text: str,
    citations: list[str],
    model: Any,
    provider: str = "luna",
    tank: Any = None,
    jurisdiction: str | None = None,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    gate = await _gate(tank, tenant_id=tenant_id, text=text, jurisdiction=jurisdiction)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    adapter = ModelAdapter(model) if model is not None else None
    called = await _masked_call(
        adapter,
        "Rewrite for a business reader. Do not change the legal effect. Keep every citation.\n\n" + text,
        home_region=home_region,
        provider=provider,
        task_type="zenas.plain_language",
    )
    if not called["ok"]:
        return called
    proposal = called["result"]["content"]
    kept = wording_keeps_law(text, proposal, citations)
    return {
        "ok": True,
        "text": proposal.strip() if kept else text,
        "kept_original": not kept,
        "cost_usd": float(called["result"]["cost_usd"]),
        "model_calls": 1,
    }


async def release_matter(
    matters: Any,
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    matter_id: str,
    document_id: str,
    reviewer: str,
    admission_no: str,
    jurisdiction: str,
    rule_card_ids: list[str],
    features: dict[str, Any],
    narrative: str,
    element_findings: list[dict[str, Any]],
    outcome: str,
    memo: str,
    lawyer_edits: str = "",
    as_at: str | None = None,
    area: str = "",
    tank: Any = None,
    reviewer_kind: str = "reviewer",
) -> dict[str, Any]:
    _home, refused = await _use_home(matters, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    gate = await _gate(tank, tenant_id=tenant_id, text=memo or narrative, jurisdiction=jurisdiction)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    from agents.zenas.core.matters import record_review

    review = await record_review(
        matters,
        tenant_id=tenant_id,
        document_id=document_id,
        reviewer=reviewer,
        jurisdiction=jurisdiction,
        admission_no=admission_no,
        decision="released",
        changes=lawyer_edits,
        reviewer_kind=reviewer_kind,
    )
    if not review.get("ok"):
        return review
    await matters.set_document_state(tenant_id, document_id, "released")
    from agents.zenas.core.release_mask import log_withheld, mask_share

    document = await matters.get_document(str(tenant_id), str(document_id))
    matter = await matters.get_matter(str(tenant_id), str(matter_id))
    shared = mask_share(
        matter=matter,
        document=document,
        parties=(matter or {}).get("parties"),
        body=str((document or {}).get("body") or ""),
        narrative=narrative,
        features=features,
        findings=element_findings,
        lawyer_edits=lawyer_edits,
        outcome=outcome,
        memo=memo,
    )
    if not shared["ok"]:
        log_withheld("masking failed")
        return {
            "ok": True,
            "shared": False,
            "reason": "masking failed",
            "worked_matter_id": None,
            "training_pair_id": None,
            "review_id": review["review_id"],
        }
    box = _box(box_region)
    worked = await ladder.add_worked(
        {
            "region": box,
            "tenant_id": tenant_id,
            "source": "released",
            "material_type": "client_work",
            "jurisdiction": jurisdiction,
            "area": area,
            "rule_card_ids": rule_card_ids,
            "fact_pattern": {
                "features": shared["features"],
                "narrative": shared["narrative"],
                "body": shared["body"],
            },
            "element_findings": shared["findings"],
            "outcome": shared["outcome"],
            "lawyer_edits": shared["lawyer_edits"],
            "as_at": as_at,
            "released_matter_id": matter_id,
        }
    )
    pair = await ladder.add_pair(
        {
            "region": box,
            "tenant_id": tenant_id,
            "source": "released",
            "worked_id": worked["id"],
            "request": shared["narrative"],
            "findings": shared["findings"],
            "outcome": shared["outcome"],
            "memo": shared["memo"],
            "lawyer_edits": shared["lawyer_edits"],
            "as_at": as_at,
        }
    )
    return {
        "ok": True,
        "shared": True,
        "worked_matter_id": worked["id"],
        "training_pair_id": pair["id"],
        "review_id": review["review_id"],
    }


async def remember_for_tenant(
    ladder: Any,
    memory: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    text: str,
    kind: str,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return {**refused, "stored": False}
    found = pii_detect(text or "")
    masked = str(found.get("text") or "")
    for match in found.get("matches") or []:
        value = str(match.get("value") or "")
        if value and value in masked:
            return _refuse(IDENTIFIER_REFUSED, stored=False)
    await ladder.add_note({"region": _box(box_region), "tenant_id": tenant_id, "kind": kind, "body": masked})
    if memory is not None:
        await memory.remember(str(tenant_id), masked, kind)
    return {"ok": True, "stored": True, "body": masked}


def _review_tags(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _tag_allows(tags: list[str], jurisdiction: str) -> bool:
    if not tags:
        return True
    if jurisdiction == "both":
        return any(tag in ("us", "za", "both") for tag in tags)
    return jurisdiction in tags or "both" in tags


def _review_match(worked: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    source = str(worked.get("source") or "")
    owner = worked.get("tenant_id")
    public = (
        source == "case"
        or str(owner or "") == str(tenant_id)
        or str(worked.get("material_type") or "") == "client_work"
    )
    findings: list[dict[str, str]] = []
    if public:
        for part in worked.get("element_findings") or []:
            element = str(part.get("element_id") or part.get("element") or "")
            findings.append(
                {"element_id": element, "element": element, "finding": str(part.get("finding") or "")}
            )
    return {
        "id": str(worked.get("id") or ""),
        "outcome": str(worked.get("outcome") or "") if public else "",
        "jurisdiction": str(worked.get("jurisdiction") or ""),
        "element_findings": findings,
    }


async def list_matters(
    store: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
) -> dict[str, Any]:
    """Tenant-scoped matters on this home box, with released documents only."""
    _home, refused = await _use_home(store, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    rows = await store.list_matters(str(tenant_id))
    matters: list[dict[str, Any]] = []
    for row in rows:
        matter_id = str(row.get("id") or "")
        documents = await store.list_released_documents(str(tenant_id), matter_id)
        matters.append(
            {
                "id": matter_id,
                "title": row.get("title") or "",
                "jurisdiction": row.get("jurisdiction") or "",
                "area": row.get("area") or "",
                "status": row.get("status") or "",
                "documents": [
                    {
                        "id": str(doc.get("id") or ""),
                        "kind": doc.get("kind") or "",
                        "version": doc.get("version"),
                        "body": doc.get("body") or "",
                        "sources": doc.get("sources") or [],
                        "state": doc.get("state") or "released",
                    }
                    for doc in documents
                ],
            }
        )
    return {"ok": True, "matters": matters}


async def review_queue(
    matters: Any,
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    jurisdiction: str | None = None,
) -> dict[str, Any]:
    """Drafts and in-review documents for this tenant, in this region only."""
    _home, refused = await _use_home(matters, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    tags = _review_tags(jurisdiction)
    documents = await matters.list_open_documents(str(tenant_id))
    worked = await ladder.visible_worked(
        region=_box(box_region),
        tenant_id=tenant_id,
        sharing=sharing_mode(None),
    )
    items: list[dict[str, Any]] = []
    for document in documents:
        matter = await matters.get_matter(str(tenant_id), str(document.get("matter_id") or ""))
        if matter is None:
            continue
        law = str(matter.get("jurisdiction") or "")
        if not _tag_allows(tags, law):
            continue
        sources = document.get("sources") or []
        if isinstance(sources, str):
            sources = json.loads(sources)
        ranked = rank_worked(
            worked,
            rule_card_ids=[],
            features={},
            narrative=str(document.get("body") or ""),
        )
        items.append(
            {
                "document": {
                    "id": str(document.get("id") or ""),
                    "matter_id": str(document.get("matter_id") or ""),
                    "body": document.get("body") or "",
                    "state": document.get("state") or "",
                    "kind": document.get("kind") or "",
                    "version": document.get("version"),
                    "sources": sources,
                },
                "matter": {
                    "id": str(matter.get("id") or ""),
                    "title": matter.get("title") or "",
                    "jurisdiction": law,
                    "area": matter.get("area") or "",
                },
                "matches": [_review_match(item["worked"], tenant_id) for item in ranked[:5]],
            }
        )
    return {"ok": True, "items": items}


async def copy_across_regions(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """There is no copy. Each region keeps its own rows."""
    return {"ok": False, "reason": "client facts do not leave their region", "copied": False}


async def ladder_metrics(
    ladder: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
) -> dict[str, Any]:
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    box = _box(box_region)
    runs = await ladder.list_runs(region=box, tenant_id=tenant_id)
    cards = await ladder.list_cards(region=box)
    visible_cards = [card for card in cards if card.get("tenant_id") in (None, str(tenant_id))]
    worked_count = await ladder.count_worked(region=box, tenant_id=tenant_id)
    pair_count = await ladder.count_pairs(region=box, tenant_id=tenant_id)
    summary = summarize(runs=runs, cards=visible_cards, worked_count=worked_count, pair_count=pair_count)
    return {"ok": True, **summary}


def _draft_gate(text: str, jurisdiction: str | None, template: str) -> dict[str, Any]:
    decision = classify(text or "", jurisdiction if jurisdiction in ("us", "za", "both") else None)
    if decision.get("criminal") or decision.get("reason") == NOT_YET:
        return {"stopped": True, "scope": decision}
    if not decision.get("in_scope") and template not in TEMPLATES:
        return {"stopped": True, "scope": decision}
    return {"stopped": False, "scope": decision}


async def add_approved_clause(
    store: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    jurisdiction: str,
    template: str,
    kind: str,
    text: str,
    approved_by: str,
    admission_no: str,
    fallback_positions: list[str] | None = None,
    notes: str = "",
    reviewer_kind: str = "reviewer",
) -> dict[str, Any]:
    """The library stays empty until a lawyer names the clause. A founder may omit the admission number."""
    _home, refused = await _use_home(store, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    if template not in TEMPLATES:
        return _refuse("no such template")
    if kind not in TEMPLATES[template]:
        return _refuse("that clause is not part of this template")
    if jurisdiction not in ("us", "za", "both"):
        return _refuse("jurisdiction must be us, za, or both")
    from agents.zenas.core.matters import sign_off_identity

    identity = sign_off_identity(reviewer_kind=reviewer_kind, admission_no=admission_no)
    if not str(approved_by or "").strip() or identity is None:
        return _refuse("a clause needs the approving lawyer and admission number")
    who, number = identity
    if not str(text or "").strip():
        return _refuse("a clause needs text")
    if pii_detect(text).get("matches"):
        return _refuse(IDENTIFIER_REFUSED)
    saved = await store.add_clause(
        {
            "region": _box(box_region),
            "jurisdiction": jurisdiction,
            "template": template,
            "kind": kind,
            "text": text,
            "fallback_positions": list(fallback_positions or []),
            "notes": notes,
            "approved_by": approved_by.strip(),
            "admission_no": number,
            "reviewer_kind": who,
        }
    )
    return {"ok": True, "clause": saved}


async def add_playbook(
    store: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    name: str,
    template: str,
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    _home, refused = await _use_home(store, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    if template not in TEMPLATES:
        return _refuse("no such template")
    if not str(name or "").strip():
        return _refuse("a playbook needs a name")
    cleaned: list[dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        kind = str(position.get("kind") or "")
        body = str(position.get("text") or "")
        if not kind or not body:
            continue
        if pii_detect(body).get("matches"):
            return _refuse(IDENTIFIER_REFUSED)
        cleaned.append(
            {
                "kind": kind,
                "text": body,
                "stance": str(position.get("stance") or "must"),
                "fallbacks": [str(item) for item in (position.get("fallbacks") or [])],
            }
        )
    if not cleaned:
        return _refuse("a playbook needs at least one position")
    saved = await store.add_playbook(
        {
            "region": _box(box_region),
            "tenant_id": tenant_id,
            "name": name.strip(),
            "template": template,
            "positions": cleaned,
        }
    )
    return {"ok": True, "playbook": saved}


def _apply_revision(sentences: list[dict[str, Any]], proposal: str, differing: list[str]) -> None:
    try:
        parsed = parse_json_object(proposal)
    except (ValueError, json.JSONDecodeError):
        return
    by_slot = {str(item["slot"]): item for item in sentences}
    for item in parsed.get("sentences") or []:
        if not isinstance(item, dict):
            continue
        slot = str(item.get("slot") or "")
        if slot not in differing or slot not in by_slot:
            continue
        revised = str(item.get("text") or "").strip()
        current = str(by_slot[slot].get("text") or "")
        if not revised or not wording_keeps_law(current, revised, []):
            continue
        by_slot[slot]["text"] = revised
        by_slot[slot]["library"] = False
        by_slot[slot]["review"] = True


async def draft_contract(
    store: Any,
    ladder: Any,
    model: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    template: str,
    jurisdiction: str,
    terms: dict[str, Any] | None = None,
    narrative: str = "",
    matter_id: str | None = None,
    provider: str = "luna",
) -> dict[str, Any]:
    _home, refused = await _use_home(store, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    if template not in TEMPLATES:
        return _refuse("no such template")
    gate_text = narrative or json.dumps(terms or {})
    gate = _draft_gate(gate_text, jurisdiction, template)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    box = _box(box_region)
    clauses = await store.list_clauses(region=box, template=template, jurisdiction=jurisdiction)
    prior = None
    if ladder is not None:
        rows = await ladder.visible_worked(
            region=box,
            tenant_id=tenant_id,
            sharing=sharing_mode(None),
        )
        prior = select_prior(rows, template, terms)
    assembled = assemble_draft(template=template, clauses=clauses, terms=terms, prior=prior)
    if not assembled.get("ok"):
        return assembled
    sentences = list(assembled["sentences"])
    model_calls = 0
    cost = 0.0
    reused = bool(assembled.get("reused"))
    differing = [] if reused else list(assembled.get("differing") or [])
    if differing and model is not None:
        prompt = (
            "Revise only these slots. Use the facts. Do not add law or citations. "
            'Reply with JSON {"sentences":[{"slot":"","text":""}]}.\n\n'
            + json.dumps({"slots": differing, "facts": terms or {}, "sentences": sentences}, default=str)
        )
        adapter = ModelAdapter(model)
        called = await _masked_call(
            adapter, prompt, home_region=home_region, provider=provider, task_type="zenas.draft"
        )
        if not called["ok"]:
            return called
        _apply_revision(sentences, str(called["result"]["content"]), differing)
        model_calls = 1
        cost = float(called["result"]["cost_usd"])
    if reused:
        text = str(assembled["text"])
        blocks = [[{"kind": "plain", "text": text}]]
    else:
        text = "\n\n".join(str(item["text"]) for item in sentences)
        blocks = [
            [{"kind": "ins" if item.get("review") else "plain", "text": str(item["text"])}]
            for item in sentences
        ]
    if not text.rstrip().endswith(_LEGAL_NOTICE):
        blocks.append([{"kind": "plain", "text": _LEGAL_NOTICE}])
    text = _with_legal_notice(text)
    document_id = None
    if matter_id:
        matter = await store.get_matter(str(tenant_id), str(matter_id))
        if matter is None:
            return _refuse("no such matter")
        version = await store.next_version(str(tenant_id), str(matter_id), "contract")
        document = await store.add_document(
            {
                "matter_id": str(matter_id),
                "tenant_id": str(tenant_id),
                "kind": "contract",
                "version": version,
                "body": text,
                "sources": [
                    {"slot": item["slot"], "clause_id": item.get("clause_id") or ""}
                    for item in sentences
                    if item.get("library")
                ],
                "state": "released" if not await review_required(store, region=box) else "draft",
                "reviewed": False,
            }
        )
        document_id = document["id"]
    review_slots = [item["slot"] for item in sentences if item.get("review")]
    library_slots = [item["slot"] for item in sentences if item.get("library") and not item.get("review")]
    return {
        "ok": True,
        "template": template,
        "text": text,
        "sentences": sentences,
        "library": library_slots,
        "review": review_slots,
        "reused": reused,
        "cost_usd": cost,
        "model_calls": model_calls,
        "document_id": document_id,
        "docx_base64": base64.b64encode(docx_bytes(blocks)).decode("ascii"),
    }


async def review_contract(
    store: Any,
    model: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    playbook_id: str,
    text: str,
    provider: str = "luna",
    matter_id: str | None = None,
) -> dict[str, Any]:
    _home, refused = await _use_home(store, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    box = _box(box_region)
    book = await store.get_playbook(region=box, tenant_id=str(tenant_id), playbook_id=str(playbook_id))
    if book is None:
        return _refuse("no such playbook")
    gate = _draft_gate(text, None, str(book.get("template") or ""))
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"))
    reviewed = review_positions(text=text, positions=list(book.get("positions") or []))
    findings = list(reviewed["findings"])
    model_calls = 0
    cost = 0.0
    partials = [item for item in findings if item.get("partial")]
    if partials and model is not None:
        prompt = (
            "Name the risk of this difference with one word: low, medium, or high. "
            "Do not add law.\n\n"
            + json.dumps({"contract": text, "positions": partials}, default=str)
        )
        adapter = ModelAdapter(model)
        called = await _masked_call(
            adapter, prompt, home_region=home_region, provider=provider, task_type="zenas.review"
        )
        if not called["ok"]:
            return called
        word = str(called["result"]["content"] or "").strip().lower().split()
        model_calls = 1
        cost = float(called["result"]["cost_usd"])
        if word and word[0] in ("low", "medium", "high"):
            for item in partials:
                item["risk"] = word[0]
    paragraphs: list[list[dict[str, str]]] = []
    for item in findings:
        if not item.get("deviation"):
            continue
        runs: list[dict[str, str]] = []
        if item.get("delete"):
            runs.append({"kind": "del", "text": str(item["delete"])})
        if item.get("insert"):
            runs.append({"kind": "ins", "text": str(item["insert"])})
        if runs:
            paragraphs.append(runs)
    if not paragraphs:
        paragraphs = [[{"kind": "plain", "text": text}]]
    deviations = [item for item in findings if item.get("deviation")]
    document_id = None
    reviewed = False
    state = None
    if matter_id:
        matter = await store.get_matter(str(tenant_id), str(matter_id))
        if matter is None:
            return _refuse("no such matter")
        review_on = await review_required(store, region=box)
        state = "in_review" if review_on else "released"
        version = await store.next_version(str(tenant_id), str(matter_id), "review")
        document = await store.add_document(
            {
                "matter_id": str(matter_id),
                "tenant_id": str(tenant_id),
                "kind": "review",
                "version": version,
                "body": text,
                "sources": [],
                "state": state,
                "reviewed": False,
            }
        )
        document_id = document["id"]
    return {
        "ok": True,
        "findings": findings,
        "deviations": len(deviations),
        "inserts": [item["insert"] for item in deviations],
        "deletes": [item["delete"] for item in deviations],
        "cost_usd": cost,
        "model_calls": model_calls,
        "document_id": document_id,
        "state": state,
        "reviewed": reviewed,
        "docx_base64": base64.b64encode(docx_bytes(paragraphs)).decode("ascii"),
    }


async def answer_question(
    store: Any,
    ladder: Any,
    model: Any,
    *,
    tenant_id: str,
    home_region: str,
    box_region: str,
    jurisdiction: str,
    question: str,
    passages: list[dict[str, Any]],
    features: dict[str, Any] | None = None,
    matter_id: str | None = None,
    provider: str = "luna",
    tank: Any = None,
    pool: Any = None,
) -> dict[str, Any]:
    """Full ladder for one question. A bad citation is never delivered.

    With review off, a memo that passes cite-check is released to the matter
    and marked reviewed false. With review on, it waits in the queue.

    When the caller sends no passages (Ask Zenas from Workspace), look them up
    in the knowledge pool (`pool` / app.state.law_pool) before building a card.
    """
    _home, refused = await _use_home(ladder, tenant_id=tenant_id, home_region=home_region, box_region=box_region)
    if refused:
        return refused
    gate = await _gate(tank, tenant_id=tenant_id, text=question, jurisdiction=jurisdiction)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, scope=gate.get("scope"), delivered=False)
    prepared = prepare_prompt(question, home_region=home_region, provider=provider)
    if not prepared["ok"]:
        return _refuse(prepared["reason"] or IDENTIFIER_REFUSED, delivered=False)
    if not passages:
        passages = await _passages_from_pool(
            pool,
            query=question,
            citation="",
            jurisdiction=jurisdiction,
            as_at=None,
            level=None,
            kind=None,
        )
    if not passages:
        return _refuse("could not verify", delivered=False)
    require_links = bool(getattr(pool, "requires_source_links", False))
    if require_links:
        passages = [passage for passage in passages if public_source_url(passage.get("url"))]
        if not passages:
            return _refuse("could not verify", delivered=False)
    built = await build_rule_card(
        ladder,
        tank,
        model,
        tenant_id=tenant_id,
        home_region=home_region,
        box_region=box_region,
        jurisdiction=jurisdiction,
        question=question,
        passages=passages,
        provider=provider,
    )
    if not built.get("ok"):
        return {**built, "delivered": False}
    if not built.get("reused") and not (built.get("cite") or {}).get("passed"):
        return _refuse("could not verify", delivered=False, cite=built.get("cite"))
    card = built["rule_card"]
    review_on = await review_required(ladder, region=_box(box_region))
    if not card_servable(
        {"status": "lawyer_approved" if card.get("status") == "lawyer_approved" else "draft", "active": card.get("active")},
        review_on=review_on,
    ):
        return _refuse("could not verify", delivered=False)
    applied = await apply_law(
        ladder,
        model,
        tenant_id=tenant_id,
        home_region=home_region,
        box_region=box_region,
        card_id=str(card["id"]),
        features=features or {},
        question=question,
        provider=provider,
        tank=tank,
    )
    if not applied.get("ok"):
        return {**applied, "delivered": False}
    fresh = await ladder.get_rule_card(str(card["id"]), region=_box(box_region), tenant_id=tenant_id)
    if fresh is None:
        return _refuse("could not verify", delivered=False)
    memo = compose_memo(fresh, applied["elements"])
    known_names: list[str] = []
    for item in passages:
        for key in ("citation", "text", "title", "name"):
            value = " ".join(str(item.get(key) or "").split())
            if value:
                known_names.append(value)
    if isinstance(features, dict):
        client_matter = " ".join(str(features.get("client_matter") or "").split())
        if client_matter:
            known_names.append(client_matter)
    if matter_id:
        matter_row = await store.get_matter(str(tenant_id), str(matter_id))
        if matter_row is not None:
            title = " ".join(str(matter_row.get("title") or "").split())
            if title:
                known_names.append(title)
    memo["text"] = drop_unverified_cases(str(memo.get("text") or ""), known_names)
    if str(jurisdiction or "") == "za" and rules_are_draft():
        memo["text"] = f"{memo['text']}\n{draft_label()}".strip()
    statements = [
        {"citation": element.get("citation"), "quote": element.get("quote")}
        for element in fresh.get("elements") or []
        if element.get("citation")
    ]
    checked = cite_check(statements, passages)
    memo_cites = [str(item) for item in memo.get("citations") or []]
    known = {str(item.get("citation") or "") for item in statements}
    if not checked.get("passed") or any(item not in known for item in memo_cites):
        return _refuse("could not verify", delivered=False, cite=checked)
    sources = cited_sources(statements, checked, passages, memo_cites, require_links=require_links)
    if sources is None:
        return _refuse("could not verify", delivered=False, cite=checked)
    memo["sources"] = sources
    memo["text"] = _with_legal_notice(memo["text"])
    state = "in_review" if review_on else "released"
    document_id = None
    if matter_id:
        matter = await store.get_matter(str(tenant_id), str(matter_id))
        if matter is None:
            return _refuse("no such matter", delivered=False)
        version = await store.next_version(str(tenant_id), str(matter_id), "memo")
        document = await store.add_document(
            {
                "matter_id": str(matter_id),
                "tenant_id": str(tenant_id),
                "kind": "memo",
                "version": version,
                "body": memo["text"],
                "sources": sources,
                "state": state,
                "reviewed": False,
            }
        )
        document_id = document["id"]
    return {
        "ok": True,
        "memo": memo,
        "rule_card": _public_card(fresh),
        "reviewed": False,
        "state": state,
        "delivered": document_id is not None and state == "released",
        "human_step": review_on,
        "document_id": document_id,
        "cost_usd": float(built.get("cost_usd") or 0) + float(applied.get("cost_usd") or 0),
    }
