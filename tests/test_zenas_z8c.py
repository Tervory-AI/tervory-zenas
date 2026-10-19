"""Z8c: passage joins, the failing quote, a case's own citation, merger letters.

Saved public judgments are the text. A merger clearance is not a judgment.
Client text is not stored.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
os.environ.setdefault("LITELLM_MASTER_KEY", "test")
os.environ.setdefault("TANK_API_KEY", "test")

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core.convert import convert_cases, fact_passages, merger_approval_letter  # noqa: E402
from agents.zenas.core.engine import quote_in_source, same_case_citation  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402

GROOTBOOM = (ROOT / "tests" / "fixtures" / "za" / "grootboom-p24.txt").read_text(encoding="utf-8")
TOURVEST = (ROOT / "tests" / "fixtures" / "za" / "tourvest-p105.txt").read_text(encoding="utf-8")
CLEARANCE = (ROOT / "tests" / "fixtures" / "za" / "lm015apr25-clearance.txt").read_text(encoding="utf-8")
HOUSING = (
    "The right of access to adequate housing cannot be seen in isolation. "
    "There is a close relationship between it and the other socio-economic rights."
)
BRICKS = "This difference is significant. It recognises that housing entails more than bricks and mortar."
MISDIRECTION = (
    "In conclusion, the central misdirection of the Tribunal in this case was the failure to appreciate "
    "that the accepted legal and economic prescripts did not allow it to embark on a characterisation enquiry "
    "which failed to take account of the character of the parties' relationship absent the impugned agreement "
    "- which relationship was, on all the facts, plainly vertical."
)
ORDER = "The Tribunal's order of 29 September 2021 is set aside and replaced by the following order:"
CLIENT = "Our client asked us to hide this matter."


class Doc:
    def __init__(
        self,
        text: str,
        *,
        doc_id: str,
        citation: str,
        title: str,
        source_id: str,
    ) -> None:
        self.id = doc_id
        self.jurisdiction = "za"
        self.citation = citation
        self._text = text
        self.kind = "case"
        self.status = "in_force"
        self.effective_to = None
        self.source_id = source_id
        self.version_id = f"v-{doc_id}"
        self.title = title
        self.docket_number = ""
        self.nature_of_suit = ""

    @property
    def text(self) -> str:
        return self._text


class Pool:
    def __init__(self, doc: Doc) -> None:
        self.docs = {doc.id: doc}
        self.home_region = "za"


class Answer:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[str] = []

    async def complete(self, messages: list[dict[str, str]], *, task_type: str, provider: str) -> dict[str, object]:
        del messages, provider
        self.calls.append(task_type)
        return {"content": self.content, "finish_reason": "STOP", "tokens_in": 10, "tokens_out": 20, "cost_usd": 0}


def _body(
    facts: str,
    *,
    citation: str,
    element: str | None = None,
    elements: list[dict[str, str]] | None = None,
    citations: list[dict[str, str]] | None = None,
) -> str:
    if elements is None:
        elements = [{"element_id": "holding", "finding": "met", "quote": ORDER if element is None else element}]
    if citations is None:
        citations = [{"citation": citation, "quote": ORDER if element is None else element}]
    return json.dumps(
        {
            "facts": facts,
            "question": "What did the court decide?",
            "elements": elements,
            "holding": "the appeal is upheld",
            "citations": citations,
        }
    )


@pytest.mark.asyncio
async def test_dot_joined_passages_pass_and_a_paraphrase_still_fails() -> None:
    joins = (
        f"{HOUSING}...{BRICKS}",
        f"{HOUSING} . . . {BRICKS}",
        f"{HOUSING}\u2026{BRICKS}",
        f"{HOUSING} \u2026 {BRICKS}",
    )
    for facts in joins:
        parts = fact_passages(facts)
        assert len(parts) == 2
        assert all(quote_in_source(part, GROOTBOOM) for part in parts)
        model = Answer(_body(facts, citation="[2000] ZACC 19", element=BRICKS))
        report = await convert_cases(
            Pool(Doc(GROOTBOOM, doc_id="grootboom", citation="[2000] ZACC 19", title="Government v Grootboom", source_id="concourt")),
            LadderMemory(),
            model,
            region="za",
        )
        assert report["converted"] == 1, facts
        assert report["failed"] == 0

    paraphrase = "The court held that the housing programme was enough because money had already been spent."
    mixed = f"{HOUSING}...{paraphrase}"
    failed = await convert_cases(
        Pool(Doc(GROOTBOOM + "\n" + CLIENT, doc_id="grootboom", citation="[2000] ZACC 19", title="Government v Grootboom", source_id="concourt")),
        LadderMemory(),
        Answer(_body(mixed, citation="[2000] ZACC 19", element=HOUSING)),
        region="za",
    )
    assert failed["converted"] == 0
    assert failed["failures"] == [{"reason": "quote is not in the judgment", "count": 1}]
    assert failed["cases"][0]["quote_part"] == "facts"
    assert failed["cases"][0]["quote_excerpt"] == paraphrase[:120]
    assert CLIENT not in failed["cases"][0]["answer"]
    assert CLIENT not in failed["cases"][0]["quote_excerpt"]


@pytest.mark.asyncio
async def test_the_why_panel_names_the_failing_part() -> None:
    bad = "The court took a different view of the appeal and set the whole order aside for a new reason."
    elements = [
        {"element_id": "one", "finding": "met", "quote": ORDER},
        {"element_id": "two", "finding": "met", "quote": bad},
    ]
    raw = _body(MISDIRECTION, citation="CAC 212", elements=elements, citations=[{"citation": "CAC 212", "quote": ORDER}])
    ladder = LadderMemory()
    report = await convert_cases(
        Pool(Doc(TOURVEST, doc_id="tourvest", citation="CAC 212", title="Tourvest Holdings v Competition Commission", source_id="cac")),
        ladder,
        Answer(raw),
        region="za",
    )
    assert report["converted"] == 0
    assert report["failures"][0]["reason"] == "quote is not in the judgment"
    case = report["cases"][0]
    assert case["quote_part"] == "element 2"
    assert case["quote_excerpt"] == bad[:120]
    assert ladder.failures[0]["quote_part"] == "element 2"
    board = (ROOT / "workspace" / "app" / "zenas" / "feeds" / "board.tsx").read_text(encoding="utf-8")
    public = (ROOT / "workspace" / "app" / "api" / "zenas" / "feeds" / "public.ts").read_text(encoding="utf-8")
    assert "row.quote_part" in board
    assert "row.quote_excerpt" in board
    assert "quote_part" in public
    assert "not a judgment" in public


@pytest.mark.asyncio
async def test_cac_212_resolves_to_its_own_row_and_another_citation_does_not() -> None:
    assert same_case_citation("CAC 212", "CAC212")
    assert same_case_citation("CAC 212", "CAC 212.")
    assert same_case_citation("Special Tribunal GP 4/2020", "Special Tribunal GP 4/2020")
    assert not same_case_citation("CAC 212", "[2018] ZACAC 2")
    doc = Doc(
        TOURVEST,
        doc_id="cac-212",
        citation="CAC 212",
        title="Tourvest Holdings v Competition Commission",
        source_id="cac",
    )
    passed = await convert_cases(
        Pool(doc),
        LadderMemory(),
        Answer(
            _body(
                MISDIRECTION,
                citation="CAC212",
                citations=[{"citation": "CAC212", "quote": ORDER}],
            )
        ),
        region="za",
    )
    assert passed["converted"] == 1
    assert passed["failed"] == 0

    foreign = await convert_cases(
        Pool(doc),
        LadderMemory(),
        Answer(
            _body(
                MISDIRECTION,
                citation="CAC 212",
                citations=[{"citation": "[2018] ZACAC 2", "quote": ORDER}],
            )
        ),
        region="za",
    )
    assert foreign["converted"] == 0
    assert foreign["failures"][0]["reason"] == "citation did not resolve"
    assert foreign["cases"][0]["quote_part"] == "citation 1"


@pytest.mark.asyncio
async def test_a_merger_letter_is_not_a_judgment() -> None:
    letter = Doc(
        CLEARANCE,
        doc_id="lm015",
        citation="CompTrib LM015APR25",
        title="Public Version Order - Conditional Merger Approval LM015Apr25",
        source_id="comptrib",
    )
    assert merger_approval_letter(
        {"source_id": letter.source_id, "title": letter.title, "citation": letter.citation},
        letter.text,
    )
    assert not merger_approval_letter(
        {"source_id": "cac", "title": "Tourvest Holdings v Competition Commission", "citation": "CAC 212"},
        TOURVEST,
    )
    assert not merger_approval_letter(
        {"source_id": "comptrib", "title": "Reasons for Decision LM015Apr25", "citation": "CompTrib LM015APR25"},
        CLEARANCE,
    )
    model = Answer(_body(HOUSING, citation="CompTrib LM015APR25"))
    report = await convert_cases(Pool(letter), LadderMemory(), model, region="za")
    assert model.calls == []
    assert report["converted"] == 0
    assert report["failures"] == [{"reason": "not a judgment", "count": 1}]
    assert report["cases"][0]["quote_part"] == ""
    assert CLEARANCE not in report["cases"][0]["answer"]


@pytest.mark.asyncio
async def test_a_failed_case_keeps_only_its_latest_attempt() -> None:
    doc = Doc(
        GROOTBOOM,
        doc_id="grootboom",
        citation="[2000] ZACC 19",
        title="Government v Grootboom",
        source_id="concourt",
    )
    first = "The court held that the housing programme was enough because money had already been spent."
    second = "A later paraphrase of the housing right that is still not the court's words at all."
    ladder = LadderMemory()
    await convert_cases(Pool(doc), ladder, Answer(_body(first, citation="[2000] ZACC 19", element=HOUSING)), region="za")
    assert ladder.failures[0]["answer_excerpt"]
    await convert_cases(Pool(doc), ladder, Answer(_body(second, citation="[2000] ZACC 19", element=HOUSING)), region="za")
    assert len(ladder.failures) == 1
    saved = ladder.failures[0]
    assert first not in saved["answer_excerpt"]
    assert second in saved["answer_excerpt"]
    assert saved["quote_part"] == "facts"
    assert saved["quote_excerpt"] == second[:120]
    cleared = await convert_cases(
        Pool(doc), ladder, Answer(_body(HOUSING, citation="[2000] ZACC 19", element=HOUSING)), region="za"
    )
    assert cleared["converted"] == 1
    assert cleared["failed"] == 0
    assert ladder.failures == []
