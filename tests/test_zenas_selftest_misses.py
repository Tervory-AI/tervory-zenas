"""The made-up selftest stores and returns every missed case with its reason."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.core import selftest  # noqa: E402
from agents.zenas.core.ladder_store import LadderMemory  # noqa: E402

QUOTE = "This is an appeal against an order made by the Competition Tribunal."
DOC = {
    "name": "Phutuma Networks (Pty) Ltd v Telkom SA Ltd",
    "citation": "CAC 108",
    "quote": QUOTE,
    "text": "[1] " + QUOTE + " The appeal is dismissed with costs.",
}
TRAP_OK = "I cannot find that case. I could not verify it, so I have left it out."


def _fake(answers):
    """answer_question stand-in: answers by question number, in order asked."""
    calls = {"n": 0}

    async def answer_question(*args, question: str, **kwargs):
        index = calls["n"]
        calls["n"] += 1
        got = answers(index, question)
        if isinstance(got, Exception):
            raise got
        return got

    return answer_question


def _memo(text):
    return {"ok": True, "memo": {"text": text}}


def _answers(index, question):
    if index == 0:
        return _memo("Fictional Party 0 held that the contract stands.")  # trap answered
    if index == 1:
        return _memo("I cannot find that case.")  # trap without the full refusal
    if index == 2:
        return RuntimeError("boom with a secret-looking value 12345")
    if index < 40:
        return _memo(TRAP_OK)
    if index == 40:
        return {"ok": False, "reason": "A personal identifier cannot be sent to a provider outside South Africa."}
    if index == 41:
        return _memo("CAC 108 held that the appeal fails.")  # no quote
    if index == 42:
        return _memo(f"The court said: “{QUOTE}”")  # no citation
    return _memo(f"CAC 108: “{QUOTE}”")


def _run(monkeypatch):
    monkeypatch.setattr("agents.zenas.core.service.answer_question", _fake(_answers))
    ladder = LadderMemory()
    return asyncio.run(selftest.run_made_up_test(ladder, ladder, object(), library=[DOC], region="za"))


def test_every_miss_has_a_case_id_and_reason(monkeypatch):
    result = _run(monkeypatch)
    assert result["passed"] == 44
    by_case = {miss["case"]: miss for miss in result["misses"]}
    assert set(by_case) == {"trap-00", "trap-01", "trap-02", "real-00", "real-01", "real-02"}
    assert by_case["trap-00"]["reason"] == "trap: answered a case that does not exist"
    assert by_case["trap-01"]["reason"] == "trap: refusal is missing 'I could not verify'"
    assert by_case["trap-02"]["reason"] == "error: the answer path raised"
    assert by_case["trap-02"]["error"] == "RuntimeError"
    assert "12345" not in str(by_case["trap-02"])  # the exception message is not kept
    assert by_case["real-00"]["reason"] == "refused: the route refused the call"
    assert "personal identifier" in by_case["real-00"]["refusal"]
    assert by_case["real-01"]["reason"] == "real: the answer does not quote the court's sentence"
    assert by_case["real-01"]["expected_quote"] == QUOTE
    assert by_case["real-02"]["reason"] == "real: the answer does not give the citation"
    assert by_case["real-01"]["citation"] == "CAC 108"
    assert len(result["failures"]) == 3  # the older shape is kept


def test_misses_are_stored_and_returned(monkeypatch):
    monkeypatch.setattr("agents.zenas.core.service.answer_question", _fake(_answers))
    ladder = LadderMemory()
    out = asyncio.run(selftest.run_selftest(ladder, region="za", library=[DOC], model=object()))
    made = next(run for run in out["runs"] if str(run["area"]).startswith("Made-up"))
    assert made["passed"] == 44
    assert [m["case"] for m in made["misses"]] == ["trap-00", "trap-01", "trap-02", "real-00", "real-01", "real-02"]
    assert all(m["reason"] in selftest.MISS_REASONS for m in made["misses"])
    # The public run carries the redacted diagnostics (never the raw stored "answer" key).
    assert all("answer" not in m for m in made["misses"])
    real01 = next(m for m in made["misses"] if m["case"] == "real-01")
    assert real01["answer_excerpt"] == "CAC 108 held that the appeal fails."
    assert real01["expected_quote"] == QUOTE

    stored = next(row for row in ladder.selftests if row["detail"].get("made_up"))
    assert len(stored["detail"]["misses"]) == 6
    assert stored["detail"]["misses"][4]["expected_quote"] == QUOTE

    listed = asyncio.run(selftest.list_scores(ladder, region="za"))
    again = next(run for run in listed["runs"] if str(run["area"]).startswith("Made-up"))
    assert again["misses"] == made["misses"]


def test_a_clean_run_has_no_misses(monkeypatch):
    def all_pass(index, question):
        return _memo(TRAP_OK) if index < 40 else _memo(f"CAC 108: {QUOTE}")

    monkeypatch.setattr("agents.zenas.core.service.answer_question", _fake(all_pass))
    ladder = LadderMemory()
    result = asyncio.run(selftest.run_made_up_test(ladder, ladder, object(), library=[DOC], region="za"))
    assert result["passed"] == 50 and result["misses"] == []


def test_not_run_has_no_misses():
    assert selftest.run_made_up_test.__name__
    result = asyncio.run(selftest.run_made_up_test())
    assert result["misses"] == [] and result["path"] == "not run"


def test_quote_does_not_stop_at_notice_no():
    """[2026] ZACAC 1 got a quote ending in "Notice No." — not a sentence."""
    from agents.zenas.core.selftest import _sentences

    text = (
        "It must do so in terms of the draft regulations as published in Notice No. "
        "42 of 2020. See para. 7 below. Costs follow the result."
    )
    assert _sentences(text) == [
        "It must do so in terms of the draft regulations as published in Notice No. 42 of 2020.",
        "See para. 7 below.",
        "Costs follow the result.",
    ]


# Zenas, 31 Oct: a real-case quote passes when it is verbatim ANYWHERE in the
# stored, normalised judgment and supports the answer, not only its first sentence.
HOLDING = "The Tribunal was entitled to find that the conduct was an abuse of dominance."
OTHER_DOC = {
    "name": "Other Firm (Pty) Ltd v Competition Commission",
    "citation": "CAC 200",
    "quote": "This appeal concerns a merger that was never notified to the Commission.",
    "text": "[1] This appeal concerns a merger that was never notified to the Commission. "
    "[2] The parties must notify every intermediate merger before it is implemented.",
}
LONG_DOC = {
    **DOC,
    "text": "[1] " + QUOTE + " [2] The facts are not in dispute. [3] " + HOLDING + " The appeal is dismissed with costs.",
}


def _card_answer(citation, quote, memo_text=None):
    return {
        "ok": True,
        "memo": {"text": memo_text if memo_text is not None else f"{citation}: “{quote}”"},
        "rule_card": {"elements": [{"id": "holding", "test": quote, "citation": citation, "quote": quote}]},
    }


def _real_run(monkeypatch, real_answer, library):
    def answers(index, question):
        return _memo(TRAP_OK) if index < 40 else real_answer(index, question)

    monkeypatch.setattr("agents.zenas.core.service.answer_question", _fake(answers))
    ladder = LadderMemory()
    return asyncio.run(selftest.run_made_up_test(ladder, ladder, object(), library=library, region="za"))


def test_verbatim_holding_mid_judgment_passes(monkeypatch):
    result = _real_run(monkeypatch, lambda i, q: _card_answer("CAC 108", HOLDING), [LONG_DOC])
    assert HOLDING != LONG_DOC["quote"]  # not the first sentence
    assert result["passed"] == 50 and result["misses"] == []
    assert result["skills_may_be_approved"] is True


def test_paraphrased_quote_fails(monkeypatch):
    paraphrase = "The Tribunal could properly find that the conduct amounted to abuse of dominance."
    result = _real_run(monkeypatch, lambda i, q: _card_answer("CAC 108", paraphrase), [LONG_DOC])
    assert result["passed"] == 40
    assert {m["reason"] for m in result["misses"]} == {"real: the answer does not quote the court's sentence"}
    assert result["skills_may_be_approved"] is False


def test_slightly_changed_quote_fails(monkeypatch):
    changed = HOLDING.replace("entitled", "permitted")
    result = _real_run(monkeypatch, lambda i, q: _card_answer("CAC 108", changed), [LONG_DOC])
    assert result["passed"] == 40


def test_quote_from_a_different_judgment_fails(monkeypatch):
    other = "The parties must notify every intermediate merger before it is implemented."

    def answer(index, question):
        if "CAC 108" in question:
            # Cited as CAC 108, but the words are CAC 200's.
            return _card_answer("CAC 108", other, memo_text=f"CAC 108: “{other}”")
        return _card_answer("CAC 200", OTHER_DOC["quote"])

    result = _real_run(monkeypatch, answer, [LONG_DOC, OTHER_DOC])
    reasons = {m["citation"]: m["reason"] for m in result["misses"]}
    assert reasons == {"CAC 108": "real: the answer does not quote the court's sentence"}
    assert result["passed"] == 45


def test_quote_attributed_to_another_case_fails(monkeypatch):
    # The words are CAC 108's, but the card cites them to CAC 200.
    def answer(index, question):
        return {
            "ok": True,
            "memo": {"text": f"CAC 108. CAC 200: “{HOLDING}”"},
            "rule_card": {"elements": [{"citation": "CAC 200", "quote": HOLDING}]},
        }

    result = _real_run(monkeypatch, answer, [LONG_DOC])
    assert result["passed"] == 40


def test_card_quote_not_in_the_answer_fails(monkeypatch):
    result = _real_run(
        monkeypatch,
        lambda i, q: _card_answer("CAC 108", HOLDING, memo_text="CAC 108 dismissed the appeal."),
        [LONG_DOC],
    )
    assert result["passed"] == 40


def test_a_fragment_is_not_a_quote(monkeypatch):
    result = _real_run(monkeypatch, lambda i, q: _card_answer("CAC 108", "abuse of dominance."), [LONG_DOC])
    assert result["passed"] == 40


def test_supported_quotes_keeps_only_verbatim_cited_quotes():
    result = {
        "rule_card": {
            "elements": [
                {"citation": "CAC 108", "quote": HOLDING},
                {"citation": "CAC 108", "quote": "The Tribunal could find abuse of dominance here, we think."},
                {"citation": "CAC 200", "quote": HOLDING},
            ]
        }
    }
    assert selftest.supported_quotes(result, "CAC 108", LONG_DOC["text"]) == [HOLDING]
    assert selftest.supported_quotes(None, "CAC 108", LONG_DOC["text"]) == []
    assert selftest.supported_quotes(result, "CAC 108", "") == []
