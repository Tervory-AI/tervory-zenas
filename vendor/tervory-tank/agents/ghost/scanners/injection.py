"""llm-guard-shaped prompt-injection scanner (marker stub, no HF model).

Stock upstream: llm_guard.input_scanners.prompt_injection (DeBERTa). We keep a
regex/marker stub so Tank CI needs no GPU or model download. Stronger-than-stock
intent: burn before Watch LLM sees dirty text when guardrails flag is ON.
"""
from __future__ import annotations

from ..guardrails import GuardBurned
from ..threat_payload import INJECTION_MARKERS

# Extra llm-guard-flavoured phrases beyond ThreatPayload markers (stronger-than-stock).
EXTRA_INJECTION = (
    "jailbreak",
    "dan mode",
    "developer mode",
    "do anything now",
    "bypass safety",
    "bypass filter",
)


def scan_injection_stub(text: str) -> str:
    """Return text unchanged, or raise GuardBurned. Never echoes input."""
    lowered = text.lower()
    if any(m in lowered for m in INJECTION_MARKERS):
        raise GuardBurned()
    if any(m in lowered for m in EXTRA_INJECTION):
        raise GuardBurned()
    return text
