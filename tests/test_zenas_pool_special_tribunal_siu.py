"""Special Tribunal: SIU paging, case numbers from the matter page, scans, and dates.

Every network call is mocked. The HTML mirrors www.siu.org.za (Elementor):
list pages are /special-tribunal-judgements/2/, /3/ ...; a matter page has a
"Case Number:" widget, a "Date:" widget (dd/mm/yyyy) and a Download button.
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters.za_special_tribunal import (  # noqa: E402
    SIU,
    SpecialTribunalAdapter,
    _citation,
    _next_listing,
    case_citation,
    date_from_name,
    date_in,
    judgment_date,
    siu_matter,
)


def tiny_pdf(text: str | None) -> bytes:
    """A one-page PDF. text=None gives a page with no text layer, like a scan."""
    if text is None:
        stream = b"q 10 0 0 10 0 0 cm 0 0 1 1 re f Q\n"
        resources = b""
    else:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 10 Tf 36 120 Td ({safe}) Tj ET\n".encode()
        resources = b" /Resources << /Font << /F1 5 0 R >> >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2000 792] /Contents 4 0 R" + resources + b" >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def matter_html(title: str, case_no: str, when: str, pdf: str) -> bytes:
    return (
        f'<h1 class="elementor-heading-title">{title}</h1>'
        '<div class="elementor-widget-container"><p><strong>Case Number:</strong></p></div>'
        f'<div class="elementor-widget-container">\n\t\t{case_no}\t\t</div>'
        '<div class="elementor-widget-container"><p><strong>Date:</strong></p></div>'
        f'<div><a class="elementor-button" href="{pdf}"><span><span>Download</span></span></a></div>'
        f'<div class="elementor-widget-container">\n\t\t{when}\t\t</div>'
    ).encode()


def listing_html(matters: list[str], pages: list[int]) -> bytes:
    items = "".join(
        f'<h3 class="elementor-post__title"><a href="https://www.siu.org.za/judgements/{slug}/">{slug}</a></h3>'
        '<span class="elementor-post-date">August 28, 2026</span>'
        for slug in matters
    )
    links = "".join(f'<a class="page-numbers" href="{SIU}{n}/">{n}</a>' for n in pages)
    return (items + links).encode()


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRIBUNAL_MIN_INTERVAL", "0")


def test_next_listing_follows_numbered_siu_pages():
    page1 = listing_html([], [2, 3, 4, 5])
    assert _next_listing(page1, SIU, SIU) == f"{SIU}2/"
    page2 = listing_html([], [1, 3, 4, 5])
    assert _next_listing(page2, SIU, f"{SIU}2/") == f"{SIU}3/"
    assert _next_listing(listing_html([], [1, 2, 3]), SIU, f"{SIU}3/") == ""
    # The older WordPress form still works.
    assert _next_listing(b'<a href="/special-tribunal-judgements/page/2/">2</a>', SIU, SIU) == f"{SIU}page/2/"


def test_siu_matter_page_gives_case_number_title_and_date():
    page = matter_html(
        "Judgment in the matter of SIU vs Minenhle Makhanya and 1 other",
        "11107/2014",
        "26/08/2026",
        "https://www.siu.org.za/wp-content/uploads/2026/08/SIU-v-M-Makhanya-11107-2014.cleaned-1.pdf",
    )
    facts = siu_matter(page, "https://www.siu.org.za/judgements/judgment-in-the-matter-of-siu-vs-makhanya/")
    assert facts["title"] == "Judgment in the matter of SIU vs Minenhle Makhanya and 1 other"
    assert facts["case_no"] == "11107/2014"
    assert facts["date"] == date(2026, 8, 26)
    assert facts["pdf"].endswith("SIU-v-M-Makhanya-11107-2014.cleaned-1.pdf")
    empty = siu_matter(matter_html("T", "", "", "https://www.siu.org.za/wp-content/uploads/a.pdf"), SIU)
    assert empty["case_no"] == "" and empty["date"] is None


def test_case_citation_normalises_siu_and_justice_forms():
    assert case_citation("GP20/2026") == "Special Tribunal GP 20/2026"
    assert case_citation("GP/10/2019") == "Special Tribunal GP 10/2019"
    assert case_citation("11107/2014") == "Special Tribunal 11107/2014"
    assert case_citation("not a case") == ""
    assert _citation("https://x/a.pdf", "IN THE SPECIAL TRIBUNAL CASE NO: 11107/2014 In the matter") == (
        "Special Tribunal 11107/2014"
    )
    assert _citation("https://x/a.pdf", "No case number here") == ""


def test_judgment_date_reads_labelled_lines_not_the_first_number():
    # parse_date(text[:800]) gives up on "Act 74 of 1996"; these do not.
    preamble = "ESTABLISHED IN TERMS OF SECTION 2(1) OF ACT 74 OF 1996 "
    assert judgment_date(preamble + "Date of hearing: 10 October 2022 Date of order: 13 October 2022") == date(
        2022, 10, 13
    )
    assert judgment_date(preamble + "Date of Judgment:    18 March 2022") == date(2022, 3, 18)
    assert judgment_date(
        preamble + "The date and time for hand-down is deemed to be 13h30 on 26 August 2026."
    ) == date(2026, 8, 26)
    assert judgment_date("CASE NO: GP/10/2019\nDATE:  2020/01/21\nIn the matter") == date(2020, 1, 21)
    assert judgment_date(preamble + "nothing labelled 5 May 2020") is None
    signed = preamble + "6. There is no order as to costs.\nJUDGE L MODIBA\nMEMBER OF THE SPECIAL TRIBUNAL\n \n15 March 2022"
    assert judgment_date(signed) == date(2022, 3, 15)
    # A settlement "DATED 25 July 2022" is not the order's date.
    assert judgment_date(preamble + "The settlement agreement DATED 25 July 2022 is made an Order.\nJUDGE") is None
    assert judgment_date(preamble, "https://x/ST-Judgment-MP-03-2021-13Oct2022.pdf") == date(2022, 10, 13)


def test_dates_in_file_names_and_fragments():
    assert date_from_name("https://x/gp20_2026-order-01102026.pdf") == date(2026, 10, 1)
    assert date_from_name("https://x/ST-Judgment-GP-01-2021-18Oct2022.pdf") == date(2022, 10, 18)
    assert date_from_name("https://x/ST-Judgment-GP-04-2020-Order29Sep2022.pdf") == date(2022, 9, 29)
    assert date_from_name("https://x/ST-Judgment-GP-01-2021.pdf") is None
    assert date_from_name("https://x/20 Others 2019.pdf") is None
    assert date_in("7th day of September 2022") == date(2022, 9, 7)
    assert date_in("18/03/2022") == date(2022, 3, 18)
    assert date_in("1 Decision 2020") is None


def test_siu_pages_beyond_the_first_load_with_case_numbers_dates_and_scans_skipped():
    """Page 1 -> 2 -> 3. Text PDFs load with the matter page's case number; a scan is skipped."""
    text_pdf = tiny_pdf(
        "IN THE SPECIAL TRIBUNAL ESTABLISHED IN TERMS OF ACT 74 OF 1996 "
        "The contract is reviewed and set aside. The date and time for hand-down is deemed to be "
        "10h00 on 26 August 2026."
    )
    undated_pdf = tiny_pdf("The award of the tender is reviewed and set aside with costs.")
    scan = tiny_pdf(None)
    up = "https://www.siu.org.za/wp-content/uploads/2026/"
    matters = {
        "judgment-alpha": matter_html("Judgment in the matter of SIU vs Alpha", "11107/2014", "", f"{up}alpha.cleaned.pdf"),
        "judgement-beta": matter_html("Judgement in a matter of SIU vs Beta", "GP20/2026", "01/10/2026", f"{up}gp20_2026-order-01102026.pdf"),
        "judgment-gamma": matter_html("Judgment in the matter of SIU vs Gamma", "GP 3/2025", "05/06/2025", f"{up}gamma.pdf"),
    }  # fmt: skip
    pdfs = {"alpha.cleaned.pdf": text_pdf, "gp20_2026-order-01102026.pdf": scan, "gamma.pdf": undated_pdf}
    listings = {
        SIU: listing_html(["judgment-alpha"], [2, 3]),
        f"{SIU}2/": listing_html(["judgement-beta"], [1, 3]),
        f"{SIU}3/": listing_html(["judgment-gamma"], [1, 2]),
    }
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        if url.endswith("/jdgm.html"):
            return b"<html></html>"
        if url in listings:
            return listings[url]
        for slug, page in matters.items():
            if url.rstrip("/").endswith(slug):
                return page
        name = url.rsplit("/", 1)[-1]
        if name in pdfs:
            assert headers.get("Accept", "").startswith("application/pdf")
            return pdfs[name]
        return b""

    adapter = SpecialTribunalAdapter()
    blobs = asyncio.run(adapter.fetch(get))
    assert f"{SIU}2/" in seen and f"{SIU}3/" in seen
    rows = sorted((json.loads(blob) for blob in blobs), key=lambda row: row["citation"])
    assert [(row["citation"], row["date"], row["title"]) for row in rows] == [
        ("Special Tribunal 11107/2014", "2026-08-26", "Judgment in the matter of SIU vs Alpha"),
        ("Special Tribunal GP 3/2025", "2025-06-05", "Judgment in the matter of SIU vs Gamma"),
    ]
    note = json.dumps(adapter.load_note())
    assert "no text layer" in note


def test_justice_judgment_date_comes_from_the_date_of_judgment_line():
    pdf = tiny_pdf(
        "IN THE SPECIAL TRIBUNAL ESTABLISHED IN TERMS OF ACT 74 OF 1996 The contract is set aside. "
        "Date of hearing: Not applicable. Date of judgment: 7 September 2022"
    )

    async def get(url, headers):
        if url.endswith("/jdgm.html"):
            return b'<a href="jdm/ST-Judgment-GP-03-2022-order.pdf">j</a>'
        if url.endswith(".pdf"):
            return pdf
        return b"<html></html>"

    blobs = asyncio.run(SpecialTribunalAdapter().fetch(get))
    assert len(blobs) == 1
    row = json.loads(blobs[0])
    assert row["citation"] == "Special Tribunal GP 3/2022"
    assert row["date"] == "2022-09-07"
