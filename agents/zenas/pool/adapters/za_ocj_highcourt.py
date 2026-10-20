"""High Court judgments from the Office of the Chief Justice site.

Gauteng, the Free State, and the Eastern Cape publish multi-page Phoca
lists. KwaZulu-Natal and the Western Cape publish a handful of files and
are walked too. Limpopo, Mpumalanga, North West, and the Northern Cape
return an empty judgments page, so they are not requested.

Eastern Cape uses `courtjudgments` with no hyphen. The file link is Phoca's
`?download=` query, which returns `application/pdf`. Pages step `?start=` by 20.
"""

from __future__ import annotations

from agents.zenas.pool.adapters.za_archive import ArchiveRun, accept_pdf, paged_listings
from agents.zenas.pool.adapters.za_case_common import (
    neutral_citation,
    parse_case_blob,
    phoca_downloads,
)
from agents.zenas.pool.adapters.za_common import html_headers
from agents.zenas.pool.model import LegalDocument, SourceSpec

HOST = "https://www.judiciary.org.za"
DIVISIONS: tuple[tuple[str, str], ...] = (
    (
        "Gauteng Division of the High Court",
        f"{HOST}/index.php/courts/high-courts/gauteng-division-of-the-high-court/court-judgments",
    ),
    (
        "Free State Division of the High Court",
        f"{HOST}/index.php/courts/high-courts/free-state-division-of-the-high-court/court-judgments",
    ),
    (
        "Eastern Cape Division of the High Court",
        f"{HOST}/index.php/courts/high-courts/eastern-cape-division-of-the-high-court/courtjudgments",
    ),
    (
        "KwaZulu-Natal Division of the High Court",
        f"{HOST}/index.php/courts/high-courts/kwazulu-natal-division-of-the-high-court/court-judgments",
    ),
    (
        "Western Cape Division of the High Court",
        f"{HOST}/index.php/courts/high-courts/western-cape-division-of-the-high-court/court-judgments",
    ),
)


class OcjHighCourtAdapter:
    source_id = "ocj-highcourt"

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
        blobs: list[bytes] = []
        order = [page_url for _body, page_url in DIVISIONS]
        for body, page_url in DIVISIONS:
            if run.stopped or run.hold:
                break
            if run.behind(order, page_url, "0", "end"):
                continue
            async for page in paged_listings(
                run,
                http_get,
                page_url,
                scope=page_url,
                year="0",
                scope_order=order,
                front_year="0",
                interval_env="OCJ_MIN_INTERVAL",
                headers=html_headers(),
            ):
                for url in phoca_downloads(page, page_url):
                    payload = await accept_pdf(
                        run,
                        http_get,
                        url,
                        title="",
                        body=body,
                        interval_env="OCJ_MIN_INTERVAL",
                        headers=html_headers(),
                        citation_for=lambda file_url, text: neutral_citation(text),
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
