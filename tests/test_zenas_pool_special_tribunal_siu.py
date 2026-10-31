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

from agents.zenas.pool.adapters.za_archive import MARKER  # noqa: E402
from agents.zenas.pool.adapters.za_special_tribunal import (  # noqa: E402
    CURSOR_TAG,
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
from agents.zenas.pool.textutil import document_id_for  # noqa: E402

# Real judgments run to pages; a fixture needs at least MIN_WORDS (50) words to count as text.
REASONS = " ".join(["The applicant seeks review of the contract award and the tribunal considers it."] * 6)


def tiny_pdf(text: str | None) -> bytes:
    """A one-page PDF. text=None gives a page with no text layer, like a scan."""
    if text is None:
        stream = b"q 10 0 0 10 0 0 cm 0 0 1 1 re f Q\n"
        resources = b""
    else:
        lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in text.split("\n")]
        shown = " ".join(f"({line}) Tj 0 -14 Td" for line in lines)
        stream = f"BT /F1 10 Tf 36 760 Td {shown} ET\n".encode()
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
    # Case number plus the judgment date, so two judgments in one case do not share an id.
    assert _citation(
        "https://x/ST-Judgment-GP-09-2025.pdf", "IN THE SPECIAL TRIBUNAL\nDate of judgment: 23 April 2025\n"
    ) == "Special Tribunal GP 9/2025 (2025-04-23)"
    # The judgment's own neutral citation comes first.
    assert _citation(
        "https://x/ST-Judgment-GP-09-2025.pdf", "[2025] ZAST 91\nIN THE SPECIAL TRIBUNAL\nDate of judgment: 23 April 2025\n"
    ) == "[2025] ZAST 91"


def test_judgment_date_reads_labelled_lines_not_the_first_number():
    # parse_date(text[:800]) gives up on "Act 74 of 1996"; these do not.
    preamble = "ESTABLISHED IN TERMS OF SECTION 2(1) OF ACT 74 OF 1996\n"
    assert judgment_date(preamble + "Date of hearing: 10 October 2022\nDate of order: 13 October 2022") == date(
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
    stacked = preamble + "\nDate of hearing:\nDate of judgment:\n1 May 2026\n26 August 2026\n"
    assert judgment_date(stacked) == date(2026, 8, 26)
    assert judgment_date(preamble + "\nDate of judgment: Reserved\nDate of hearing: 1 May 2026\n") is None
    assert judgment_date("Case no: GP 1/2026\nDate of hearing: 1 May 2026\n") is None
    quoted = (
        preamble + "\nIn Smith the court said, in the judgment delivered on 12 March 2019, that it was so.\n"
        "JUDGE L MODIBA\nMEMBER OF THE SPECIAL TRIBUNAL\n26 August 2026"
    )
    assert judgment_date(quoted) == date(2026, 8, 26)
    assert judgment_date(preamble + "\nset aside on the date of order of the High Court, 3 March 2019\n") is None
    assert judgment_date(preamble + "\nan update of delivery: 3 March 2025\n") is None
    assert judgment_date(preamble + "\nCounsel for the applicant: Adv Raj\n3 March 2026") is None
    assert judgment_date(preamble + "\nDate of judgment: 1st of September 2026\n") == date(2026, 9, 1)
    body = preamble + "\n" + "word " * 600 + "\ninterest from date of judgment: 5 May 2021\n" + "word " * 400
    assert judgment_date(body) is None  # the order's text, not its header or signature
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
        "10h00 on 26 August 2026. " + REASONS
    )
    undated_pdf = tiny_pdf("The award of the tender is reviewed and set aside with costs. " + REASONS)
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
        ("Special Tribunal 11107/2014 (2026-08-26)", "2026-08-26", "Judgment in the matter of SIU vs Alpha"),
        ("Special Tribunal GP 3/2025 (2025-06-05)", "2025-06-05", "Judgment in the matter of SIU vs Gamma"),
    ]
    note = json.dumps(adapter.load_note())
    assert "no text layer" in note


def test_justice_judgment_date_comes_from_the_date_of_judgment_line():
    pdf = tiny_pdf(
        "IN THE SPECIAL TRIBUNAL ESTABLISHED IN TERMS OF ACT 74 OF 1996\nThe contract is set aside.\n"
        "Date of hearing: 1 September 2022\nDate of judgment: 7 September 2022\n" + REASONS
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
    assert row["citation"] == "Special Tribunal GP 3/2022 (2022-09-07)"
    assert row["date"] == "2022-09-07"


# --- Review of #502 ------------------------------------------------------------

def _justice_and_siu(pdfs: dict[str, bytes], matters: dict[str, bytes], listings: dict[str, bytes], justice: bytes):
    seen: list[str] = []

    async def get(url, headers):
        seen.append(url)
        if url.endswith("/jdgm.html"):
            return justice
        if url in listings:
            return listings[url]
        for slug, page in matters.items():
            if url.rstrip("/").endswith(slug):
                return page
        name = url.rsplit("/", 1)[-1]
        if name in pdfs:
            return pdfs[name]
        return b""

    return get, seen


def test_an_old_finished_checkpoint_is_read_again_and_tagged():
    """B1: before st-v2 every SIU PDF was marked 'not a judgment'; resuming that cursor must not skip them."""
    up = "https://www.siu.org.za/wp-content/uploads/2026/"
    pdfs = {
        "a.pdf": tiny_pdf("IN THE SPECIAL TRIBUNAL\n" + REASONS),
        "b.pdf": tiny_pdf("IN THE SPECIAL TRIBUNAL\n" + REASONS),
    }
    matters = {
        "judgment-a": matter_html("Judgment in the matter of SIU vs A", "GP 1/2026", "02/03/2026", f"{up}a.pdf"),
        "judgment-b": matter_html("Judgment in the matter of SIU vs B", "GP 2/2025", "04/05/2025", f"{up}b.pdf"),
    }
    listings = {SIU: listing_html(["judgment-a"], [2]), f"{SIU}2/": listing_html(["judgment-b"], [1])}
    get, seen = _justice_and_siu(pdfs, matters, listings, b"<html></html>")
    from agents.zenas.pool.adapters.za_archive import url_key

    old = "\n".join([MARKER, "1", "siu", "0", "2", ",".join(sorted([url_key(f"{up}a.pdf"), url_key(f"{up}b.pdf")]))])
    adapter = SpecialTribunalAdapter()
    blobs, cursor = asyncio.run(adapter.fetch_bulk(get, resume=old))
    rows = sorted(json.loads(blob)["date"] for blob in blobs)
    assert rows == ["2025-05-04", "2026-03-02"]
    assert f"{SIU}2/" in seen
    assert cursor.endswith("\n" + CURSOR_TAG)
    # The new cursor is honoured: a second run skips both as unchanged and reads only page 1.
    seen.clear()
    again, _ = asyncio.run(SpecialTribunalAdapter().fetch_bulk(get, resume=cursor))
    assert again == []
    assert f"{SIU}2/" not in seen


def test_two_judgments_in_one_case_get_two_ids_and_one_judgment_on_both_sites_gets_one():
    """B2: GP 09/2025 has judgments of 23 Apr 2025 and 3 Dec 2025; a ZAST citation wins on either site."""
    up = "https://www.siu.org.za/wp-content/uploads/2025/"
    shared = tiny_pdf("[2025] ZAST 91\nIN THE SPECIAL TRIBUNAL\nCASE NO: GP 12/2025\nDate of judgment: 9 June 2025\n" + REASONS)
    pdfs = {
        "smart-safety.pdf": tiny_pdf("IN THE SPECIAL TRIBUNAL\n" + REASONS),
        "absa.pdf": tiny_pdf("IN THE SPECIAL TRIBUNAL\n" + REASONS),
        "shared.pdf": shared,
        "ST-Judgment-GP-12-2025.pdf": shared,
    }
    matters = {
        "judgment-smart": matter_html("Ex parte SIU v Smart Safety PPE", "GP09/2025", "23/04/2025", f"{up}smart-safety.pdf"),
        "judgment-absa": matter_html("Ex parte SIU v Absa Bank", "GP09/2025", "03/12/2025", f"{up}absa.pdf"),
        "judgment-shared": matter_html("Judgment in the matter of SIU vs C", "GP12/2025", "09/06/2025", f"{up}shared.pdf"),
    }  # fmt: skip
    listings = {SIU: listing_html(list(matters), [])}
    justice = b'<a href="jdm/ST-Judgment-GP-12-2025.pdf">j</a>'
    get, _seen = _justice_and_siu(pdfs, matters, listings, justice)
    blobs = asyncio.run(SpecialTribunalAdapter().fetch(get))
    rows = [json.loads(blob) for blob in blobs]
    ids = {document_id_for("special-tribunal", row["citation"]) for row in rows}
    citations = sorted(row["citation"] for row in rows)
    assert citations == [
        "Special Tribunal GP 9/2025 (2025-04-23)",
        "Special Tribunal GP 9/2025 (2025-12-03)",
        "[2025] ZAST 91",
        "[2025] ZAST 91",
    ]
    assert len(ids) == 3  # two in GP 9/2025, and one for the judgment listed on both sites


def test_without_a_judgment_date_the_hearing_date_is_not_stored():
    """B3: the adapter's date reader is final; parse_date(text[:800]) would take 'Date of hearing'."""
    pdf = tiny_pdf("IN THE SPECIAL TRIBUNAL\nCase no: GP 1/2026\nDate of hearing: 1 May 2026\n" + REASONS)

    async def get(url, headers):
        if url.endswith("/jdgm.html"):
            return b'<a href="jdm/ST-Judgment-GP-01-2026.pdf">j</a>'
        return pdf if url.endswith(".pdf") else b"<html></html>"

    rows = [json.loads(blob) for blob in asyncio.run(SpecialTribunalAdapter().fetch(get))]
    assert [(row["citation"], row["date"]) for row in rows] == [("Special Tribunal GP 1/2026", "")]


def test_stamps_cut_off_files_and_bad_matter_pages_are_counted_not_stored():
    """A CamScanner stamp is no text; a cut-off PDF is unreadable and retried; matter-page failures are counted."""
    up = "https://www.siu.org.za/wp-content/uploads/2026/"
    whole = tiny_pdf("IN THE SPECIAL TRIBUNAL\n" + REASONS)
    pdfs = {"stamp.pdf": tiny_pdf("Scanned with CamScanner"), "cut.pdf": whole[: len(whole) // 2]}
    matters = {
        "judgment-stamp": matter_html("Judgment in the matter of SIU vs Stamp", "GP 4/2024", "01/02/2026", f"{up}stamp.pdf"),
        "judgment-cut": matter_html("Judgment in the matter of SIU vs Cut", "GP 5/2024", "01/02/2026", f"{up}cut.pdf"),
        "judgment-nopdf": b"<h1>Judgment in the matter of SIU vs None</h1><p>No download yet.</p>",
    }  # fmt: skip
    listings = {SIU: listing_html([*matters, "judgment-missing"], [])}
    get, _seen = _justice_and_siu(pdfs, matters, listings, b"<html></html>")
    adapter = SpecialTribunalAdapter()
    blobs, cursor = asyncio.run(adapter.fetch_bulk(get))
    assert blobs == []
    skips = adapter.load_note()["skips"]
    assert skips["no text layer"] == 1
    assert skips["unreadable pdf"] == 1
    assert skips["no pdf on matter page"] == 1
    assert skips["matter page error"] == 1  # the empty body for judgment-missing
    from agents.zenas.pool.adapters.za_archive import url_key

    assert url_key(f"{up}stamp.pdf") in cursor
    assert url_key(f"{up}cut.pdf") not in cursor  # retried next run


def test_a_4xx_is_not_retried_but_a_5xx_still_is(monkeypatch: pytest.MonkeyPatch):
    import io
    import urllib.error
    from email.message import Message

    from agents.zenas.pool.adapters.za_archive import polite_get

    calls: list[int] = []

    def failing(code):
        async def get(url, headers):
            calls.append(code)
            raise urllib.error.HTTPError(url, code, "x", Message(), io.BytesIO(b""))

        return get

    got = asyncio.run(polite_get(failing(404), "https://www.siu.org.za/gone/", {}, "TRIBUNAL_MIN_INTERVAL"))
    assert got.error and calls == [404]
    calls.clear()
    asyncio.run(polite_get(failing(503), "https://www.siu.org.za/busy/", {}, "TRIBUNAL_MIN_INTERVAL"))
    assert calls == [503, 503, 503]
    calls.clear()
    asyncio.run(polite_get(failing(429), "https://www.siu.org.za/slow/", {}, "TRIBUNAL_MIN_INTERVAL"))
    assert calls == [429, 429, 429]


# --- Review of #502, round 2 ---------------------------------------------------

_SIGNED = "\nJUDGE L MODIBA\nMEMBER OF THE SPECIAL TRIBUNAL\n26 August 2026"


def test_wrapped_prose_is_not_a_date_label():
    """D2: hard-wrapped prose can start a line; a label needs ':' or '-', or a date and nothing else after it."""
    head = "IN THE SPECIAL TRIBUNAL\nCASE NO: GP 1/2026\n"
    for prose in (
        "The applicant relies on the\ndate of order of the High Court, 3 March 2019, as the start.\n",
        "As was confirmed in the\njudgment delivered on 12 March 2019 in SIU v Y.\n",
    ):
        assert judgment_date(head + prose + _SIGNED) == date(2026, 8, 26), prose
    order = "\n" + "word " * 500 + "\nInterest from the\ndate of judgment, being 1 May 2026 (the hearing), at the prescribed rate."
    assert judgment_date(head + "word " * 600 + order + _SIGNED) == date(2026, 8, 26)
    # The real label forms still work.
    assert judgment_date(head + "Date of judgment - 26 August 2026\n") == date(2026, 8, 26)
    assert judgment_date(head + "Judgment delivered on 26 August 2026.\n") == date(2026, 8, 26)
    assert judgment_date(head + "Judgment handed down on this 26th day of August 2026\n") == date(2026, 8, 26)


def test_a_stack_maps_by_position_only_when_every_label_has_a_date_below():
    """D3: a Coram line among the values must not shift the hearing date onto the judgment label."""
    for stack in (
        "Date of hearing:\nDate of judgment:\nCoram: Mia J\n1 May 2026\n26 August 2026\n",
        "Coram:\nDate of hearing:\nDate of judgment:\nMia J\n1 May 2026\n26 August 2026\n",
    ):
        assert judgment_date("IN THE SPECIAL TRIBUNAL\n" + stack) is None, stack
        assert judgment_date("IN THE SPECIAL TRIBUNAL\n" + stack + "Reasons follow." + _SIGNED) == date(2026, 8, 26)
    even = "Date of hearing:\nDate of judgment:\n1 May 2026\n26 August 2026 at 10h00\n"
    assert judgment_date("IN THE SPECIAL TRIBUNAL\n" + even) == date(2026, 8, 26)
    assert judgment_date("IN THE SPECIAL TRIBUNAL\nDate of judgment:\nDate of hearing:\n1 May 2026\n") is None


# D1. A dated citation re-keys a judgment that main stored under its bare case number.
# store.url_replaces already closes the old row: a ZA case file holds one judgment, so a new
# document from the same source and file supersedes any open row there under another id
# (MemoryStore.upsert_version and the same UPDATE in PostgresStore.upsert_version).
# These tests pin that for this source, through the nightly ingest path.

_OLD_URL = "https://www.justice.gov.za/tribunal/jdm/ST-Judgment-GP-17-2020.pdf"
_OTHER_URL = "https://www.justice.gov.za/tribunal/jdm/ST-Judgment-GP-17-2020-Jdm.pdf"


def _row(citation: str, url: str, when: str) -> bytes:
    text = "IN THE SPECIAL TRIBUNAL\n" + REASONS
    return json.dumps({"citation": citation, "title": "SIU v X", "url": url, "date": when, "text": text}).encode()


async def _rekey(store) -> None:
    from collections import Counter

    from agents.zenas.pool.catalogue import CATALOGUE
    from agents.zenas.pool.ingest import ingest_snapshot

    source = next(item for item in CATALOGUE if item.source_id == "special-tribunal")
    listing = source.canonical_url
    # What main stored: bare case numbers. Two rows on the source's own page stand for a
    # loader whose URL names a listing, not one file; those must never close each other.
    before = [
        _row("Special Tribunal GP 17/2020", _OLD_URL, ""),
        _row("Special Tribunal GP 17/2020 (2021-03-08)", _OTHER_URL, "2021-03-08"),
        _row("Special Tribunal GP 1/2019", listing, ""),
        _row("Special Tribunal GP 2/2019", listing, ""),
    ]
    await ingest_snapshot(store, source, before, on=date(2026, 10, 1))
    dated = [_row("Special Tribunal GP 17/2020 (2021-02-26)", _OLD_URL, "2021-02-26")]
    assert await ingest_snapshot(store, source, dated, on=date(2026, 11, 1)) == 1
    again: Counter[str] = Counter()
    assert await ingest_snapshot(store, source, dated, on=date(2026, 11, 2), skips=again) == 0
    assert again == Counter({"unchanged": 1})


def _assert_rekeyed(rows: list[tuple[str, str, str, date | None]]) -> None:
    by_citation = {citation: (url, status, effective_to) for citation, url, status, effective_to in rows}
    assert len(rows) == len(by_citation) == 5
    assert by_citation["Special Tribunal GP 17/2020"][1:] == ("superseded", date(2026, 11, 1))
    open_rows = sorted(citation for citation, (_url, _status, until) in by_citation.items() if until is None)
    assert open_rows == [
        "Special Tribunal GP 1/2019",
        "Special Tribunal GP 17/2020 (2021-02-26)",
        "Special Tribunal GP 17/2020 (2021-03-08)",
        "Special Tribunal GP 2/2019",
    ]


def test_a_rekeyed_judgment_closes_its_old_row_in_memory():
    from agents.zenas.pool.store import MemoryStore

    store = MemoryStore(home_region="za")
    asyncio.run(_rekey(store))
    _assert_rekeyed([(doc.citation, doc.url, doc.status, doc.effective_to) for doc in store.docs.values()])


def _database_url() -> str:
    import os

    url = os.environ.get("POOL_SCHEMA_DATABASE_URL", "").strip()
    if url:
        return url
    if os.environ.get("GITHUB_ACTIONS"):
        pytest.fail("POOL_SCHEMA_DATABASE_URL is not set")
    pytest.skip("POOL_SCHEMA_DATABASE_URL is not set")
    return ""


def test_a_rekeyed_judgment_closes_its_old_row_in_postgres():
    asyncio.run(_rekey_postgres(_database_url()))


async def _rekey_postgres(url: str) -> None:
    import asyncpg

    from agents.zenas.pool.store import PostgresStore

    conn = await asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS zenas_pool CASCADE")
        store = PostgresStore(conn, home_region="za")
        await store.apply_schema()
        await _rekey(store)
        rows = await conn.fetch(
            "SELECT citation, url, status, effective_to FROM zenas_pool.documents WHERE source_id = 'special-tribunal'"
        )
        _assert_rekeyed([(row["citation"], row["url"], row["status"], row["effective_to"]) for row in rows])
    finally:
        await conn.execute("DROP SCHEMA IF EXISTS zenas_pool CASCADE")
        await conn.close()


# --- Review of #502, round 3 ---------------------------------------------------


def test_a_dash_after_a_label_needs_a_date_and_nothing_else():
    """D4: ':' takes anything after it; a dash, or no separator, only a date (and a time)."""
    head = "IN THE SPECIAL TRIBUNAL\nCASE NO: GP 1/2026\n"
    for prose in (
        "the registrar's\ndate-stamped copy of 3 March 2019 was filed.\n",
        "relies on the\ndate of order - 3 March 2019 - of the High Court.\n",
        "As was confirmed in the\njudgment delivered on 12 March 2019.\nThat case is distinguishable.\n",
    ):
        assert judgment_date(head + prose + _SIGNED) == date(2026, 8, 26), prose
    assert judgment_date(head + "Date of judgment \u2013 26 August 2026\n") == date(2026, 8, 26)
    assert judgment_date(head + "Date of judgment \u2014 26 August 2026\n") == date(2026, 8, 26)
    assert judgment_date(head + "Judgment delivered on 26 August 2026 at 10h00\n") == date(2026, 8, 26)
    assert judgment_date(head + "Date of judgment: 26 August 2026, electronically\n") == date(2026, 8, 26)


def test_a_stack_needs_exactly_as_many_dates_as_labels():
    """D5: a two-day hearing puts three dates under two labels; position no longer says which is which."""
    stack = "IN THE SPECIAL TRIBUNAL\nDate of hearing:\nDate of judgment:\n1 May 2026\n2 May 2026\n26 August 2026\n"
    assert judgment_date(stack) is None
    assert judgment_date(stack + "Reasons." + _SIGNED) == date(2026, 8, 26)
