"""A South African judgment is named by its own header, not by the cases it cites.

Version 1 of the archive adapters took the first neutral citation anywhere in
the PDF (usually a case the judgment cites) and a title built from the file
name ('212CACApr23 March 2024'). These fixtures are real header shapes.
"""

import asyncio
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.adapters import za_cac, za_comptrib, za_sars_taxcourt  # noqa: E402
from agents.zenas.pool.adapters.za_case_common import (  # noqa: E402
    caption_title,
    court_citation,
    header_case_number,
    judgment_head,
    own_citation,
    parse_case_blob,
    case_bytes,
    slug_case_number,
)
from agents.zenas.pool.builddoc import build_document  # noqa: E402
from agents.zenas.pool.catalogue import sources_for_region  # noqa: E402
from agents.zenas.pool.chunk import chunk_document  # noqa: E402
from agents.zenas.pool.model import Section  # noqa: E402
from agents.zenas.pool.store import MemoryStore, url_replaces  # noqa: E402

SASOL = (
    "(1) REPORTABLE: YES \n5 March 2024 \n1 \nTHE COMPETITION APPEAL COURT \nOF SOUTH AFRICA \n"
    "CASE NO: 212/CAC/Apr 23 \nIn the matter between: \nSASOL GAS (PTY) LTD \nAnd \n"
    "THE COMPETITION COMMISSION OF SOUTH AFRICA \nINDUSTRIAL GAS USERS ASSOCIATION OF \n"
    "SOUTHERN AFRICA \nEGOLI GAS PROPRIETARY LIMITED \nApplicant \nFirst Respondent \n"
    "Coram: Davis, S \nJUDGMENT \n"
    "[1] The decisions sought to be reviewed were taken by the Commission. See Pharmaceutical "
    "Manufacturers Association of SA: In re Ex parte President of the RSA [2000] ZACC 1 and "
    "Bel Porto School [2002] ZACC 2.\n"
)
DAWN = (
    "THE COMPETITION APPEAL COURT OF SOUTH AFRICA \n Case No: 155/CACOct2017 \nIn the matter between \n"
    "DAWN CONSOLIDATED HOLDINGS (PTY) \nLTD \nFIRST APPELLANT \nDPI PLASTICS (PTY) LTD SECOND APPELLANT \n"
    "SANGIO PIPE (PTY) LTD THIRD APPELLANT \n \nand \n \nTHE COMPETITION COMMISSION  RESPONDENT \n"
    "Coram: Davis JP \n[1] As held in [1984] ZASCA 51, ...\n"
)
COKE = (
    "IN THE COMPETITION APPEAL COURT OF SOUTH AFRICA \nHELD IN CAPE TOWN \n Case NO: 194/CAC/Oct 21 \n"
    "In the matter between: \n \nIn the matter between \n \nCOMPETITION COMMISSION  APPELLANT \n \nand \n \n"
    "COCA-COLA BEVERAGES AFRICA \n(Pty) Ltd  FIRST RESPONDENT \n \nFOOD AND ALLIED WORKERS UNION \n"
    "(\u2018FAWU\u2019) SECOND RESPONDENT \n \nThis judgment was handed down electronically.\n"
)
SARS = (
    "IN THE TAX COURT OF SOUTH AFRICA \nHELD AT JOHANNESBURG \nCase No: IT 45628 \n"
    "In the matter between: \n \nTAXPAYER LE (PTY) LTD APPELLANT \nand  \n"
    "THE COMMISSIONER FOR THE RESPONDENT \nSOUTH AFRICAN REVENUE SERVICE \n \nJUDGMENT  \n"
    "[1] In Commissioner, SARS v Founders Hill [2011] ZASCA 66 the court held ...\n"
)
OWN = (
    "THE SUPREME COURT OF APPEAL OF SOUTH AFRICA \nJUDGMENT \nCase no: 639/2024 \n"
    "In the matter between: \nJAN BARNARD  APPELLANT \nand \nPIETER GOUWS  RESPONDENT \n"
    "Neutral citation: Barnard v Gouws (639/2024) [2026] ZASCA 139 (16 October 2026) \n"
    "[1] Following Fourie [2012] ZASCA 13 ...\n"
)


def test_header_stops_at_paragraph_one():
    head = judgment_head(SASOL)
    assert "In the matter between" in head
    assert "[2000] ZACC 1" not in head


def test_cited_case_is_never_the_judgments_own_citation():
    assert own_citation(SASOL, ("ZACAC",)) == ""
    assert own_citation(DAWN, ("ZACAC",)) == ""
    assert own_citation(SARS, ("ZATC",)) == ""
    assert za_cac._citation("https://www.comptrib.co.za/x/212CACApr23.pdf", SASOL) != "[2000] ZACC 1"
    assert za_sars_taxcourt._citation("https://www.sars.gov.za/x.pdf", SARS) != "[2011] ZASCA 66"
    assert "ZASCA" not in za_comptrib._citation("https://www.comptrib.co.za/x.pdf", DAWN)


def test_own_citation_from_header_title_or_file_name():
    assert own_citation(OWN, ("ZASCA", "ZAEC")) == "[2026] ZASCA 139"
    assert own_citation("", ("ZAEC",), title="X v Y (005/25EC) [2026] ZAEC 01 (3 July 2026)") == "[2026] ZAEC 01"
    url = "https://www.supremecourtofappeal.org.za/lectoral-commission-005-25ec-2026-zaec-01-03-july-2026"
    assert own_citation("", ("ZAEC",), url=url) == "[2026] ZAEC 01"
    # The right year and number but another court's code is not this judgment.
    assert own_citation(OWN, ("ZACAC",)) == ""


def test_cac_falls_back_to_its_own_case_number():
    assert za_cac._citation("https://www.comptrib.co.za/x/judgment.pdf", SASOL) == "CAC 212"
    assert header_case_number(SASOL) == "212/CAC/APR 23"
    assert header_case_number(COKE) == "194/CAC/OCT 21"


def test_slug_case_numbers_and_court_citation():
    assert slug_case_number("https://x/31-january-2017-cape-town-c344-2016-imatu-v-ccma-others") == "C344/2016"
    assert slug_case_number("https://x/07-october-2024-randburg-lcc18r-2024-piketberg") == "LCC18R/2024"
    assert slug_case_number("https://x/21-september-2026-johannesburg-2026-124805-hlabanang") == "124805/2026"
    text = "Case no: LCC 16/2020 \n[1] See [2007] ZACC 12.\n"
    assert court_citation("https://x/judgment", text, ("ZALCC",), "Land Court") == "Land Court LCC 16/2020"
    assert court_citation("https://x/judgment", "[1] See [2007] ZACC 12.", ("ZALCC",), "Land Court") == ""


def test_caption_title_reads_the_parties():
    assert caption_title(SASOL) == "Sasol Gas (Pty) Ltd v The Competition Commission of South Africa and Others"
    assert caption_title(DAWN) == "Dawn Consolidated Holdings (Pty) Ltd and Others v The Competition Commission"
    assert caption_title(COKE) == "Competition Commission v Coca-Cola Beverages Africa (Pty) Ltd and Others"
    assert caption_title(SARS) == "Taxpayer Le (Pty) Ltd v The Commissioner for the South African Revenue Service"
    assert caption_title("[1] No caption here.") == ""
    for title in map(caption_title, (SASOL, DAWN, COKE, SARS)):
        assert "CAC" not in title and "2024" not in title and "Case" not in title


def test_parsed_document_carries_the_caption_title():
    source = next(item for item in sources_for_region("za") if item.source_id == "cac")
    blob = case_bytes(
        citation=za_cac._citation("https://www.comptrib.co.za/x/212CACApr23.pdf", SASOL),
        title=caption_title(SASOL),
        body="Competition Appeal Court",
        url="https://www.comptrib.co.za/x/212CACApr23.pdf",
        text=SASOL,
        doc_date=date(2024, 3, 5),
    )
    doc = parse_case_blob(blob, source)[0]
    assert doc.title.startswith("Sasol Gas (Pty) Ltd v ")
    assert "212CACApr23" not in doc.title


def test_corrected_citation_replaces_the_row_for_the_same_file():
    asyncio.run(_replaces())


async def _replaces():
    source = next(item for item in sources_for_region("za") if item.source_id == "cac")
    store = MemoryStore(home_region="za")
    await store.record_source(source)

    def doc(citation: str, url: str):
        return build_document(
            source,
            citation=citation,
            title="Sasol Gas (Pty) Ltd v The Competition Commission",
            level="national",
            kind="case",
            body="Competition Appeal Court",
            sections=[Section("", "Judgment", SASOL, [])],
            url=url,
            status="in_force",
            doc_date=date(2024, 3, 5),
            effective_from=date(2024, 3, 5),
        )

    url = "https://www.comptrib.co.za/x/212CACApr23.pdf"
    wrong = doc("[2000] ZACC 1", url)
    other = doc("CAC 213", "https://www.comptrib.co.za/x/213CACApr23.pdf")
    assert url_replaces(wrong)
    assert await store.upsert_version(wrong, chunk_document(wrong), on=date(2026, 10, 1))
    assert await store.upsert_version(other, chunk_document(other), on=date(2026, 10, 1))
    right = doc("CAC 212", url)
    assert await store.upsert_version(right, chunk_document(right), on=date(2026, 10, 31))
    open_rows = {item.citation for item in store.docs.values() if item.effective_to is None}
    assert open_rows == {"CAC 212", "CAC 213"}
    closed = next(item for item in store.docs.values() if item.citation == "[2000] ZACC 1")
    assert closed.status == "superseded" and closed.effective_to == date(2026, 10, 31)


def test_only_za_case_files_replace_by_url():
    source = next(item for item in sources_for_region("us-east") if item.source_id == "govinfo-uscode")
    statute = build_document(
        source,
        citation="15 U.S.C. § 80a-1",
        title="Findings",
        level="federal",
        kind="statute",
        body="Congress",
        sections=[Section("(a)", "General", "text", [])],
        url="https://www.govinfo.gov/title15",
        status="in_force",
        doc_date=None,
        effective_from=None,
    )
    assert not url_replaces(statute)


def test_a_source_page_url_is_not_one_judgment_file():
    # SAFLII records its listing page as every case's URL; two cases from it both stay open.
    source = next(item for item in sources_for_region("za") if item.source_id == "saflii")
    case = build_document(
        source,
        citation="[2023] ZASCA 12",
        title="Appeal",
        level="national",
        kind="case",
        body="Judgment",
        sections=[Section("1", "Judgment", "text", [])],
        url=source.canonical_url,
        status="in_force",
        doc_date=None,
        effective_from=None,
    )
    assert not url_replaces(case, source)
    assert url_replaces(replace(case, url="https://www.saflii.org/za/cases/ZASCA/2023/12.html"), source)


def test_files_without_a_neutral_citation_use_the_printed_case_number():
    # Real header shapes from the 25 files that had no identifier of their own.
    # Each takes the number printed in its header; none is made up.
    assert za_comptrib._citation(
        "https://www.comptrib.co.za/uploads/topics/CompTrib_Case_Files/Reasons%20for%20Decision%20-%20KKI.pdf",
        "COMPETITION TRIBUNAL OF SOUTH AFRICA\nIn the matter between:\nCASE No: IM238JAN19\nKlein Karoo International (Pty) Ltd First Applicant\n",
    ) == "CompTrib IM238JAN19"
    assert za_comptrib._citation(
        "https://www.comptrib.co.za/uploads/topics/CompTrib_Case_Files/20141106134700204_A0-4781e.pdf",
        "COMPETITION TRIBUNAL OF SOUTH AFRICA\nCase No: (019513)\nIn the matter between:\nLINPAC PLASTICS (SA) PTY LTD\n",
    ) == "CompTrib 019513"
    # An unlabelled code with a footnote mark glued on ("CR212Feb17" + "1").
    assert za_comptrib._citation(
        "https://www.comptrib.co.za/uploads/topics/CompTrib_Case_Files/REASONS%20Various.pdf",
        "COMPETITION TRIBUNAL OF SOUTH AFRICA \n \nCR212Feb171 \nIn the consolidated exception\n",
    ) == "CompTrib CR212FEB17"
    assert header_case_number("I\nN THE LAND COURT OF SOUTH AFRICA \nCAS\nE NO: LanC 89/2019B \nB\nefore: Bishop AJ\n") == "LANC 89/2019B"
    assert header_case_number("IN THE LAND COURT\nCase number:  2025 \u2013 154050  \nBefore: Montzinger AJ\n") == "2025-154050"
    assert header_case_number("THE LAND COURT OF SOUTH AFRICA \nCase 49/2011B \nHeard on 25 July 2025\n") == "49/2011B"
    assert za_sars_taxcourt._citation(
        "https://www.sars.gov.za/lapd-drj-tc-2018-07-tc-tadm-14294-11-july-2018/",
        "THE TAX COURT OF SOUTH AFRICA \n(JOHANNESBURG) \nCase no: IT 14294\nIn the matter between: \n",
    ) == "Tax Court IT 14294"
    # Prose that mentions "case" is not a case number.
    assert header_case_number("The first case of COVID-19 on 5 March 2020.\nIn this case 5 people\n") == ""


def test_electoral_court_judgment_on_the_sca_site_names_its_own_court():
    from agents.zenas.pool.adapters import za_sca_jdownloads

    text = (
        "THE ELECTORAL COURT OF SOUTH AFRICA \n(BLOEMFONTEIN) \n Not Reportable \n Case No: 0013/26EC \n"
        "In the matter between: \nAFRICAN NATIONAL CONGRESS APPELLANT \nand \nELECTORAL COMMISSION RESPONDENT\n"
        "[1] This appeal concerns [2012] ZASCA 13.\n"
    )
    assert za_sca_jdownloads._citation("https://www.supremecourtofappeal.org.za/x/4745-anc-v-iec", text, "") == (
        "Electoral Court 0013/26EC"
    )


def test_cac_keeps_a_constitutional_court_judgments_own_citation():
    text = (
        "CONSTITUTIONAL COURT OF SOUTH AFRICA \nCase CCT 31/20 \nIn the matter between: \nCOMPETITION COMMISSION Applicant\n"
        "and\nMEDICLINIC SOUTHERN AFRICA (PTY) LIMITED First Respondent\nNeutral citation: Competition Commission v Mediclinic "
        "[2021] ZACC 35\n[1] As held in [2016] ZACAC 2 ...\n"
    )
    assert za_cac._citation("https://www.cac.org.za/wp-content/uploads/CCT%2031-20%20CC%20v%20Mediclinic.pdf", text) == (
        "[2021] ZACC 35"
    )
