"""Labour Court and Labour Appeal Court judgments on judiciary.org.za.

The index links to year folders. The 2025 Labour Court folder and the Labour
Appeal Court year folders are live. Each folder is a Phoca `?download=` list
of PDFs. Court rolls are not collected.
"""

from __future__ import annotations

import re

from agents.zenas.pool.adapters.za_archive import (
    ArchiveRun,
    accept_pdf,
    limit_years,
    paged_listings,
    polite_get,
)
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    env_int,
    court_citation,
    parse_case_blob,
    phoca_downloads,
)
from agents.zenas.pool.adapters.za_common import html_headers
from agents.zenas.pool.model import LegalDocument, SourceSpec

INDEX = (
    "https://www.judiciary.org.za/index.php/courts/specialised-courts/"
    "labour-and-labour-appeal-court/court-judgments"
)
LC_MARKER = "/judgements-labour-court/"
LAC_MARKER = "/judgements-labour-court-2/"


def year_folders(page: bytes, marker: str, base: str = INDEX) -> list[str]:
    """Year folder URLs for one court, newest first."""
    text = page.decode("utf-8", errors="replace")
    found: dict[str, int] = {}
    for href in re.findall(r"""href=["']([^"']+)["']""", text, re.I):
        url = absolute_url(base, href).split("?", 1)[0].rstrip("/")
        if marker not in url or "court-rolls" in url:
            continue
        match = re.search(r"(20\d{2})", url)
        if not match:
            continue
        found[url] = int(match.group(1))
    return [url for url, _year in sorted(found.items(), key=lambda item: (-item[1], item[0]))]


def undated_folders(page: bytes, marker: str, base: str = INDEX) -> list[str]:
    """Folders under the court that publish no year in the URL. Dated folders stay in year_folders."""
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r"""href=["']([^"']+)["']""", text, re.I):
        if "download=" in href or "court-roll" in href.lower():
            continue
        url = absolute_url(base, href).split("?", 1)[0].rstrip("/")
        if marker not in url or re.search(r"(20\d{2})", url):
            continue
        if url in seen or url.rstrip("/") == base.split("?", 1)[0].rstrip("/"):
            continue
        seen.add(url)
        found.append(url)
    return found


def _folder_year(url: str) -> int:
    match = re.search(r"(20\d{2})", url)
    return int(match.group(1)) if match else 0


class _LabourFamily:
    source_id = ""
    marker = ""
    body = ""
    codes: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.run = ArchiveRun("")

    def load_note(self) -> dict:
        return self.run.load_note()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        self.run = ArchiveRun(resume)
        run = self.run
        got = await polite_get(http_get, INDEX, html_headers(), "LABOUR_MIN_INTERVAL")
        blobs: list[bytes] = []
        if got.challenge:
            run.skip("challenge page")
            run.hold = True
            return [], run.cursor()
        if got.error:
            run.hold = True
            return [], run.cursor()
        cap = env_int("LABOUR_MAX_YEARS", 0)
        folders = limit_years(year_folders(got.body, self.marker), cap, _folder_year)
        for extra in undated_folders(got.body, self.marker):
            if extra not in folders:
                folders.append(extra)
        order = folders
        front = ""
        dated = [url for url in folders if _folder_year(url)]
        if dated:
            front_url = max(dated, key=_folder_year)
            front = str(_folder_year(front_url))
        for folder in folders:
            if run.stopped or run.hold:
                break
            year = str(_folder_year(folder) or 0)
            if run.behind(order, folder, year, "end"):
                continue
            async for page in paged_listings(
                run,
                http_get,
                folder,
                scope=folder,
                year=year,
                scope_order=order,
                front_year=front or year,
                interval_env="LABOUR_MIN_INTERVAL",
                headers=html_headers(),
            ):
                for url in phoca_downloads(page, folder):
                    if "directive" in url.lower():
                        if not run.already(url):
                            run.skip("not a judgment")
                            run.mark(url)
                        continue
                    payload = await accept_pdf(
                        run,
                        http_get,
                        url,
                        title="",
                        body=self.body,
                        interval_env="LABOUR_MIN_INTERVAL",
                        headers=html_headers(),
                        citation_for=lambda file_url, text: court_citation(file_url, text, self.codes, self.body),
                    )
                    if payload:
                        blobs.append(payload)
                    if run.stopped:
                        break
                if run.stopped:
                    break
        run.finish()
        return blobs, run.cursor()

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)


class LabourCourtAdapter(_LabourFamily):
    source_id = "labour-court"
    marker = LC_MARKER
    body = "Labour Court"
    codes = ("ZALCJHB", "ZALCCT", "ZALCD", "ZALCPE", "ZALCBF")


class LabourAppealAdapter(_LabourFamily):
    source_id = "labour-appeal"
    marker = LAC_MARKER
    body = "Labour Appeal Court"
    codes = ("ZALAC",)
