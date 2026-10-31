"""ZA court PDF text: no space inside a word, no real words merged.

Fixtures in tests/fixtures/za/pdf_spacing are real lines from CAC judgments
(cac.org.za, public record), read once with pypdf 6.21.2 and PDFium.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.pool.adapters.za_case_common import pdf_text  # noqa: E402
from agents.zenas.pool.pdf_spacing import join_line_hyphens, respace  # noqa: E402

SNIPPETS = json.loads((ROOT / "tests" / "fixtures" / "za" / "pdf_spacing" / "cac_snippets.json").read_text("utf-8"))


def _snippet(needle: str) -> dict:
    return next(s for s in SNIPPETS if s["needle"] == needle)


def _fold(text: str) -> str:
    return " ".join(text.split())


def _word_pdf() -> bytes:
    """One justified line written the way Word writes it.

    "It is m" is one text object stretched with TJ kerning; "ade by the Tribunal"
    starts a second object exactly where the first one ends.
    """
    # Helvetica widths: I t space i s space m = 278+278+278+222+500+278+833 = 2667.
    # Four -300 kerns add 4 * 0.3 * 12 = 14.4 points: the line ends at 72 + 32.004 + 14.4.
    stream = (
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm [(It)-300( )-300(is)-300( )-300(m)] TJ ET\n"
        b"BT /F1 12 Tf 1 0 0 1 118.404 700 Tm (ade by the Tribunal.) Tj ET\n"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_root_cause_pypdf_alone_splits_a_justified_word():
    from pypdf import PdfReader

    plain = PdfReader(io.BytesIO(_word_pdf())).pages[0].extract_text()
    assert "m ade" in plain  # the bug: kerning is read as a gap


def test_pdf_text_keeps_the_word_whole():
    text = pdf_text(_word_pdf())
    assert "It is made by the Tribunal." in _fold(text)


def test_cac_108_opening_sentence_is_whole():
    snip = _snippet("m ade")
    fixed = _fold(respace(snip["pypdf"], snip["pdfium"]))
    assert "This is an appeal against an order made by the Competition Tribunal (‘the tribunal’)." in fixed
    assert "On the 21st January 2010" in fixed
    assert "m ade" not in fixed and "21 st" not in fixed


def test_hyphen_compound_spaces_go():
    fixed = _fold(respace(_snippet("anti -competitive")["pypdf"], _snippet("anti -competitive")["pdfium"]))
    assert "the anti-competitive conduct" in fixed
    assert "and/or that Mediclinic" in fixed
    sgb = _fold(respace(_snippet("SGB -Cape")["pypdf"], _snippet("SGB -Cape")["pdfium"]))
    assert "Superfecta SGB-Cape Joint Venture" in sgb
    assert "JV’s are" in sgb


def test_footnote_markers_do_not_merge_into_words():
    snip = _snippet("patients 1")
    fixed = _fold(respace(snip["pypdf"], snip["pdfium"]))
    assert "in-hospital patients" in fixed
    # PDFium glues the superscript to the word; the stored text must not.
    assert "patients 1 and crash landers. 2 Both" in fixed


def test_a_wide_gap_is_never_closed():
    snip = _snippet("The  definition")
    fixed = respace(snip["pypdf"], snip["pdfium"])
    assert "Thedefinition" not in fixed
    assert "The  definition" in fixed


def test_respace_never_adds_or_moves_characters():
    for snip in SNIPPETS:
        fixed = respace(snip["pypdf"], snip["pdfium"])
        assert "".join(fixed.split()) == "".join(snip["pypdf"].split())
        assert fixed.count("\n") == snip["pypdf"].count("\n")


@pytest.mark.parametrize(
    "plain, geometry, expected",
    [
        ("no t time", "not time", "not time"),  # the real gap stays; the false one goes
        ("a n order", "a n order", "a n order"),  # PDFium shows a gap: keep it
        ("the 21 st day", "the 21st day", "the 21st day"),
        ("paras 10 6", "paras 106", "paras 10 6"),  # digit then digit may be a footnote marker
        ("s 49C (2) (b)", "s 49C(2)(b)", "s 49C(2)(b)"),
        ("the Act. 7 Both", "the Act.7 Both", "the Act. 7 Both"),
    ],
)
def test_respace_rules(plain, geometry, expected):
    assert respace(plain, geometry) == expected


def test_line_end_hyphens_join():
    snip = _snippet("vis-")
    text = join_line_hyphens(respace(snip["pypdf"], snip["pdfium"]))
    assert "the argument presented vis-à-vis the reasons" in _fold(text)
    assert join_line_hyphens("the non-\nreferral of the complaint") == "the non-referral of the complaint"
    # The document writes the word whole elsewhere and never with a hyphen: drop it.
    assert join_line_hyphens("its deter-\nmination stands.\nA determination follows.") == (
        "its determination stands.\nA determination follows."
    )
    # A suspended hyphen is not a split word.
    assert join_line_hyphens("pre-\nand post-merger") == "pre-\nand post-merger"
    assert join_line_hyphens("pay the costs. -\n(ii) the") == "pay the costs. -\n(ii) the"
    assert join_line_hyphens("non\u00ad\nreferral") == "nonreferral"


def test_not_a_pdf_and_broken_pdf():
    assert pdf_text(b"not a pdf") == ""
    assert pdf_text(b"%PDF-1.4 broken") == ""
