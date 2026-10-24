"""MOOT-1a text edition: fixture proofs for Sunday §6.1 Done-when (no network, no live ZA)."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path

from agents.zenas.core.moot.address import court_style
from agents.zenas.core.moot.judge import flag_authorities, judge_text_ruling, questions_after_submission
from agents.zenas.core.moot.learning import gold_bucket, points_to_gold
from agents.zenas.core.moot.problem import (
    counsel_bundle,
    counsel_payload_leaks_bench,
    edit_problem,
    list_fixture_ids,
    load_fixture_problem,
    lock_problem,
    reset_edits,
)
from agents.zenas.core.moot.session import publish_match, run_fairness_pair, run_student_match, watch_payload

ROOT = Path(__file__).resolve().parents[1]


def setup_function() -> None:
    reset_edits()


def test_fixtures_are_civil_and_listed():
    ids = list_fixture_ids()
    assert "fixture-sa-lease-appeal" in ids
    assert "fixture-us-forum-appeal" in ids
    for pid in ids:
        problem = load_fixture_problem(pid)
        assert problem["kind"] == "civil_appeal"
        assert problem.get("criminal") is False


def test_counsel_bundle_never_includes_bench_notes():
    problem = load_fixture_problem("fixture-sa-lease-appeal")
    for side in ("appellant", "respondent"):
        payload = counsel_bundle(problem, side)
        assert counsel_payload_leaks_bench(payload) == []
        blob = str(payload).lower()
        assert "bench_notes" not in blob
        assert "press_questions" not in blob
        assert "real_holding" not in blob
        assert "preferred_order" not in blob


def test_founder_edit_then_lock():
    edited = edit_problem("fixture-sa-lease-appeal", {"title": "Edited fixture title [TEST]"})
    assert edited["title"].startswith("Edited")
    assert edited["locked"] is False
    locked = lock_problem("fixture-sa-lease-appeal")
    assert locked["locked"] is True
    try:
        edit_problem("fixture-sa-lease-appeal", {"title": "again"})
        assert False, "expected locked error"
    except ValueError as exc:
        assert "locked" in str(exc)


def test_made_up_and_nonbinding_as_binding_are_flagged():
    library = [
        "Fixture Pacta Sunt Servanda [TEST] ZASCA 1",
        "Fixture Other Division Persuasive [TEST] ZAGPPHC 9",
    ]
    binding = ["Fixture Pacta Sunt Servanda [TEST] ZASCA 1"]
    made = flag_authorities(
        "Counsel relies on Invented v Fake and says Fixture Other Division Persuasive [TEST] ZAGPPHC 9 binds this court.",
        library_names=library,
        binding_names=binding,
    )
    assert "Invented v Fake" in made["made_up"]
    assert any("ZAGPPHC 9" in n for n in made["nonbinding_as_binding"])
    assert made["ok"] is False
    assert made["penalty"] >= 2


def test_judgment_cites_only_verified_and_includes_reveal_and_penalties():
    problem = load_fixture_problem("fixture-sa-lease-appeal")
    library = [
        "Fixture Pacta Sunt Servanda [TEST] ZASCA 1",
        "Fixture Good Faith Lease [TEST] ZASCA 3",
    ]
    bad_app = ["I rely on Invented v Fake."]
    ruling = judge_text_ruling(
        problem=problem,
        appellant_points=bad_app,
        respondent_points=["Notice required."],
        library_names=library,
        appellant_flags=flag_authorities(" ".join(bad_app), library_names=library, binding_names=library),
    )
    assert ruling["order"] == "appeal_dismissed"
    assert "Appeal dismissed" in ruling["reveal"]["real_holding"]
    assert ruling["penalties"]["appellant"] >= 1
    assert any(f["kind"] == "made_up" for f in ruling["citation_flags"])
    for auth in ruling["authorities"]:
        assert "[TEST]" in auth or auth in library


def test_judge_questions_scale_with_strictness():
    problem = load_fixture_problem("fixture-sa-lease-appeal")
    style = court_style("za")
    gentle = questions_after_submission(
        problem, side="appellant", submission="plain point", strictness="gentle", style=style
    )
    sca = questions_after_submission(
        problem, side="appellant", submission="plain point", strictness="sca", style=style
    )
    assert len(gentle) <= len(sca)
    assert any("My Lord" in q for q in sca)


def test_fairness_pair_runs_both_orders_with_transcript_and_cost():
    match = run_fairness_pair("fixture-sa-lease-appeal", strictness="standard")
    assert match["published"] is False
    assert len(match["runs"]) == 2
    assert match["runs"][0]["first_side"] == "appellant"
    assert match["runs"][1]["first_side"] == "respondent"
    assert match["fairness"]["same_party_wins_both"] is True
    assert match["cost_usd"] > 0
    assert "moot.counsel" in match["task_types"]
    assert match["runs"][0]["transcript"]
    assert any(t["role"] == "judge" for t in match["runs"][0]["transcript"])
    watch = match["watch"]
    assert watch["published"] is False
    assert watch["transcript"]
    assert watch["authorities"]
    published = publish_match(match)
    assert published["published"] is True
    assert published["watch"]["published"] is True


def test_us_fairness_uses_us_forms_of_address():
    match = run_fairness_pair("fixture-us-forum-appeal")
    assert match["region"] == "us-east"
    style = match["runs"][0]["style"]
    assert style["judge_address"] == "Your Honor"
    assert style["party_labels"]["respondent"] == "Appellee"
    assert any("Your Honor" in t["text"] for t in match["runs"][0]["transcript"] if t["role"] == "judge")


def test_sa_fairness_uses_sa_forms_of_address():
    match = run_fairness_pair("fixture-sa-lease-appeal")
    assert match["region"] == "za"
    assert match["runs"][0]["style"]["judge_address"] == "My Lord"
    assert match["runs"][0]["style"]["opening"].startswith("May it please")


def test_student_ai_and_student_student_modes():
    ai = run_student_match(
        "fixture-sa-lease-appeal",
        mode="student_ai",
        student_side="appellant",
        student_text="The clause is clear.\nAuthority: Fixture Pacta Sunt Servanda [TEST] ZASCA 1",
    )
    assert ai["mode"] == "student_ai"
    assert ai["fairness"] is None
    assert ai["transcript"]
    ss = run_student_match(
        "fixture-us-forum-appeal",
        mode="student_student",
        student_side="respondent",
        student_text="Enforcement would be unreasonable.",
    )
    assert ss["mode"] == "student_student"
    assert ss["region"] == "us-east"


def test_gold_candidates_bucket():
    match = run_fairness_pair("fixture-sa-lease-appeal")
    rows = points_to_gold(match, when=date(2026, 10, 24))
    assert rows
    assert all(r["bucket"] == "zenas:moot:2026-10" for r in rows)
    assert gold_bucket(date(2026, 10, 1)) == "zenas:moot:2026-10"


def test_region_forms_of_address():
    za = court_style("za")
    us = court_style("us-east")
    assert za["judge_address"] == "My Lord"
    assert us["judge_address"] == "Your Honor"


def test_counsel_route_strips_bench_notes(monkeypatch):
    monkeypatch.setenv("ZENAS_HOME_REGION", "za")
    monkeypatch.setenv("TANK_REGION", "za")
    from agents.zenas import app as zenas_app

    monkeypatch.setattr(zenas_app, "_box_region", lambda: "za")
    payload = asyncio.run(zenas_app.get_moot_counsel_bundle("fixture-sa-lease-appeal", "appellant"))
    assert counsel_payload_leaks_bench(payload) == []
    assert "bench_notes" not in payload


def test_watch_page_unpublished_returns_404_rule():
    store = (ROOT / "workspace/app/moot/store.ts").read_text(encoding="utf-8")
    page = (ROOT / "workspace/app/moot/[match]/page.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "workspace/app/moot/WatchPanel.tsx").read_text(encoding="utf-8")
    assert 'matchId: "fixture-unpublished"' in store
    assert "published: false" in store
    assert "notFound()" in page
    assert "WatchPanel" in page
    for label in ("Transcript", "Judgment", "Fairness check", "What the court actually held", "Authorities"):
        assert label in panel


def test_watch_payload_shape():
    match = run_fairness_pair("fixture-sa-lease-appeal")
    watch = watch_payload(match)
    assert set(watch) >= {
        "matchId",
        "published",
        "title",
        "transcript",
        "judgmentOrder",
        "fairnessReading",
        "reveal",
        "authorities",
    }


def test_policy_seeds_above_models2():
    schema = (ROOT / "tank" / "schema.sql").read_text(encoding="utf-8")
    i_counsel = schema.index("moot.counsel")
    i_judge = schema.index("moot.judge")
    i_models = schema.index("-- MODELS-2: provider tool charges")
    assert i_counsel < i_models and i_judge < i_models


def test_tank_allowlist_includes_moot_paths():
    main = (ROOT / "tank" / "main.py").read_text(encoding="utf-8")
    for fragment in (
        "moot/fixtures",
        "moot/matches/ai-ai",
        "moot/matches/student",
        "moot/problems/",
        "/publish$",
        "(edit|lock)",
    ):
        assert fragment in main
    # Compile the regex from source
    m = re.search(r"_ZENAS_PATH = re\.compile\(\n((?:    r\".*\"\n)+)\)", main)
    assert m
    ns: dict = {"re": re}
    exec("import re\n_ZENAS_PATH = re.compile(\n" + m.group(1) + ")", ns)
    pat = ns["_ZENAS_PATH"]
    assert pat.fullmatch("moot/matches/student")
    assert pat.fullmatch("moot/problems/fixture-sa-lease-appeal/edit")
    assert pat.fullmatch("moot/matches/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/publish")
