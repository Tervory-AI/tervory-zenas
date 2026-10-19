"""CourtListener back catalogue from the free quarterly bulk files.

The files are public. No key is used. Each CSV is read from S3 and decompressed
while it is read. A whole bulk file is never written to disk and never held in
memory. Dockets are filtered first, then clusters, then opinions. The API is
not called from here.
"""

from __future__ import annotations

import asyncio
import bz2
import csv
import gzip
import io
import json
import lzma
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from agents.zenas.pool.adapters.cases import (
    ALLOWED_COURTS,
    is_commercial,
    is_criminal,
    opinion_payload_text,
)
from agents.zenas.pool.http import USER_AGENT
from agents.zenas.pool.licence import FetchNotConfigured

BUCKET = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/"
LIST_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
SOURCE_ID = "courtlistener"
FILE_ORDER = ("schema", "courts", "dockets", "clusters", "citations", "opinions")
REQUIRED_KINDS = frozenset({"courts", "dockets", "clusters", "opinions", "schema"})
_DATE = r"(\d{4}-\d{2}-\d{2})"
_PACKED = r"(?:\.bz2|\.gz|\.xz)?"
PATTERNS: dict[str, re.Pattern[str]] = {
    "courts": re.compile(rf"^bulk-data/courts-{_DATE}\.csv{_PACKED}$"),
    "dockets": re.compile(rf"^bulk-data/dockets-{_DATE}\.csv{_PACKED}$"),
    "clusters": re.compile(rf"^bulk-data/opinion-clusters-{_DATE}\.csv{_PACKED}$"),
    "opinions": re.compile(rf"^bulk-data/opinions-{_DATE}\.csv{_PACKED}$"),
    "citations": re.compile(rf"^bulk-data/citations-{_DATE}\.csv{_PACKED}$"),
    "schema": re.compile(rf"^bulk-data/schema-{_DATE}\.sql$"),
}
LIST_PREFIXES = (
    "bulk-data/courts-",
    "bulk-data/dockets-",
    "bulk-data/opinion-clusters-",
    "bulk-data/opinions-",
    "bulk-data/citations-",
    "bulk-data/schema-",
)
_READER_THREAD = "cl-bulk-reader"


def bulk_cap() -> int:
    """Opinions stored in one run. Default 50,000."""
    raw = os.environ.get("ZENAS_CL_BULK_MAX", "").strip()
    if not raw:
        return 50_000
    try:
        return max(0, int(raw))
    except ValueError:
        return 50_000


def be_polite() -> None:
    """Lower the calling thread. Only the file reader calls this, never the event loop."""
    if threading.current_thread() is threading.main_thread():
        return
    try:
        os.nice(19)
    except OSError:
        pass


def _reject_credential(url: str) -> None:
    lowered = url.lower()
    if "token=" in lowered or "api_key=" in lowered or "?key=" in lowered or "&key=" in lowered:
        raise FetchNotConfigured("refusing a URL that contains a credential")


def list_page_url(prefix: str, token: str = "") -> str:
    query = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
    if token:
        query["continuation-token"] = token
    return BUCKET + "?" + urllib.parse.urlencode(query)


def object_url(key: str) -> str:
    return BUCKET + urllib.parse.quote(key, safe="/")


def parse_list_page(payload: bytes) -> tuple[list[str], str]:
    """Keys on one S3 list page, and the next continuation token when there is one."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FetchNotConfigured("the bulk listing could not be read") from exc
    keys = [item.text or "" for item in root.iter(LIST_NS + "Key") if item.text]
    truncated = root.find(LIST_NS + "IsTruncated")
    token = root.find(LIST_NS + "NextContinuationToken")
    if truncated is not None and (truncated.text or "").lower() == "true" and token is not None and token.text:
        return keys, token.text
    return keys, ""


@dataclass(frozen=True)
class Snapshot:
    day: str
    files: dict[str, str]

    def when(self) -> date:
        return date.fromisoformat(self.day)


def choose_snapshot(keys: list[str]) -> Snapshot | None:
    """The newest date that has courts, dockets, clusters, opinions, and the schema."""
    found: dict[str, dict[str, str]] = {}
    for key in keys:
        for kind, pattern in PATTERNS.items():
            match = pattern.match(key)
            if match:
                found.setdefault(match.group(1), {})[kind] = key
                break
    dates = [day for day, files in found.items() if REQUIRED_KINDS <= set(files)]
    if not dates:
        return None
    day = max(dates)
    return Snapshot(day, found[day])


def split_cursor(text: str) -> tuple[str, str]:
    """Bulk cursor, then an API resume URL when one was saved beside it."""
    if "|https://" in text or "|http://" in text:
        bulk, api = text.split("|", 1)
        return bulk, api
    return text, ""


def join_cursor(bulk: str, api: str) -> str:
    if api:
        return f"{bulk}|{api}"
    return bulk


@dataclass
class BulkCursor:
    day: str
    file: str
    last_id: int = 0

    def render(self, api: str = "") -> str:
        if self.file == "done":
            bulk = f"cl-bulk:{self.day}:done"
        elif self.file == "opinions" and self.last_id:
            bulk = f"cl-bulk:{self.day}:opinions:{self.last_id}"
        else:
            bulk = f"cl-bulk:{self.day}:{self.file}"
        return join_cursor(bulk, api)


def parse_cursor(text: str) -> BulkCursor | None:
    bulk, _api = split_cursor(text.strip())
    if not bulk.startswith("cl-bulk:"):
        return None
    parts = bulk.split(":")
    if len(parts) < 3:
        return None
    day = parts[1]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    file = parts[2]
    last_id = 0
    if len(parts) >= 4 and parts[3].isdigit():
        last_id = int(parts[3])
    return BulkCursor(day, file, last_id)


def snapshot_day(text: str) -> str:
    parsed = parse_cursor(text)
    return parsed.day if parsed else ""


def snapshot_is_done(text: str) -> bool:
    parsed = parse_cursor(text)
    return bool(parsed and parsed.file == "done")


def progress_line(day: str, file: str, rows_read: int, cases_kept: int, skips: Counter[str] | dict[str, int]) -> str:
    """The feeds line: snapshot, file, rows, cases kept, skips by reason."""
    counted = skips if isinstance(skips, dict) else dict(skips)

    def n(reason: str) -> int:
        try:
            return int(counted.get(reason) or 0)
        except (TypeError, ValueError):
            return 0

    return (
        f"snapshot {day}; file {file}; rows read {rows_read}; cases kept {cases_kept}; "
        f"skipped not our court {n('not-appellate')}, criminal {n('criminal')}, "
        f"not commercial {n('not-commercial')}, no text {n('no-text')}, "
        f"no reporter citation {n('no-reporter-citation')}"
    )


def _csv_reader(text: io.TextIOBase) -> csv.reader:
    csv.field_size_limit(sys.maxsize)
    return csv.reader(text, delimiter=",", quotechar='"', escapechar="\\", doublequote=True)


def _decompress(raw: io.BufferedIOBase, name: str) -> io.BufferedIOBase:
    lowered = name.lower().split("?", 1)[0]
    if lowered.endswith(".bz2"):
        return bz2.BZ2File(raw)  # type: ignore[return-value]
    if lowered.endswith(".gz"):
        return gzip.GzipFile(fileobj=raw)  # type: ignore[return-value]
    if lowered.endswith(".xz"):
        return lzma.LZMAFile(raw)  # type: ignore[return-value]
    return raw


def iter_csv_bytes(payload: bytes, name: str) -> Iterator[list[str]]:
    """Rows of one small CSV, including the header. Used for fixture bytes."""
    stream = _decompress(io.BytesIO(payload), name)
    text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
    try:
        yield from _csv_reader(text)
    finally:
        text.close()


def _iter_http_rows(url: str, stop: threading.Event) -> Iterator[list[str]]:
    """Stream one object. Stop closes the response before the rest of the file."""
    _reject_credential(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )
    response = urllib.request.urlopen(request, timeout=120)  # noqa: S310
    stream = _decompress(response, url)
    text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
    try:
        for row in _csv_reader(text):
            if stop.is_set():
                break
            yield row
    finally:
        text.close()
        response.close()


def _iter_http_lines(url: str, stop: threading.Event) -> Iterator[str]:
    _reject_credential(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )
    response = urllib.request.urlopen(request, timeout=120)  # noqa: S310
    stream = _decompress(response, url)
    text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
    try:
        for line in text:
            if stop.is_set():
                break
            yield line
    finally:
        text.close()
        response.close()


async def _try_put(queue: asyncio.Queue, item: object) -> bool:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        return False
    return True


async def _aiter_thread(worker, stop: threading.Event) -> AsyncIterator[object]:
    """Pull rows off a thread so the room's event loop keeps answering."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    done = object()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            be_polite()
            for item in worker():
                if stop.is_set():
                    break
                placed = False
                while not stop.is_set():
                    future = asyncio.run_coroutine_threadsafe(_try_put(queue, item), loop)
                    try:
                        placed = bool(future.result(timeout=2))
                    except TimeoutError:
                        continue
                    if placed:
                        break
                    time.sleep(0.05)
                if not placed:
                    break
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            asyncio.run_coroutine_threadsafe(_try_put(queue, done), loop).result(timeout=30)

    thread = threading.Thread(target=run, name=_READER_THREAD, daemon=True)
    thread.start()
    try:
        while True:
            item = await queue.get()
            if item is done:
                break
            yield item
    finally:
        stop.set()
        while thread.is_alive():
            await asyncio.sleep(0.01)
    if errors:
        raise errors[0]


def _cell(row: dict[str, str], name: str) -> str:
    value = row.get(name)
    if value is None:
        return ""
    text = str(value).strip()
    if text == r"\N":
        return ""
    return text


def _mapping(header: list[str], row: list[str]) -> dict[str, str]:
    return {name: (row[index] if index < len(row) else "") for index, name in enumerate(header)}


def _int_id(value: str) -> int:
    text = (value or "").strip()
    if not text or not text.lstrip("-").isdigit():
        return 0
    return int(text)


def opinion_text(row: dict[str, str]) -> str:
    """Plain text, or the HTML with the tags stripped."""
    html = (
        _cell(row, "html")
        or _cell(row, "html_lawbox")
        or _cell(row, "html_columbia")
        or _cell(row, "html_anon_2020")
    )
    return opinion_payload_text(
        {
            "plain_text": _cell(row, "plain_text"),
            "html_with_citations": _cell(row, "html_with_citations"),
            "html": html,
            "xml_harvard": _cell(row, "xml_harvard"),
        }
    )


def docket_skip(row: dict[str, str]) -> str:
    """Empty when the docket stays. Otherwise not-appellate or criminal."""
    court = _cell(row, "court_id").lower()
    record = {
        "court_id": court,
        "case_name": _cell(row, "case_name"),
        "docket_number": _cell(row, "docket_number"),
        "nature_of_suit": _cell(row, "nature_of_suit"),
    }
    if court not in ALLOWED_COURTS:
        return "not-appellate"
    if is_criminal(record):
        return "criminal"
    return ""


@dataclass
class DocketKept:
    docket_id: int
    court_id: str
    docket_number: str
    nature_of_suit: str
    case_name: str


@dataclass
class ClusterKept:
    cluster_id: int
    docket_id: int
    case_name: str
    date_filed: str
    precedential_status: str
    slug: str
    citation: str = ""
    citation_type: str = ""


@dataclass
class BulkState:
    """Ids and rows kept for one snapshot. Reused when the same process resumes."""

    day: str = ""
    files_done: set[str] = field(default_factory=set)
    courts: dict[str, str] = field(default_factory=dict)
    dockets: dict[int, DocketKept] = field(default_factory=dict)
    clusters: dict[int, ClusterKept] = field(default_factory=dict)
    last_opinion_id: int = 0

    def clear(self) -> None:
        self.day = ""
        self.files_done.clear()
        self.courts.clear()
        self.dockets.clear()
        self.clusters.clear()
        self.last_opinion_id = 0


@dataclass
class BulkResult:
    blobs: list[bytes]
    cursor: str
    skips: Counter[str]
    snapshot: str
    file_label: str
    rows_read: int
    cases_kept: int


def _case_payload(docket: DocketKept, cluster: ClusterKept, text: str) -> dict:
    url = ""
    if cluster.slug:
        url = f"https://www.courtlistener.com/opinion/{cluster.cluster_id}/{cluster.slug}/"
    return {
        "court_id": docket.court_id,
        "case_name": cluster.case_name or docket.case_name,
        "date_filed": cluster.date_filed,
        "docket_number": docket.docket_number,
        "nature_of_suit": docket.nature_of_suit,
        "citations": [cluster.citation] if cluster.citation else [],
        "plain_text": text,
        "precedential_status": cluster.precedential_status or "Published",
        "absolute_url": url,
    }


def _docket_citation(docket: DocketKept, cluster: ClusterKept, courts: dict[str, str]) -> str:
    """A citation from the cluster's own docket, court, and date filed."""
    court = courts.get(docket.court_id.lower(), "")
    return f"No. {docket.docket_number} ({court} {cluster.date_filed})"


def _prefer_citation(cluster: ClusterKept, volume: str, reporter: str, page: str, cite_type: str) -> None:
    cite = " ".join(part for part in (volume.strip(), reporter.strip(), page.strip()) if part)
    if not cite:
        return
    if not cluster.citation or (cite_type == "1" and cluster.citation_type != "1"):
        cluster.citation = cite
        cluster.citation_type = cite_type


class _Progress:
    def __init__(self, conn, day: str) -> None:
        self.conn = conn
        self.day = day
        self.file = ""
        self.rows_read = 0
        self.cases_kept = 0
        self.skips: Counter[str] = Counter()
        self._since_flush = 0

    def line(self) -> str:
        return progress_line(self.day, self.file or "starting", self.rows_read, self.cases_kept, self.skips)

    async def note(self, force: bool = False) -> None:
        self._since_flush += 1
        if not force and self._since_flush < 5000:
            return
        self._since_flush = 0
        conn = self.conn
        if conn is None:
            return
        try:
            await conn.execute(
                """
                UPDATE zenas_pool.updates
                SET detail = $1
                WHERE source_id = $2 AND last_status = 'running'
                """,
                self.line(),
                SOURCE_ID,
            )
        except Exception:
            self.conn = None


async def _open_db():
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return None
    try:
        import asyncpg

        return await asyncpg.connect(dsn)
    except Exception:
        return None


# The account allows 5/min, 50/hour and 125/day. These defaults stay under that
# so a person can still look things up. Bulk reads do not use this ledger.
DAILY_BUDGET_STOP = "daily budget used, resumes tomorrow"
HOURLY_BUDGET_STOP = "hourly budget used, resumes next hour"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def api_min_interval() -> float:
    """Seconds between CourtListener API calls. Default 12."""
    raw = os.environ.get("COURTLISTENER_MIN_INTERVAL", "").strip()
    if not raw:
        return 12.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 12.0


def api_hourly_max() -> int:
    """API calls kept in any rolling hour. Default 45."""
    return _env_int("COURTLISTENER_HOURLY_MAX", 45)


def api_daily_max() -> int:
    """API calls kept in the rolling 24 hours. Default 100."""
    return _env_int("COURTLISTENER_DAILY_MAX", 100)


def _aware(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def api_daily_start(now: datetime) -> datetime:
    """24 hours before now. A call at this instant still counts."""
    return _aware(now) - timedelta(hours=24)


def api_hour_start(now: datetime) -> datetime:
    """One hour before now. Calls at this instant still count."""
    return _aware(now) - timedelta(hours=1)


class MemoryApiBudget:
    """Call times kept in this process. Tests use this as the pool table."""

    def __init__(self) -> None:
        self.calls: list[datetime] = []

    async def count_since(self, start: datetime) -> int:
        start = _aware(start)
        return sum(1 for item in self.calls if _aware(item) >= start)

    async def record(self, when: datetime) -> None:
        self.calls.append(_aware(when))

    async def aclose(self) -> None:
        return None


class PostgresApiBudget:
    """Call times in zenas_pool.cl_api_calls. A restart reads the same rows."""

    def __init__(self, conn) -> None:
        self.conn = conn

    async def count_since(self, start: datetime) -> int:
        value = await self.conn.fetchval(
            "SELECT count(*) FROM zenas_pool.cl_api_calls WHERE called_at >= $1",
            _aware(start),
        )
        return int(value or 0)

    async def record(self, when: datetime) -> None:
        await self.conn.execute(
            "INSERT INTO zenas_pool.cl_api_calls (called_at) VALUES ($1)",
            _aware(when),
        )

    async def aclose(self) -> None:
        await self.conn.close()


async def open_api_budget() -> MemoryApiBudget | PostgresApiBudget:
    """The pool table when this process has a database. Otherwise memory."""
    conn = await _open_db()
    if conn is None:
        return MemoryApiBudget()
    return PostgresApiBudget(conn)


async def api_block_reason(budget: MemoryApiBudget | PostgresApiBudget, now: datetime) -> str:
    """Empty when another API call is allowed. Daily is checked before hourly."""
    if await budget.count_since(api_daily_start(now)) >= api_daily_max():
        return DAILY_BUDGET_STOP
    if await budget.count_since(api_hour_start(now)) >= api_hourly_max():
        return HOURLY_BUDGET_STOP
    return ""


async def _flush_ids(conn, rows: list[tuple]) -> None:
    if conn is None or not rows:
        return
    try:
        await conn.executemany(
            """
            INSERT INTO zenas_pool.cl_bulk_ids (
                source_id, snapshot_date, kind, row_id, parent_id, court_id, label,
                citation, filed, precedential, slug, docket_number, nature_of_suit
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            )
            ON CONFLICT (source_id, snapshot_date, kind, row_id) DO UPDATE SET
                parent_id = EXCLUDED.parent_id,
                court_id = EXCLUDED.court_id,
                label = EXCLUDED.label,
                citation = EXCLUDED.citation,
                filed = EXCLUDED.filed,
                precedential = EXCLUDED.precedential,
                slug = EXCLUDED.slug,
                docket_number = EXCLUDED.docket_number,
                nature_of_suit = EXCLUDED.nature_of_suit
            """,
            rows,
        )
    except Exception:
        return


async def _mark_file(conn, day: str, kind: str, key: str, status: str, rows_read: int, rows_kept: int, last_id: int) -> None:
    if conn is None:
        return
    try:
        await conn.execute(
            """
            INSERT INTO zenas_pool.cl_bulk_files (
                source_id, snapshot_date, file_kind, object_name, status,
                rows_read, rows_kept, last_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (source_id, snapshot_date, file_kind) DO UPDATE SET
                object_name = EXCLUDED.object_name,
                status = EXCLUDED.status,
                rows_read = EXCLUDED.rows_read,
                rows_kept = EXCLUDED.rows_kept,
                last_id = EXCLUDED.last_id,
                updated_at = now()
            """,
            SOURCE_ID,
            date.fromisoformat(day),
            kind,
            key,
            status,
            rows_read,
            rows_kept,
            last_id,
        )
    except Exception:
        return


async def _load_ids(conn, state: BulkState) -> None:
    if conn is None or not state.day:
        return
    try:
        rows = await conn.fetch(
            """
            SELECT kind, row_id, parent_id, court_id, label, citation, filed,
                   precedential, slug, docket_number, nature_of_suit
            FROM zenas_pool.cl_bulk_ids
            WHERE source_id = $1 AND snapshot_date = $2
            """,
            SOURCE_ID,
            date.fromisoformat(state.day),
        )
    except Exception:
        return
    for row in rows:
        if row["kind"] == "docket":
            state.dockets[int(row["row_id"])] = DocketKept(
                int(row["row_id"]),
                row["court_id"] or "",
                row["docket_number"] or "",
                row["nature_of_suit"] or "",
                row["label"] or "",
            )
        elif row["kind"] == "cluster":
            state.clusters[int(row["row_id"])] = ClusterKept(
                int(row["row_id"]),
                int(row["parent_id"] or 0),
                row["label"] or "",
                row["filed"] or "",
                row["precedential"] or "",
                row["slug"] or "",
                row["citation"] or "",
            )


def _uses_buffer(http_get) -> bool:
    from agents.zenas.pool.nightly import urllib_get

    return http_get is not urllib_get


async def _buffered_rows(http_get, url: str) -> list[list[str]]:
    _reject_credential(url)
    payload = await http_get(url, {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    if not isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload)
    return list(iter_csv_bytes(bytes(payload), url))


async def _buffered_text(http_get, url: str) -> str:
    _reject_credential(url)
    payload = await http_get(url, {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    if not isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload)
    stream = _decompress(io.BytesIO(bytes(payload)), url)
    text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
    try:
        return text.read()
    finally:
        text.close()


async def _collect_keys(http_get) -> list[str]:
    keys: list[str] = []
    buffered = _uses_buffer(http_get)
    for prefix in LIST_PREFIXES:
        token = ""
        while True:
            url = list_page_url(prefix, token)
            _reject_credential(url)
            if buffered:
                payload = await http_get(url, {"User-Agent": USER_AGENT})
                if not isinstance(payload, (bytes, bytearray)):
                    payload = bytes(payload)
            else:
                payload = await asyncio.to_thread(_read_listing, url)
            page, token = parse_list_page(bytes(payload))
            keys.extend(page)
            if not token:
                break
    return keys


def _read_listing(url: str) -> bytes:
    """One listing page. Listings are small. Bulk CSVs are not read this way."""
    _reject_credential(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return response.read()


def _past(cursor: BulkCursor | None, kind: str) -> bool:
    """True when this file was finished on an earlier run."""
    if cursor is None or cursor.file == kind:
        return False
    if cursor.file == "done":
        return True
    if cursor.file not in FILE_ORDER or kind not in FILE_ORDER:
        return False
    return FILE_ORDER.index(kind) < FILE_ORDER.index(cursor.file)


async def load_bulk(http_get, *, resume: str = "", state: BulkState | None = None) -> BulkResult:
    """Stream the latest snapshot. Returns stored-row JSON and the resume cursor."""
    if os.environ.get("TANK_REGION", "").strip().lower() == "za":
        raise FetchNotConfigured("CourtListener bulk runs on the US box only")
    state = state if state is not None else BulkState()
    bulk_text, api = split_cursor(resume)
    cursor = parse_cursor(bulk_text)
    keys = await _collect_keys(http_get)
    snapshot = choose_snapshot(keys)
    if snapshot is None:
        raise FetchNotConfigured("no CourtListener bulk snapshot was listed")
    if state.day and state.day != snapshot.day:
        state.clear()
    state.day = snapshot.day
    if cursor and cursor.day != snapshot.day:
        cursor = None
        state.clear()
        state.day = snapshot.day
    if cursor and cursor.file == "opinions":
        state.last_opinion_id = max(state.last_opinion_id, cursor.last_id)
    if cursor and cursor.file == "done" and cursor.day == snapshot.day:
        done = BulkCursor(snapshot.day, "done")
        return BulkResult([], done.render(api), Counter(), snapshot.day, "done", 0, 0)

    conn = await _open_db()
    progress = _Progress(conn, snapshot.day)
    try:
        if cursor and _past(cursor, "dockets") and not state.dockets:
            await _load_ids(conn, state)
            if state.dockets:
                # Court names are not in the id table. Re-read that small file
                # so a docket-number citation still names the court.
                state.files_done.update({"schema", "dockets"})
            if state.clusters:
                state.files_done.add("clusters")
        for kind in FILE_ORDER:
            if kind in state.files_done or _past(cursor, kind) and _ready(state, kind):
                continue
            progress.file = kind
            await progress.note(force=True)
            if kind == "schema":
                await _read_schema(http_get, object_url(snapshot.files["schema"]), progress)
            elif kind == "courts":
                await _read_courts(http_get, object_url(snapshot.files["courts"]), state, progress)
            elif kind == "dockets":
                await _read_dockets(http_get, object_url(snapshot.files["dockets"]), state, progress, conn)
            elif kind == "clusters":
                await _read_clusters(http_get, object_url(snapshot.files["clusters"]), state, progress, conn)
            elif kind == "citations":
                key = snapshot.files.get("citations", "")
                if key:
                    await _read_citations(http_get, object_url(key), state, progress, conn)
            elif kind == "opinions":
                blobs, stopped = await _read_opinions(
                    http_get,
                    object_url(snapshot.files["opinions"]),
                    state,
                    progress,
                )
                await progress.note(force=True)
                if stopped:
                    rendered = BulkCursor(snapshot.day, "opinions", state.last_opinion_id).render(api)
                else:
                    state.files_done.add("opinions")
                    rendered = BulkCursor(snapshot.day, "done").render(api)
                    await _mark_file(conn, snapshot.day, "opinions", snapshot.files["opinions"], "done", progress.rows_read, progress.cases_kept, state.last_opinion_id)
                return BulkResult(
                    blobs,
                    rendered,
                    progress.skips,
                    snapshot.day,
                    progress.file,
                    progress.rows_read,
                    progress.cases_kept,
                )
            state.files_done.add(kind)
        rendered = BulkCursor(snapshot.day, "done").render(api)
        return BulkResult([], rendered, progress.skips, snapshot.day, "done", progress.rows_read, progress.cases_kept)
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


def _ready(state: BulkState, kind: str) -> bool:
    """A finished file can be skipped when the ids it produced are still here."""
    if kind == "schema":
        return kind in state.files_done or bool(state.dockets or state.clusters)
    if kind == "courts":
        return bool(state.courts)
    if kind == "dockets":
        return bool(state.dockets)
    if kind == "clusters":
        return bool(state.clusters)
    if kind == "citations":
        return any(item.citation for item in state.clusters.values()) or "citations" in state.files_done
    return False


async def _read_schema(http_get, url: str, progress: _Progress) -> None:
    if _uses_buffer(http_get):
        text = await _buffered_text(http_get, url)
        progress.rows_read += max(1, text.count("\n"))
        return
    stop = threading.Event()

    def worker():
        return _iter_http_lines(url, stop)

    try:
        async for _line in _aiter_thread(worker, stop):
            progress.rows_read += 1
    finally:
        stop.set()
    await progress.note(force=True)


async def _consume_csv(http_get, url: str):
    if _uses_buffer(http_get):
        rows = await _buffered_rows(http_get, url)
        for row in rows:
            yield row
        return
    stop = threading.Event()

    def worker():
        return _iter_http_rows(url, stop)

    try:
        async for row in _aiter_thread(worker, stop):
            yield row
    finally:
        stop.set()


async def _read_courts(http_get, url: str, state: BulkState, progress: _Progress) -> None:
    header: list[str] | None = None
    async for raw in _consume_csv(http_get, url):
        if header is None:
            header = raw
            continue
        progress.rows_read += 1
        row = _mapping(header, raw)
        court_id = _cell(row, "id").lower()
        if court_id:
            state.courts[court_id] = _cell(row, "short_name") or _cell(row, "full_name") or court_id
        if progress.rows_read % 5000 == 0:
            await progress.note()
    await progress.note(force=True)


async def _read_dockets(http_get, url: str, state: BulkState, progress: _Progress, conn) -> None:
    header: list[str] | None = None
    batch: list[tuple] = []
    async for raw in _consume_csv(http_get, url):
        if header is None:
            header = raw
            continue
        progress.rows_read += 1
        row = _mapping(header, raw)
        reason = docket_skip(row)
        if reason:
            progress.skips[reason] += 1
            continue
        docket_id = _int_id(_cell(row, "id"))
        if not docket_id:
            continue
        kept = DocketKept(
            docket_id,
            _cell(row, "court_id").lower(),
            _cell(row, "docket_number"),
            _cell(row, "nature_of_suit"),
            _cell(row, "case_name"),
        )
        state.dockets[docket_id] = kept
        batch.append(
            (
                SOURCE_ID,
                date.fromisoformat(state.day),
                "docket",
                docket_id,
                0,
                kept.court_id,
                kept.case_name,
                "",
                "",
                "",
                "",
                kept.docket_number,
                kept.nature_of_suit,
            )
        )
        if len(batch) >= 2000:
            await _flush_ids(conn, batch)
            batch = []
            await progress.note()
    if batch:
        await _flush_ids(conn, batch)
    await _mark_file(conn, state.day, "dockets", url, "done", progress.rows_read, len(state.dockets), 0)
    await progress.note(force=True)


async def _read_clusters(http_get, url: str, state: BulkState, progress: _Progress, conn) -> None:
    header: list[str] | None = None
    batch: list[tuple] = []
    async for raw in _consume_csv(http_get, url):
        if header is None:
            header = raw
            continue
        progress.rows_read += 1
        row = _mapping(header, raw)
        docket_id = _int_id(_cell(row, "docket_id"))
        docket = state.dockets.get(docket_id)
        if docket is None:
            continue
        cluster_id = _int_id(_cell(row, "id"))
        if not cluster_id:
            continue
        kept = ClusterKept(
            cluster_id,
            docket_id,
            _cell(row, "case_name") or docket.case_name,
            _cell(row, "date_filed"),
            _cell(row, "precedential_status"),
            _cell(row, "slug"),
        )
        state.clusters[cluster_id] = kept
        batch.append(
            (
                SOURCE_ID,
                date.fromisoformat(state.day),
                "cluster",
                cluster_id,
                docket_id,
                docket.court_id,
                kept.case_name,
                "",
                kept.date_filed,
                kept.precedential_status,
                kept.slug,
                docket.docket_number,
                docket.nature_of_suit,
            )
        )
        if len(batch) >= 2000:
            await _flush_ids(conn, batch)
            batch = []
            await progress.note()
    if batch:
        await _flush_ids(conn, batch)
    await _mark_file(conn, state.day, "clusters", url, "done", progress.rows_read, len(state.clusters), 0)
    await progress.note(force=True)


async def _read_citations(http_get, url: str, state: BulkState, progress: _Progress, conn) -> None:
    header: list[str] | None = None
    touched: set[int] = set()
    async for raw in _consume_csv(http_get, url):
        if header is None:
            header = raw
            continue
        progress.rows_read += 1
        row = _mapping(header, raw)
        cluster = state.clusters.get(_int_id(_cell(row, "cluster_id")))
        if cluster is None:
            continue
        _prefer_citation(
            cluster,
            _cell(row, "volume"),
            _cell(row, "reporter"),
            _cell(row, "page"),
            _cell(row, "type"),
        )
        touched.add(cluster.cluster_id)
        if progress.rows_read % 5000 == 0:
            await progress.note()
    if conn is not None and touched:
        rows = []
        for cluster_id in touched:
            cluster = state.clusters[cluster_id]
            docket = state.dockets.get(cluster.docket_id)
            rows.append(
                (
                    SOURCE_ID,
                    date.fromisoformat(state.day),
                    "cluster",
                    cluster.cluster_id,
                    cluster.docket_id,
                    docket.court_id if docket else "",
                    cluster.case_name,
                    cluster.citation,
                    cluster.date_filed,
                    cluster.precedential_status,
                    cluster.slug,
                    docket.docket_number if docket else "",
                    docket.nature_of_suit if docket else "",
                )
            )
            if len(rows) >= 2000:
                await _flush_ids(conn, rows)
                rows = []
        if rows:
            await _flush_ids(conn, rows)
    await progress.note(force=True)


async def _read_opinions(http_get, url: str, state: BulkState, progress: _Progress) -> tuple[list[bytes], bool]:
    """Returns JSON blobs and whether the cap stopped the file early."""
    header: list[str] | None = None
    texts: dict[int, list[str]] = {}
    kept_opinions = 0
    cap = bulk_cap()
    stopped = False
    async for raw in _consume_csv(http_get, url):
        if header is None:
            header = raw
            continue
        progress.rows_read += 1
        row = _mapping(header, raw)
        opinion_id = _int_id(_cell(row, "id"))
        if opinion_id and opinion_id <= state.last_opinion_id:
            continue
        if opinion_id:
            state.last_opinion_id = opinion_id
        cluster = state.clusters.get(_int_id(_cell(row, "cluster_id")))
        if cluster is None:
            if progress.rows_read % 5000 == 0:
                await progress.note()
            continue
        text = opinion_text(row)
        if not text:
            progress.skips["no-text"] += 1
            continue
        texts.setdefault(cluster.cluster_id, []).append(text)
        kept_opinions += 1
        if kept_opinions >= cap:
            stopped = True
            break
        if progress.rows_read % 5000 == 0:
            await progress.note()
    blobs, kept = _emit(state, texts, progress.skips)
    progress.cases_kept += kept
    await progress.note(force=True)
    return blobs, stopped


def _emit(state: BulkState, texts: dict[int, list[str]], skips: Counter[str]) -> tuple[list[bytes], int]:
    rows: list[dict] = []
    for cluster_id in sorted(texts):
        cluster = state.clusters.get(cluster_id)
        if cluster is None:
            continue
        docket = state.dockets.get(cluster.docket_id)
        if docket is None:
            continue
        text = "\n\n".join(part for part in texts[cluster_id] if part).strip()
        if not text:
            skips["no-text"] += 1
            continue
        # A missing reporter citation uses the docket number. It does not skip
        # the commercial filter, or any other filter.
        if not cluster.citation:
            cluster.citation = _docket_citation(docket, cluster, state.courts)
            skips["no-reporter-citation"] += 1
        payload = _case_payload(docket, cluster, text)
        if not is_commercial(payload):
            skips["not-commercial"] += 1
            continue
        rows.append(payload)
    blobs: list[bytes] = []
    step = 25
    for start in range(0, len(rows), step):
        blobs.append(json.dumps({"results": rows[start : start + step]}).encode("utf-8"))
    return blobs, len(rows)
