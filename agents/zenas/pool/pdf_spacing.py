"""Court PDF text with word spacing that matches the page.

Root cause of broken words such as "m ade" and "21 st": Word justifies a line
with TJ kerning numbers and often writes one line as several text objects,
sometimes in the middle of a word. pypdf decides where a space goes from the
glyph widths alone. It leaves the kerning out, so the justification looks like
a gap and pypdf writes a space inside the word.

PDFium places every glyph with the kerning applied. This module keeps pypdf's
lines and paragraphs, because the pool splits paragraphs on blank lines, and
removes a single pypdf space only where PDFium shows the two characters touching.
A space is never added. A space next to a digit stays (footnote markers such as
"occurred. 8 It" must not merge), except an ordinal suffix ("21 st" -> "21st").
A hyphen at a line end joins the two halves. It is dropped only when the same
document writes the joined word without a hyphen and never with one.
"""

from __future__ import annotations

import difflib
import io
import logging
import re

log = logging.getLogger(__name__)

# PDFium writes U+0002 or U+FFFE for a hyphen it found at a line end.
_AS_HYPHEN = str.maketrans({"\x02": "-", "\ufffe": "-", "\u00ad": "-"})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ORDINAL = re.compile(r"(st|nd|rd|th)(?![A-Za-z])")
_OPENERS = set("([{‘“/")
_LINE_HYPHEN = re.compile(r"([^\W\d_]{2,})(-|\u00ad)[ \t]*\n[ \t]*([^\W\d_]+)")
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
_NOT_A_WORD_END = {"and", "or", "nor", "to", "the", "of"}


def _joinable(plain: str, left_at: int, right_at: int) -> bool:
    """May the whitespace between these two characters go, when PDFium shows no gap?"""
    left, right = plain[left_at], plain[right_at]
    if right.isdigit():
        # "patients 1", "Act. 7", "Kate, 4": a footnote marker. Keep the space.
        return left in _OPENERS or left == "-"
    if left.isdigit() and right.isalpha():
        return bool(_ORDINAL.match(plain, right_at))
    if right == "-":
        return left.isalnum()
    if left.isalnum() or left in _OPENERS:
        return True
    # "unfair. ’ The" -> "unfair.’"; "dipping’ ," -> "dipping’,".
    return not right.isalnum()


def respace(plain: str, geometry: str) -> str:
    """Drop the spaces in ``plain`` that PDFium's text for the same page does not have."""
    if not plain or not geometry:
        return plain
    marks = [(i, c) for i, c in enumerate(plain) if not c.isspace()]
    chars: list[str] = []
    gap: list[bool] = []
    spaced = False
    for c in geometry.translate(_AS_HYPHEN):
        if c.isspace():
            spaced = True
            continue
        chars.append(c)
        gap.append(spaced)
        spaced = False
    a = "".join(c for _, c in marks)
    g = "".join(chars)
    if a == g:
        blocks = [(0, 0, len(a))]
    else:
        matcher = difflib.SequenceMatcher(None, a, g, autojunk=False)
        blocks = [(m.a, m.b, m.size) for m in matcher.get_matching_blocks() if m.size]
    cut: list[tuple[int, int]] = []
    for a0, g0, size in blocks:
        for k in range(1, size):
            left_at = marks[a0 + k - 1][0]
            right_at = marks[a0 + k][0]
            between = plain[left_at + 1 : right_at]
            if between != " " or gap[g0 + k]:
                continue
            if _joinable(plain, left_at, right_at):
                cut.append((left_at + 1, right_at))
    if not cut:
        return plain
    out: list[str] = []
    last = 0
    for start, end in cut:
        out.append(plain[last:start])
        last = end
    out.append(plain[last:])
    return "".join(out)


def join_line_hyphens(text: str) -> str:
    """Join a word split by a hyphen at a line end. Keep the hyphen unless the document says not to."""
    if "\n" not in text:
        return text
    lower = {w.lower() for w in _WORD.findall(text.replace("\u00ad", ""))}

    def fix(m: re.Match[str]) -> str:
        head, mark, tail = m.group(1), m.group(2), m.group(3)
        if tail.lower() in _NOT_A_WORD_END:
            return m.group(0)  # "pre-\nand post-merger": a suspended hyphen, not a split word
        if mark == "\u00ad":
            return head + tail
        joined = head + tail
        hyphened = f"{head}-{tail}"
        if tail[0].islower() and joined.lower() in lower and hyphened.lower() not in lower:
            return joined
        return hyphened

    return _LINE_HYPHEN.sub(fix, text)


def _pdfium_pages(blob: bytes) -> list[str] | None:
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover - the image installs it
        return None
    pages: list[str] = []
    try:
        doc = pdfium.PdfDocument(blob)
        try:
            for index in range(len(doc)):
                page = doc[index]
                textpage = page.get_textpage()
                try:
                    pages.append(textpage.get_text_range() or "")
                finally:
                    textpage.close()
                    page.close()
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 - a broken PDF falls back to pypdf spacing
        log.info("pdfium could not read a PDF (%s); pypdf spacing kept", type(exc).__name__)
        return None
    return pages


def _pypdf_pages(blob: bytes) -> list[str] | None:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(blob))
        return [(page.extract_text() or "") for page in reader.pages]
    except (PdfReadError, OSError, ValueError, KeyError, TypeError):
        return None


def tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufffe", "-")
    return _CONTROL.sub("", text)


def pdf_text(blob: bytes) -> str:
    """The whole PDF as text. A file that is not a PDF yields nothing."""
    if not blob.startswith(b"%PDF"):
        return ""
    plain = _pypdf_pages(blob)
    if plain is None:
        return ""
    geometry = _pdfium_pages(blob)
    if geometry is not None and len(geometry) != len(plain):
        geometry = None
    parts: list[str] = []
    for index, page in enumerate(plain):
        if not page:
            continue
        if geometry is not None:
            page = respace(page, tidy(geometry[index]))
        parts.append(tidy(page))
    return join_line_hyphens("\n".join(parts).strip())
