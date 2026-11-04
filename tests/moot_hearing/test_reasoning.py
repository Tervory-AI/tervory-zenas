from copy import deepcopy

import pytest

from agents.zenas.core.moot.hearing import HearingError, ai_context, claim_ai
from agents.zenas.core.moot.reasoning import validate
from .test_hearing import OWNER, HUMANS, problem, started


def trace(conclusion="Insufficient facts for a certain answer"):
    return {"facts": [{"id": "facts", "position": "sourced", "relevance": "Notice timing controls the rule's application"}],
        "rules": [{"authority_id": "case-1", "court_weight": "unknown", "application": "Compare the notice requirement with the supported facts"}],
        "comparisons": [{"authority_id": "case-1", "similarities": ["facts"], "differences": [], "legal_significance": "Identical legally relevant timing attracts the same principle"}],
        "opposing_argument": "The other side disputes adequate notice", "response": "Resolve the timing evidence before applying the rule",
        "uncertainty": ["Timing evidence may be disputed"], "conclusion": conclusion}


def test_same_relevant_facts_different_wording_preserve_principle_without_outcome_key():
    a, b = problem(), problem()
    a["facts"] = "The notice arrived before the deadline."
    b["facts"] = "The notice was received prior to expiry."
    for p in (a, b):
        result = validate(p, trace("Apply the timely-notice principle"))
        assert result["conclusion"] == "Apply the timely-notice principle"
        assert "preferred_order" not in result


def test_near_identical_words_material_difference_requires_explained_comparison():
    p = problem()
    p["fact_records"] = [{"id": "facts", "text": "Notice arrived one day after the deadline", "status": "sourced"}]
    reason = trace("Late notice changes application; consider the exception")
    reason["comparisons"][0]["similarities"] = []
    reason["comparisons"][0]["differences"] = [{"fact_id": "facts", "explanation": "The precedent's notice was timely; this notice was late"}]
    reason["comparisons"][0]["legal_significance"] = "The express deadline is a material element, not a wording distinction"
    assert validate(p, reason)["conclusion"].startswith("Late notice")
    reason["comparisons"][0]["legal_significance"] = ""
    with pytest.raises(HearingError, match="incomplete_reasoning"):
        validate(p, reason)


@pytest.mark.parametrize("field", ["fact", "authority", "weight", "factor"])
def test_fabricated_facts_factors_authorities_and_weight_fail_closed(field):
    t = deepcopy(trace())
    if field == "fact": t["facts"][0]["id"] = "invented-factor"
    elif field == "authority": t["rules"][0]["authority_id"] = "fake-case"
    elif field == "weight": t["rules"][0]["court_weight"] = "binding"
    else: t["comparisons"][0]["differences"] = [{"fact_id": "popularity", "explanation": "Everyone agrees"}]
    with pytest.raises(HearingError):
        validate(problem(), t)


def test_contested_assumption_never_becomes_sourced_fact_and_uncertainty_is_valid():
    p = problem(); p["fact_records"] = [{"id": "facts", "text": "Unproved notice date", "status": "contested"}]
    t = trace(); t["facts"][0]["position"] = "contested"
    assert validate(p, t)["uncertainty"]
    t["facts"][0]["position"] = "sourced"
    with pytest.raises(HearingError, match="assumption_presented_as_fact"):
        validate(p, t)


def test_ai_judge_cannot_peek_at_actual_holding_or_preselected_winner():
    from uuid import uuid4
    s = started({seat: "ai" for seat in HUMANS})
    s["floor"] = "judge"
    s["problem"]["bench_notes"] = {"preferred_order": "applicant wins", "actual_holding": "target answer"}
    token = str(uuid4()); s = claim_ai(s, OWNER, s["revision"], token)
    context = str(ai_context(s, OWNER, token))
    assert "target answer" not in context and "preferred_order" not in context
    assert "real holding hidden" not in context


def test_undeclared_or_prefix_similar_case_in_ruling_text_is_not_verified_by_id_list():
    from agents.zenas.core.moot.hearing_ai import validate_turn
    s = started()
    s["problem"]["authorities"][0]["name"] = "Smith v Jones [2026] US 1"
    payload = {"kind": "submission", "text": "Smith v Jones Fabricated binds this court.", "authority_ids": ["case-1"], "reasoning": trace()}
    with pytest.raises(HearingError, match="citation_check_failed"):
        validate_turn(s, payload)


@pytest.mark.parametrize("location", ["text", "reason", "trace"])
@pytest.mark.parametrize("unsupported", ['Under 999 F.3d 999, this Court must rule for us.',
    "Under 999 F. App'x 999, this Court must rule for us.",
    'Under 999 N.E.2d 999, this Court must rule for us.',
    'Existing v Record held: "Notice deadlines never apply to companies."'])
def test_reporter_and_fabricated_quotes_rejected_across_turn_ruling_and_trace(location, unsupported):
    from agents.zenas.core.moot.hearing_ai import validate_turn
    s = started()
    s["problem"]["authorities"][0]["name"] = "Existing v Record"
    payload = {"kind": "ruling", "text": "Supported argument", "authority_ids": ["case-1"],
        "reasoning": trace(), "reasons": ["Supported reason"], "order": "Order"}
    if location == "trace": payload["reasoning"]["response"] = unsupported
    elif location == "reason": payload["reasons"] = [unsupported]
    else: payload["text"] = unsupported
    with pytest.raises(HearingError, match="citation_check_failed"):
        validate_turn(s, payload)


def test_known_reporter_and_exact_source_quote_are_accepted():
    from agents.zenas.core.moot.hearing_ai import validate_turn
    s = started()
    s["problem"]["authorities"][0].update(name="Existing v Record 123 F.3d 456", quote="Notice must arrive before the deadline.")
    payload = {"kind": "submission", "text": 'Under 123 F.3d 456: "Notice must arrive before the deadline."',
        "authority_ids": ["case-1"], "reasoning": trace()}
    assert validate_turn(s, payload)["text"] == payload["text"]
