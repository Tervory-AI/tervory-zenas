import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.core import convert
from agents.zenas.core.convert import convert_cases, parse_worked
from agents.zenas.core.engine import fold_for_quote
from agents.zenas.core.ladder_store import LadderMemory
from agents.zenas.core.pointers import PointerError, _fold_with_offsets, judgment_text, resolve_answer, resolve_pointer
from test_zenas_z8c import Answer, Doc, Pool


FACTS = (
    "The supplier delivered the goods to the buyer under their written agreement. "
    "The buyer accepted delivery and retained the goods without raising any objection. "
    "Payment remained outstanding despite repeated written demands from the supplier."
)
HOLDING = "The buyer must pay the agreed price for the goods."
FIXTURES = Path(__file__).parent / "fixtures/zenas-pointers"


@pytest.mark.asyncio
async def test_pointers_extract_stored_passages_and_report_unresolved():
    text = f"[1] {FACTS}[15]\nFootnote 15: An interleaved reference.\n\n[2] {HOLDING}"
    pool = Pool(Doc(text, doc_id="civil", citation="Civil 1", title="Supplier v Buyer", source_id="saflii"))
    for pointer, reason in (({"paragraphs": [1]}, ""), ({"paragraphs": [99]}, "pointer did not resolve"), ({"paragraphs": [2]}, "facts too short")):
        body = {
            "facts_pointer": pointer,
            "question": "Was payment due?",
            "holding": "Payment was due.",
            "elements": [{"element_id": "payment", "finding": "met", "quote_pointer": {"paragraphs": [2]}}],
        }
        ladder = LadderMemory()
        result = await convert_cases(pool, ladder, Answer(json.dumps(body)), region="za")
        if reason:
            assert result["failures"] == [{"reason": reason, "count": 1}]
        else:
            assert result["converted"] == 1
            assert "interleaved" not in str(ladder.worked)
            assert FACTS in str(ladder.worked)


def test_word_boundaries_ambiguity_and_reversed_pointers():
    words = FACTS.split()
    pointer = {"first": " ".join(words[:8]), "last": " ".join(words[-8:])}
    assert resolve_pointer(pointer, FACTS) == FACTS
    for source in (FACTS + FACTS, " ".join(words[-8:] + words[:8])):
        with pytest.raises(PointerError, match="pointer did not resolve"):
            resolve_pointer(pointer, source)
    with pytest.raises(PointerError):
        resolve_pointer({"paragraphs": [True]}, "[1] text")
    unmarked = "[1] Facts.\nFootnote 1: Reference.\n\n[2] Holding."
    assert judgment_text(unmarked) == unmarked


def test_interleaved_numbered_footnote_is_not_part_of_facts():
    text = (
        "[24] The supplier delivered the goods.[15]\n"
        "15 A reference to the delivery record.\n"
        "A continuation of that footnote.\n\n"
        "The buyer accepted them.\n\n"
        f"[25] {HOLDING}"
    )
    cleaned = judgment_text(text)
    facts = resolve_pointer({"paragraphs": [24]}, cleaned)
    assert "delivery record" not in facts
    assert "A continuation of that footnote." in facts
    assert "delivered the goods." in facts and "buyer accepted" in facts
    assert resolve_pointer({"paragraphs": [25]}, cleaned) == HOLDING


@pytest.mark.parametrize("filename", ["de-vasconcelos.txt", "wolmarans.txt"])
def test_real_judgments_and_dot_numbered_paragraphs_are_not_footnotes(filename):
    original = (FIXTURES / filename).read_text()
    assert judgment_text(original) == original
    dot_numbered = original.replace("[1]", "1.").replace("[6]", "6.").replace("[15]", "15.")
    marked = FACTS + "[1][6][15]\n" + dot_numbered
    assert judgment_text(marked) == marked


def test_only_later_matching_footnote_blocks_are_deleted():
    original = (FIXTURES / "wolmarans.txt").read_text()
    before = "17 An earlier reference must survive.\n\n"
    after = "\n17 A later reference can be removed.\nWrapped reference text.\n\n"
    text = before + original.rstrip() + "[17]\n" + after + "18 An unmarked reference must survive.\n"
    cleaned = judgment_text(text)
    assert cleaned.startswith(before)
    assert original.rstrip() in cleaned
    assert "later reference" not in cleaned
    assert "Wrapped reference text." in cleaned
    assert "unmarked reference" in cleaned


@pytest.mark.parametrize("continuation", ["2013 to 23 August 2013 between Tufh and Wolmarans.", "November 2019 Wolmarans’ municipal account", "15. The last payment to the CoJ"])
def test_footnote_blocks_stop_before_wrapped_dates_and_numbered_paragraphs(continuation):
    text = FACTS + "[15]\n15 A reference that can be removed.\n" + continuation
    assert judgment_text(text).endswith(continuation)
    assert "reference that" not in judgment_text(text)


def test_an_adjacent_unmarked_note_is_not_deleted_with_a_marked_note():
    text = FACTS + "[15]\n15 A marked reference.\n16 An unmarked reference.\nIts continuation.\n"
    cleaned = judgment_text(text)
    assert "15 A marked reference" not in cleaned
    assert cleaned.endswith("16 An unmarked reference.\nIts continuation.\n")


@pytest.mark.parametrize("mark", ["[2019] ZASCA", "[999] ZACC", "[12] ZAGPJHC", "[1000]", "[2019]\nZASCA"])
def test_citation_years_and_court_codes_are_not_footnote_marks(mark):
    number = mark.split("]")[0][1:]
    text = f"{FACTS} {mark} 81.\n{number} A wrapped date or citation continuation must survive.\n"
    assert judgment_text(text) == text


def test_word_anchors_return_original_punctuation_hyphens_and_wrapping():
    text = (FIXTURES / "wolmarans.txt").read_text()
    passage = text.split("[15] ", 1)[1].strip()
    words = fold_for_quote(passage).split()
    pointer = {"first": " ".join(words[:8]), "last": " ".join(words[-8:])}
    assert resolve_pointer(pointer, text) == passage
    assert "30\nOctober 2015" in passage and "Wolmarans’" in passage
    variant = passage.replace("municipal services", "munici-\npal services")
    assert resolve_pointer(pointer, variant) == variant
    parsed = parse_worked(json.dumps({"facts_pointer": pointer, "holding": HOLDING}), text)
    assert parsed["facts"] == passage


def test_word_anchors_preserve_the_2019_citation_removed_by_folding():
    text = (FIXTURES / "de-vasconcelos.txt").read_text().strip()
    words = fold_for_quote(text).split()
    resolved = resolve_pointer({"first": " ".join(words[:8]), "last": " ".join(words[-8:])}, text)
    assert resolved == text
    assert "[2019] ZASCA" in resolved and "Trust’s" in resolved


@pytest.mark.parametrize("text", [
    "‘Court’s’ non-\ncompliance\u00a0is recorded¹ in [12] the judgment.",
    "1. First line\n\n2. Second line with re-entry* and \u00adsoft hyphens.",
    (FIXTURES / "wolmarans.txt").read_text(),
])
def test_offset_fold_matches_cite_check_fold(text):
    folded, offsets = _fold_with_offsets(text)
    assert folded == fold_for_quote(text)
    assert len(folded) == len(offsets)
    assert all(0 <= start < end <= len(text) for start, end in offsets)


@pytest.mark.parametrize("paragraphs,text", [
    ([1, 1], f"[1] {FACTS}"),
    ([2, 1], f"[1] {FACTS}\n[2] {FACTS}"),
    ([1, 2], f"[2] {FACTS}\n[1] {FACTS}"),
    ([1], f"[1] {FACTS}\n[1] {FACTS}"),
    ([1], "[1]\n[2] " + FACTS),
    ([1], "[1] One two three four five six seven."),
])
def test_bad_paragraph_pointers_fail(paragraphs, text):
    with pytest.raises(PointerError, match="pointer did not resolve"):
        resolve_pointer({"paragraphs": paragraphs}, text)


def test_anchor_span_keeps_the_whole_passage():
    first = "One two three four five six seven eight"
    last = "Nine ten eleven twelve thirteen fourteen fifteen sixteen"
    pointer = {"first": first, "last": last}
    for filler in (" middle" * 500, " " * 3001):
        text = first + filler + " " + last
        got = resolve_pointer(pointer, text)
        assert got == text
        assert len(got) > 3000
    with pytest.raises(PointerError):
        resolve_pointer(pointer, "")


def test_facts_pointer_drops_the_model_narrative():
    result = resolve_answer({"facts_pointer": {"paragraphs": [1]}, "narrative": "invented"}, "[1] " + FACTS)
    assert "narrative" not in result
    assert result["facts"] == FACTS


@pytest.mark.asyncio
async def test_legacy_answers_cite_check_raw_judgment(monkeypatch):
    real = (FIXTURES / "de-vasconcelos.txt").read_text()
    text = real.rstrip() + "[15]\n15 A reference copied in the legacy answer must remain available.\n\n"
    assert judgment_text(text) != text
    body = {"facts": text, "holding": HOLDING}
    seen = []
    original = convert.cite_check

    def check(statements, passages):
        seen.append(passages[0]["text"])
        return original(statements, passages)

    monkeypatch.setattr(convert, "cite_check", check)
    pool = Pool(Doc(text, doc_id="civil", citation="Civil 1", title="Supplier v Buyer", source_id="saflii"))
    result = await convert_cases(pool, LadderMemory(), Answer(json.dumps(body)), region="za")
    assert result["converted"] == 1
    assert seen == [fold_for_quote(text)]


@pytest.mark.asyncio
async def test_pointer_conversion_stores_exact_court_text_not_narrative():
    text = (FIXTURES / "wolmarans.txt").read_text()
    passage = text.split("[15] ", 1)[1].strip()
    words = fold_for_quote(passage).split()
    body = {
        "facts_pointer": {"first": " ".join(words[:8]), "last": " ".join(words[-8:])},
        "narrative": "The model invented this instead.",
        "holding": HOLDING,
    }
    pool = Pool(Doc(text, doc_id="civil", citation="Civil 1", title="Wolmarans v Tufh", source_id="saflii"))
    ladder = LadderMemory()
    result = await convert_cases(pool, ladder, Answer(json.dumps(body)), region="za")
    assert result["converted"] == 1
    worked = await ladder.worked_for_doc(region="za", pool_doc_id="civil")
    assert worked["fact_pattern"]["facts"] == passage
    assert worked["fact_pattern"]["narrative"] == passage


@pytest.mark.parametrize("line", ["in favour of Wolmarans in order to assist it with the purchase and refurbishment of", "2013 to 23 August 2013 between Tufh and Wolmarans.", "November 2019 Wolmarans’ municipal account in respect of rates, water,"])
def test_marked_note_does_not_consume_real_wrapped_judgment_lines(line):
    real = (FIXTURES / "wolmarans.txt").read_text()
    assert line in real
    text = real.replace(line, "Footnote 17: Reference only.\n" + line, 1)
    text = "A preceding reference mark[17]\n\n" + text
    # Put the note at a block boundary; its unlabelled continuation remains court text.
    text = text.replace("Footnote 17:", "\nFootnote 17:")
    cleaned = judgment_text(text)
    assert "Reference only." not in cleaned
    assert line in cleaned


def test_numbered_wrapped_line_is_not_a_late_footnote():
    text = "A reference[12]\n\n[15] The court allowed\n12 days for payment before execution could proceed.\n"
    assert judgment_text(text) == text


def test_anchor_slice_keeps_intermediate_paragraph_numbers():
    text = (FIXTURES / "wolmarans.txt").read_text().strip()
    words = fold_for_quote(text).split()
    result = resolve_pointer({"first": " ".join(words[:8]), "last": " ".join(words[-8:])}, text)
    assert result == text[len("[6] "):]
    assert "[15]" in result


@pytest.mark.parametrize("numbers", [(6, 6), (15, 6)])
def test_real_judgment_repeated_or_backwards_numbering_refuses_both_pointer_forms(numbers):
    text = (FIXTURES / "wolmarans.txt").read_text().replace("[6]", f"[{numbers[0]}]").replace("[15]", f"[{numbers[1]}]")
    words = fold_for_quote(text).split()
    for pointer in ({"paragraphs": [6]}, {"first": " ".join(words[:8]), "last": " ".join(words[-8:])}):
        with pytest.raises(PointerError, match="pointer did not resolve"):
            resolve_pointer(pointer, text)


@pytest.mark.parametrize("text", [
    "...section 12[4] of the Act, read with section\n4 of the Regulations, the notice was valid.\n",
    "evicted.[3]\n\n3 months later the occupier returned.\n",
])
def test_lowercase_numbered_continuations_are_not_footnote_headers(text):
    assert judgment_text(text) == text


def test_capitalized_reference_after_inline_mark_is_a_footnote():
    text = "...Wolmarans.[3] He argued\n3 Wolmarans v Gaul 2019 (1) SA 1 (SCA).\n"
    assert judgment_text(text) == "...Wolmarans.[3] He argued\n"
