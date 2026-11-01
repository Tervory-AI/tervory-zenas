"""Compare two independent hearings with actual counsel seats exchanged.

The original court's holding is never an outcome key. Missing actual model/settings
receipts yield unverified evidence, never a fairness pass based on intended config.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from . import hearing


def pair(state: dict[str, Any], actor: hearing.Actor, revision: int, pair_id: str, other_id: str):
    primary = hearing._begin(state, actor, revision)
    hearing._operator(primary, actor)
    hearing._require(primary["phase"] == "locked" and primary.get("fairness_pair_id") is None, "locked_unpaired_hearing_required")
    hearing._require(all(primary["seats"][s]["kind"] == "ai" for s in hearing.SIDES), "ai_counsel_required")
    primary["fairness_pair_id"] = hearing._uuid(pair_id)
    primary["counsel_seats"] = {"applicant": "counsel_a", "respondent": "counsel_b"}
    primary["first_side"] = "applicant"
    primary["floor"] = "applicant"
    second = deepcopy(primary)
    second["id"] = hearing._uuid(other_id)
    second["revision"] = 0
    second["counsel_seats"] = {"applicant": "counsel_b", "respondent": "counsel_a"}
    second["first_side"] = "respondent"
    second["floor"] = "respondent"
    return hearing._finish(primary), second


def _settings(receipts: list[dict[str, Any]]) -> tuple[str, str] | None:
    if not receipts or any(not r.get("model") or not r.get("settings_hash") for r in receipts):
        return None
    settings = {(r["model"], r["settings_hash"]) for r in receipts}
    return next(iter(settings)) if len(settings) == 1 else ("mismatch", "mismatch")


def evidence(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "incomplete", "first_hearing": first["id"], "second_hearing": second["id"],
        "same_party_wins_both": None, "same_seat_wins_both": None, "settings_verified": False}
    if first["phase"] != "closed" or second["phase"] != "closed":
        return result
    if json.dumps(first["problem"], sort_keys=True) != json.dumps(second["problem"], sort_keys=True):
        return {**result, "status": "problem_changed"}
    if first.get("counsel_seats") != {"applicant": "counsel_a", "respondent": "counsel_b"} or second.get("counsel_seats") != {"applicant": "counsel_b", "respondent": "counsel_a"}:
        return {**result, "status": "seats_not_swapped"}
    for state in (first, second):
        if any(state["seats"][seat]["kind"] != "ai" or state["seats"][seat]["epoch"] != first["seats"][seat]["epoch"] for seat in hearing.SIDES) or any(t["controller"] != "ai" or t.get("epoch", 0) != state["seats"][t["seat"]]["epoch"] for t in state["transcript"] if t["seat"] in hearing.SIDES):
            return {**result, "status": "counsel_controller_changed"}
    # A human judge is supported, but must be the same assigned person in both
    # runs. An AI judge needs its own actual model/settings consistency evidence.
    a, b = first["seats"]["judge"], second["seats"]["judge"]
    if a["kind"] != b["kind"] or a["kind"] == "human" and a["user_id"] != b["user_id"]:
        return {**result, "status": "judge_controller_changed"}
    for state in (first, second):
        judge = state["seats"]["judge"]
        if judge["epoch"] != a["epoch"] or any(t["controller"] != judge["kind"] or t.get("epoch", 0) != judge["epoch"] or judge["kind"] == "human" and t.get("actor_id") != judge["user_id"] for t in state["transcript"] if t["seat"] == "judge"):
            return {**result, "status": "judge_controller_changed"}
    groups = [[r for s in (first, second) for r in s["receipts"] if r["seat"] in hearing.SIDES and not r.get("discarded")]]
    if a["kind"] == "ai":
        groups.append([r for s in (first, second) for r in s["receipts"] if r["seat"] == "judge" and not r.get("discarded")])
    settings = [_settings(group) for group in groups]
    if any(s == ("mismatch", "mismatch") for s in settings):
        return {**result, "status": "settings_mismatch"}
    if any(s is None for s in settings):
        return {**result, "status": "settings_unverified"}
    result["settings_verified"] = True
    decisions = [s["ruling"].get("decision", "uncertain") for s in (first, second)]
    if any(d not in hearing.SIDES for d in decisions):
        return {**result, "status": "uncertain_decision"}
    same_party = decisions[0] == decisions[1]
    same_seat = first["counsel_seats"][decisions[0]] == second["counsel_seats"][decisions[1]]
    return {**result, "status": "measured", "same_party_wins_both": same_party,
            "same_seat_wins_both": same_seat, "decisions": decisions}


def public_evidence(result: dict[str, Any]) -> dict[str, Any]:
    labels = {
        "incomplete": "The paired hearing is incomplete.",
        "problem_changed": "The paired problems differ.",
        "seats_not_swapped": "The counsel seats were not exchanged.",
        "counsel_controller_changed": "Counsel control changed during the comparison.",
        "judge_controller_changed": "Judge control changed during the comparison.",
        "settings_mismatch": "The runs used different model settings.",
        "settings_unverified": "The model settings have not been verified.",
        "uncertain_decision": "The evidence does not support a definite comparative result.",
    }
    reading = labels.get(result["status"], "Fairness check not measured.")
    if result["status"] == "measured":
        reading = ("The same party prevailed in both runs." if result["same_party_wins_both"]
                   else "The same AI seat prevailed in both runs." if result["same_seat_wins_both"]
                   else "The paired runs had different results.")
    # Partner IDs and private actor/model details never become a public export.
    return {"reading": reading, **{k: result[k] for k in (
        "settings_verified", "same_party_wins_both", "same_seat_wins_both")}}
