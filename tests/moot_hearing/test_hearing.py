"""Offline domain acceptance: three interchangeable seats, retained state, no model calls."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agents.zenas.core.moot.hearing import (
    Actor, HearingError, abandon_ai, ai_context, change, claim_ai, create,
    mark_ai_started, turn, view,
)

TENANT = str(uuid4())
OWNER = Actor(TENANT, "founder", "us-east", True)
HUMANS = {s: Actor(TENANT, s + "-student", "us-east") for s in ("applicant", "respondent", "judge")}


def problem():
    return {"worked_id": str(uuid4()), "region": "us-east", "kind": "civil_appeal", "criminal": False,
            "title": "Offline civil appeal", "facts": "Offline public facts", "issues": ["Issue one", "Issue two"],
            "bundles": {s: {"grounds": [s], "bench_notes": {"secret": "never counsel"}} for s in ("applicant", "respondent")},
            "bench_notes": {"secret": "never counsel"}, "real_holding": "real holding hidden until judgment",
            "authorities": [{"id": "case-1", "name": "Offline authority", "quote": "A source quote verified in the service boundary.",
                             "source_url": "https://example.invalid/case", "verified": True}]}


def hearing(kinds=None):
    kinds = kinds or {s: "human" for s in HUMANS}
    seats = {s: {"kind": k, **({"user_id": HUMANS[s].user_id} if k == "human" else {})} for s, k in kinds.items()}
    return create(str(uuid4()), OWNER, problem(), seats)


def started(kinds=None):
    s = hearing(kinds)
    s = change(s, OWNER, s["revision"], "lock", {})
    return change(s, OWNER, s["revision"], "start", {})


def submit(s, seat, kind="submission", **extra):
    return turn(s, HUMANS[seat], s["revision"], {"kind": kind, "text": seat + " words", **extra})


def test_three_actual_students_and_human_judge_finish_without_stub_counsel():
    s = started()
    s = submit(s, "applicant")
    s = submit(s, "judge", "question", target="respondent")
    assert s["pending_question"]["target"] == "respondent"
    s = submit(s, "respondent")
    s = submit(s, "judge", "ruling", order="Actual human order", reasons=["Actual human reason"], authority_ids=["case-1"])
    assert s["phase"] == "closed"
    assert [t["actor_id"] for t in s["transcript"]] == ["applicant-student", "judge-student", "respondent-student", "judge-student"]
    assert s["receipts"] == []  # Human hearing costs are not fabricated.
    assert s["ruling"]["order"] == "Actual human order"


@pytest.mark.parametrize("seat", ["applicant", "respondent", "judge"])
def test_every_seat_handover_both_directions_preserves_all_context(seat):
    s = started()
    s = submit(s, "applicant")
    s = submit(s, "judge", "question", target="respondent")
    before = deepcopy(s)
    s = change(s, OWNER, s["revision"], "handover", {"seat": seat, "controller": {"kind": "ai"}})
    s = change(s, OWNER, s["revision"], "handover", {"seat": seat, "controller": {"kind": "human", "user_id": HUMANS[seat].user_id}})
    for key in ("transcript", "current_issue", "citations", "pending_question", "floor", "problem"):
        assert s[key] == before[key]
    assert s["seats"][seat]["epoch"] == 2
    assert s["revision"] == before["revision"] + 2


@pytest.mark.parametrize("human_seats", [[], ["applicant"], ["respondent"], ["judge"], list(HUMANS)])
def test_all_requested_modes_are_controller_configuration(human_seats):
    s = started({seat: "human" if seat in human_seats else "ai" for seat in HUMANS})
    assert {seat for seat, c in s["seats"].items() if c["kind"] == "human"} == set(human_seats)


def test_started_ai_answer_cannot_commit_after_human_takes_seat():
    s = started({seat: "ai" for seat in HUMANS})
    token = str(uuid4())
    s = claim_ai(s, OWNER, s["revision"], token)
    s = mark_ai_started(s, OWNER, s["revision"], token)
    pending_revision = s["revision"]
    s = change(s, OWNER, s["revision"], "handover", {"seat": "applicant", "controller": {"kind": "human", "user_id": "applicant-student"}})
    for revision in (pending_revision, s["revision"]):
        with pytest.raises(HearingError):
            turn(s, OWNER, revision, {"kind": "submission", "text": "stale AI"}, ai_token=token)
    assert not s["transcript"] and s["pending_ai"] is None
    s = submit(s, "applicant")
    assert s["transcript"][0]["text"] == "applicant words"


def test_no_duplicate_ai_claim_or_started_call_replay():
    s = started({seat: "ai" for seat in HUMANS})
    token = str(uuid4())
    s = claim_ai(s, OWNER, s["revision"], token)
    with pytest.raises(HearingError, match="ai_turn_pending"):
        claim_ai(s, OWNER, s["revision"], str(uuid4()))
    s = mark_ai_started(s, OWNER, s["revision"], token)
    with pytest.raises(HearingError, match="ai_claim_invalid"):
        mark_ai_started(s, OWNER, s["revision"], token)
    s = abandon_ai(s, OWNER, s["revision"], token)
    with pytest.raises(HearingError, match="ai_token_reused"):
        claim_ai(s, OWNER, s["revision"], token)


def test_ai_commit_has_real_receipt_contract_and_exactly_one_committed_turn():
    s = started({seat: "ai" for seat in HUMANS})
    token = str(uuid4())
    s = claim_ai(s, OWNER, s["revision"], token)
    s = mark_ai_started(s, OWNER, s["revision"], token)
    rev = s["revision"]
    with pytest.raises(HearingError, match="receipt_required"):
        turn(s, OWNER, rev, {"kind": "submission", "text": "AI words"}, ai_token=token)
    receipt = {"ledger_id": str(uuid4()), "model": "offline-mock", "cost_usd": None}
    committed = turn(s, OWNER, rev, {"kind": "submission", "text": "AI words"}, ai_token=token, receipt=receipt)
    assert len(committed["transcript"]) == len(committed["receipts"]) == 1
    assert committed["receipts"][0]["cost_usd"] is None
    with pytest.raises(HearingError):
        turn(committed, OWNER, committed["revision"], {"kind": "submission", "text": "duplicate"}, ai_token=token, receipt=receipt)
    assert s["pending_ai"] is not None  # Pure transitions never mutate their input.


def test_judge_notes_and_reveal_are_not_counsel_or_observer_context():
    s = started()
    for side in ("applicant", "respondent"):
        exposed = str(view(s, HUMANS[side], side))
        assert "never counsel" not in exposed and "bench_notes" not in exposed
        assert "real holding hidden" not in exposed
    assert "never counsel" in str(view(s, HUMANS["judge"], "judge"))
    assert "never counsel" not in str(view(s, OWNER))
    with pytest.raises(HearingError):
        view(s, HUMANS["applicant"], "judge")
    for bad in (Actor(str(uuid4()), OWNER.user_id, OWNER.region, True), Actor(TENANT, "outsider", OWNER.region), Actor(TENANT, OWNER.user_id, "za", True)):
        with pytest.raises(HearingError, match="not_found"):
            view(s, bad)


def test_ai_counsel_prompt_never_inherits_judge_notes_after_switch():
    s = started({seat: "ai" for seat in HUMANS})
    token = str(uuid4())
    s = claim_ai(s, OWNER, s["revision"], token)
    assert "never counsel" not in str(ai_context(s, OWNER, token))


def test_stale_human_and_wrong_controller_cannot_submit():
    s = started()
    prior = s["revision"]
    s = change(s, OWNER, prior, "handover", {"seat": "applicant", "controller": {"kind": "ai"}})
    with pytest.raises(HearingError):
        turn(s, HUMANS["applicant"], prior, {"kind": "submission", "text": "stale"})
    with pytest.raises(HearingError):
        turn(s, HUMANS["respondent"], s["revision"], {"kind": "submission", "text": "wrong side"})


def test_civil_region_lock_and_operator_are_fail_closed():
    s = hearing()
    with pytest.raises(HearingError, match="problem_not_locked"):
        change(s, OWNER, 0, "start", {})
    with pytest.raises(HearingError):
        change(s, HUMANS["applicant"], 0, "lock", {})
    locked = change(s, OWNER, 0, "lock", {})
    with pytest.raises(HearingError, match="problem_locked"):
        change(locked, OWNER, locked["revision"], "edit", {"problem": problem()})
    for field, value in (("criminal", True), ("region", "za"), ("kind", "criminal_trial")):
        p = problem(); p[field] = value
        with pytest.raises(HearingError):
            create(str(uuid4()), OWNER, p, s["seats"])


def test_unknown_authority_and_early_judgment_never_commit():
    s = started()
    with pytest.raises(HearingError, match="unverified_authority"):
        turn(s, HUMANS["applicant"], s["revision"], {"kind": "submission", "text": "unknown", "authority_ids": ["fake"]})
    s = submit(s, "applicant")
    with pytest.raises(HearingError, match="both_sides_must_be_heard"):
        submit(s, "judge", "ruling", order="premature", reasons=["premature"])
