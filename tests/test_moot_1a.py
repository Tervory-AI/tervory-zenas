"""MOOT-1a text edition: fixture proofs for Sunday §6.1 Done-when (no network, no live ZA)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from agents.zenas.core.moot.address import court_style
from agents.zenas.core.moot.judge import flag_authorities, judge_text_ruling, questions_after_submission
from agents.zenas.core.moot.learning import gold_bucket, points_to_gold
from agents.zenas.core.moot.problem import (
    counsel_bundle,
    counsel_payload_leaks_bench,
)
from agents.zenas.core.moot.session import run_one_order, watch_payload

ROOT = Path(__file__).resolve().parents[1]

# Existing civil fixture stays test-only; it is never packaged in the room.
PROBLEM = {'id': 'fixture-sa-lease-appeal',
 'kind': 'civil_appeal',
 'criminal': False,
 'region': 'za',
 'title': 'Fixture Landlord v Tenant (SCA) [TEST]',
 'court': 'Supreme Court of Appeal',
 'neutral_citation': '[TEST] ZASCA 0',
 'source_url': 'https://example.test/fixture-sa-lease-appeal',
 'facts': 'A commercial landlord cancelled a lease for late payment without '
          'notice. The High Court set the cancellation aside. The landlord '
          'appeals.',
 'grounds_of_appeal': ['The lease clause allowed cancellation on any late '
                       'payment.',
                       'No notice was required on a proper reading of the '
                       'clause.'],
 'bundles': {'appellant': {'role': 'Appellant (landlord)',
                           'grounds': ['The cancellation clause is clear and '
                                       'was triggered by late payment.',
                                       'The High Court rewrote a commercial '
                                       'bargain.'],
                           'authorities': [{'name': 'Fixture Pacta Sunt '
                                                    'Servanda [TEST] ZASCA 1',
                                            'binding': True,
                                            'paragraph': '12'},
                                           {'name': 'Fixture Commercial Lease '
                                                    'Co [TEST] ZASCA 2',
                                            'binding': True,
                                            'paragraph': '8'}]},
             'respondent': {'role': 'Respondent (tenant)',
                            'grounds': ['Cancellation without notice is '
                                        'contrary to good faith in this lease.',
                                        'The clause must be read with the '
                                        'notice provision in clause 14.'],
                            'authorities': [{'name': 'Fixture Good Faith Lease '
                                                     '[TEST] ZASCA 3',
                                             'binding': True,
                                             'paragraph': '21'},
                                            {'name': 'Fixture Other Division '
                                                     'Persuasive [TEST] '
                                                     'ZAGPPHC 9',
                                             'binding': False,
                                             'paragraph': '4'}]}},
 'bench_notes': {'issues': ['Whether notice was required before cancellation.',
                            'Whether the clause is enforceable as written.'],
                 'binding_authorities': ['Fixture Pacta Sunt Servanda [TEST] '
                                         'ZASCA 1',
                                         'Fixture Good Faith Lease [TEST] '
                                         'ZASCA 3'],
                 'press_questions': ['Counsel, where in the lease is notice '
                                     'dispensed with in clear words?'],
                 'preferred_order': 'appeal_dismissed',
                 'reason_paragraphs': ['1. The cancellation clause must be '
                                       "read with clause 14's notice "
                                       'requirement.',
                                       '2. On these facts, cancellation '
                                       'without notice could not stand.'],
                 'real_holding': 'Appeal dismissed. Notice was required before '
                                 'cancellation.'},
 'real_holding': 'Appeal dismissed. Notice was required before cancellation.'}

def _match():
    return {"match_id": "offline", "region": "za", "problem_id": PROBLEM["id"],
            "mode": "ai_ai", "runs": [run_one_order(PROBLEM, first_side="appellant")]}


def test_counsel_bundle_never_includes_bench_notes():
    problem = PROBLEM
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
    assert made["penalty"] >= 2


def test_judgment_cites_only_verified_and_includes_reveal_and_penalties():
    problem = PROBLEM
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
    problem = PROBLEM
    style = court_style("za")
    gentle = questions_after_submission(
        problem, side="appellant", submission="plain point", strictness="gentle", style=style
    )
    sca = questions_after_submission(
        problem, side="appellant", submission="plain point", strictness="sca", style=style
    )
    assert len(gentle) <= len(sca)
    assert any("My Lord" in q for q in sca)


def test_gold_candidates_bucket():
    match = _match()
    rows = points_to_gold(match, when=date(2026, 10, 24))
    assert rows
    assert all(r["bucket"] == "zenas:moot:2026-10" for r in rows)
    assert gold_bucket(date(2026, 10, 1)) == "zenas:moot:2026-10"


def test_region_forms_of_address():
    za = court_style("za")
    us = court_style("us-east")
    assert za["judge_address"] == "My Lord"
    assert us["judge_address"] == "Your Honor"


def test_watch_page_unpublished_returns_404_rule():
    store = (ROOT / "tests/moot-watch-fixtures.ts").read_text(encoding="utf-8")
    page = (ROOT / "workspace/app/moot/[match]/page.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "workspace/app/moot/WatchPanel.tsx").read_text(encoding="utf-8")
    assert 'matchId: "fixture-unpublished"' in store
    assert "published: false" in store
    assert "notFound()" in page
    assert "WatchPanel" in page
    for label in ("Transcript", "Judgment", "Fairness check", "What the court actually held", "Authorities"):
        assert label in panel


def test_watch_payload_shape():
    match = _match()
    watch = watch_payload(match, PROBLEM)
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


def test_tank_refuses_retired_moot_paths():
    main = (ROOT / "tank" / "main.py").read_text(encoding="utf-8")
    # Compile the regex from source
    m = re.search(r"_ZENAS_PATH = re\.compile\(\n((?:    r\".*\"\n)+)\)", main)
    assert m
    ns: dict = {"re": re}
    exec("import re\n_ZENAS_PATH = re.compile(\n" + m.group(1) + ")", ns)
    pat = ns["_ZENAS_PATH"]
    for path in ("moot/fixtures", "moot/matches/ai-ai", "moot/matches/student",
                 "moot/problems/fixture-sa-lease-appeal/edit",
                 "moot/problems/fixture-sa-lease-appeal/lock",
                 "moot/problems/fixture-sa-lease-appeal/counsel/appellant",
                 "moot/matches/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/publish"):
        assert pat.fullmatch(path) is None
    assert pat.fullmatch("moot/published")
