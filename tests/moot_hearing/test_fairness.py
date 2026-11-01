from copy import deepcopy
from uuid import uuid4

import pytest

from agents.zenas.core.moot import hearing
from agents.zenas.core.moot.fairness import evidence, pair
from .test_hearing import HUMANS, OWNER, hearing as draft


def paired():
    s = draft({seat: "ai" for seat in HUMANS})
    s = hearing.change(s, OWNER, s["revision"], "lock", {})
    return pair(s, OWNER, s["revision"], str(uuid4()), str(uuid4()))


def completed(state, decision):
    """Offline completed-state double, not a provider or live hearing."""
    s = deepcopy(state); s["phase"] = "closed"; s["ruling"] = {"decision": decision}
    s["transcript"] = [{"seat": seat, "controller": "ai"} for seat in hearing.SEATS]
    s["receipts"] = [{"seat": seat, "model": "offline-model", "settings_hash": "a" * 64} for seat in hearing.SEATS]
    return s


def test_pair_exchanges_actual_counsel_identity_and_role_without_context_or_outcome_key():
    a, b = paired()
    assert a["problem"] == b["problem"] and a["id"] != b["id"]
    assert a["floor"] == "applicant" and b["floor"] == "respondent"
    assert a["counsel_seats"]["applicant"] == b["counsel_seats"]["respondent"] == "counsel_a"
    assert a["ruling"] is None and b["ruling"] is None
    assert evidence(a, b)["status"] == "incomplete"


def test_measurement_uses_actual_independent_decisions_not_real_holding():
    a, b = paired(); a, b = completed(a, "applicant"), completed(b, "respondent")
    result = evidence(a, b)
    assert result["status"] == "measured" and result["settings_verified"]
    assert result["same_party_wins_both"] is False and result["same_seat_wins_both"] is True
    b["ruling"]["decision"] = "applicant"
    assert evidence(a, b)["same_party_wins_both"] is True
    assert evidence(a, b)["same_seat_wins_both"] is False


@pytest.mark.parametrize("missing", ["model", "settings_hash"])
def test_missing_actual_receipt_evidence_never_implies_equal_settings(missing):
    a, b = paired(); a, b = completed(a, "applicant"), completed(b, "respondent")
    b["receipts"][0].pop(missing)
    result = evidence(a, b)
    assert result["status"] == "settings_unverified" and not result["settings_verified"]
    assert result["same_party_wins_both"] is None


def test_changed_settings_mixed_controllers_and_uncertainty_are_honest_states():
    a, b = paired(); a, b = completed(a, "applicant"), completed(b, "uncertain")
    assert evidence(a, b)["status"] == "uncertain_decision"
    b["receipts"][0]["settings_hash"] = "b" * 64
    assert evidence(a, b)["status"] == "settings_mismatch"
    b["transcript"][0]["controller"] = "human"
    assert evidence(a, b)["status"] == "counsel_controller_changed"


def test_public_fairness_never_exports_unpublished_partner_or_actor_identifiers():
    from agents.zenas.core.moot.fairness import public_evidence
    a, b = paired()
    result = public_evidence(evidence(a, b))
    assert a["id"] not in str(result) and b["id"] not in str(result)
    assert "owner_id" not in result and result["settings_verified"] is False
    assert result["reading"] == "The paired hearing is incomplete."


@pytest.mark.parametrize("seat,expected", [("judge", "judge_controller_changed"), ("applicant", "counsel_controller_changed")])
def test_restored_controller_does_not_hide_handover_history(seat, expected):
    a, b = paired(); a, b = completed(a, "applicant"), completed(b, "respondent")
    b["seats"][seat]["epoch"] += 2
    assert evidence(a, b)["status"] == expected


def test_human_judge_requires_actual_same_author_on_every_judge_turn():
    a, b = paired(); a, b = completed(a, "applicant"), completed(b, "respondent")
    for state in (a, b):
        state["seats"]["judge"] = {"kind": "human", "user_id": "same-student", "epoch": 0}
        state["transcript"][-1].update(controller="human", actor_id="same-student")
    assert evidence(a, b)["status"] == "measured"
    b["transcript"][-1]["actor_id"] = "another-student"
    assert evidence(a, b)["status"] == "judge_controller_changed"
