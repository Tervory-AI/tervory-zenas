"""Regulations published in the Government Gazette, from the gov.za notices index.

Only rows that are regulations or rules are followed. The notice is stored as
published. Where it amends rules, the amendment is recorded in the amending
words. No consolidated regulations are written.

The same polite gov.za client as the Acts adapter is used, and the same rule:
a US box does not fetch.
"""

from __future__ import annotations

from agents.zenas.pool.adapters.za_govza import Report, parse_payload, walk_index
from agents.zenas.pool.model import LegalDocument, SourceSpec

NOTICES_INDEX = "https://www.gov.za/documents/notices"


class GovzaRegulationsAdapter:
    source_id = "govza-regulations"

    def __init__(self) -> None:
        self.report = Report()

    def feeds_detail(self) -> str:
        return self.report.detail()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get)
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        self.report = Report()
        return await walk_index(
            http_get,
            index_url=NOTICES_INDEX,
            tag="govza-regulation",
            kind="regulation",
            resume=resume,
            report=self.report,
            max_pages_env="GOVZA_REG_MAX_PAGES",
            max_pages_default=10,
            max_files_env="GOVZA_REG_MAX_FILES",
            max_files_default=20,
        )

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_payload(blob, source, kind="regulation", report=self.report)
