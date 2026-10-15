"""High Court judgments from the Office of the Chief Justice site.

Johannesburg/Gauteng, the Free State, and the Eastern Cape publish PDF
listings. Western Cape and KwaZulu-Natal are nearly empty on this site,
and Mpumalanga, North West, Limpopo and the Northern Cape do not have a
populated judgments archive here. Those divisions are not requested.

Eastern Cape uses `courtjudgments` with no hyphen. The file link is Phoca's
`?download=` query, which returns `application/pdf`.
"""

from __future__ import annotations

from agents.zenas.pool.adapters.za_case_common import (
    after_cursor,
    case_pause,
    max_docs,
    neutral_citation,
    parse_case_blob,
    pdfs_to_cases,
    phoca_downloads,
)
from agents.zenas.pool.adapters.za_common import html_headers, reject_credential_url
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
)


class OcjHighCourtAdapter:
    source_id = "ocj-highcourt"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        urls: list[str] = []
        bodies: dict[str, str] = {}
        for body, page_url in DIVISIONS:
            reject_credential_url(page_url)
            page = await http_get(page_url, html_headers())
            await case_pause("OCJ_MIN_INTERVAL")
            for url in phoca_downloads(page, page_url):
                if url not in bodies:
                    urls.append(url)
                    bodies[url] = body
        pending = after_cursor(urls, resume)[: max_docs()]
        blobs: list[bytes] = []
        for body in {bodies[url] for url in pending}:
            group = [url for url in pending if bodies[url] == body]
            blobs.extend(
                await pdfs_to_cases(
                    http_get,
                    group,
                    body=body,
                    interval_env="OCJ_MIN_INTERVAL",
                    citation_for=lambda url, text: neutral_citation(text),
                )
            )
        return blobs, (pending[-1] if pending else resume)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
