"""Autumbra air-gap inbound guardrails (tank-only Dual-LLM front door).

Optional NeMo / LLM Guard-shaped adapter in front of Watch intake. Default OFF —
ThreatPayload burn alone still protects the Watch→Enforce handoff.

When a GPU bunker exists, swap the body of `guard_inbound` / OSS adapters for
real `nemoguardrails` or `llm_guard` calls and keep this public API stable.
Vault cache (do not re-clone): `/workspace/autumbra-oss-vault/nemo-guardrails`,
`/workspace/autumbra-oss-vault/llm-guard` (see each TERVORY.md).

Injection markers are imported from threat_payload — one shared source of truth.
On deny we raise GuardBurned with a fixed detail string and never echo input.
Zero-ingestion: never ship client payload to Tervory cloud from these paths.
"""
from __future__ import annotations

import os
from pathlib import Path

from .threat_payload import INJECTION_MARKERS

GUARD_BURN_DETAIL = "Inbound text burned by guardrails. No action was taken."
# Same size bar as ThreatPayload preflight — drop oversized dirty text early.
MAX_INBOUND = 8_000

ENABLED_ENVS = ("GHOST_GUARDRAILS_ENABLED", "AUTUMBRA_GUARDRAILS_ENABLED")
OSS_GUARD_ENV = "AUTUMBRA_OSS_GUARD"
# Box vault cache only — never re-clone; never required at import time.
OSS_VAULT_ROOT = Path("/workspace/autumbra-oss-vault")
OSS_NEMO_PATH = OSS_VAULT_ROOT / "nemo-guardrails"
OSS_LLM_GUARD_PATH = OSS_VAULT_ROOT / "llm-guard"


class GuardBurned(ValueError):
    """Raised when inbound text is refused. Message is always GUARD_BURN_DETAIL."""

    def __init__(self) -> None:
        super().__init__(GUARD_BURN_DETAIL)


def enabled() -> bool:
    """True only when GHOST_GUARDRAILS_ENABLED or AUTUMBRA_GUARDRAILS_ENABLED is on.

    Default OFF. ThreatPayload burn still runs on triage/enforce regardless.
    """
    for name in ENABLED_ENVS:
        if os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _decode_inbound(text: str | bytes) -> str:
    """Decode/size-check only — no injection burn. Used for flag-OFF passthrough."""
    if isinstance(text, (bytes, bytearray)):
        if len(text) > MAX_INBOUND:
            raise GuardBurned()
        try:
            return text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GuardBurned() from exc
    if isinstance(text, str):
        if len(text) > MAX_INBOUND:
            raise GuardBurned()
        return text
    raise GuardBurned()


def guard_inbound(text: str | bytes) -> str:
    """Pass through clean inbound text, or burn on injection / decode / size.

    Always-on scanner (used when the flag is enabled, and by unit tests).
    Never echoes the rejected input. Callers should map GuardBurned → 422 with
    GUARD_BURN_DETAIL only.
    """
    decoded = _decode_inbound(text)
    # Optional vault OSS hook (tank-only). Prefer stock marker scan when packages
    # are unavailable — no GPU / heavy deps required.
    oss = os.environ.get(OSS_GUARD_ENV, "").strip().lower()
    if oss in {"nemo", "llm-guard", "llm_guard"}:
        _try_oss_inbound_hook(decoded, oss)
    lowered = decoded.lower()
    # Shared markers with ThreatPayload (ignore previous, system prompt, ```, shell…).
    if any(marker in lowered for marker in INJECTION_MARKERS):
        raise GuardBurned()
    # llm-guard-shaped PII / injection adapters (regex stubs; no HF models).
    from .scanners import scan_injection_stub, scan_pii_stub

    scan_injection_stub(decoded)  # raises GuardBurned on hit
    # PII: redact in place for Watch intake; do not burn (Watch may still need context).
    # Zero-ingestion: redaction stays onsite — never forwarded to Tervory cloud.
    return scan_pii_stub(decoded)


def guard_inbound_if_enabled(text: str | bytes) -> str:
    """No-op passthrough when guardrails flag is OFF; else `guard_inbound`.

    Flag OFF (default): decode/size only — ThreatPayload burn alone still protects.
    Flag ON: full marker + optional OSS / scanner path.
    """
    if not enabled():
        return _decode_inbound(text)
    return guard_inbound(text)


def _try_oss_inbound_hook(decoded: str, which: str) -> None:
    """Optional vault OSS call. Stock marker path remains the real burn today.

    TODO(autumbra-airgap): when `nemoguardrails` / `llm_guard` are installed onsite
    from the vault cache, invoke input rails / PromptInjection+Anonymize here.
    Until then: verify vault tree is readable, then fall through to marker scan.
    Never phone home; never upload client payload to Tervory cloud.
    """
    if which == "nemo":
        # Vault: /workspace/autumbra-oss-vault/nemo-guardrails (see TERVORY.md)
        if not (OSS_NEMO_PATH / "TERVORY.md").is_file():
            return
        # TODO: from nemoguardrails import LLMRails — tank models only, telemetry off.
        return
    if which in {"llm-guard", "llm_guard"}:
        # Vault: /workspace/autumbra-oss-vault/llm-guard (see TERVORY.md; upstream ARCHIVED)
        if not (OSS_LLM_GUARD_PATH / "TERVORY.md").is_file():
            return
        # TODO: llm_guard.input_scanners.PromptInjection + Anonymize — pin models onsite.
        return


def oss_vault_readable() -> dict[str, bool]:
    """Report whether vault cache paths exist (no import of heavy packages)."""
    return {
        "nemo-guardrails": (OSS_NEMO_PATH / "TERVORY.md").is_file(),
        "llm-guard": (OSS_LLM_GUARD_PATH / "TERVORY.md").is_file(),
    }
