"""Review candidates only: no gold acceptance, training, skill writes or activation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .learning import gold_bucket


def candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    if state["phase"] != "closed":
        return []
    return [{"bucket": gold_bucket(), "hearing_id": state["id"], "turn_number": turn["number"],
        "seat": turn["seat"], "point": turn["text"], "reasoning": deepcopy(turn["reasoning"]),
        "worked_id": state["problem"]["worked_id"], "problem_version": state["problem_version"],
        "region": state["region"], "authority_ids": list(turn["authority_ids"]),
        "status": "pending_review", "verdict": None}
        for turn in state["transcript"] if turn.get("reasoning")]
