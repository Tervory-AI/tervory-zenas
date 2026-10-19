"""UNCITRAL convention and model-law text from the official public ebooks.

The landing pages do not carry the articles. The ebooks do. Text is stored
one article at a time. The PDF itself is not the record.
"""

from __future__ import annotations

import io
import re

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.http import USER_AGENT
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

CISG_URL = "https://uncitral.un.org/sites/default/files/media-documents/uncitral/en/19-09951_e_ebook.pdf"
MODEL_URL = "https://uncitral.un.org/sites/default/files/media-documents/uncitral/en/19-09955_e_ebook.pdf"
_ARTICLE = re.compile(r"(?im)^[ \t]*article\s+(\d+)\b[^\n]*")
_MAX_PAGES = 80


def _headers() -> dict[str, str]:
    return {"Accept": "application/pdf", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}


def ebook_text(blob: bytes) -> str:
    """Text of an official ebook. A file that is not a PDF yields nothing."""
    if not blob.startswith(b"%PDF"):
        return ""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(blob))
        parts: list[str] = []
        for index, page in enumerate(reader.pages):
            if index >= _MAX_PAGES:
                break
            parts.append(page.extract_text() or "")
    except (PdfReadError, OSError, ValueError, KeyError, TypeError):
        return ""
    return "\n".join(part for part in parts if part).strip()


def articles(text: str, source: SourceSpec, *, prefix: str, url: str, body: str) -> list[LegalDocument]:
    matches = list(_ARTICLE.finditer(text))
    documents: list[LegalDocument] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        number = match.group(1)
        if number in seen:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = " ".join(text[match.start() : end].split())
        if len(chunk) < 40:
            continue
        seen.add(number)
        citation = f"{prefix} art. {number}"
        heading = " ".join(match.group(0).split())
        documents.append(
            build_document(
                source,
                citation=citation,
                title=heading or citation,
                level="international",
                kind="treaty",
                body=body,
                sections=[Section(number, heading, chunk, [])],
                url=url,
                status="in_force",
                doc_date=None,
                effective_from=None,
                text=chunk,
            )
        )
    return documents


class _EbookAdapter:
    source_id = ""
    url = ""
    prefix = ""
    body_name = "UNCITRAL"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get)
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        return [await http_get(self.url, _headers())], ""

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        text = ebook_text(blob)
        if not text:
            return []
        return articles(text, source, prefix=self.prefix, url=self.url, body=self.body_name)


class CisgAdapter(_EbookAdapter):
    source_id = "uncitral-cisg"
    url = CISG_URL
    prefix = "CISG"
    body_name = "UNCITRAL"


class ModelLawAdapter(_EbookAdapter):
    source_id = "uncitral-model-law"
    url = MODEL_URL
    prefix = "UNCITRAL Model Law"
    body_name = "UNCITRAL"
