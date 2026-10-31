"""The sanctioned re-normalise job: dry run by default, batched, idempotent, counted."""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.zenas.pool import renormalise as job  # noqa: E402
from agents.zenas.pool.adapters.za_case_common import case_bytes  # noqa: E402
from agents.zenas.pool.ingest import ingest_snapshot  # noqa: E402
from agents.zenas.pool.store import MemoryStore  # noqa: E402

BROKEN = (
    "PHUTUMA NETWORKS (PTY) LTD v TELKOM SA LTD\n\n"
    "[1] This is an appeal against an order m ade by the Competition Tribunal. On the 21 st January 2010 "
    "the appellant lodged a complaint."
)


def _pdf(stream: bytes) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# "It is m" stretched by TJ kerning, then "ade by ..." where that line really ends.
FIXED_PDF = _pdf(
    b"BT /F1 12 Tf 1 0 0 1 72 720 Tm (PHUTUMA NETWORKS PTY LTD v TELKOM SA LTD) Tj ET\n"
    b"BT /F1 12 Tf 1 0 0 1 72 700 Tm [(It)-300( )-300(is)-300( )-300(m)] TJ ET\n"
    b"BT /F1 12 Tf 1 0 0 1 118.404 700 Tm (ade by the Competition Tribunal on appeal.) Tj ET\n"
)


def _source():
    return next(s for s in job.pdf_case_sources("za") if s.source_id == "cac")


async def _seeded(n: int = 1) -> MemoryStore:
    store = MemoryStore(home_region="za")
    source = _source()
    for index in range(n):
        blob = case_bytes(
            citation=f"CAC {108 + index}/CAC/Mar11",
            title=f"Phutuma Networks v Telkom {index}",
            body="Competition Appeal Court",
            url=f"https://www.cac.org.za/wp-content/uploads/2012/11/{108 + index}.pdf",
            text=BROKEN,
            doc_date=date(2012, 11, 20),
        )
        await ingest_snapshot(store, source, [blob], on=date(2026, 10, 1), region="za")
    return store


def _run(store, http_get, **kw):
    async def rows(**q):
        return await job.stored_cases_memory(store, **q)

    return asyncio.run(
        job.renormalise(
            store,
            fetch_rows=rows,
            http_get=http_get,
            sources=job.pdf_case_sources("za"),
            pause=0,
            on=date(2026, 10, 31),
            **kw,
        )
    )


async def _serve_pdf(url, headers):
    assert url.startswith("https://www.cac.org.za/")
    return FIXED_PDF


def _open_texts(store):
    return [d.text for d in store.docs.values() if d.effective_to is None and d.status != "superseded"]


def test_dry_run_is_the_default_and_writes_nothing():
    store = asyncio.run(_seeded())
    before = {k: (v.text, v.effective_to, v.status) for k, v in store.docs.items()}
    report = _run(store, _serve_pdf)
    assert report.dry_run is True
    assert report.counts["rows"] == 1 and report.counts["changed"] == 1
    assert report.counts["written"] == 0
    assert report.changed[0]["suspect_spaces_before"] >= 2
    assert report.changed[0]["suspect_spaces_after"] == 0
    assert {k: (v.text, v.effective_to, v.status) for k, v in store.docs.items()} == before


def test_apply_writes_a_new_version_and_a_second_run_is_a_no_op():
    store = asyncio.run(_seeded())
    first = _run(store, _serve_pdf, dry_run=False)
    assert first.counts["written"] == 1
    texts = _open_texts(store)
    assert len(texts) == 1
    assert "It is made by the Competition Tribunal" in " ".join(texts[0].split())
    closed = [d for d in store.docs.values() if d.status == "superseded"]
    assert len(closed) == 1 and "m ade" in closed[0].text  # the old row is kept, closed

    second = _run(store, _serve_pdf, dry_run=False)
    assert second.counts["unchanged"] == 1
    assert second.counts["written"] == 0 and second.counts["changed"] == 0
    assert len(store.docs) == 2


def test_batches_limit_and_resume_cursor():
    store = asyncio.run(_seeded(3))
    report = _run(store, _serve_pdf, batch_size=2)
    assert report.counts["rows"] == 3 and report.counts["batches"] == 2
    limited = _run(store, _serve_pdf, batch_size=2, limit=1)
    assert limited.counts["rows"] == 1
    resumed = _run(store, _serve_pdf, batch_size=2, after=limited.last_document_id)
    assert resumed.counts["rows"] == 2


def test_bad_fetches_are_counted_and_skipped():
    store = asyncio.run(_seeded(2))

    async def html(url, headers):
        return b"<html>challenge</html>"

    async def down(url, headers):
        raise OSError("connection reset")

    assert _run(store, html).counts["skipped: not a pdf"] == 2
    assert _run(store, down).counts["skipped: fetch failed"] == 2
    assert len(_open_texts(store)) == 2


def test_cli_refuses_without_za_region(monkeypatch):
    import pytest

    monkeypatch.setenv("TANK_REGION", "us")
    with pytest.raises(SystemExit):
        asyncio.run(job._main(["--dry-run"]))
    monkeypatch.setenv("TANK_REGION", "za")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(job._main([]))
    with pytest.raises(SystemExit):
        asyncio.run(job._main(["--apply", "--dry-run"]))


def test_only_court_pdf_sources():
    ids = {s.source_id for s in job.pdf_case_sources("za")}
    assert "cac" in ids and "comptrib" in ids
    assert "saflii" not in ids and "popia" not in ids
