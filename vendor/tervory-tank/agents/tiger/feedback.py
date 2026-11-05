"""Record a client rejection as an unverified gold row.

This writes a line to a file Tiger already keeps. It does not add a screen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def record_correction(
    path: Path,
    *,
    skill: str,
    case_id: str,
    what_changed: str,
    expected: str,
    note: str = "",
) -> dict[str, Any]:
    row = {
        "id": case_id,
        "skill": skill,
        "what_changed": what_changed,
        "expected": expected,
        "note": note,
        "verified": False,
        "source": "client-correction",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
