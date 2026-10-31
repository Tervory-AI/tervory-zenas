"""Dated quotes: checked unmasked, word for word, then masked for output.

[2026] ZACAC 1 (ZA) and 1 F.2d 8, 10, 11 (Boston): the model quoted a sentence
with a date or a patent number. The quote used to be masked ("[Date]",
"[Amount]") BEFORE cite-check, so a true quote could never match. Now the exact
words are checked first. A wrong date, number or name still fails.

The new behaviour is opt-in (card_from_proposal(verify_unmasked=True),
cite_check(allow_masked=True)) so approved Skala versions that load the shared
engine (rule_card 1.0.x, cite_check 1.0.x) behave exactly as before.
"""

import importlib.util
import itertools
import json
import sys
import time
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


def _card(quote, passages=PASSAGES, citation=CITE, eng=engine, verify_unmasked=True):
    proposal = {"elements": [{"id": "e", "test": "Who decides.", "citation": citation, "quote": quote}]}
    return eng.card_from_proposal(proposal, passages, question="What did the court hold?", secrets=[],
                                  verify_unmasked=verify_unmasked)


def _recheck(card, passages=PASSAGES, eng=engine):
    return eng.cite_check([eng.cite_statement(e) for e in card["elements"]], passages, allow_masked=True)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHARED = _load(ROOT / "tank" / "skills" / "zenas.shared" / "engine.py", "zenas_shared_engine_501")
SA_ID = "8001015009087"
PHONE_CITE = "[2025] ZAWCHC 12"
PHONE_TEXT = ("The notice was served at the registry. The registry may be reached on 021 555 0123 during "
              "office hours. No later notice was filed.")
PHONE_QUOTE = "The registry may be reached on 021 555 0123 during office hours."
MASK_PHONE = "[PHONE_1]"
PHONE_PASSAGES = [{"doc_id": "w1", "version_id": "v1", "citation": PHONE_CITE, "text": PHONE_TEXT,
                   "status": "effective"}]


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
    assert len(new.cases) == len(old.cases) + 6
    assert "QUOTE_RULE" not in (old.path / "run.py").read_text(encoding="utf-8")
    assert "verify_unmasked" not in (old.path / "run.py").read_text(encoding="utf-8")


def test_cite_check_1_1_0_keeps_every_1_0_2_case():
    pkgs = _packages()
    old, new = pkgs[("zenas.cite_check", "1.0.2")], pkgs[("zenas.cite_check", "1.1.0")]
    assert {skala.evidence_hash(c) for c in old.cases} <= {skala.evidence_hash(c) for c in new.cases}
    assert len(new.cases) == len(old.cases) + 6
    assert "allow_masked=True" in (new.path / "run.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rule_card_1_1_0_evals_pass_offline_with_dated_evidence():
    pkg = _packages()[("zenas.rule_card", "1.1.0")]
    result = await skala.run_evals(pkg, lambda: _context(pkg))
    assert result["passed"] == result["total"] == len(pkg.cases), [d for d in result["details"] if not d["passed"]]
    assert result["eligible"] is True, result["evidence_reason"]
    by_name = {d["name"]: d for d in result["details"]}
    assert by_name["a true dated quote is approvable"]["result"]["approvable"] is True
    assert by_name["a quote with an altered date is not approvable"]["result"]["approvable"] is False
    # Tank's zenas.pii/1.0.0 masks phone and SA ID numbers (not dates), so these exercise the fix on Tank.
    assert by_name["a true quote with a phone number is approvable on Tank"]["result"]["approvable"] is True
    assert by_name["a true quote with an SA ID number is approvable on Tank"]["result"]["approvable"] is True
    assert by_name["a quote with an altered phone number is not approvable"]["result"]["approvable"] is False


@pytest.mark.asyncio
async def test_cite_check_1_1_0_evals_pass_offline():
    pkg = _packages()[("zenas.cite_check", "1.1.0")]
    result = await skala.run_evals(pkg, lambda: _context(pkg))
    assert result["passed"] == result["total"] == len(pkg.cases), [d for d in result["details"] if not d["passed"]]
    assert result["eligible"] is True, result["evidence_reason"]


# Approved versions keep their behaviour (review point 1).
async def _run_skill(skill, version, payload, model=None):
    pkg = _packages()[(skill, version)]
    module = _load(pkg.path / "run.py", f"run_{skill}_{version}".replace(".", "_"))

    class Ctx:
        async def model(self, *args, **kwargs):
            return model

    return await module.run(payload, Ctx())


def _phone_masked_statement():
    return {"citation": PHONE_CITE, "quote": "The registry may be reached on [PHONE_1] during office hours.",
            "quote_sha": SHARED.quote_sha(PHONE_QUOTE), "quote_len": SHARED.quote_len(PHONE_QUOTE)}


@pytest.mark.asyncio
@pytest.mark.parametrize("version,approvable", [("1.0.0", False), ("1.0.1", False), ("1.1.0", True)])
async def test_rule_card_versions_on_a_true_phone_quote(version, approvable):
    model = json.dumps({"elements": [{"id": "c", "test": "Reachable.", "citation": PHONE_CITE, "quote": PHONE_QUOTE}]})
    payload = {"question": "How is the registry reached?", "passages": PHONE_PASSAGES}
    out = await _run_skill("zenas.rule_card", version, payload, model=model)
    assert out["approvable"] is approvable


@pytest.mark.asyncio
@pytest.mark.parametrize("version,passed", [("1.0.0", False), ("1.0.1", False), ("1.0.2", False), ("1.1.0", True)])
async def test_cite_check_versions_on_a_masked_quote_with_its_fingerprint(version, passed):
    out = await _run_skill("zenas.cite_check", version,
                           {"statements": [_phone_masked_statement()], "passages": PHONE_PASSAGES})
    assert out["passed"] is passed


def test_engine_defaults_are_mains_behaviour():
    # Default build masks before cite-check, adds no fingerprint, and a true phone or dated quote fails.
    for eng in (engine, SHARED):
        phone = _card(PHONE_QUOTE, PHONE_PASSAGES, PHONE_CITE, eng=eng, verify_unmasked=False)
        assert phone["cite"]["passed"] is False and "quote_sha" not in phone["elements"][0]
        assert phone["elements"][0]["quote"] == "The registry may be reached on [PHONE_1] during office hours."
        # Default cite_check ignores quote_sha.
        assert eng.cite_check([_phone_masked_statement()], PHONE_PASSAGES)["passed"] is False
    room_dated = _card(DATED, verify_unmasked=False)
    assert room_dated["cite"]["passed"] is False and "quote_sha" not in room_dated["elements"][0]
    # An undated quote passes either way.
    plain = "Only the Tribunal may order a divestiture under section 43D(2) of the Competition Act."
    assert _card(plain, verify_unmasked=False)["cite"]["passed"] is True


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


# Recheck tries every window, not the first regex match (review point 2).
@pytest.mark.parametrize("quote,judgment", [
    ("08 March 2023 was the date the Tribunal approved the merger.",
     "Background. On 08 March 2023 was the date the Tribunal approved the merger. Costs followed."),
    ("The Tribunal later approved the merger on 08 March 2023",
     "The Tribunal later approved the merger on 08 March 2023 and costs followed."),
    ("The Tribunal approved the merger on 08 March 2023.",
     "The Tribunal approved the merger on 01 May 2020 and later The Tribunal approved the merger on 08 March 2023."),
])
def test_true_quote_that_starts_ends_or_repeats_around_a_date_passes_build_and_recheck(quote, judgment):
    passages = [{"doc_id": "z", "version_id": "v", "citation": CITE, "text": judgment, "status": "effective"}]
    card = _card(quote, passages)
    assert "[Date]" in card["elements"][0]["quote"] and card["elements"][0]["quote_len"] > 0
    assert card["cite"]["passed"] is True
    assert _recheck(card, passages)["passed"] is True
    other = quote.replace("08 March 2023", "09 March 2023")
    assert _card(other, passages)["cite"]["passed"] is False


def test_recheck_without_a_length_fails_closed():
    card = _card(DATED)
    element = {k: v for k, v in card["elements"][0].items() if k != "quote_len"}
    assert _recheck({"elements": [element]})["passed"] is False


def test_build_reports_a_pass_only_when_the_stored_form_rechecks(monkeypatch):
    monkeypatch.setattr(engine, "masked_quote_in_source", lambda *args: False)
    card = _card(DATED)
    assert card["cite"]["passed"] is False and card["cite"]["results"][0]["quote_match"] is False


def test_recheck_is_bounded_on_1_mb_of_repetitive_text():
    text = ("on 1 the 2 and 3 " * 70000)[:1_000_000]
    for eng in (engine, SHARED):
        for quote, size in (("on [Date] the [Amount] and", 40), ("[PHONE_1]", 12), ("on 1 the 2 [PHONE_1]", 30)):
            started = time.monotonic()
            assert eng.masked_quote_in_source(quote, "0" * 64, size, text) is False
            assert time.monotonic() - started < 10
    # A true phone quote deep in 1 MB still builds and rechecks.
    big = ("on 1 the 2 and 3 " * 60000) + PHONE_TEXT
    passages = [{"doc_id": "w", "version_id": "v", "citation": PHONE_CITE, "text": big, "status": "effective"}]
    started = time.monotonic()
    card = _card(PHONE_QUOTE, passages, PHONE_CITE, eng=SHARED)
    assert card["cite"]["passed"] is True and _recheck(card, passages, eng=SHARED)["passed"] is True
    assert time.monotonic() - started < 10


# Every placeholder the detectors write is a mask token (review point 3).
def _detectors():
    yield _load(ROOT / "agents" / "zenas" / "core" / "pii_detect.py", "room_pii_501")
    for path in sorted((ROOT / "tank" / "skills" / "zenas.pii").glob("*/detect.py")):
        yield _load(path, f"tank_pii_{path.parent.name.replace('.', '_')}")


def test_every_detector_placeholder_is_a_mask_token():
    seen = 0
    for module in _detectors():
        labels = dict(getattr(module, "_LABELS", {}))
        stable = dict(getattr(module, "_STABLE", {}))
        for kind, label in labels.items():
            forms = [f"[{label}_1]", f"[{label}_12]"]
            if kind == "party":
                forms = ["[Party A]", "[Party Z]", "[Party 27]"]
            if kind in stable:
                forms = [stable[kind]]
            for form in forms:
                assert engine.MASK_TOKEN.fullmatch(form), form
                assert SHARED.MASK_TOKEN.fullmatch(form), form
                seen += 1
    assert seen > 10
    assert engine.MASK_TOKEN.fullmatch("[SA_ID_1]") and engine.MASK_TOKEN.fullmatch("[US_PASSPORT_1]")


@pytest.mark.parametrize("eng_name", ["room", "tank"])
def test_true_sa_id_quote_builds_and_rechecks_and_another_valid_id_fails(eng_name):
    eng = engine if eng_name == "room" else SHARED
    detect = eng.load_detect()
    other = None
    for tail in itertools.product("0123456789", repeat=3):
        candidate = "8001015009" + "".join(tail)
        if candidate != SA_ID and detect(candidate)["matches"]:
            other = candidate
            break
    assert other
    quote = f"The applicant, identity number {SA_ID}, was not a party to the sale agreement."
    text = f"The record was corrected. {quote}"
    passages = [{"doc_id": "w", "version_id": "v", "citation": PHONE_CITE, "text": text, "status": "effective"}]
    card = _card(quote, passages, PHONE_CITE, eng=eng)
    assert "[SA_ID_1]" in card["elements"][0]["quote"] and card["cite"]["passed"] is True
    assert _recheck(card, passages, eng=eng)["passed"] is True
    assert _card(quote.replace(SA_ID, other), passages, PHONE_CITE, eng=eng)["cite"]["passed"] is False


# An oversize caller quote_len is refused before any scan (round-3 review point 2).
OVERSIZE_TEXT = ("on 1 the 2 and 3 " * 70000)[:1_000_000]


def test_oversize_quote_len_is_refused_in_under_a_second_on_1_mb():
    assert engine.MASK_TOKEN_MAX_CHARS == SHARED.MASK_TOKEN_MAX_CHARS == 256
    for eng in (engine, SHARED):
        started = time.monotonic()
        assert eng.masked_quote_in_source("[Date]", "0" * 64, 100_000, OVERSIZE_TEXT) is False
        assert time.monotonic() - started < 1


def test_oversize_quote_len_is_refused_in_under_a_second_through_cite_check_1_1_0():
    module = _load(ROOT / "tank" / "skills" / "zenas.cite_check" / "1.1.0" / "run.py", "cite_check_110_oversize")
    payload = {
        "statements": [{"citation": PHONE_CITE, "quote": "[Date]", "quote_sha": "0" * 64, "quote_len": 100_000}],
        "passages": [{"doc_id": "w", "version_id": "v", "citation": PHONE_CITE, "text": OVERSIZE_TEXT,
                      "status": "effective"}],
    }
    import asyncio

    started = time.monotonic()
    result = asyncio.run(module.run(payload, None))
    assert time.monotonic() - started < 1
    assert result["passed"] is False and result["blocked"] is True


@pytest.mark.parametrize("eng_name", ["room", "tank"])
def test_quote_len_bound_is_literal_words_plus_256_per_placeholder(eng_name):
    # Tank's zenas.pii/1.0.0 masks phone numbers, not dates, so the phone quote runs on both.
    eng = engine if eng_name == "room" else SHARED
    card = _card(PHONE_QUOTE, PHONE_PASSAGES, PHONE_CITE, eng=eng)
    element = card["elements"][0]
    shown, sha, size = element["quote"], element["quote_sha"], element["quote_len"]
    assert MASK_PHONE in shown and size == len(PHONE_QUOTE)
    assert eng.masked_quote_in_source(shown, sha, size, PHONE_TEXT) is True
    tokens = eng.MASK_TOKEN.findall(shown)
    literal = len(shown) - sum(len(token) for token in tokens)
    bound = literal + eng.MASK_TOKEN_MAX_CHARS * len(tokens)
    # Lengths up to the bound are still scanned (and fail only on the fingerprint) ...
    assert eng.masked_quote_in_source(shown, sha, bound, PHONE_TEXT + " x" * 200) is False
    # ... and anything past it is refused before any scan.
    calls = []
    real = eng._quote_starts
    try:
        eng._quote_starts = lambda *a: calls.append(a) or real(*a)
        assert eng.masked_quote_in_source(shown, sha, bound + 1, PHONE_TEXT) is False
        assert calls == []
        assert eng.masked_quote_in_source(shown, sha, bound, PHONE_TEXT) is False
        assert calls
    finally:
        eng._quote_starts = real


def test_cite_check_1_1_0_has_a_must_refuse_case_for_an_oversize_quote_len():
    path = ROOT / "tank" / "skills" / "zenas.cite_check" / "1.1.0" / "evals" / "cases.jsonl"
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    oversize = [c for c in cases if any(int(s.get("quote_len") or 0) > 256 * 10
                                        for s in c["input"]["statements"])]
    assert oversize and all(c["must_refuse"] is True and c["expected"] == {"passed": False, "blocked": True}
                            for c in oversize)
