"""Constitutional Court judgments from the official DSpace repository.

Discovery is OAI-PMH on the Latest Judgments set. The PDF is fetched only from
`/server/api/core/bitstreams/{uuid}/content`. `/download` returns HTML, and
`dc.link.judgment` is often a stale id, so neither is used.

robots.txt disallows `/discover`, `/search`, `/browse` and `/admin`. Those
paths are refused before a request. The commented crawl-delay is 10 seconds;
the default interval matches that.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from xml.etree import ElementTree as ET

from urllib.parse import quote

from agents.zenas.pool.adapters.za_archive import (
    ArchiveRun,
    ChallengePage,
    archive_max,
    hand_over,
    is_challenge_page,
    log_challenge,
    polite_get,
)
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


def year_sets(blob: bytes) -> list[tuple[int, str]]:
    """Year collections only. Rolls, hearings, and other sets are left out."""
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return []
    found: dict[int, str] = {}
    for element in root.iter():
        if _local(element.tag) != "set":
            continue
        spec = ""
        name = ""
        for child in list(element):
            kind = _local(child.tag)
            if kind == "setspec":
                spec = " ".join((child.text or "").split())
            elif kind == "setname":
                name = " ".join((child.text or "").split())
        if spec and re.fullmatch(r"(?:19|20)\d{2}", name):
            found[int(name)] = spec
    return [(year, found[year]) for year in sorted(found)]


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

    def __init__(self) -> None:
        self.run = ArchiveRun("")

    def load_note(self) -> dict:
        return self.run.load_note()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        """Every year collection, then Latest Judgments.

        CONCOURT_MAX_ITEMS still bounds a run when set. Unset, the walk follows
        each set's resumption token and stops at ZENAS_ZA_ARCHIVE_MAX.
        A ListSets response with no year collections falls back to Latest
        Judgments, which is what a short test double returns.
        """
        self.run = ArchiveRun(resume)
        run = self.run
        limit = env_int("CONCOURT_MAX_ITEMS", 0)
        sets, challenged = await _list_year_sets(http_get)
        if challenged:
            run.skip("challenge page")
            run.hold = True
            return [], run.cursor()
        if sets:
            walk = [(str(year), spec, 40) for year, spec in sets]
            walk.append(("0", OAI_SET, 20))
            highest = max(year for year, _spec in sets)
        else:
            walk = [("0", OAI_SET, 4)]
            highest = 0
        order = [spec for _year, spec, _pages in walk]
        blobs: list[bytes] = []
        considered = 0
        for year, spec, max_pages in walk:
            if run.stopped or run.hold:
                break
            if run.resume_done and spec != OAI_SET and int(year or 0) <= int(run.resume_year or 0):
                continue
            if run.behind(order, spec, year, "end"):
                continue
            token = ""
            if run.resume_scope == spec and run.resume_page not in {"", "0", "end"}:
                token = run.resume_page
            if run.resume_scope == spec and run.resume_page == "end" and not run.resume_done:
                continue
            seen_tokens: set[str] = set()
            for _page in range(max_pages):
                if run.stopped or run.hold:
                    break
                page_label = token or "0"
                run.place(spec, year, page_label)
                if int(year or 0):
                    run.note_year(int(year))
                rows, token, failed = await _oai_page(http_get, spec, token)
                if failed == "challenge":
                    run.skip("challenge page")
                    run.hold = True
                    break
                if failed == "error":
                    run.hold = True
                    break
                for row in rows:
                    handle_url = f"https://collections.concourt.org.za/handle/{row['handle']}"
                    if run.already(handle_url):
                        run.skip("unchanged")
                        continue
                    if limit and considered >= limit:
                        run.stopped = True
                        break
                    considered += 1
                    payload = await _consider(run, http_get, row)
                    if payload:
                        blobs.append(payload)
                    if run.stopped:
                        break
                if run.stopped or run.hold or not token or token in seen_tokens:
                    if not token and not run.hold and not run.stopped:
                        run.place(spec, year, "end")
                    break
                seen_tokens.add(token)
        if not run.stopped and not run.hold:
            run.place(OAI_SET, str(highest), "0")
            run.finish()
        return blobs, run.cursor()

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)


async def _consider(run: ArchiveRun, http_get, row: dict) -> bytes:
    handle_url = f"https://collections.concourt.org.za/handle/{row['handle']}"
    if run.already(handle_url):
        run.skip("unchanged")
        return b""
    if is_criminal_caption(row["title"]):
        run.skip("criminal")
        run.mark(handle_url)
        return b""
    if run.downloaded >= archive_max():
        run.stopped = True
        return b""
    run.downloaded += 1
    try:
        payload = await _one_item(http_get, row)
    except ChallengePage:
        run.skip("challenge page")
        run.mark(handle_url)
        return b""
    except OSError:
        run.downloaded -= 1
        return b""
    if not payload:
        run.skip("not a judgment")
        run.mark(handle_url)
        return b""
    match = re.search(r"\[((?:19|20)\d{2})\]", str(row.get("citation") or ""))
    if match:
        run.note_year(int(match.group(1)))
    return await hand_over(run, handle_url, payload)


async def _list_year_sets(http_get) -> tuple[list[tuple[int, str]], bool]:
    found: dict[int, str] = {}
    token = ""
    for _page in range(5):
        url = f"{OAI}?verb=ListSets" if not token else f"{OAI}?verb=ListSets&resumptionToken={quote(token, safe='')}"
        assert_allowed(url)
        got = await polite_get(http_get, url, xml_headers(), "CONCOURT_MIN_INTERVAL", pause_default="10")
        if got.challenge:
            return [], True
        if got.error or not got.body:
            return [], False
        for year, spec in year_sets(got.body):
            found[year] = spec
        _rows, token = records_from_oai(got.body)
        if not token:
            break
    return [(year, found[year]) for year in sorted(found)], False


async def _oai_page(http_get, set_spec: str, token: str) -> tuple[list[dict], str, str]:
    """One ListRecords page. The third value is '', 'challenge', or 'error'."""
    if token:
        url = f"{OAI}?verb=ListRecords&resumptionToken={quote(token, safe='')}"
    else:
        url = f"{OAI}?verb=ListRecords&metadataPrefix=oai_dc&set={set_spec}"
    assert_allowed(url)
    got = await polite_get(http_get, url, xml_headers(), "CONCOURT_MIN_INTERVAL", pause_default="10")
    if got.challenge:
        return [], token, "challenge"
    if got.error:
        return [], token, "error"
    rows, next_token = records_from_oai(got.body)
    return rows, next_token, ""


async def _list_latest(http_get) -> list[dict]:
    """Latest Judgments via OAI. Kept for callers that want one set."""
    found: dict[str, dict] = {}
    token = ""
    for _page in range(4):
        rows, token, failed = await _oai_page(http_get, OAI_SET, token)
        if failed:
            break
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
    if is_challenge_page(pdf):
        log_challenge(content)
        raise ChallengePage()
    text = await asyncio.to_thread(pdf_text, pdf)  # CPU-bound; keep the room answering
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
