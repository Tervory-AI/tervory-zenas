"""Constitutional Court judgments from the official DSpace repository.

Discovery is OAI-PMH on the Latest Judgments set. The PDF is fetched only from
`/server/api/core/bitstreams/{uuid}/content`. `/download` returns HTML, and
`dc.link.judgment` is often a stale id, so neither is used.

robots.txt disallows `/discover`, `/search`, `/browse` and `/admin`. Those
paths are refused before a request. The commented crawl-delay is 10 seconds;
the default interval matches that.
"""

from __future__ import annotations

import html
import json
import re
from xml.etree import ElementTree as ET

from agents.zenas.pool.adapters.za_case_common import (
    case_bytes,
    env_int,
    is_criminal_caption,
    parse_case_blob,
    parse_date,
    pdf_text,
)
from agents.zenas.pool.adapters.za_common import (
    json_headers,
    pause,
    pdf_headers,
    reject_credential_url,
    xml_headers,
)
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, SourceSpec

OAI = "https://collections.concourt.org.za/server/oai/request"
API = "https://collections.concourt.org.za/server/api"
# Latest Judgments. Recorded so a reader can see which collection this wave uses.
# Discovery search on this UUID is a robots path, so the adapter does not call it.
COLLECTION_UUID = "4863ab62-58eb-45c0-b173-ba9adc4a29fe"
OAI_SET = "col_20.500.12144_1949"
_HANDLE = re.compile(r"(20\.500\.12144/\d+)")
_FORBIDDEN = ("/discover", "/search", "/browse", "/admin")


def assert_allowed(url: str) -> None:
    reject_credential_url(url)
    path = url.split("?", 1)[0].lower()
    for piece in _FORBIDDEN:
        if piece in path:
            raise FetchNotConfigured(f"refusing a ConCourt path robots.txt disallows: {path}")
    if path.endswith("/download"):
        raise FetchNotConfigured("ConCourt /download returns HTML; use /bitstreams/{uuid}/content")


def bitstream_content_url(href: str) -> str:
    """Content URL for one bitstream. A `/download` href is rewritten, not requested."""
    url = (href or "").split("?", 1)[0].rstrip("/")
    if url.endswith("/download"):
        url = url[: -len("/download")] + "/content"
    marker = "/server/api/core/bitstreams/"
    if marker not in url or not url.endswith("/content"):
        return ""
    return url


def choose_judgment_name(names: list[str]) -> str:
    """ORIGINAL PDF of the judgment. Media summaries and argument bundles are skipped."""
    ranked: list[tuple[int, str]] = []
    for name in names:
        lower = name.lower()
        if not lower.endswith(".pdf"):
            continue
        if "media summary" in lower or "mediasummary" in lower:
            continue
        if lower.startswith("[final judgment]") or "final judgment" in lower:
            ranked.append((0, name))
        elif "judgment" in lower or "judgement" in lower:
            ranked.append((1, name))
    if not ranked:
        return ""
    ranked.sort(key=lambda item: (item[0], item[1].lower()))
    return ranked[0][1]


def choose_text_fallback(names: list[str], judgment_pdf: str) -> str:
    """TEXT bundle `.pdf.txt` for that judgment, used only when the PDF has no text."""
    stem = judgment_pdf.lower().removesuffix(".pdf")
    for name in names:
        lower = name.lower()
        if not lower.endswith(".pdf.txt"):
            continue
        if "media summary" in lower:
            continue
        if stem and stem in lower:
            return name
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def records_from_oai(blob: bytes) -> tuple[list[dict], str]:
    """One OAI page. Deleted records are omitted. The resumption token may be empty."""
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return [], ""
    token = ""
    records: list[dict] = []
    for element in root.iter():
        if _local(element.tag) == "resumptiontoken" and (element.text or "").strip():
            token = element.text.strip()
        if _local(element.tag) != "record":
            continue
        header = next((child for child in list(element) if _local(child.tag) == "header"), None)
        if header is None or header.attrib.get("status") == "deleted":
            continue
        titles: list[str] = []
        identifiers: list[str] = []
        dates: list[str] = []
        for node in element.iter():
            text = html.unescape(" ".join((node.text or "").split()))
            if not text:
                continue
            name = _local(node.tag)
            if name == "title":
                titles.append(text)
            elif name == "identifier":
                identifiers.append(text)
            elif name == "date":
                dates.append(text)
        handle = ""
        citation = ""
        for identifier in identifiers:
            found = _HANDLE.search(identifier)
            if found and not handle:
                handle = found.group(1)
            if "[" in identifier and not citation:
                from agents.zenas.pool.adapters.za_case_common import neutral_citation

                citation = neutral_citation(identifier)
        title = titles[0] if titles else ""
        when = None
        for piece in reversed(dates):
            when = parse_date(piece)
            if when:
                break
        if handle:
            records.append(
                {"handle": handle, "title": title, "citation": citation, "date": when.isoformat() if when else ""}
            )
    return records, token


def _values(item: dict, key: str) -> list[str]:
    rows = (item.get("metadata") or {}).get(key) or []
    found: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            text = " ".join(str(row.get("value") or "").split())
            if text:
                found.append(html.unescape(text))
    return found


def describe_item(item: dict) -> dict:
    from agents.zenas.pool.adapters.za_case_common import neutral_citation

    citation = ""
    for value in _values(item, "dc.identifier.citation"):
        citation = neutral_citation(value)
        if citation:
            break
    title = (_values(item, "dc.title") or [""])[0]
    when = ""
    for value in _values(item, "dc.date.judgment"):
        parsed = parse_date(value)
        if parsed:
            when = parsed.isoformat()
            break
    handle = str(item.get("handle") or "")
    return {"citation": citation, "title": title, "date": when, "handle": handle, "uuid": str(item.get("uuid") or "")}


def _embedded(payload: dict, key: str) -> list[dict]:
    rows = (payload.get("_embedded") or {}).get(key) or []
    return [row for row in rows if isinstance(row, dict)]


class ConcourtAdapter:
    source_id = "concourt"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        limit = env_int("CONCOURT_MAX_ITEMS", 5)
        records = await _list_latest(http_get)
        pending = [row for row in records if row["handle"] > resume] if resume else records
        pending = pending[:limit]
        blobs: list[bytes] = []
        last = resume
        for row in pending:
            last = row["handle"]
            if is_criminal_caption(row["title"]):
                continue
            payload = await _one_item(http_get, row)
            if payload:
                blobs.append(payload)
        return blobs, last

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)


async def _list_latest(http_get) -> list[dict]:
    """Latest Judgments via OAI. A few resumption pages, then stop."""
    found: dict[str, dict] = {}
    token = ""
    verb = f"{OAI}?verb=ListRecords&metadataPrefix=oai_dc&set={OAI_SET}"
    for _page in range(4):
        url = verb if not token else f"{OAI}?verb=ListRecords&resumptionToken={token}"
        assert_allowed(url)
        page = await http_get(url, xml_headers())
        await pause("CONCOURT_MIN_INTERVAL", "10")
        rows, token = records_from_oai(page)
        for row in rows:
            found[row["handle"]] = row
        if not token:
            break
    return [found[key] for key in sorted(found)]


async def _one_item(http_get, row: dict) -> bytes:
    item_url = f"{API}/pid/find?id={row['handle']}"
    assert_allowed(item_url)
    item = _json(await http_get(item_url, json_headers()))
    await pause("CONCOURT_MIN_INTERVAL", "10")
    described = describe_item(item) if item else {}
    citation = described.get("citation") or row["citation"]
    title = described.get("title") or row["title"]
    when = parse_date(described.get("date") or "") or parse_date(row.get("date") or "")
    uuid = described.get("uuid") or str(item.get("uuid") or "")
    if not citation or not uuid or is_criminal_caption(title):
        return b""
    names, content_by_name = await _bitstreams(http_get, uuid)
    judgment = choose_judgment_name(names)
    if not judgment:
        return b""
    content = content_by_name.get(judgment) or ""
    content = bitstream_content_url(content)
    if not content:
        return b""
    assert_allowed(content)
    pdf = await http_get(content, pdf_headers())
    await pause("CONCOURT_MIN_INTERVAL", "10")
    text = pdf_text(pdf)
    if not text:
        fallback = choose_text_fallback(names, judgment)
        text_url = bitstream_content_url(content_by_name.get(fallback) or "")
        if text_url:
            assert_allowed(text_url)
            raw = await http_get(text_url, pdf_headers())
            await pause("CONCOURT_MIN_INTERVAL", "10")
            text = raw.decode("utf-8", errors="replace").strip()
    page = f"https://collections.concourt.org.za/handle/{row['handle']}"
    return case_bytes(
        citation=citation,
        title=title or citation,
        body="Constitutional Court",
        url=page,
        text=text,
        doc_date=when,
    )


async def _bitstreams(http_get, uuid: str) -> tuple[list[str], dict[str, str]]:
    bundles_url = f"{API}/core/items/{uuid}/bundles?size=20"
    assert_allowed(bundles_url)
    bundles = _json(await http_get(bundles_url, json_headers()))
    await pause("CONCOURT_MIN_INTERVAL", "10")
    names: list[str] = []
    content_by_name: dict[str, str] = {}
    for bundle in _embedded(bundles, "bundles"):
        bundle_name = str(bundle.get("name") or "")
        if bundle_name not in {"ORIGINAL", "TEXT"}:
            continue
        link = ((bundle.get("_links") or {}).get("bitstreams") or {}).get("href") or ""
        if not link:
            continue
        page_url = link if "size=" in link else link + ("&" if "?" in link else "?") + "size=100"
        assert_allowed(page_url)
        listing = _json(await http_get(page_url, json_headers()))
        await pause("CONCOURT_MIN_INTERVAL", "10")
        for bitstream in _embedded(listing, "bitstreams"):
            name = str(bitstream.get("name") or "")
            href = ((bitstream.get("_links") or {}).get("content") or {}).get("href") or ""
            if name:
                names.append(name)
                content_by_name[name] = href
    return names, content_by_name


def _json(blob: bytes) -> dict:
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
