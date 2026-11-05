"""Nonsecret evidence of an actual foreground dispatch, never resolved defaults.

The gateway currently does not attest effective generation settings. Therefore
settings_hash stays None, even when two dispatched settings fingerprints match.
Cache/code/provider-batch paths do not create this foreground execution receipt.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

# Only these scalar generation controls may enter a metadata fingerprint. No
# messages, tools, provider extras, headers, keys or client data are exported.
_CONTROLS = {"max_tokens", "temperature", "top_p", "top_k", "seed", "presence_penalty", "frequency_penalty",
             "reasoning_effort", "disable_fallbacks"}
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@+\-]{0,199}\Z")


def label(value: Any) -> str | None:
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else None


def capture(*, ledger_id: str, tenant_id: str, task_type: str, requested_model: str,
            answered_model: str, dispatched: Mapping[str, Any], headers: Mapping[str, str],
            response: Mapping[str, Any], delivered: bool) -> dict[str, Any]:
    controls = {key: value for key, value in dispatched.items() if key in _CONTROLS}
    safe = set(dispatched) <= _CONTROLS and all(type(v) in (str, int, float, bool) for v in controls.values())
    fingerprint = None
    if safe:
        try:
            raw = json.dumps({"version": 1, "requested_model": requested_model, "controls": controls},
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
            fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        except (TypeError, ValueError):
            pass
    return {"version": 1, "ledger_id": ledger_id, "tenant_id": tenant_id, "task_type": task_type,
            "model": answered_model, "requested_model": label(requested_model),
            "upstream_model": label(response.get("model")),
            "upstream_model_group": label(headers.get("x-litellm-model-group")),
            "delivered": delivered, "dispatched_settings_hash": fingerprint,
            "settings_hash": None, "settings_status": "upstream_effective_settings_unavailable"}
