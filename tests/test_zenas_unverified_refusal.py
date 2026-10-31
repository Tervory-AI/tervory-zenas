"""A refused answer names the case the question asked about when that case is not in the passages."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402
from agents.zenas.core.precedent import drop_unverified_cases, unverified_cases  # noqa: E402
from agents.zenas.core.service import answer_question  # noqa: E402
from agents.zenas.core.store import MemoryStore  # noqa: E402

TENANT = "00000000-0000-0000-0000-0000000000a1"
PASSAGE = {
    "doc_id": "d1",
    "version_id": "v1",
    "citation": "[2000] ZACC 12",
    "text": "The decisions sought to be reviewed were those taken by the Commission.",
    "title": "Sasol v Competition Commission",
    "jurisdiction": "za",
    "status": "effective",
}
# The model misquotes the passage, so cite-check fails before a memo is written.
MISQUOTED = {
    "elements": [
        {
            "id": "review",
            "test": "The Commission's decisions are reviewable.",
            "citation": "[2000] ZACC 12",
            "quote": "The Commission's decisions may always be reviewed.",
        }
    ],
    "exceptions": [],
    "defences": [],
    "remedies": [],
    "decision_table": [{"element_id": "review", "when": {"asked": "yes"}, "finding": "unclear"}],
}


class Model:
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, messages, *, task_type: str, provider: str) -> dict:
        return {"content": self.content, "cost_usd": 0.0, "model": "luna"}


async def _ask(question: str, passages: list[dict]) -> dict:
    ladder = LadderMemory()
    await ladder.register_tenant(TENANT, "us-east")
    return await answer_question(
        MemoryStore(),
        ladder,
        Model(json.dumps(MISQUOTED)),
        tenant_id=TENANT,
        home_region="us-east",
        box_region="us-east",
        jurisdiction="us",
        question=question,
        passages=passages,
        features={"asked": "yes"},
    )


@pytest.mark.asyncio
async def test_a_failed_cite_check_names_the_made_up_case() -> None:
    result = await _ask(
        "What did Fictional Party 31 v Nobody 31 [2099] ZAGPJHC 9031 decide in this commercial dispute?",
        [PASSAGE],
    )
    assert result["ok"] is False
    assert result["delivered"] is False
    reason = result["reason"]
    assert reason.startswith("could not verify")
    assert "cannot find" in reason.lower()
    assert "I could not verify" in reason
    assert "I have left it out" in reason
    assert "I could not verify [2099] ZAGPJHC 9031, so I have left it out. I cannot find it." in reason
    assert "[2000] ZACC 12" not in reason


@pytest.mark.asyncio
async def test_a_failed_cite_check_on_a_known_case_stays_plain() -> None:
    result = await _ask("What did Sasol v Competition Commission [2000] ZACC 12 hold in this commercial dispute?", [PASSAGE])
    assert result["ok"] is False
    assert result["reason"] == "could not verify"


@pytest.mark.asyncio
async def test_no_passages_names_every_case_asked_about() -> None:
    result = await _ask("What did Imaginary Holdings v Nobody [2099] ZASCA 1 decide in this commercial dispute?", [])
    assert result["ok"] is False
    assert "I could not verify [2099] ZASCA 1, so I have left it out. I cannot find it." in result["reason"]


def test_drop_unverified_cases_is_unchanged() -> None:
    library = ["Grancy Property Limited and Another v Gihwala and Others [2026] ZACC 40"]
    text = "Imaginary Holdings v Nobody [2099] ZASCA 1 decides the point."
    assert unverified_cases(text, library) == ["Imaginary Holdings v Nobody", "[2099] ZASCA 1"] or set(
        unverified_cases(text, library)
    ) == {"Imaginary Holdings v Nobody", "[2099] ZASCA 1"}
    miss = drop_unverified_cases(text, library)
    assert "I could not verify Imaginary Holdings v Nobody, so I have left it out. I cannot find it." in miss
    assert drop_unverified_cases("Grancy v Gihwala [2026] ZACC 40 applies.", library) == "Grancy v Gihwala [2026] ZACC 40 applies."
