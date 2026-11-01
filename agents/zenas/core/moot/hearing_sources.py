"""Build a hearing from existing regional worked matters and stored public sources.

Never accepts a judgment, verdict, verification flag, or model answer from the
browser as a source. Reuses conversion's civil gate and the existing quote gate.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID
from urllib.parse import urlsplit

from agents.zenas.core.pointers import _PARAGRAPH

from agents.zenas.core.convert import criminal_case
from agents.zenas.core.engine import cite_check, quote_in_source
from agents.zenas.core.moot.hearing import Actor, HearingError
from agents.zenas.core.moot.store_compat import decoded


WORKED = """SELECT w.*, d.title, d.text, d.citation, d.url, d.status, d.kind,
 d.docket_number, d.nature_of_suit, d.court, d.division, d.bench_size, d.appeal,
 s.region AS source_region, s.bulk_use
 FROM agent_zenas.worked_matters w
 JOIN zenas_pool.documents d ON d.document_id=w.pool_doc_id AND d.version_id=w.pool_version_id
 JOIN zenas_pool.sources s ON s.source_id=d.source_id
 WHERE w.id=$1 AND w.region=$2 AND (w.tenant_id=$3 OR w.tenant_id IS NULL)
 AND w.source='case' AND w.material_type='public_record' AND w.holdout=false
 AND s.region=$2 AND s.bulk_use='allowed' AND d.kind='case' AND d.status <> 'superseded'"""


def verified_paragraph(source: str, quote: str) -> int | None:
    marks = list(_PARAGRAPH.finditer(source))
    numbers = [int(m.group(1) or m.group(2)) for m in marks]
    if any(a >= b for a, b in zip(numbers, numbers[1:])):
        return None
    found = [numbers[i] for i, mark in enumerate(marks)
             if quote_in_source(quote, source[mark.end():marks[i + 1].start() if i + 1 < len(marks) else len(source)])]
    return found[0] if len(found) == 1 else None


def build_problem(row: dict[str, Any], actor: Actor) -> dict[str, Any]:
    if row.get("region") != actor.region or row.get("source_region") != actor.region:
        raise HearingError("wrong_region")
    if (row.get("tenant_id") is not None and str(row["tenant_id"]) != actor.tenant_id
            or row.get("holdout") or row.get("source") != "case" or row.get("material_type") != "public_record"):
        raise HearingError("not_found")
    if criminal_case(row):
        raise HearingError("civil_only")
    pattern = decoded(row.get("fact_pattern"))
    facts = pattern.get("facts") or pattern.get("narrative") or ""
    holding = str(row.get("outcome") or "")
    source = str(row.get("text") or "")
    if not isinstance(facts, str) or not facts.strip() or not holding.strip() or not row.get("url"):
        raise HearingError("source_incomplete")
    if not quote_in_source(facts, source) or not quote_in_source(holding, source):
        raise HearingError("source_quote_check_failed")
    url = urlsplit(str(row["url"]))
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password:
        raise HearingError("invalid_source_url")
    citation = str(row.get("citation") or "")
    passage = {"citation": citation, "text": source, "status": row.get("status"),
               "doc_id": row.get("pool_doc_id"), "version_id": row.get("pool_version_id")}
    authorities = []
    seen = set()
    # Only actual source-backed bases are admitted. Other citations need their
    # own stored-library resolution, never a fabricated link to the primary case.
    for finding in decoded(row.get("element_findings"), list):
        for basis in finding.get("basis") or []:
            if not isinstance(basis, dict) or basis.get("citation") != citation:
                continue
            quote = str(basis.get("quote") or "")
            if quote in seen or not cite_check([{"citation": citation, "quote": quote}], [passage])["passed"]:
                continue
            seen.add(quote)
            authorities.append({"id": f"{row['pool_version_id']}:{len(authorities)}", "name": citation,
                "quote": quote, "source_url": row["url"], "verified": True, "weight": "unknown",
                "court": row.get("court") or "", "paragraph": verified_paragraph(source, quote)})
    issues = pattern.get("issues") or [pattern.get("question") or ""]
    issues = [i if isinstance(i, str) else str(i.get("issue") or i.get("question") or "") for i in issues]
    issues = [i for i in issues if i.strip()]
    if not issues:
        raise HearingError("source_issues_missing")
    return {"worked_id": str(row["id"]), "region": actor.region, "kind": "civil_appeal", "criminal": False,
        "title": str(row.get("title") or citation), "facts": facts, "issues": issues,
        "fact_records": [{"id": "facts", "text": facts, "status": "sourced"}],
        "bundles": {side: {"issues": issues, "authority_ids": [a["id"] for a in authorities]}
                    for side in ("applicant", "respondent")},
        "bench_notes": {"issues": issues, "court": row.get("court") or ""},
        "real_holding": holding, "authorities": authorities}


async def worked_problem(pool: Any, actor: Actor, worked_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(WORKED, UUID(worked_id), actor.region, UUID(actor.tenant_id))
    if row is None:
        raise HearingError("not_found")
    return build_problem(dict(row), actor)


async def edited_problem(pool: Any, actor: Actor, current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    base = await worked_problem(pool, actor, current["worked_id"])
    allowed = {"title", "facts", "issues", "bundles", "bench_notes", "fact_records"}
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise HearingError("invalid_problem_edit")
    for key, value in changes.items():
        if key == "facts" and (not isinstance(value, str) or not quote_in_source(value, base["facts"])):
            raise HearingError("unsupported_fact")
        if key == "fact_records":
            if not isinstance(value, list):
                raise HearingError("invalid_facts")
            for record in value:
                if (not isinstance(record, dict) or record.get("status") not in ("sourced", "contested", "assumed")
                    or record["status"] == "sourced" and not quote_in_source(str(record.get("text") or ""), base["facts"])):
                    raise HearingError("unsupported_fact")
        base[key] = value
    return base
