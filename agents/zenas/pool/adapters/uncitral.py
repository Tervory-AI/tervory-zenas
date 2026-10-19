"""UNCITRAL convention and model-law text from the official public ebooks.

The landing pages do not carry the articles. The ebooks do. Text is stored
one article at a time. The PDF itself is not the record.

The pool drops a PDF before parse, so a fetch that returns the ebook bytes
stores nothing. Fetch extracts the article text and returns that. A saved
PDF still parses.

An HTML answer (the text's landing page, or a page the CDN serves instead of
the file) is not article text. Fetch follows the ebook link on such a page.
When no ebook text can be had, fetch raises with the reason, so the run is not
recorded as "ok, inserted 0".
"""

from __future__ import annotations

import io
import re

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.http import USER_AGENT
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

CISG_URL = "https://uncitral.un.org/sites/default/files/media-documents/uncitral/en/19-09951_e_ebook.pdf"
MODEL_URL = "https://uncitral.un.org/sites/default/files/media-documents/uncitral/en/19-09955_e_ebook.pdf"
_ARTICLE = re.compile(r"(?im)^[ \t]*article\s+(\d+)\b[^\n]*")
_MAX_PAGES = 80
_PDF_HREF = re.compile(r"""href=["'](https://uncitral\.un\.org/[^"']+_ebook\.pdf)["']""", re.I)
_HOST = "https://uncitral.un.org/"


def is_html(blob: bytes) -> bool:
    head = blob[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<html" in head


def ebook_links(page: bytes) -> list[str]:
    """Ebook PDF links on an UNCITRAL page, on the official host only."""
    text = page.decode("utf-8", "replace")
    links: list[str] = []
    for match in _PDF_HREF.finditer(text):
        url = match.group(1)
        if url.startswith(_HOST) and url not in links:
            links.append(url)
    return links


def _describe(blob: bytes) -> str:
    if not blob.strip():
        return "an empty response"
    if blob.startswith(b"%PDF"):
        return "a PDF with no extractable text"
    if is_html(blob):
        title = re.search(rb"(?is)<title[^>]*>(.*?)</title>", blob)
        name = " ".join(title.group(1).decode("utf-8", "replace").split())[:80] if title else ""
        return f'an HTML page ("{name}")' if name else "an HTML page"
    return "a response that is not the ebook"


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


def articles_found(text: str) -> bool:
    return bool(_ARTICLE.search(text or ""))


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
        raw = await http_get(self.url, _headers())
        text = ebook_text(raw)
        if not text and is_html(raw):
            # A landing page or a CDN page came back. Follow the ebook link,
            # this text's own file first.
            links = ebook_links(raw)
            name = self.url.rsplit("/", 1)[-1]
            links.sort(key=lambda link: link.rsplit("/", 1)[-1] != name)
            for link in links[:1]:
                raw = await http_get(link, _headers())
                text = ebook_text(raw)
        if not text:
            raise FetchNotConfigured(f"{self.source_id}: uncitral.un.org returned {_describe(raw)}, not the ebook text")
        if not articles_found(text):
            raise FetchNotConfigured(f"{self.source_id}: the ebook text has no article headings")
        return [text.encode("utf-8")], ""

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        if blob.startswith(b"%PDF"):
            text = ebook_text(blob)
        elif is_html(blob):
            # A web page is not the convention text. Nothing is stored from it.
            return []
        else:
            text = blob.decode("utf-8", "replace").strip()
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
