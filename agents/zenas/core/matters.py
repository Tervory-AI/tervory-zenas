"""Matters, consent, and documents. The scope gate runs before any other Zenas skill."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agents.zenas.core.pii_detect import detect
from agents.zenas.core.scope_rules import classify, ruled
from agents.zenas.core.seal import open_seal, seal

CURRENT_WORDING_VERSION = "2026-10-13"
ZA_HOME_MESSAGE = "needs the South Africa region"
US_HOME_MESSAGE = "needs the United States region"
CONSENT_MESSAGE = "A current consent is required before a matter can be opened."
PII_KEY_MESSAGE = "personal information cannot be stored"
SCOPE_SKILL = "zenas.scope"
_JURISDICTIONS = ("us", "za", "both")


def normalize_home(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw == "us":
        return "us-east"
    return raw


def normalize_box(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in ("", "us", "us-east"):
        return "us-east"
    return raw


def region_refusal(home: str | None, box: str | None) -> str | None:
    """A box only opens matters for tenants whose home is that same region."""
    home_region = normalize_home(home)
    box_region = normalize_box(box)
    if home_region not in ("us-east", "za"):
        return "home region must be us-east or za"
    if home_region == "za" and box_region != "za":
        return ZA_HOME_MESSAGE
    if home_region == "us-east" and box_region != "us-east":
        return US_HOME_MESSAGE
    return None


def text_hash(wording: str) -> str:
    return hashlib.sha256(wording.encode("utf-8")).hexdigest()


def model_prompt(redacted: str) -> str:
    """The only text a model is allowed to see."""
    return redacted


def _law(value: str | None) -> str | None:
    if value in _JURISDICTIONS:
        return value
    return None


def _refuse(reason: str, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": False, "reason": reason}
    if scope is not None:
        body["scope"] = scope
    return body


async def consider(
    tank: Any,
    *,
    tenant_id: str,
    text: str,
    jurisdiction: str | None = None,
    follow: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Call zenas.scope, then other Zenas skills only when the matter is in scope.

    Rules that already decided cannot be overruled by the model. A criminal
    question never reaches a second skill.
    """
    law = _law(jurisdiction)
    payload: dict[str, Any] = {"text": text or ""}
    if law is not None:
        payload["jurisdiction"] = law
    scope = await tank.run_skill(str(tenant_id), SCOPE_SKILL, payload)
    if not isinstance(scope, dict):
        scope = {}
    kind = ruled(text or "")
    local = classify(text or "", law)
    if kind is None:
        decision = scope
        stopped = bool(scope.get("criminal") or not scope.get("in_scope"))
    else:
        decision = local
        stopped = bool(local["criminal"] or not local["in_scope"])
        if scope.get("criminal") or not scope.get("in_scope"):
            stopped = True
            if scope.get("criminal"):
                decision = local if local["criminal"] else scope
    if stopped:
        return {"stopped": True, "scope": decision, "outputs": []}
    outputs: list[Any] = []
    for item in follow or []:
        skill_id = str(item.get("skill") or "")
        if not skill_id.startswith("zenas.") or skill_id == SCOPE_SKILL:
            continue
        outputs.append(await tank.run_skill(str(tenant_id), skill_id, item.get("input") or {}))
    return {"stopped": False, "scope": decision, "outputs": outputs}


def _redact(text: str) -> tuple[str, list[dict[str, str]]]:
    found = detect(text or "")
    return str(found["text"]), list(found["matches"])


def _safe_prompt(redacted: str, matches: list[dict[str, str]]) -> str | None:
    prompt = model_prompt(redacted)
    for match in matches:
        value = match.get("value") or ""
        if value and value in prompt:
            return None
    return prompt


async def record_consent(
    store: Any,
    *,
    tenant_id: str,
    home_region: str,
    user_id: str,
    jurisdiction: str,
    wording: str,
    ip: str,
    box_region: str,
    wording_version: str = CURRENT_WORDING_VERSION,
) -> dict[str, Any]:
    refusal = region_refusal(home_region, box_region)
    if refusal is not None:
        return _refuse(refusal)
    law = _law(jurisdiction)
    if law is None or not user_id or not wording or not ip:
        return _refuse("consent needs a user, wording, jurisdiction, and ip")
    row = await store.add_consent(
        {
            "tenant_id": str(tenant_id),
            "user_id": user_id,
            "jurisdiction": law,
            "wording_version": wording_version,
            "text_hash": text_hash(wording),
            "ip": ip,
        }
    )
    return {"ok": True, "consent_id": row["id"]}


async def open_matter(
    store: Any,
    tank: Any,
    *,
    tenant_id: str,
    home_region: str,
    jurisdiction: str,
    title: str,
    created_by: str,
    user_id: str,
    box_region: str,
    parties: list[Any] | None = None,
    key: bytes | None = None,
) -> dict[str, Any]:
    refusal = region_refusal(home_region, box_region)
    if refusal is not None:
        return _refuse(refusal)
    law = _law(jurisdiction)
    if law is None or not title.strip() or not created_by:
        return _refuse("a matter needs a title, a creator, and a jurisdiction")
    if normalize_home(home_region) == "za":
        current = await store.has_current_consent(str(tenant_id), user_id, CURRENT_WORDING_VERSION)
        if not current:
            return _refuse(CONSENT_MESSAGE)
    redacted_title, title_matches = _redact(title)
    parties_text = json.dumps(parties or [])
    redacted_parties_text, party_matches = _redact(parties_text)
    try:
        redacted_parties = json.loads(redacted_parties_text)
    except json.JSONDecodeError:
        return _refuse(PII_KEY_MESSAGE)
    matches = title_matches + party_matches
    if _safe_prompt(redacted_title, matches) is None:
        return _refuse(PII_KEY_MESSAGE)
    gate = await consider(tank, tenant_id=str(tenant_id), text=redacted_title, jurisdiction=law)
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, gate.get("scope"))
    if matches and key is None:
        return _refuse(PII_KEY_MESSAGE)
    scope = gate["scope"]
    area = str(scope.get("area") or "")
    if not area:
        return _refuse("Not in scope.", scope)
    row = await store.add_matter(
        {
            "tenant_id": str(tenant_id),
            "jurisdiction": law,
            "area": area,
            "title": redacted_title,
            "parties": redacted_parties,
            "status": "open",
            "created_by": created_by,
        }
    )
    await _store_pii(store, tenant_id=str(tenant_id), matter_id=row["id"], matches=matches, key=key)
    return {"ok": True, "matter": _public_matter(row), "scope": scope}


async def upload_document(
    store: Any,
    tank: Any,
    *,
    tenant_id: str,
    matter_id: str,
    kind: str,
    body: str,
    box_region: str,
    home_region: str,
    key: bytes | None = None,
    sources: list[Any] | None = None,
) -> dict[str, Any]:
    refusal = region_refusal(home_region, box_region)
    if refusal is not None:
        return _refuse(refusal)
    matter = await store.get_matter(str(tenant_id), str(matter_id))
    if matter is None:
        return _refuse("no such matter")
    if not kind.strip():
        return _refuse("a document needs a kind")
    redacted, matches = _redact(body or "")
    prompt = _safe_prompt(redacted, matches)
    if prompt is None:
        return _refuse(PII_KEY_MESSAGE)
    gate = await consider(
        tank,
        tenant_id=str(tenant_id),
        text=redacted,
        jurisdiction=matter.get("jurisdiction"),
    )
    if gate["stopped"]:
        reason = str((gate.get("scope") or {}).get("reason") or "Not in scope.")
        return _refuse(reason, gate.get("scope"))
    if matches and key is None:
        return _refuse(PII_KEY_MESSAGE)
    version = await store.next_version(str(tenant_id), str(matter_id), kind)
    row = await store.add_document(
        {
            "matter_id": str(matter_id),
            "tenant_id": str(tenant_id),
            "kind": kind,
            "version": version,
            "body": redacted,
            "sources": sources or [],
            "state": "draft",
        }
    )
    await _store_pii(store, tenant_id=str(tenant_id), matter_id=str(matter_id), matches=matches, key=key)
    return {
        "ok": True,
        "document": {
            "id": row["id"],
            "matter_id": row["matter_id"],
            "tenant_id": row["tenant_id"],
            "kind": row["kind"],
            "version": row["version"],
            "body": row["body"],
            "state": row["state"],
        },
        "prompt": prompt,
        "scope": gate["scope"],
    }


async def record_review(
    store: Any,
    *,
    tenant_id: str,
    document_id: str,
    reviewer: str,
    jurisdiction: str,
    admission_no: str,
    decision: str,
    changes: str = "",
) -> dict[str, Any]:
    law = _law(jurisdiction)
    if law is None or not reviewer or not admission_no or not decision:
        return _refuse("a review needs a reviewer, admission number, jurisdiction, and decision")
    document = await store.get_document(str(tenant_id), str(document_id))
    if document is None:
        return _refuse("no such document")
    row = await store.add_review(
        {
            "document_id": str(document_id),
            "tenant_id": str(tenant_id),
            "reviewer": reviewer,
            "jurisdiction": law,
            "admission_no": admission_no,
            "decision": decision,
            "changes": changes,
        }
    )
    return {"ok": True, "review_id": row["id"]}


async def _store_pii(
    store: Any,
    *,
    tenant_id: str,
    matter_id: str,
    matches: list[dict[str, str]],
    key: bytes | None,
) -> None:
    if not matches:
        return
    if key is None:
        raise RuntimeError("pii key is not set")
    for match in matches:
        token = seal(match["value"], key)
        if match["value"] in token:
            raise RuntimeError("pii seal kept the identifier")
        if open_seal(token, key) != match["value"]:
            raise RuntimeError("pii seal failed")
        await store.add_pii(
            {
                "matter_id": matter_id,
                "tenant_id": tenant_id,
                "kind": match["kind"],
                "ciphertext": token,
            }
        )


def _public_matter(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "jurisdiction": row["jurisdiction"],
        "area": row["area"],
        "title": row["title"],
        "parties": row.get("parties") or [],
        "status": row.get("status") or "open",
        "created_by": row["created_by"],
    }
