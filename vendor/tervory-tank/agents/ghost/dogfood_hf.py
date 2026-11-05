"""Synthetic Autumbra dogfood; optional model work goes through Tank.

The historical HF-named CLI and receipt fields remain compatible. The agent
never reads provider credentials or chooses a model. AUTUMBRA_HF_LIVE=1 and
live=True still gate any paid work; the default exercises only local code.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from .models import PLATFORM_TENANT
from .threat_payload import ThreatBurned, accept_threat_payload

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "autumbra_dogfood"
TASK_TYPE = "reason.brief"  # Existing Tank policy; no new provider or paid route.


class HfDogfoodError(RuntimeError):
    """The optional Tank call could not finish; never include upstream bodies."""


def hf_token() -> bool:
    """Legacy availability probe. Checks Tank access; returns no credential."""
    return bool(os.environ.get("TANK_KEY", "").strip())


def live_armed() -> bool:
    """True only when AUTUMBRA_HF_LIVE=1 (Zenas explicit go after credit top-up)."""
    return os.environ.get("AUTUMBRA_HF_LIVE", "").strip() == "1"


def load_synthetic_fixtures(path: Path | None = None) -> list[dict[str, Any]]:
    """Load synthetic SIEM-ish JSONL. Never client data."""
    target = path or (FIXTURES_DIR / "synthetic_siem.jsonl")
    rows: list[dict[str, Any]] = []
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def build_watch_prompt(events: list[dict[str, Any]]) -> str:
    """Prompt for Watch triage over synthetic fixtures only."""
    blob = "\n".join(json.dumps(e, sort_keys=True) for e in events)
    return (
        "You are Autumbra Watch (propose-only). Summarize these SYNTHETIC SIEM "
        "fixture lines for dogfood. Do not invent client data. Reply with a short "
        "JSON object: {\"summary\": str, \"suspect_site\": str|null}.\n\n"
        f"FIXTURES:\n{blob}\n"
    )


async def call_tank_inference(prompt: str, *, timeout: float = 60.0) -> dict[str, Any]:
    """Use the room's existing Tank client and require a durable receipt."""
    from .app import Tank

    if not live_armed():
        raise HfDogfoodError("Live dogfood STOPPED; AUTUMBRA_HF_LIVE requires explicit authorization.")
    if not hf_token():
        raise HfDogfoodError("Autumbra's Tank key is not configured.")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            data = await Tank(client).call("POST", "/v1/route", {
                "tenant_id": PLATFORM_TENANT, "agent": "ghost", "task_type": TASK_TYPE, "max_tokens": 200,
                "messages": [
                    {"role": "system", "content": "Autumbra Watch dogfood. Synthetic fixtures only."},
                    {"role": "user", "content": prompt},
                ],
            })
        UUID(str(data["ledger_id"]))
        if not isinstance(data["content"], str):
            raise ValueError("Missing content")
        return data
    except (RuntimeError, httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HfDogfoodError("Tank could not complete the dogfood request with a receipt.") from None


def call_hf_inference(prompt: str, *, model: str | None = None, timeout: float = 60.0) -> str:
    """Synchronous CLI compatibility; model selection belongs to Tank."""
    if model is not None:
        raise HfDogfoodError("Model overrides are not accepted; Tank selects the model.")
    return asyncio.run(call_tank_inference(prompt, timeout=timeout))["content"]


def dogfood_watch_triage(
    *,
    live: bool = False,
    fixtures_path: Path | None = None,
    soft_live: bool = True,
) -> dict[str, Any]:
    """Run synthetic dogfood. Local burn path always; Tank only when live+room-key.

    Returns a receipt dict suitable for tests/docs. Never uploads client payloads.
    When soft_live=True, Tank/network errors are recorded in the receipt instead
    of raising (so CI / dogfood scripts stay usable without a live connection).
    """
    events = load_synthetic_fixtures(fixtures_path)
    receipt: dict[str, Any] = {
        "product": "Autumbra",
        "role": "Watch",
        "zero_ingestion": True,
        "fixture_count": len(events),
        "live_hf": False,
        "hf_text": None,
        "hf_error": None,
        "threat_payload_local": None,
        "note": "Synthetic fixtures only. ThreatPayload burn stays on Tank.",
    }
    # Local ThreatPayload path still exercised with a clean propose fixture shape.
    clean = {
        "schema_version": "ghost.threat.v1",
        "action": "propose_shield",
        "site": "coolify.tervory.com",
        "kind": "block_multipart_post",
        "path": None,
        "match_id": "dogfood-synthetic",
        "cve_ids": ["CVE-2026-109618"],
        "severity": "known_exploited",
        "reason": "synthetic dogfood SIEM fixture match",
        "confidence": 0.9,
    }
    try:
        payload = accept_threat_payload(json.dumps(clean))
        receipt["threat_payload_local"] = {
            "accepted": True,
            "action": payload.action.value,
            "site": payload.site,
        }
    except ThreatBurned:
        receipt["threat_payload_local"] = {"accepted": False}

    if not live:
        return receipt
    if not live_armed():
        receipt["note"] = (
            "Live HF STOPPED (fixtures-only). Set AUTUMBRA_HF_LIVE=1 only after "
            "Zenas tops up credits and says go."
        )
        receipt["hf_error"] = "AUTUMBRA_HF_LIVE not set — refusing live call"
        return receipt
    if not hf_token():
        receipt["note"] = "Tank key unset — local-only dogfood"
        return receipt
    prompt = build_watch_prompt(events)
    try:
        answer = asyncio.run(call_tank_inference(prompt))
        text = answer["content"]
    except (HfDogfoodError, httpx.HTTPError) as exc:
        receipt["hf_error"] = str(exc)[:300]
        receipt["note"] = "Tank request did not finish; see hf_error."
        if not soft_live:
            raise
        return receipt
    receipt["live_hf"] = True  # Historical field: a live model call completed via Tank.
    receipt["tank_ledger_id"] = answer["ledger_id"]
    receipt["task_type"] = TASK_TYPE
    receipt["cost_usd"] = answer.get("cost_usd")
    # Do not store raw prompt with secrets; only a short model reply excerpt.
    receipt["hf_text"] = text[:500]
    return receipt
