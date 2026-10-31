"""Special Tribunal judgments from justice.gov.za and the SIU site.

justice.gov.za/tribunal/jdgm.html is the historic PDF list. Affidavits and
schedules are skipped. siu.org.za/special-tribunal-judgements/ is the newer
list: each matter page links to one PDF under wp-content/uploads. Other SIU
pages (NLC, Eskom, High Court) are not followed.

PDFs are asked for with a PDF Accept: justice.gov.za (IIS) answers HTTP 406 to
a PDF requested as text/html. www.siu.org.za leaves its TLS intermediate out;
agents.zenas.pool.http fills it in from the certificate, as a browser does.

The SIU list pages are /special-tribunal-judgements/2/, /3/, ... (not /page/N/).
Each SIU matter page states the case number and the judgment date; those are
used for the citation and date, because SIU file names rarely carry them. A PDF
with no text layer (a scan) is skipped as "no text layer": there is no OCR.

The date is the judgment's own: a "Date of judgment:" / "Date of order:" line,
the hand-down line, the SIU matter page, or a date in the file name
(13Oct2022, 01102026). A page's publication date is never used.
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape
from urllib.parse import unquote

from agents.zenas.pool.adapters.za_archive import ArchiveRun, accept_pdf, polite_get
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    own_citation,
    parse_case_blob,
)
from agents.zenas.pool.adapters.za_common import html_headers, pdf_headers
from agents.zenas.pool.model import LegalDocument, SourceSpec

JUSTICE = "https://www.justice.gov.za/tribunal/jdgm.html"
SIU = "https://www.siu.org.za/special-tribunal-judgements/"
# Special-tribunal checkpoints carry this tag. Not the shared za_archive MARKER, which would re-walk every ZA archive.
CURSOR_TAG = "st-v2"
# Fewer real words than this is a scan with a stamp ("Scanned with CamScanner"), not a text layer.
MIN_WORDS = 50
_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
_FILE = re.compile(r"ST[-_\s]?Judgment[-_\s]?([A-Za-z]+)-(\d+)-(\d{4})", re.I)
_SIU_NO = re.compile(r"\b(gp|ec|wc|kzn|kn|fs|lp|mp|nw)\s*0*(\d+)[_\-](20\d{2})", re.I)


def _keep_justice(name: str) -> bool:
    lower = name.lower()
    if any(piece in lower for piece in ("affidavit", "scha", "settlement")):
        return False
    return any(piece in lower for piece in ("judgment", "judgement", "jdm", "order"))


def justice_pdfs(page: bytes, base: str = "https://www.justice.gov.za/tribunal/") -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in _HREF.findall(text):
        if ".pdf" not in href.lower():
            continue
        url = absolute_url(base, href).split("#", 1)[0]
        if "saflii" in url.lower() or not _keep_justice(unquote(url)):
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def siu_matter_urls(page: bytes, base: str = SIU) -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in _HREF.findall(text):
        url = absolute_url(base, href).split("?", 1)[0].rstrip("/")
        path = url.lower()
        if "/judgements/" not in path:
            continue
        if not any(piece in path for piece in ("/judgment-", "/judgement-", "/order-")):
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def siu_pdf(page: bytes, base: str) -> str:
    text = page.decode("utf-8", errors="replace")
    for href in _HREF.findall(text):
        if ".pdf" not in href.lower():
            continue
        url = absolute_url(base, href).split("#", 1)[0]
        if "/wp-content/uploads/" in url.lower():
            return url
    return ""


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_FULL_MONTHS = {
    "jan": "january", "feb": "february", "mar": "march", "apr": "april", "may": "may", "jun": "june",
    "jul": "july", "aug": "august", "sep": "september", "oct": "october", "nov": "november", "dec": "december",
}  # fmt: skip
_CASE_NO = re.compile(r"\bcase\s+(?:no|number)\.?\s*:?\s*([A-Za-z]{0,3}\s*/?\s*\d+\s*/\s*\d{4})\b", re.I)
_CASE_PARTS = re.compile(r"^\s*([A-Za-z]{2,3})?\s*/?\s*0*(\d+)\s*[/_-]\s*(\d{4})\s*$")
_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+)?(?:of\s+)?([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"
)
_YMD = re.compile(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
# Strongest first. Each is a label at the start of a line. It counts only when a ':' or
# '-' follows it, or when the rest of the line is a date and nothing else: PDF text is
# hard-wrapped, so prose ("relies on the\ndate of order of the High Court, 3 March 2019")
# can start a line too. The value is the rest of the line, or (labels stacked above their
# values) the matching line below.
# "Date of hearing" / "Heard on" is never the judgment date and is not listed.
_DATE_LABELS = (
    re.compile(r"\s*date\s+of\s+(?:judg(?:e)?ment|order|delivery|hand[\s-]*down)\b", re.I),
    re.compile(r"\s*(?:judg(?:e)?ment|order)\s+(?:delivered|handed\s+down)\b(?:\s*on\b)?", re.I),
    re.compile(r"\s*date\b", re.I),
    re.compile(r"\s*date\s+held\b", re.I),
)
_ONLY_DATE = re.compile(
    r"\s*(?:(?:this|the)\s+)?(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:day\s+)?(?:of\s+)?[A-Za-z]{3,9}\.?,?\s+\d{4}"
    r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4})\s*\.?\s*",
    re.I,
)
# The hand-down sentence, anywhere on a line of the header: "... is deemed to be 13h30 on 26 August 2026."
_DEEMED = re.compile(r"\b(?:hand[\s-]*down|delivery)\s+is\s+deemed\s+to\s+be\b", re.I)
# Any label line, including the ones never used (hearing), so a stack of labels can be counted.
_ANY_LABEL = re.compile(
    r"\s*(?:date\b[^:\n]{0,30}|heard(?:\s+on)?|(?:judg(?:e)?ment|order)\s+(?:delivered|handed\s+down)(?:\s+on)?)\s*:",
    re.I,
)
_HEAD = 2000
_TAIL = 1500
_FILE_LONG = re.compile(r"(?<![0-9])(\d{1,2})([A-Za-z]{3,9})(\d{4})(?![0-9])")
# A date alone on the last line, under the judge's signature ("MEMBER OF THE SPECIAL TRIBUNAL\n15 March 2022").
_SIGNED_DATE = re.compile(r"\b(?:tribunal|judge|j)\.?\s*\n\s*((?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})|(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}))\s*$", re.I)
_FILE_DIGITS = re.compile(r"(?<![0-9])(\d{2})(\d{2})(20\d{2})(?![0-9])")


def _make(year: int, month: int, day: int) -> date | None:
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    return found if 1990 <= found.year <= date.today().year + 1 else None


def _month(name: str) -> int:
    """A month name or its usual short form; other words (Others, Decision) are not months."""
    lower = name.lower().rstrip(".")
    if lower in _MONTHS:
        return _MONTHS[lower]
    full = _FULL_MONTHS.get(lower[:3], "")
    return _MONTHS[lower[:3]] if full == lower else 0


def date_in(fragment: str) -> date | None:
    """The first real date in a short piece of text: 7 September 2022, 2020/01/21, 18/03/2022."""
    best: tuple[int, date] | None = None
    for match in _DAY_MONTH_YEAR.finditer(fragment):
        month = _month(match.group(2))
        if month and (found := _make(int(match.group(3)), month, int(match.group(1)))):
            best = (match.start(), found)
            break
    for pattern, order in ((_YMD, (1, 2, 3)), (_DMY, (3, 2, 1))):
        match = pattern.search(fragment)
        if not match:
            continue
        year, month, day = (int(match.group(i)) for i in order)
        found = _make(year, month, day)
        if found and (best is None or match.start() < best[0]):
            best = (match.start(), found)
    return best[1] if best else None


def date_from_name(url: str) -> date | None:
    """A date written into the file name: ...-13Oct2022.pdf or ...-order-01102026.pdf (day first)."""
    name = unquote(url).rsplit("/", 1)[-1]
    for match in _FILE_LONG.finditer(name):
        month = _month(match.group(2))
        if month and (found := _make(int(match.group(3)), month, int(match.group(1)))):
            return found
    for match in _FILE_DIGITS.finditer(name):
        if found := _make(int(match.group(3)), int(match.group(2)), int(match.group(1))):
            return found
    return None


def _label_value(lines: list[str], index: int, label: re.Pattern) -> str | None:
    """The value for the label on lines[index], or None when that line is not a label.

    "Date of judgment: 26 August 2026" or "Judgment delivered on 26 August 2026" give the
    rest of the line. A bare "Date of judgment:" in a stack of N empty labels takes its
    value by position, but only when the N lines after the stack are all dates; any other
    layout (a Coram line in between, a missing value) gives None rather than a guess.
    """
    match = label.match(lines[index])
    if not match:
        return None
    rest = lines[index][match.end() :].strip()
    if rest[:1] in (":", "-"):
        rest = rest[1:].strip()
    elif not (rest and _ONLY_DATE.fullmatch(rest) and date_in(rest)):
        return None  # prose that happens to start a line, or a bare label without ':'
    if rest:
        return rest
    start = index
    while start > 0 and (above := _ANY_LABEL.match(lines[start - 1])) and not lines[start - 1][above.end() :].strip():
        start -= 1
    end = index
    while end + 1 < len(lines) and (below := _ANY_LABEL.match(lines[end + 1])) and not lines[end + 1][below.end() :].strip():
        end += 1
    values = lines[end + 1 : end + 1 + (end - start + 1)]
    if len(values) != end - start + 1 or not all(date_in(value) for value in values):
        return None
    return values[index - start]


def _segments(text: str) -> list[list[str]]:
    """Header and signature block, as non-blank lines. Footnotes and reasons are not read."""
    text = text or ""
    pieces = [text] if len(text) <= _HEAD + _TAIL else [text[:_HEAD], text[-_TAIL:]]
    return [[line for line in piece.split("\n") if line.strip()] for piece in pieces]


def judgment_date(text: str, url: str = "") -> date | None:
    """The date the judgment or order was given, from labelled lines in the header or
    signature block, the hand-down line, a date alone under the signature, else the file name."""
    segments = _segments(text)
    for label in _DATE_LABELS:
        for lines in segments:
            for index, line in enumerate(lines):
                if not label.match(line):
                    continue
                value = _label_value(lines, index, label)
                found = date_in(value) if value else None
                if found:
                    return found
    for lines in segments:
        for index, line in enumerate(lines):
            match = _DEEMED.search(line)
            if match:
                tail = line[match.end() :] + (" " + lines[index + 1] if index + 1 < len(lines) else "")
                found = date_in(tail[:120])
                if found:
                    return found
    signed = _SIGNED_DATE.search((text or "")[-400:])
    if signed:
        found = date_in(signed.group(1))
        if found:
            return found
    return date_from_name(url) if url else None


def case_citation(case_no: str) -> str:
    """'GP20/2026', 'GP/10/2019', '11107/2014' -> 'Special Tribunal GP 20/2026' etc."""
    match = _CASE_PARTS.match(case_no or "")
    if not match:
        return ""
    prefix = (match.group(1) or "").upper()
    number = f"{int(match.group(2))}/{match.group(3)}"
    return f"Special Tribunal {prefix} {number}" if prefix else f"Special Tribunal {number}"


def _dated(citation: str, when: date | None) -> str:
    """'Special Tribunal GP 9/2025 (2025-04-23)': one case has several judgments (interim, final)."""
    if not citation:
        return ""
    return f"{citation} ({when.isoformat()})" if when else citation


def _case_from_name_or_text(url: str, text: str) -> str:
    name = unquote(url)
    match = _FILE.search(name)
    if match:
        return f"Special Tribunal {match.group(1).upper()} {int(match.group(2))}/{match.group(3)}"
    match = _SIU_NO.search(name)
    if match:
        return f"Special Tribunal {match.group(1).upper()} {int(match.group(2))}/{match.group(3)}"
    match = _CASE_NO.search((text or "")[:3000])
    if match:
        return case_citation(match.group(1))
    return ""


def cite(url: str, text: str, *, case_no: str = "", when: date | None = None) -> str:
    """[YYYY] ZAST N when the judgment carries one; else case number + judgment date; else the bare case number.

    The same judgment on justice.gov.za and SIU then gets one id, and two judgments
    in one case get two.
    """
    found = own_citation(text, ("ZAST",), url=url)
    if found:
        return found
    case = case_citation(case_no) or _case_from_name_or_text(url, text)
    return _dated(case, when)


def _citation(url: str, text: str) -> str:
    return cite(url, text, when=judgment_date(text, url))


def _chunks(html: str) -> list[str]:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    pieces = re.split(r"<[^>]+>", text)
    return [cleaned for piece in pieces if (cleaned := re.sub(r"\s+", " ", unescape(piece)).strip())]


def siu_matter(page: bytes, base: str) -> dict:
    """Title, case number, judgment date and PDF from one SIU matter page. Missing values are empty."""
    html = page.decode("utf-8", errors="replace")
    title = ""
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if heading:
        title = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", heading.group(1)))).strip()
    chunks = _chunks(html)
    case_no = ""
    when: date | None = None
    for index, chunk in enumerate(chunks):
        label = chunk.lower().rstrip(":").strip()
        following = chunks[index + 1 : index + 4]
        if label in {"case number", "case no"} and not case_no:
            case_no = next((item for item in following if _CASE_PARTS.match(item)), "")
        elif label == "date" and when is None:
            when = next((found for item in following if (found := date_in(item))), None)
    return {"title": title, "case_no": case_no, "date": when, "pdf": siu_pdf(page, base)}


def _listing_number(url: str) -> int:
    match = re.search(r"/(?:page/)?(\d+)/?$", url.split("?", 1)[0].rstrip("/") + "/")
    return int(match.group(1)) if match else 1


def _next_listing(page: bytes, base: str, current: str) -> str:
    """The next SIU listing page: /special-tribunal-judgements/2/ (or a /page/2/ form)."""
    text = page.decode("utf-8", errors="replace")
    current_page = _listing_number(current)
    best: tuple[int, str] | None = None
    for href in _HREF.findall(text):
        url = absolute_url(base, unescape(href)).split("#", 1)[0]
        match = re.search(r"/special-tribunal-judgements/(?:page/)?(\d+)/?$", url.split("?", 1)[0])
        if not match:
            continue
        number = int(match.group(1))
        if number > current_page and (best is None or number < best[0]):
            best = (number, url if url.endswith("/") else url + "/")
    return best[1] if best else ""


def _justice_date(url: str, text: str) -> date | None:
    return judgment_date(text, url)


def _siu_date(facts: dict):
    def date_for(url: str, text: str) -> date | None:
        return judgment_date(text) or facts.get("date") or date_from_name(url)

    return date_for


def _siu_citation(facts: dict):
    when_for = _siu_date(facts)

    def citation_for(url: str, text: str) -> str:
        return cite(url, text, case_no=facts.get("case_no") or "", when=when_for(url, text))

    return citation_for


class SpecialTribunalAdapter:
    source_id = "special-tribunal"

    def __init__(self) -> None:
        self.run = ArchiveRun("")

    def load_note(self) -> dict:
        return self.run.load_note()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        # A checkpoint from before CURSOR_TAG marked every SIU PDF "not a judgment" and
        # stored justice PDFs without dates; reading it would skip them for ever.
        if not (resume or "").endswith("\n" + CURSOR_TAG):
            resume = ""
        self.run = ArchiveRun(resume[: -len(CURSOR_TAG) - 1] if resume else "")
        run = self.run
        blobs: list[bytes] = []
        justice = await polite_get(http_get, JUSTICE, html_headers(), "TRIBUNAL_MIN_INTERVAL")
        if justice.challenge:
            run.skip("challenge page")
            run.hold = True
        elif not justice.error:
            run.place("justice", "0", "0")
            for url in justice_pdfs(justice.body):
                payload = await accept_pdf(
                    run,
                    http_get,
                    url,
                    title="",
                    body="Special Tribunal",
                    interval_env="TRIBUNAL_MIN_INTERVAL",
                    headers=pdf_headers(),
                    citation_for=_citation,
                    date_for=_justice_date,
                    min_words=MIN_WORDS,
                )
                if payload:
                    blobs.append(payload)
                if run.stopped:
                    break
        if not run.stopped and not run.hold:
            listing_url = SIU
            seen_listings: set[str] = set()
            page_no = 1
            while listing_url and listing_url not in seen_listings and not run.stopped and not run.hold:
                seen_listings.add(listing_url)
                if run.resume_done and page_no > 1:
                    break
                run.place("siu", "0", str(page_no))
                listing = await polite_get(http_get, listing_url, html_headers(), "TRIBUNAL_MIN_INTERVAL")
                if listing.challenge:
                    run.skip("challenge page")
                    run.hold = True
                    break
                if listing.error:
                    run.hold = True
                    break
                for matter in siu_matter_urls(listing.body):
                    matter_page = await polite_get(http_get, matter, html_headers(), "TRIBUNAL_MIN_INTERVAL")
                    if matter_page.challenge:
                        run.skip("challenge page")
                        run.mark(matter)
                        continue
                    if matter_page.error or not matter_page.body:
                        run.skip("matter page error")
                        continue
                    facts = siu_matter(matter_page.body, matter)
                    pdf = facts["pdf"]
                    if not pdf:
                        run.skip("no pdf on matter page")
                        continue
                    payload = await accept_pdf(
                        run,
                        http_get,
                        pdf,
                        title=facts["title"],
                        body="Special Tribunal",
                        interval_env="TRIBUNAL_MIN_INTERVAL",
                        headers=pdf_headers(),
                        citation_for=_siu_citation(facts),
                        date_for=_siu_date(facts),
                        min_words=MIN_WORDS,
                    )
                    if payload:
                        blobs.append(payload)
                    if run.stopped:
                        break
                nxt = _next_listing(listing.body, listing_url, listing_url)
                page_no += 1
                listing_url = nxt
        run.finish()
        return blobs, run.cursor() + "\n" + CURSOR_TAG

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
