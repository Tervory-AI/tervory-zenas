"""Shared, code-only contracts for Zenas Command adapters."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def drafting_templates() -> frozenset[str]:
    """Load the existing packaged drafting catalogue without copying its keys."""
    path = Path(__file__).resolve().parent / "skills" / "zenas.shared" / "drafting.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_drafting", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("drafting module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return frozenset(module.TEMPLATES)


def draft_payload(*, tenant_id: str, home_region: str, template: str,
                  jurisdiction: str, terms: dict[str, Any], text: str,
                  matter_id: str | None, include_empty_matter: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "home_region": home_region,
        "template": template,
        "jurisdiction": jurisdiction,
        "terms": terms,
        "narrative": text,
    }
    if matter_id is not None or include_empty_matter:
        payload["matter_id"] = matter_id
    return payload
