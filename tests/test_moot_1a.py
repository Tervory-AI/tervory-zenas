"""MOOT-1a text edition: fixture proofs (no network, no live ZA)."""

from __future__ import annotations

from pathlib import Path

from agents.zenas.core.moot.address import court_style
from agents.zenas.core.moot.judge import flag_authorities, judge_text_ruling
from agents.zenas.core.moot.problem import (
    counsel_bundle,
    counsel_payload_leaks_bench,
    list_fixture_ids,
    load_fixture_problem,
)
from agents.zenas.core.moot.session import publish_match, run_fairness_pair
ROOT = Path(__file__).resolve().parents[1]


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


def test_judgment_cites_only_verified_and_includes_reveal():
    problem = load_fixture_problem("fixture-sa-lease-appeal")
    library = [
        "Fixture Pacta Sunt Servanda [TEST] ZASCA 1",
        "Fixture Good Faith Lease [TEST] ZASCA 3",
    ]
    ruling = judge_text_ruling(
        problem=problem,
        appellant_points=["Cancellation clause clear."],
        respondent_points=["Notice required."],
        library_names=library,
    )
    assert ruling["order"] == "appeal_dismissed"
    assert "Appeal dismissed" in ruling["reveal"]["real_holding"]
    for auth in ruling["authorities"]:
        assert auth in library or auth in (problem.get("bench_notes") or {}).get("binding_authorities", [])


def test_fairness_pair_runs_both_orders():
    match = run_fairness_pair("fixture-sa-lease-appeal")
    assert match["published"] is False
    assert len(match["runs"]) == 2
    assert match["runs"][0]["first_side"] == "appellant"
    assert match["runs"][1]["first_side"] == "respondent"
    assert "fairness" in match and "reading" in match["fairness"]
    # Preferred order dismisses appeal → respondent wins both seatings → lopsided reading
    assert match["fairness"]["same_party_wins_both"] is True
    published = publish_match(match)
    assert published["published"] is True


def test_region_forms_of_address():
    za = court_style("za")
    us = court_style("us-east")
    assert za["judge_address"] == "My Lord"
    assert us["judge_address"] == "Your Honor"
    assert za["opening"].startswith("May it please")
    assert us["party_labels"]["respondent"] == "Appellee"


def test_counsel_route_strips_bench_notes(monkeypatch):
    monkeypatch.setenv("ZENAS_HOME_REGION", "za")
    monkeypatch.setenv("TANK_REGION", "za")
    from agents.zenas import app as zenas_app
    import asyncio

    monkeypatch.setattr(zenas_app, "_box_region", lambda: "za")
    payload = asyncio.run(zenas_app.get_moot_counsel_bundle("fixture-sa-lease-appeal", "appellant"))
    assert counsel_payload_leaks_bench(payload) == []
    assert "bench_notes" not in payload


def test_watch_page_unpublished_returns_404_rule():
    """Public /moot/{match} must 404 unless published (page + store contract)."""
    store = (ROOT / "workspace/app/moot/store.ts").read_text(encoding="utf-8")
    page = (ROOT / "workspace/app/moot/[match]/page.tsx").read_text(encoding="utf-8")
    assert 'matchId: "fixture-unpublished"' in store
    assert "published: false" in store
    assert "published: true" in store
    assert "notFound()" in page
    assert "!row.published" in page or "row.published" in page


def test_policy_seeds_above_models2():
    schema = (ROOT / "tank" / "schema.sql").read_text(encoding="utf-8")
    i_counsel = schema.index("moot.counsel")
    i_judge = schema.index("moot.judge")
    i_models = schema.index("-- MODELS-2: provider tool charges")
    assert i_counsel < i_models and i_judge < i_models


def test_tank_allowlist_includes_moot_paths():
    main = (ROOT / "tank" / "main.py").read_text(encoding="utf-8")
    assert "moot/fixtures" in main
    assert "moot/matches/ai-ai" in main
    assert "moot/problems/" in main
