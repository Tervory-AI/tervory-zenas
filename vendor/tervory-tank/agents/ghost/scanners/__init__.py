"""llm-guard-shaped Autumbra scanners (tank-only stubs).

Inspired by vault `/workspace/autumbra-oss-vault/llm-guard` input scanners
(PromptInjection, Anonymize). No HuggingFace / Presidio deps required.
When packages are installed onsite from the vault, swap stub bodies for real
scanners and keep these names stable.

Product: Autumbra Watch intake (pre-LLM). Never Ghost/Wraith in user copy.
"""
from __future__ import annotations

from .injection import scan_injection_stub
from .pii import scan_pii_stub

__all__ = ["scan_injection_stub", "scan_pii_stub"]
