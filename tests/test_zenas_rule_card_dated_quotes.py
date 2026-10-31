"""zenas.rule_card 1.1.0: quotes with dates or identifiers are not asked for.

[2026] ZACAC 1 on ZA: the model quoted "... 08 March 2023". Cleaning masks the
date before cite-check, so the quote could not match and the answer became
"could not verify". The mask and cite-check stay as they are.
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

from agents.zenas.core import service  # noqa: E402


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


def test_room_prompt_and_skill_carry_the_same_quote_rule():
    rule = _quote_rule()
    assert "dates" in rule and "cannot be verified" in rule
    assert rule in service._RULE_PROMPT
    assert rule in service.rule_card_prompt("Who may order a divestiture?", [{"citation": "X", "text": "Y."}])


def test_1_0_2_keeps_every_1_0_1_case_and_1_0_1_is_untouched():
    pkgs = _packages()
    old, new = pkgs[("zenas.rule_card", "1.0.1")], pkgs[("zenas.rule_card", "1.1.0")]
    assert {skala.evidence_hash(c) for c in old.cases} <= {skala.evidence_hash(c) for c in new.cases}
    assert len(new.cases) == len(old.cases) + 1
    assert "QUOTE_RULE" not in (old.path / "run.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_1_0_2_evals_pass_offline_and_are_eligible():
    pkg = _packages()[("zenas.rule_card", "1.1.0")]
    result = await skala.run_evals(pkg, lambda: _context(pkg))
    assert result["passed"] == result["total"] == len(pkg.cases), [d for d in result["details"] if not d["passed"]]
    assert result["eligible"] is True, result["evidence_reason"]
    by_name = {d["name"]: d for d in result["details"]}
    assert by_name["an undated sentence from the same judgment is approvable"]["result"]["approvable"] is True


JUDGMENT = ("The Tribunal approved the merger on 08 March 2023. Only the Tribunal may order a "
            "divestiture under section 43D(2) of the Competition Act.")
PASSAGES = [{"doc_id": "z1", "version_id": "v1", "citation": "[2026] ZACAC 1", "text": JUDGMENT, "status": "effective"}]


def _room_card(quote):
    from agents.zenas.core import engine
    proposal = {"elements": [{"id": "e", "test": "Who decides.", "citation": "[2026] ZACAC 1", "quote": quote}]}
    return engine.card_from_proposal(proposal, PASSAGES, question="Who may order a divestiture?", secrets=[])


def test_room_still_masks_a_dated_quote_and_fails_it():
    # Unchanged gate: the date is masked, so the quote cannot be verified.
    card = _room_card("The Tribunal approved the merger on 08 March 2023.")
    assert "[Date]" in card["elements"][0]["quote"]
    assert card["cite"]["passed"] is False


def test_room_passes_an_undated_sentence_from_the_same_judgment():
    card = _room_card("Only the Tribunal may order a divestiture under section 43D(2) of the Competition Act.")
    assert card["cite"]["passed"] is True
