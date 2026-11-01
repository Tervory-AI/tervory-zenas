"""Re-extract stored ZA judgments whose text came from a PDF. Dry run by default.

Why: rows loaded before pool/pdf_spacing.py hold pypdf text with spaces inside
justified words ("an order m ade by"). The stored text alone cannot say which
space is false ("a n" may be two words), so this job fetches the judgment PDF
again from the row's own URL, extracts it with the fixed ``pdf_text``, and
stores the result through the normal ingest path:

* same text  -> nothing is written ("unchanged"); a second run is a no-op;
* new text   -> a new version; the old row is closed as superseded, its chunks
  stay with it, and the new chunks wait for the usual embedding pass.

Citation, title, court and date are kept from the stored row, so the document
id does not move. A row whose re-parsed id would differ is skipped. Nothing is
hand-edited and no row is deleted.

Run (ZA box only, pool-loader image; see docs/ops/ZENAS-ZA-RENORMALISE.md):

    docker compose -f docker-compose.za.yml --profile load run --rm \
      --entrypoint "nice -n 19 python -m agents.zenas.pool.renormalise" \
      pool-loader --batch-size 25 [--source cac] [--limit 50] [--apply]

Without ``--apply`` it fetches and compares and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable

from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.adapters.za_case_common import case_bytes, pdf_text
from agents.zenas.pool.adapters.za_common import pdf_headers, reject_credential_url
from agents.zenas.pool.catalogue import sources_for_region
from agents.zenas.pool.ingest import ingest_snapshot
from agents.zenas.pool.model import SourceSpec

log = logging.getLogger("zenas.pool.renormalise")

HttpGet = Callable[[str, dict[str, str]], Awaitable[bytes]]

# Spaces inside a word, as pypdf wrote them: "m ade", "21 st", "anti -competitive".
_SUSPECT = re.compile(r"(?<![\w’'])(?![ai]\b)[a-z] [a-z]{2,}\b|\b\d{1,2} (?:st|nd|rd|th)\b|[a-z] -[a-z]")

_ROWS_SQL = """
SELECT document_id, source_id, citation, title, body, url, doc_date, content_hash
FROM zenas_pool.documents
WHERE kind = 'case'
  AND jurisdiction = $1
  AND effective_to IS NULL
  AND status IS DISTINCT FROM 'superseded'
  AND source_id = ANY($2::text[])
  AND document_id > $3
ORDER BY document_id
LIMIT $4
""".strip()


@dataclass
class StoredCase:
    document_id: str
    source_id: str
    citation: str
    title: str
    body: str
    url: str
    doc_date: date | None
    content_hash: str
    text: str = ""


@dataclass
class Report:
    dry_run: bool
    counts: Counter = field(default_factory=Counter)
    changed: list[dict[str, Any]] = field(default_factory=list)
    last_document_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "counts": dict(sorted(self.counts.items())),
            "changed": self.changed[:50],
            "last_document_id": self.last_document_id,
        }


def suspect_spaces(text: str) -> int:
    """How many places look like a space inside a word. A count for the log, not a fix."""
    return len(_SUSPECT.findall(text or ""))


# Court adapters that store text read from a judgment PDF (pdf_text). SAFLII is HTML.
PDF_CASE_ADAPTERS = frozenset(
    {
        "cac",
        "comptrib",
        "concourt",
        "sca",
        "sars-taxcourt",
        "landcourt",
        "ocj-highcourt",
        "labour-court",
        "labour-appeal",
        "special-tribunal",
    }
)


# Left out of the default set, and never written, until #502 settles their text.
# A dry run may still name them with --source.
WAIT_FOR_502 = frozenset({"special-tribunal"})
WAIT_FOR_502_MESSAGE = "special-tribunal is not re-normalised with --apply yet: wait for #502"


def pdf_case_sources(region: str = "za", only: list[str] | None = None) -> list[SourceSpec]:
    """ZA case sources whose adapters store text read from a PDF. Without ``only``,
    the sources in WAIT_FOR_502 are left out."""
    return [
        source
        for source in sources_for_region(region)
        if source.adapter in PDF_CASE_ADAPTERS
        and (source.source_id in only if only else source.source_id not in WAIT_FOR_502)
    ]


async def stored_cases_pg(conn, *, sources: list[str], after: str, limit: int, region: str = "za") -> list[StoredCase]:
    rows = await conn.fetch(_ROWS_SQL, region, sources, after, limit)
    return [
        StoredCase(
            document_id=str(row["document_id"]),
            source_id=str(row["source_id"]),
            citation=str(row["citation"] or ""),
            title=str(row["title"] or ""),
            body=str(row["body"] or ""),
            url=str(row["url"] or ""),
            doc_date=row["doc_date"],
            content_hash=str(row["content_hash"] or ""),
        )
        for row in rows
    ]


async def stored_cases_memory(store, *, sources: list[str], after: str, limit: int, region: str = "za") -> list[StoredCase]:
    """The same query over a MemoryStore. Tests use it."""
    docs = sorted(
        (
            doc
            for doc in store.docs.values()
            if doc.kind == "case"
            and doc.jurisdiction == region
            and doc.effective_to is None
            and doc.status != "superseded"
            and doc.source_id in sources
            and doc.id > after
        ),
        key=lambda doc: doc.id,
    )[:limit]
    return [
        StoredCase(doc.id, doc.source_id, doc.citation, doc.title, doc.body, doc.url, doc.date, doc.content_hash, doc.text)
        for doc in docs
    ]


def _envelope(row: StoredCase, text: str) -> bytes | None:
    return case_bytes(
        citation=row.citation,
        title=row.title,
        body=row.body,
        url=row.url,
        text=text,
        doc_date=row.doc_date,
    )


async def renormalise(
    store,
    *,
    fetch_rows: Callable[..., Awaitable[list[StoredCase]]],
    http_get: HttpGet,
    sources: list[SourceSpec],
    dry_run: bool = True,
    batch_size: int = 25,
    limit: int | None = None,
    after: str = "",
    on: date | None = None,
    pause: float = 1.0,
    region: str = "za",
) -> Report:
    """Walk the stored rows in document_id order, ``batch_size`` at a time."""
    report = Report(dry_run=dry_run)
    by_id = {source.source_id: source for source in sources}
    day = on or date.today()
    cursor = after
    seen = 0
    while True:
        want = batch_size if limit is None else min(batch_size, limit - seen)
        if want <= 0:
            break
        rows = await fetch_rows(sources=list(by_id), after=cursor, limit=want, region=region)
        if not rows:
            break
        report.counts["batches"] += 1
        for row in rows:
            seen += 1
            cursor = row.document_id
            report.last_document_id = cursor
            report.counts["rows"] += 1
            await _one(store, row, by_id[row.source_id], http_get, report, dry_run=dry_run, on=day, region=region)
            if pause:
                await asyncio.sleep(pause)
        log.info("renormalise batch %d: %s", report.counts["batches"], json.dumps(dict(report.counts), sort_keys=True))
        if len(rows) < want:
            break
    return report


async def _one(store, row: StoredCase, source: SourceSpec, http_get: HttpGet, report: Report, *,
               dry_run: bool, on: date, region: str) -> None:
    counts = report.counts
    url = row.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        counts["skipped: no url"] += 1
        return
    try:
        reject_credential_url(url)
        raw = await http_get(url, pdf_headers())
    except Exception as exc:  # noqa: BLE001 - one bad URL does not stop the job
        counts["skipped: fetch failed"] += 1
        log.info("renormalise fetch failed for %s (%s)", row.document_id, type(exc).__name__)
        return
    if not raw.startswith(b"%PDF"):
        counts["skipped: not a pdf"] += 1
        return
    text = await asyncio.to_thread(pdf_text, raw)
    if not text:
        counts["skipped: no text"] += 1
        return
    blob = _envelope(row, text)
    if not blob:
        counts["skipped: not a case"] += 1
        return
    adapter = get_adapter(source)
    docs = await asyncio.to_thread(lambda: list(adapter.parse(blob, source)))
    if len(docs) != 1 or docs[0].id != row.document_id:
        counts["skipped: id would change"] += 1
        return
    doc = docs[0]
    if doc.content_hash == row.content_hash:
        counts["unchanged"] += 1
        return
    counts["changed"] += 1
    if len(report.changed) < 50:
        report.changed.append(
            {
                "document_id": row.document_id,
                "citation": row.citation,
                "suspect_spaces_before": suspect_spaces(row.text) if row.text else None,
                "suspect_spaces_after": suspect_spaces(text),
            }
        )
    if dry_run:
        return
    inserted = await ingest_snapshot(store, source, [blob], on=on, region=region)
    counts["written" if inserted else "unchanged on write"] += 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agents.zenas.pool.renormalise", description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write new versions. Without it nothing is written.")
    parser.add_argument("--dry-run", action="store_true", help="the default; kept for clarity")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many rows")
    parser.add_argument("--after", default="", help="resume after this document_id")
    parser.add_argument("--source", action="append", default=[], help="source_id; repeat for more")
    parser.add_argument("--pause", type=float, default=float(os.environ.get("ZA_CASE_MIN_INTERVAL", "1") or 1))
    return parser


async def _main(argv: list[str] | None = None) -> dict:
    args = _parser().parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("choose --apply or --dry-run, not both")
    if args.apply and WAIT_FOR_502.intersection(args.source):
        raise SystemExit(WAIT_FOR_502_MESSAGE)
    from agents.zenas.pool.config import current_region

    region = current_region()
    if region != "za":
        raise SystemExit("renormalise runs on the ZA box only")
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    import asyncpg

    from agents.zenas.pool.nightly import urllib_get
    from agents.zenas.pool.store import PostgresStore

    conn = await asyncpg.connect(dsn)
    try:
        store = PostgresStore(conn, home_region=region)

        async def rows(**kw):
            return await stored_cases_pg(conn, **kw)

        report = await renormalise(
            store,
            fetch_rows=rows,
            http_get=urllib_get,
            sources=pdf_case_sources(region, args.source or None),
            dry_run=not args.apply,
            batch_size=max(1, args.batch_size),
            limit=args.limit,
            after=args.after,
            pause=max(0.0, args.pause),
            region=region,
        )
    finally:
        await conn.close()
    return report.as_dict()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        os.nice(19)
    except OSError:
        pass
    print(json.dumps(asyncio.run(_main(argv)), indent=2, default=str))


if __name__ == "__main__":
    main()
