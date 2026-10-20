"""Shared pieces for keyless South African case adapters.

Snapshots are JSON envelopes. A live fetch turns a PDF into that envelope
before the loader sees it, because the loader does not store PDF bytes.
"""

from __future__ import annotations

import html
import io
import json
import os
import re
from datetime import date
from urllib.parse import unquote, urljoin

from agents.zenas.pool.adapters.za_common import pause, reject_credential_url
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

MAX_PDF_BYTES = 8_000_000
_NEUTRAL = re.compile(r"\[(\d{4})\]\s+(ZA[A-Z]{2,8})\s+(\d+)")
_LONG_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
# The body scan stays narrow so a commercial judgment that cites a criminal
# case in its opening is kept. Titles and file names also match "The State vs",
# a mid-title "S v", and the DPP and NPA initials.
_CRIMINAL = re.compile(
    r"(^\s*s\s+v\s+\S|\bv\s+the\s+state\b|\bv\s+s\b|\bthe\s+state\s+vs?\.?\s+\S|"
    r"\bdirector of public prosecutions\b|\bnational prosecuting authority\b|"
    r"criminal appeal|criminal trial)",
    re.I,
)
_CRIMINAL_TITLE = re.compile(
    r"(^\s*s\s+v\s+\S|(?:^|[\s\[\(])s\s+v\s+\S|\bthe\s+state\s+vs?\.?\s+\S|"
    r"\bv\s+the\s+state\b|\bv\s+s\b|\bdpp\b|\bnpa\b|"
    r"\bdirector of public prosecutions\b|\bnational prosecuting authority\b|"
    r"criminal appeal|criminal trial)",
    re.I,
)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def max_docs() -> int:
    return env_int("ZA_CASE_MAX_DOCS", 8)


async def case_pause(env_name: str) -> None:
    """Per-source interval when set, otherwise ZA_CASE_MIN_INTERVAL (default 1s)."""
    if os.environ.get(env_name, "").strip():
        await pause(env_name, "0")
        return
    await pause("ZA_CASE_MIN_INTERVAL", "1")


def absolute_url(base: str, href: str) -> str:
    return urljoin(base, html.unescape(href.strip()))


def neutral_citation(text: str) -> str:
    match = _NEUTRAL.search(text or "")
    if not match:
        return ""
    return f"[{match.group(1)}] {match.group(2)} {match.group(3)}"


def parse_date(text: str) -> date | None:
    iso = _ISO_DATE.search(text or "")
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    match = _LONG_DATE.search(text or "")
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def is_criminal_caption(title: str, text: str = "") -> bool:
    """Drop criminal matters. The check reads the caption, not the whole reasons."""
    head = title or ""
    opening = (text or "")[:600]
    return bool(_CRIMINAL_TITLE.search(head) or _CRIMINAL.search(opening))


def slug_title(url: str) -> str:
    raw = unquote(url.split("#", 1)[0])
    if "download=" in raw:
        raw = raw.split("download=", 1)[1]
        raw = raw.split(":", 1)[-1]
    else:
        raw = raw.split("?", 1)[0].rstrip("/").split("/")[-1]
    raw = re.sub(r"\.(pdf|txt)$", "", raw, flags=re.I)
    raw = raw.replace("-", " ").replace("_", " ").replace("%20", " ")
    return " ".join(raw.split())[:180]


def pdf_text(blob: bytes, *, max_pages: int = 25) -> str:
    """Text of a small PDF. A file that is not a PDF, or is too large, yields nothing."""
    if not blob.startswith(b"%PDF") or len(blob) > MAX_PDF_BYTES:
        return ""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(blob))
        parts: list[str] = []
        for index, page in enumerate(reader.pages):
            if index >= max_pages:
                break
            parts.append(page.extract_text() or "")
    except (PdfReadError, OSError, ValueError, KeyError, TypeError):
        return ""
    return "\n".join(part for part in parts if part).strip()


def case_bytes(
    *,
    citation: str,
    title: str,
    body: str,
    url: str,
    text: str,
    doc_date: date | None,
) -> bytes:
    citation = " ".join((citation or "").split())
    title = " ".join((title or "").split()) or citation
    text = (text or "").strip()
    if not citation or not text or is_criminal_caption(title, text):
        return b""
    payload = {
        "citation": citation,
        "title": title[:300],
        "body": body,
        "url": url,
        "text": text,
        "date": doc_date.isoformat() if doc_date else "",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def parse_case_blob(blob: bytes, source: SourceSpec) -> list[LegalDocument]:
    raw = blob.strip()
    if not raw or raw.startswith(b"%PDF") or raw.startswith(b"<"):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    citation = " ".join(str(data.get("citation") or "").split())
    text = str(data.get("text") or "").strip()
    title = " ".join(str(data.get("title") or "").split()) or citation
    if not citation or not text or is_criminal_caption(title, text):
        return []
    when = parse_date(str(data.get("date") or ""))
    paragraphs = [piece.strip() for piece in re.split(r"\n\s*\n", text) if piece.strip()]
    if not paragraphs:
        paragraphs = [text]
    court = " ".join(str(data.get("body") or "").split()) or "South African court"
    page = " ".join(str(data.get("url") or "").split()) or source.canonical_url
    return [
        build_document(
            source,
            citation=citation,
            title=title,
            level="national",
            kind="case",
            body=court,
            sections=[Section("", title, piece, []) for piece in paragraphs],
            url=page,
            status="published",
            doc_date=when,
            effective_from=when,
        )
    ]


def after_cursor(urls: list[str], resume: str) -> list[str]:
    ordered = list(dict.fromkeys(urls))
    if not resume:
        return ordered
    if resume in ordered:
        return ordered[ordered.index(resume) + 1 :]
    return [url for url in ordered if url > resume]


def phoca_downloads(page: bytes, page_url: str) -> list[str]:
    """Phoca Download file links. The query is `download=<id>:<slug>` and the response is a PDF."""
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"""href=["']([^"']*?download=\d+:[^"']+)["']""", text, re.I):
        url = absolute_url(page_url, match.group(1)).split("#", 1)[0]
        if "court-rolls" in url.lower() or "courtr-olls" in url.lower():
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


async def pdfs_to_cases(
    http_get,
    urls: list[str],
    *,
    body: str,
    interval_env: str,
    citation_for,
    title_for=None,
) -> list[bytes]:
    """Download PDFs and return JSON envelopes. One bad file does not stop the wave."""
    blobs: list[bytes] = []
    for url in urls:
        reject_credential_url(url)
        try:
            raw = await http_get(url, _pdf_headers())
        except OSError:
            await case_pause(interval_env)
            continue
        await case_pause(interval_env)
        text = pdf_text(raw)
        if not text:
            continue
        citation = citation_for(url, text)
        if not citation:
            continue
        title = slug_title(url)
        if title_for is not None:
            title = title_for(url, text) or title
        when = parse_date(text[:800]) or parse_date(title)
        payload = case_bytes(
            citation=citation,
            title=title or citation,
            body=body,
            url=url.split("?")[0] if "download=" not in url else url,
            text=text,
            doc_date=when,
        )
        if payload:
            blobs.append(payload)
    return blobs


def _pdf_headers() -> dict[str, str]:
    from agents.zenas.pool.adapters.za_common import pdf_headers

    return pdf_headers()


def citation_or_neutral(url: str, text: str, fallback) -> str:
    found = neutral_citation(text) or neutral_citation(unquote(url))
    if found:
        return found
    return fallback(url, text)
