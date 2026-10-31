"""Attach law-only rule cards to public converted matters in this region."""

from __future__ import annotations

from typing import Any
import hashlib
import json

from agents.zenas.core.engine import cite_check
from agents.zenas.core.scope_rules import _hit, classify, load_config
from agents.zenas.core.service import _passages_from_pool, build_rule_card

PLATFORM = "00000000-0000-0000-0000-000000000001"


async def link_card(pool: Any, ladder: Any, model: Any, worked: dict[str, Any], *, region: str) -> bool:
    jurisdiction = "za" if region == "za" else "us"
    if (
        region not in {"za", "us-east"}
        or worked.get("region") != region
        or worked.get("jurisdiction") != jurisdiction
        or worked.get("source") != "case"
        or worked.get("material_type") != "public_record"
        or worked.get("tenant_id") is not None
        or getattr(pool, "home_region", region) not in {None, region}
    ):
        return False
    pattern = worked.get("fact_pattern") or {}
    facts = str(pattern.get("question") or "") + " " + str(pattern.get("facts") or pattern.get("narrative") or "")
    scope = classify(facts, jurisdiction)
    if not scope.get("in_scope") or scope.get("criminal"):
        return False
    area = str(scope["area"])
    config = load_config()
    phrases = [str(phrase) for item in config["allowed"] + config["regulatory"]
               if item["id"] == area for phrase in item["phrases"]]
    topic = next((phrase for phrase in phrases if _hit(facts, phrase)), area.replace("_", " "))
    passages = []
    # Retrieve the area's own vocabulary, independent of which case arrived first.
    # Cards describe current law, rather than an individual case's historical date.
    for query in dict.fromkeys([topic, *phrases]):
        for kind in ("statute", "court_rule"):
            passages.extend(await _passages_from_pool(
                pool, query=query, citation="", jurisdiction=jurisdiction,
                as_at=None, level=None, kind=kind,
            ))
    passages = []
    for kind in ("statute", "court_rule"):
        passages.extend(await _passages_from_pool(
            pool, query=topic, citation="", jurisdiction=jurisdiction,
            as_at=worked.get("as_at"), level=None, kind=kind,
        ))
    passages = [
        passage for passage in passages
        if passage.get("jurisdiction") == jurisdiction and passage.get("kind") in {"statute", "court_rule"}
    ]
    if not passages:
        return False
    registered = await ladder.register_tenant(PLATFORM, region)
    if not registered.get("ok"):
        return False
    source_versions = {(str(item["doc_id"]), str(item["version_id"])) for item in passages}
    version_key = hashlib.sha256(json.dumps(sorted(source_versions)).encode()).hexdigest()
    question = f"What are the legal elements for {area} in {jurisdiction}?"
    failure_key = f"converted:{jurisdiction}:{area}:{version_key}"
    for card in await ladder.list_cards(region=region):
        if (card.get("tenant_id") is not None or card.get("jurisdiction") != jurisdiction
                or card.get("question") != question or card.get("status") == "stale"):
            continue
        previous = {(str(item.get("doc_id")), str(item.get("version_id")))
                    for item in card.get("sources") or []}
        superseded = any(doc == old_doc and version != old_version
                         for old_doc, old_version in previous for doc, version in source_versions)
        if superseded:
            await ladder.set_status(str(card["id"]), region=region, status="stale", active=False)
            continue
        if card.get("elements") and previous <= source_versions and cite_check(card["elements"], passages, allow_masked=True).get("passed"):
            return await ladder.link_worked_card(
                region=region, worked_id=str(worked["id"]), card_id=str(card["id"]), area=area,
            )
        # Missing search results do not prove that a source was superseded.
        return False
    if any(run.get("kind") == "converted_card_failed" and run.get("question_key") == failure_key
           for run in await ladder.list_runs(region=region, tenant_id=PLATFORM)):
        return False
    succeeded = False
    try:
        result = await build_rule_card(
            _CheckedLadder(ladder, passages), None, model, tenant_id=PLATFORM, home_region=region, box_region=region,
            jurisdiction=jurisdiction, question=question, passages=passages, scope_text=topic,
        )
        if not result.get("ok"):
            return False
        card = result["rule_card"]
        if card.get("status") == "stale" or not card.get("elements") or not cite_check(card["elements"], passages, allow_masked=True).get("passed"):
            return False
        succeeded = True
        return await ladder.link_worked_card(
            region=region, worked_id=str(worked["id"]), card_id=str(card["id"]), area=area,
        )
    finally:
        if not succeeded:
            await ladder.add_run({"region": region, "tenant_id": PLATFORM,
                                  "kind": "converted_card_failed", "question_key": failure_key})


class _CheckedLadder:
    """Prevent an unchecked converted card from ever being persisted as usable."""

    def __init__(self, ladder: Any, passages: list[dict[str, Any]]) -> None:
        self.ladder, self.passages = ladder, passages

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ladder, name)

    async def add_rule_card(self, row: dict[str, Any]) -> dict[str, Any]:
        if not row.get("elements") or not cite_check(row["elements"], self.passages, allow_masked=True).get("passed"):
            row = {**row, "status": "stale", "active": False}
        return await self.ladder.add_rule_card(row)
