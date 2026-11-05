"""llm-guard-shaped PII redact stub (regex only, no Presidio/HF).

Stock upstream: llm_guard.input_scanners.anonymize. Onsite-first / zero-ingestion:
redact locally; never ship raw client payload to Tervory cloud.
"""
from __future__ import annotations

import re

# Conservative patterns — Watch intake hygiene, not a compliance suite.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_PHONE = re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b")


def scan_pii_stub(text: str) -> str:
    """Redact common PII shapes. Returns redacted text; does not burn."""
    out = _EMAIL.sub("[REDACTED_EMAIL]", text)
    out = _SSN.sub("[REDACTED_SSN]", out)
    out = _CC.sub("[REDACTED_CC]", out)
    out = _PHONE.sub("[REDACTED_PHONE]", out)
    return out
