"""The selftest's expected quote is a sentence of the judgment, never its header.

The ZA library loaded only the first 1,200 characters of each judgment, which is
all header, so the 'quote' was a court-name or case-number line. These fixtures
are real Competition Appeal Court and Tax Court header shapes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.core import selftest  # noqa: E402
from agents.zenas.core.selftest import _header_line, _verbatim_quote, library_passage  # noqa: E402

CAC = (
    "(1) REPORTABLE: YES \n5 March 2024 \n1 \nTHE COMPETITION APPEAL COURT \nOF SOUTH AFRICA \n"
    "CASE NO: 212/CAC/Apr 23 \nIn the matter between: \nSASOL GAS (PTY) LTD \nAnd \n"
    "THE COMPETITION COMMISSION OF SOUTH AFRICA \nINDUSTRIAL GAS USERS ASSOCIATION OF \n"
    "SOUTHERN AFRICA \nEGOLI GAS PROPRIETARY LIMITED \nApplicant \nFirst Respondent \n"
    "Coram: Davis, S Manoim and Nuku JJA \nHeard: 14 November 2023 \nDelivered: 5 March 2024 \n"
    "JUDGMENT \nDAVIS JP: \nIntroduction \n"
    "[1] The applicant seeks to review and set aside two decisions of the Commission\n"
    "taken in terms of section 49B of the Act. The decisions were taken without\n"
    "affording the applicant a hearing.\n"
    "2 \n"
    "[2] The Commission opposes the application.\n"
)
TAX = (
    "IN THE TAX COURT OF SOUTH AFRICA \nHELD AT JOHANNESBURG \nCase No: IT 45628 \n"
    "In the matter between: \n \nTAXPAYER LE (PTY) LTD APPELLANT \nand  \n"
    "THE COMMISSIONER FOR THE RESPONDENT \nSOUTH AFRICAN REVENUE SERVICE \n \nJUDGMENT  \n"
    "MOKOENA AJ: \n"
    "[1] Although Taxpayer LE instituted the appeal, it proceeded by default, as it closed\n"
    "its case without leading evidence.\n"
)
HEADER_ONLY = (
    "THE COMPETITION APPEAL COURT OF SOUTH AFRICA \nCASE NO: 212/CAC/Apr 23 \n"
    "In the matter between: \nSASOL GAS (PTY) LTD Applicant \nand \nTHE COMPETITION COMMISSION Respondent \n"
    "Coram: Davis JP \nHeard: 14 November 2023 \nDelivered: 5 March 2024 \n"
)
ORDER = (
    "THE LAND COURT \nCase no: LCC 16/2020 \nORDER \n"
    "1. The late filing of the amended notice of motion is condoned; 2. Each party pays its own costs.\n"
    "[1] The applicant is the Bakgatla Ba Kgafela Communal Property Association, which owns the farm.\n"
)


def test_header_lines_are_never_quotes():
    for line in (
        "THE COMPETITION APPEAL COURT",
        "CASE NO: 212/CAC/Apr 23",
        "212CACApr23 March 2024",
        "In the matter between:",
        "SASOL GAS (PTY) LTD",
        "First Respondent",
        "Coram: Davis, S Manoim and Nuku JJA",
        "Heard: 14 November 2023",
        "5 March 2024",
        "EMEDIA INVESTMENTS PROPRIETARY LIMITED  Appellant",
        "2",
    ):
        assert _header_line(line), line


def test_quote_is_the_first_whole_sentence_of_the_judgment():
    quote = _verbatim_quote(CAC)
    assert quote == (
        "The applicant seeks to review and set aside two decisions of the Commission "
        "taken in terms of section 49B of the Act."
    )
    assert quote in library_passage(CAC)
    for header in ("COMPETITION APPEAL COURT", "CASE NO", "212/CAC", "Sasol", "SASOL", "March 2024", "Respondent"):
        assert header not in quote


def test_judge_opening_and_caption_are_skipped():
    quote = _verbatim_quote(TAX)
    assert quote.startswith("Although Taxpayer LE instituted the appeal")
    assert "MOKOENA" not in quote and "COMMISSIONER" not in quote


def test_header_only_text_gives_no_quote_so_the_case_is_left_out():
    assert _verbatim_quote(HEADER_ONLY) == ""


def test_numbered_order_items_are_not_cut_mid_list():
    quote = _verbatim_quote(ORDER)
    assert quote
    assert not quote.rstrip(".").endswith(("; 2", " 2"))


def test_passage_is_the_judgment_body_folded():
    passage = library_passage(CAC)
    assert passage.startswith("[1] The applicant seeks")
    assert "In the matter between" not in passage
    assert "\n" not in passage


def test_library_rows_carry_body_passage_and_body_quote():
    class Conn:
        async def fetch(self, sql, *args):
            assert "left(text, 1200)" not in sql
            return [
                {"citation": "CAC 212", "title": "Sasol Gas (Pty) Ltd v The Competition Commission", "text": CAC},
                {"citation": "CAC 213", "title": "Header only", "text": HEADER_ONLY},
            ]

    documents = asyncio.run(selftest.load_case_library(Conn(), region="za"))
    assert [item["citation"] for item in documents] == ["CAC 212"]
    item = documents[0]
    assert item["quote"] in item["text"]
    assert item["text"].startswith("[1] The applicant")
    assert selftest._library_documents(documents)[0]["quote"] == item["quote"]
