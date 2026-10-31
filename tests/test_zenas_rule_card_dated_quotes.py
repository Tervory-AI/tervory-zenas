"""Dated quotes: checked unmasked, word for word, then masked for output.

[2026] ZACAC 1 (ZA) and 1 F.2d 8, 10, 11 (Boston): the model quoted a sentence
with a date or a patent number. The quote used to be masked ("[Date]",
"[Amount]") BEFORE cite-check, so a true quote could never match. Now the exact
words are checked first. A wrong date, number or name still fails.
"""

import importlib.util
import sys
import uuid
from pathlib import Path

import httpx
import pytest

import skala

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core import engine, service  # noqa: E402
from agents.zenas.core.selftest import supported_quotes  # noqa: E402

CITE = "[2026] ZACAC 1"
JUDGMENT = ("The Tribunal approved the merger on 08 March 2023. Only the Tribunal may order a "
            "divestiture under section 43D(2) of the Competition Act.")
PASSAGES = [{"doc_id": "z1", "version_id": "v1", "citation": CITE, "text": JUDGMENT, "status": "effective"}]
DATED = "The Tribunal approved the merger on 08 March 2023."
PATENT_TEXT = "Claims 9 and 10 of United States patent 1,237,432 are void for want of invention."
PATENT = [{"doc_id": "f", "version_id": "v1", "citation": "1 F.2d 10", "text": PATENT_TEXT, "status": "effective"}]


def _packages():
    return {(p.id, p.version): p for p in skala.load_packages().values()}


def _quote_rule() -> str:
    path = ROOT / "tank" / "skills" / "zenas.rule_card" / "1.1.0" / "run.py"
    spec = importlib.util.spec_from_file_location("rule_card_110", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.QUOTE_RULE


def _context(pkg):
    async def no_model(**kwargs):
        raise AssertionError("saved model output only")

    def no_network(request):
        raise AssertionError("no network")

    return skala.SkillContext(uuid.uuid4(), pkg.ref, pkg.agent, None, no_model,
                              httpx.AsyncClient(transport=httpx.MockTransport(no_network)))


def _card(quote, passages=PASSAGES, citation=CITE):
    proposal = {"elements": [{"id": "e", "test": "Who decides.", "citation": citation, "quote": quote}]}
    return engine.card_from_proposal(proposal, passages, question="What did the court hold?", secrets=[])


def _recheck(card, passages=PASSAGES):
    statements = [{"citation": e["citation"], "quote": e["quote"], "quote_sha": e.get("quote_sha")}
                  for e in card["elements"]]
    return engine.cite_check(statements, passages)


# Prompt hint (kept, not the fix).
def test_room_prompt_and_skill_carry_the_same_quote_hint():
    rule = _quote_rule()
    assert "dates" in rule and "amounts" in rule and "patent" in rule
    assert rule in service._RULE_PROMPT
    assert rule in service.rule_card_prompt("Who may order a divestiture?", [{"citation": "X", "text": "Y."}])


def test_rule_card_1_1_0_keeps_every_1_0_1_case_and_1_0_1_is_untouched():
    pkgs = _packages()
    old, new = pkgs[("zenas.rule_card", "1.0.1")], pkgs[("zenas.rule_card", "1.1.0")]
    assert {skala.evidence_hash(c) for c in old.cases} <= {skala.evidence_hash(c) for c in new.cases}
    assert len(new.cases) == len(old.cases) + 3
    assert "QUOTE_RULE" not in (old.path / "run.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rule_card_1_1_0_evals_pass_offline_with_dated_evidence():
    pkg = _packages()[("zenas.rule_card", "1.1.0")]
    result = await skala.run_evals(pkg, lambda: _context(pkg))
    assert result["passed"] == result["total"] == len(pkg.cases), [d for d in result["details"] if not d["passed"]]
    assert result["eligible"] is True, result["evidence_reason"]
    by_name = {d["name"]: d for d in result["details"]}
    assert by_name["a true dated quote is approvable"]["result"]["approvable"] is True
    assert by_name["a quote with an altered date is not approvable"]["result"]["approvable"] is False


# Root fix in the room.
def test_true_dated_quote_passes_and_is_shown_masked():
    card = _card(DATED)
    element = card["elements"][0]
    assert card["cite"]["passed"] is True
    assert element["quote"] == "The Tribunal approved the merger on [Date]."
    assert "2023" not in element["quote"] and element["quote_sha"]
    assert engine.law_only_problem(card, []) is None
    assert _recheck(card)["passed"] is True  # stored card, rechecked later


@pytest.mark.parametrize("wrong", [
    "The Tribunal approved the merger on 09 March 2023.",
    "The Tribunal approved the merger on 08 March 2022.",
    "The Tribunal approved the merger on 8 April 2023.",
])
def test_quote_with_an_altered_date_still_fails(wrong):
    card = _card(wrong)
    assert card["cite"]["passed"] is False
    assert _recheck(card)["passed"] is False


def test_true_patent_number_quote_passes_and_an_altered_one_fails():
    good = _card(PATENT_TEXT, PATENT, "1 F.2d 10")
    assert good["cite"]["passed"] is True and "[Amount]" in good["elements"][0]["quote"]
    assert _recheck(good, PATENT)["passed"] is True
    bad = _card(PATENT_TEXT.replace("1,237,432", "1,237,433"), PATENT, "1 F.2d 10")
    assert bad["cite"]["passed"] is False
    assert _recheck(bad, PATENT)["passed"] is False


def test_masked_quote_without_the_right_fingerprint_fails():
    masked = "The Tribunal approved the merger on [Date]."
    assert engine.cite_check([{"citation": CITE, "quote": masked}], PASSAGES)["passed"] is False
    wrong = engine.quote_sha("The Tribunal approved the merger on 09 March 2023.")
    assert engine.cite_check([{"citation": CITE, "quote": masked, "quote_sha": wrong}], PASSAGES)["passed"] is False


def test_edited_words_around_the_mask_fail_even_with_the_old_fingerprint():
    card = _card(DATED)
    element = dict(card["elements"][0])
    element["quote"] = "The Tribunal refused the merger on [Date]."
    assert _recheck({"elements": [element]})["passed"] is False


def test_undated_quote_is_unchanged_and_carries_no_fingerprint():
    card = _card("Only the Tribunal may order a divestiture under section 43D(2) of the Competition Act.")
    assert card["cite"]["passed"] is True and "quote_sha" not in card["elements"][0]


def test_selftest_counts_a_true_masked_quote_only_through_its_fingerprint():
    card = _card(DATED)
    result = {"rule_card": {"elements": card["elements"]}}
    assert supported_quotes(result, CITE, JUDGMENT) == ["The Tribunal approved the merger on [Date]."]
    forged = {**card["elements"][0], "quote_sha": engine.quote_sha("The Tribunal approved the merger on 09 March 2023.")}
    assert supported_quotes({"rule_card": {"elements": [forged]}}, CITE, JUDGMENT) == []
    bare = {k: v for k, v in card["elements"][0].items() if k != "quote_sha"}
    assert supported_quotes({"rule_card": {"elements": [bare]}}, CITE, JUDGMENT) == []
