"""In-memory store for tests, and Postgres for a region deployment.

Public law is shared inside one region. Queries are scoped by jurisdiction,
which is the region boundary. They are not tenant rows.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from agents.zenas.pool.config import jurisdiction_for
from agents.zenas.pool.licence import LicenceError, may_store_text, refusal_reason, terms_recorded
from agents.zenas.pool.model import (
    Chunk,
    LegalDocument,
    RunRecord,
    SourceSpec,
    VersionHead,
)
from agents.zenas.pool.region import RegionError
from agents.zenas.pool.textutil import VersionPlan, applies, citation_key, plan_version, version_id_for

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

CITATION_DOCUMENT_SQL = """
SELECT c.chunk_id, c.version_id, c.document_id, c.jurisdiction, c.path, c.heading,
       c.text, c.citation, c.citation_key, c.ordinal
FROM zenas_pool.chunks c
JOIN zenas_pool.documents d ON d.version_id = c.version_id
WHERE d.jurisdiction = $1
  AND d.citation_key = $2
  AND (
        ($3::date IS NULL AND d.effective_to IS NULL)
        OR (
            $3::date IS NOT NULL
            AND (d.effective_from IS NOT NULL OR d.known_from IS NOT NULL)
            AND COALESCE(d.effective_from, d.known_from) <= $3::date
            AND (d.effective_to IS NULL OR d.effective_to > $3::date)
        )
  )
ORDER BY c.ordinal
"""

CITATION_CHUNK_SQL = """
SELECT chunk_id, version_id, document_id, jurisdiction, path, heading, text,
       citation, citation_key, ordinal
FROM zenas_pool.chunks
WHERE jurisdiction = $1
  AND citation_key = $2
  AND version_id IN (
      SELECT version_id FROM zenas_pool.documents
      WHERE jurisdiction = $1
  AND (
        ($3::date IS NULL AND effective_to IS NULL)
        OR (
            $3::date IS NOT NULL
            AND (effective_from IS NOT NULL OR known_from IS NOT NULL)
            AND COALESCE(effective_from, known_from) <= $3::date
            AND (effective_to IS NULL OR effective_to > $3::date)
        )
  )
  )
ORDER BY ordinal
"""

TEXT_CHUNK_SQL = """
SELECT chunk_id, version_id, document_id, jurisdiction, path, heading, text,
       citation, citation_key, ordinal
FROM zenas_pool.chunks
WHERE jurisdiction = $1
  AND ($2::text IS NULL OR version_id IN (
      SELECT version_id FROM zenas_pool.documents WHERE kind = $2 AND jurisdiction = $1
  ))
  AND to_tsvector('english', coalesce(heading, '') || ' ' || coalesce(text, ''))
      @@ plainto_tsquery('english', $3)
  AND version_id IN (
      SELECT version_id FROM zenas_pool.documents
      WHERE jurisdiction = $1
        AND ($4::text IS NULL OR level = $4)
        AND (
            ($5::date IS NULL AND effective_to IS NULL)
            OR (
                $5::date IS NOT NULL
                AND (effective_from IS NOT NULL OR known_from IS NOT NULL)
                AND COALESCE(effective_from, known_from) <= $5::date
                AND (effective_to IS NULL OR effective_to > $5::date)
            )
        )
  )
ORDER BY ts_rank(
    to_tsvector('english', coalesce(heading, '') || ' ' || coalesce(text, '')),
    plainto_tsquery('english', $3)
) DESC, ordinal
LIMIT $6
"""

INSERT_SOURCE_SQL = """
INSERT INTO zenas_pool.sources (
    source_id, name, region, jurisdiction, licence, terms, terms_url,
    bulk_use, store_full_text, canonical_url, notes, enabled, key_name, key_required
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
ON CONFLICT (source_id) DO UPDATE SET
    name = EXCLUDED.name,
    region = EXCLUDED.region,
    jurisdiction = EXCLUDED.jurisdiction,
    licence = EXCLUDED.licence,
    terms = EXCLUDED.terms,
    terms_url = EXCLUDED.terms_url,
    bulk_use = EXCLUDED.bulk_use,
    store_full_text = EXCLUDED.store_full_text,
    canonical_url = EXCLUDED.canonical_url,
    notes = EXCLUDED.notes,
    enabled = EXCLUDED.enabled,
    key_name = EXCLUDED.key_name,
    key_required = EXCLUDED.key_required
"""


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def schema_statements() -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in schema_sql().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            parts.append("\n".join(buf).strip())
            buf = []
    if buf:
        parts.append("\n".join(buf).strip())
    return parts


def _row_field(row, key: str, default):
    try:
        keys = row.keys()
    except AttributeError:
        return default
    if key not in keys or row[key] is None:
        return default
    return row[key]


def _status_dict(source: SourceSpec, run: RunRecord | None, links: dict, documents: int, versions: int) -> dict:
    detail = run.detail if run else ""
    return {
        "source_id": source.source_id,
        "name": source.name,
        "region": source.region,
        "jurisdiction": source.jurisdiction,
        "licence": source.licence,
        "terms_url": source.terms_url,
        "bulk_use": source.bulk_use,
        "enabled": source.enabled,
        "key_name": source.key_name,
        "key_required": source.key_required,
        "key_set": key_is_set(source.key_name),
        "documents": documents,
        "versions": versions,
        "last_update": run.last_update.isoformat() if run and run.last_update else None,
        "last_load": run.last_update.isoformat() if run and run.last_update else None,
        "last_status": run.last_status if run else "never",
        "detail": detail,
        "errors": [detail] if run and run.last_status == "error" and detail else [],
        "link_only": source.source_id in links,
    }


def key_is_set(name: str) -> bool:
    """True when no key is required, or the named variable is non-empty.

    The value is never returned.
    """
    if not name:
        return True
    return bool(os.environ.get(name, "").strip())


def _guard_home(home_region: str | None, *, region: str | None = None, jurisdiction: str | None = None) -> None:
    """A store with no known home region stores nothing and reads nothing."""
    if home_region not in {"us-east", "za"}:
        shown = home_region if home_region else "missing"
        raise RegionError(f"this box's region is {shown}, so it stores nothing")
    if region is not None and region != home_region:
        raise RegionError(f"this box is {home_region} and does not write {region}")
    if jurisdiction is not None and jurisdiction != jurisdiction_for(home_region):
        raise RegionError(f"this box is {home_region} and does not read {jurisdiction} law")


def _chunk_from_mapping(row: dict) -> Chunk:
    return Chunk(
        chunk_id=row["chunk_id"],
        version_id=row["version_id"],
        document_id=row["document_id"],
        jurisdiction=row["jurisdiction"],
        path=row["path"],
        heading=row["heading"],
        text=row["text"],
        citation=row["citation"],
        citation_key=row["citation_key"],
        ordinal=row["ordinal"],
    )


class Store(Protocol):
    async def record_source(self, source: SourceSpec) -> None: ...

    async def get_source(self, source_id: str) -> SourceSpec | None: ...

    async def upsert_version(self, doc: LegalDocument, chunks: list[Chunk], *, on: date) -> bool: ...

    async def record_link(self, source_id: str, url: str, reason: str) -> None: ...

    async def record_run(
        self,
        source_id: str,
        *,
        status: str,
        detail: str,
        documents: int,
        when: datetime,
    ) -> None: ...

    async def find_by_citation(
        self,
        citation: str,
        *,
        jurisdiction: str,
        as_at: date | None,
    ) -> list[Chunk]: ...

    async def search_text(
        self,
        query: str,
        *,
        jurisdiction: str,
        as_at: date | None,
        level: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[Chunk]: ...

    async def get_chunks(self, chunk_ids: list[str], *, jurisdiction: str) -> list[Chunk]: ...

    async def version_count(self, source_id: str | None = None) -> int: ...

    async def instrument_count(self, source_id: str) -> int: ...

    async def status_rows(self) -> list[dict]: ...

    async def all_text(self) -> str: ...

    async def record_cites(
        self,
        from_version: str,
        rows: list[tuple[str, str, bool]],
    ) -> None: ...

    async def cites_from(self, from_version: str) -> list[tuple[str, str, bool]]: ...

    async def get_checkpoint(self, source_id: str) -> str: ...

    async def set_checkpoint(self, source_id: str, cursor: str, bytes_stored: int) -> None: ...

    async def stored_bytes(self) -> int: ...


def _require_source(source: SourceSpec | None, doc: LegalDocument) -> SourceSpec:
    if source is None:
        raise LicenceError("record the source and its terms before storing text")
    if not may_store_text(source):
        raise LicenceError(refusal_reason(source) or "not ingestible")
    if doc.jurisdiction != source.jurisdiction:
        raise LicenceError("document jurisdiction does not match the source")
    return source


class MemoryStore:
    def __init__(self, *, home_region: str | None = None) -> None:
        self.home_region = home_region
        self.sources: dict[str, SourceSpec] = {}
        self.docs: dict[str, LegalDocument] = {}
        self.chunks: dict[str, Chunk] = {}
        self.links: dict[str, tuple[str, str]] = {}
        self.runs: dict[str, RunRecord] = {}
        self.cites: dict[tuple[str, str], tuple[str, bool]] = {}
        self.checkpoints: dict[str, tuple[str, int]] = {}

    async def record_source(self, source: SourceSpec) -> None:
        _guard_home(self.home_region, region=source.region)
        if not terms_recorded(source):
            raise LicenceError("a source needs a licence and terms before it is recorded")
        self.sources[source.source_id] = source

    async def get_source(self, source_id: str) -> SourceSpec | None:
        return self.sources.get(source_id)

    async def upsert_version(self, doc: LegalDocument, chunks: list[Chunk], *, on: date) -> bool:
        _guard_home(self.home_region, jurisdiction=doc.jurisdiction)
        source = _require_source(self.sources.get(doc.source_id), doc)
        del source
        if doc.known_from is None:
            doc.known_from = on
        same = [
            item
            for item in self.docs.values()
            if item.id == doc.id and item.jurisdiction == doc.jurisdiction
        ]
        existing = [
            VersionHead(
                item.version_id,
                item.content_hash,
                item.effective_from,
                item.effective_to,
                item.known_from,
            )
            for item in same
        ]
        plan = plan_version(
            existing,
            content_hash=doc.content_hash,
            effective_from=doc.effective_from,
            on=on,
            known_from=doc.known_from,
        )
        if plan.action == "skip":
            return False
        _bind_version(doc, chunks, plan, {item.version_id for item in self.docs.values()})
        for version_id in plan.close_ids:
            old = self.docs[version_id]
            old.effective_to = plan.close_on
            old.status = "superseded"
        self.docs[doc.version_id] = doc
        for chunk in chunks:
            if chunk.jurisdiction != doc.jurisdiction:
                raise LicenceError("chunk jurisdiction does not match the document")
            self.chunks[chunk.chunk_id] = chunk
        return True

    async def record_link(self, source_id: str, url: str, reason: str) -> None:
        _guard_home(self.home_region)
        if source_id not in self.sources:
            raise LicenceError("record the source before recording a link")
        self.links[source_id] = (url, reason)

    async def record_run(
        self,
        source_id: str,
        *,
        status: str,
        detail: str,
        documents: int,
        when: datetime,
    ) -> None:
        _guard_home(self.home_region)
        self.runs[source_id] = RunRecord(source_id, when, status, documents, detail)

    def _versions(self) -> list[LegalDocument]:
        return list(self.docs.values())

    def _open_chunk_ok(self, chunk: Chunk, *, jurisdiction: str, as_at: date | None, level: str | None, kind: str | None) -> bool:
        if chunk.jurisdiction != jurisdiction:
            return False
        doc = self.docs.get(chunk.version_id)
        if doc is None or doc.jurisdiction != jurisdiction:
            return False
        if level and doc.level != level:
            return False
        if kind and doc.kind != kind:
            return False
        return applies(doc.effective_from, doc.effective_to, as_at, doc.known_from)

    async def find_by_citation(
        self,
        citation: str,
        *,
        jurisdiction: str,
        as_at: date | None,
    ) -> list[Chunk]:
        _guard_home(self.home_region, jurisdiction=jurisdiction)
        key = citation_key(citation)
        pinpoint_hits = [
            chunk
            for chunk in self.chunks.values()
            if chunk.citation_key == key
            and self._open_chunk_ok(chunk, jurisdiction=jurisdiction, as_at=as_at, level=None, kind=None)
        ]
        if pinpoint_hits:
            return sorted(pinpoint_hits, key=lambda item: item.ordinal)
        doc_hits = [
            chunk
            for chunk in self.chunks.values()
            if self._open_chunk_ok(chunk, jurisdiction=jurisdiction, as_at=as_at, level=None, kind=None)
            and self.docs[chunk.version_id].citation
            and citation_key(self.docs[chunk.version_id].citation) == key
        ]
        return sorted(doc_hits, key=lambda item: item.ordinal)

    async def search_text(
        self,
        query: str,
        *,
        jurisdiction: str,
        as_at: date | None,
        level: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[Chunk]:
        _guard_home(self.home_region, jurisdiction=jurisdiction)
        tokens = [tok for tok in query.lower().split() if len(tok) > 1]
        if not tokens:
            return []
        scored: list[tuple[int, Chunk]] = []
        for chunk in self.chunks.values():
            if not self._open_chunk_ok(
                chunk, jurisdiction=jurisdiction, as_at=as_at, level=level, kind=kind
            ):
                continue
            hay = f"{chunk.heading} {chunk.text} {chunk.citation}".lower()
            if not all(tok in hay for tok in tokens):
                continue
            score = sum(hay.count(tok) for tok in tokens)
            scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].ordinal))
        return [chunk for _, chunk in scored[:limit]]

    async def get_chunks(self, chunk_ids: list[str], *, jurisdiction: str) -> list[Chunk]:
        _guard_home(self.home_region, jurisdiction=jurisdiction)
        out: list[Chunk] = []
        for chunk_id in chunk_ids:
            chunk = self.chunks.get(chunk_id)
            if chunk is not None and chunk.jurisdiction == jurisdiction:
                out.append(chunk)
        return out

    async def version_count(self, source_id: str | None = None) -> int:
        if source_id is None:
            return len(self.docs)
        return sum(1 for doc in self.docs.values() if doc.source_id == source_id)

    async def instrument_count(self, source_id: str) -> int:
        return len({doc.id for doc in self.docs.values() if doc.source_id == source_id})

    async def status_rows(self) -> list[dict]:
        rows = []
        for source in self.sources.values():
            if self.home_region is not None and source.region != self.home_region:
                continue
            run = self.runs.get(source.source_id)
            rows.append(_status_dict(source, run, self.links, await self.instrument_count(source.source_id), await self.version_count(source.source_id)))
        return rows

    async def all_text(self) -> str:
        docs = self.docs.values()
        if self.home_region is not None:
            jurisdiction = jurisdiction_for(self.home_region)
            docs = [doc for doc in docs if doc.jurisdiction == jurisdiction]
        return "\n".join(doc.text for doc in docs)

    async def record_cites(self, from_version: str, rows: list[tuple[str, str, bool]]) -> None:
        _guard_home(self.home_region)
        for citation_text, to_document, resolved in rows:
            self.cites[(from_version, citation_text)] = (to_document, resolved)

    async def cites_from(self, from_version: str) -> list[tuple[str, str, bool]]:
        return [
            (citation, dest, resolved)
            for (version, citation), (dest, resolved) in self.cites.items()
            if version == from_version
        ]

    async def get_checkpoint(self, source_id: str) -> str:
        _guard_home(self.home_region, region=(self.sources[source_id].region if source_id in self.sources else self.home_region))
        found = self.checkpoints.get(source_id)
        return found[0] if found else ""

    async def set_checkpoint(self, source_id: str, cursor: str, bytes_stored: int) -> None:
        if source_id in self.sources:
            _guard_home(self.home_region, region=self.sources[source_id].region)
        else:
            _guard_home(self.home_region)
        self.checkpoints[source_id] = (cursor, bytes_stored)

    async def stored_bytes(self) -> int:
        docs = self.docs.values()
        if self.home_region is not None:
            jurisdiction = jurisdiction_for(self.home_region)
            docs = [doc for doc in docs if doc.jurisdiction == jurisdiction]
        return sum(len(doc.text.encode("utf-8")) for doc in docs)


class PostgresStore:
    """asyncpg connection or any object with execute / fetch / executemany."""

    def __init__(self, conn, *, home_region: str | None = None) -> None:
        self._conn = conn
        self.home_region = home_region

    async def apply_schema(self) -> None:
        for statement in schema_statements():
            await self._conn.execute(statement)

    async def record_source(self, source: SourceSpec) -> None:
        _guard_home(self.home_region, region=source.region)
        if not terms_recorded(source):
            raise LicenceError("a source needs a licence and terms before it is recorded")
        await self._conn.execute(
            INSERT_SOURCE_SQL,
            source.source_id,
            source.name,
            source.region,
            source.jurisdiction,
            source.licence,
            source.terms,
            source.terms_url,
            source.bulk_use,
            source.store_full_text,
            source.canonical_url,
            source.notes,
            source.enabled,
            source.key_name,
            source.key_required,
        )

    async def get_source(self, source_id: str) -> SourceSpec | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM zenas_pool.sources WHERE source_id = $1",
            source_id,
        )
        if row is None:
            return None
        return _source_from_row(row)

    async def upsert_version(self, doc: LegalDocument, chunks: list[Chunk], *, on: date) -> bool:
        _guard_home(self.home_region, jurisdiction=doc.jurisdiction)
        source = await self.get_source(doc.source_id)
        _require_source(source, doc)
        if doc.known_from is None:
            doc.known_from = on
        import json

        async with self._conn.transaction():
            rows = await self._conn.fetch(
                """
                SELECT version_id, content_hash, effective_from, effective_to, known_from
                FROM zenas_pool.documents
                WHERE document_id = $1 AND jurisdiction = $2
                """,
                doc.id,
                doc.jurisdiction,
            )
            existing = [
                VersionHead(
                    r["version_id"],
                    r["content_hash"],
                    r["effective_from"],
                    r["effective_to"],
                    r["known_from"],
                )
                for r in rows
            ]
            plan = plan_version(
                existing,
                content_hash=doc.content_hash,
                effective_from=doc.effective_from,
                on=on,
                known_from=doc.known_from,
            )
            if plan.action == "skip":
                return False
            _bind_version(doc, chunks, plan, {row.version_id for row in existing})
            if plan.close_ids:
                await self._conn.execute(
                    """
                    UPDATE zenas_pool.documents
                    SET effective_to = $1, status = 'superseded'
                    WHERE jurisdiction = $2 AND version_id = ANY($3::text[])
                    """,
                    plan.close_on,
                    doc.jurisdiction,
                    list(plan.close_ids),
                )
            await self._conn.execute(
                """
                INSERT INTO zenas_pool.documents (
                    version_id, document_id, source_id, jurisdiction, level, body, kind,
                    citation, citation_key, title, doc_date, effective_from, effective_to,
                    known_from, status, url, licence, text, content_hash, sections
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20::jsonb
                )
                """,
                doc.version_id,
                doc.id,
                doc.source_id,
                doc.jurisdiction,
                doc.level,
                doc.body,
                doc.kind,
                doc.citation,
                citation_key(doc.citation),
                doc.title,
                doc.date,
                doc.effective_from,
                doc.effective_to,
                doc.known_from,
                doc.status,
                doc.url,
                doc.licence,
                doc.text,
                doc.content_hash,
                json.dumps([section.to_dict() for section in doc.sections]),
            )
            await self._conn.executemany(
                """
                INSERT INTO zenas_pool.chunks (
                    chunk_id, version_id, document_id, jurisdiction, path, heading,
                    text, citation, citation_key, ordinal
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.version_id,
                        chunk.document_id,
                        chunk.jurisdiction,
                        chunk.path,
                        chunk.heading,
                        chunk.text,
                        chunk.citation,
                        chunk.citation_key,
                        chunk.ordinal,
                    )
                    for chunk in chunks
                ],
            )
        return True

    async def record_link(self, source_id: str, url: str, reason: str) -> None:
        _guard_home(self.home_region)
        if await self.get_source(source_id) is None:
            raise LicenceError("record the source before recording a link")
        await self._conn.execute(
            """
            INSERT INTO zenas_pool.links (source_id, url, reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (source_id) DO UPDATE SET url = EXCLUDED.url, reason = EXCLUDED.reason
            """,
            source_id,
            url,
            reason,
        )

    async def record_run(
        self,
        source_id: str,
        *,
        status: str,
        detail: str,
        documents: int,
        when: datetime,
    ) -> None:
        _guard_home(self.home_region)
        await self._conn.execute(
            """
            INSERT INTO zenas_pool.updates (source_id, last_update, last_status, documents, detail)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (source_id) DO UPDATE SET
                last_update = EXCLUDED.last_update,
                last_status = EXCLUDED.last_status,
                documents = EXCLUDED.documents,
                detail = EXCLUDED.detail
            """,
            source_id,
            when,
            status,
            documents,
            detail,
        )

    async def find_by_citation(
        self,
        citation: str,
        *,
        jurisdiction: str,
        as_at: date | None,
    ) -> list[Chunk]:
        _guard_home(self.home_region, jurisdiction=jurisdiction)
        key = citation_key(citation)
        rows = await self._conn.fetch(CITATION_CHUNK_SQL, jurisdiction, key, as_at)
        if not rows:
            rows = await self._conn.fetch(CITATION_DOCUMENT_SQL, jurisdiction, key, as_at)
        return [_chunk_from_mapping(row) for row in rows]

    async def search_text(
        self,
        query: str,
        *,
        jurisdiction: str,
        as_at: date | None,
        level: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[Chunk]:
        _guard_home(self.home_region, jurisdiction=jurisdiction)
        rows = await self._conn.fetch(
            TEXT_CHUNK_SQL,
            jurisdiction,
            kind,
            query,
            level,
            as_at,
            limit,
        )
        return [_chunk_from_mapping(row) for row in rows]

    async def get_chunks(self, chunk_ids: list[str], *, jurisdiction: str) -> list[Chunk]:
        _guard_home(self.home_region, jurisdiction=jurisdiction)
        rows = await self._conn.fetch(
            """
            SELECT chunk_id, version_id, document_id, jurisdiction, path, heading,
                   text, citation, citation_key, ordinal
            FROM zenas_pool.chunks
            WHERE jurisdiction = $1 AND chunk_id = ANY($2::text[])
            """,
            jurisdiction,
            chunk_ids,
        )
        return [_chunk_from_mapping(row) for row in rows]

    async def version_count(self, source_id: str | None = None) -> int:
        if source_id is None:
            row = await self._conn.fetchrow("SELECT count(*) AS n FROM zenas_pool.documents")
        else:
            row = await self._conn.fetchrow(
                "SELECT count(*) AS n FROM zenas_pool.documents WHERE source_id = $1",
                source_id,
            )
        return int(row["n"])

    async def instrument_count(self, source_id: str) -> int:
        row = await self._conn.fetchrow(
            """
            SELECT count(DISTINCT document_id) AS n
            FROM zenas_pool.documents
            WHERE source_id = $1
            """,
            source_id,
        )
        return int(row["n"])

    async def status_rows(self) -> list[dict]:
        rows = await self._conn.fetch(
            """
            SELECT s.source_id, s.name, s.region, s.jurisdiction, s.licence, s.terms,
                   s.terms_url, s.bulk_use, s.store_full_text, s.canonical_url, s.notes,
                   s.enabled, s.key_name, s.key_required,
                   (SELECT count(DISTINCT d.document_id) FROM zenas_pool.documents d
                     WHERE d.source_id = s.source_id) AS documents,
                   (SELECT count(*) FROM zenas_pool.documents d
                     WHERE d.source_id = s.source_id) AS versions,
                   u.last_update, u.last_status, u.detail,
                   (l.source_id IS NOT NULL) AS link_only
            FROM zenas_pool.sources s
            LEFT JOIN zenas_pool.updates u ON u.source_id = s.source_id
            LEFT JOIN zenas_pool.links l ON l.source_id = s.source_id
            WHERE ($1::text IS NULL OR s.region = $1)
            ORDER BY s.source_id
            """,
            self.home_region,
        )
        out = []
        for row in rows:
            source = _source_from_row(row)
            when = row["last_update"]
            run = None
            if row["last_status"] or when:
                run = RunRecord(
                    source.source_id,
                    when,
                    row["last_status"] or "never",
                    int(row["documents"]),
                    row["detail"] or "",
                )
            item = _status_dict(
                source,
                run,
                {source.source_id: ("", "")} if row["link_only"] else {},
                int(row["documents"]),
                int(row["versions"]),
            )
            out.append(item)
        return out

    async def all_text(self) -> str:
        jurisdiction = jurisdiction_for(self.home_region) if self.home_region else None
        rows = await self._conn.fetch(
            """
            SELECT text FROM zenas_pool.documents
            WHERE ($1::text IS NULL OR jurisdiction = $1)
            """,
            jurisdiction,
        )
        return "\n".join(row["text"] for row in rows)

    async def record_cites(self, from_version: str, rows: list[tuple[str, str, bool]]) -> None:
        _guard_home(self.home_region)
        for citation_text, to_document, resolved in rows:
            await self._conn.execute(
                """
                INSERT INTO zenas_pool.cites (from_version, citation_text, to_document, resolved)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (from_version, citation_text) DO UPDATE SET
                    to_document = EXCLUDED.to_document,
                    resolved = EXCLUDED.resolved
                """,
                from_version,
                citation_text,
                to_document,
                resolved,
            )

    async def cites_from(self, from_version: str) -> list[tuple[str, str, bool]]:
        rows = await self._conn.fetch(
            """
            SELECT citation_text, to_document, resolved
            FROM zenas_pool.cites
            WHERE from_version = $1
            ORDER BY citation_text
            """,
            from_version,
        )
        return [(row["citation_text"], row["to_document"], bool(row["resolved"])) for row in rows]

    async def get_checkpoint(self, source_id: str) -> str:
        row = await self._conn.fetchrow(
            "SELECT cursor FROM zenas_pool.checkpoints WHERE source_id = $1",
            source_id,
        )
        return row["cursor"] if row else ""

    async def set_checkpoint(self, source_id: str, cursor: str, bytes_stored: int) -> None:
        _guard_home(self.home_region)
        await self._conn.execute(
            """
            INSERT INTO zenas_pool.checkpoints (source_id, cursor, bytes_stored)
            VALUES ($1, $2, $3)
            ON CONFLICT (source_id) DO UPDATE SET
                cursor = EXCLUDED.cursor,
                bytes_stored = EXCLUDED.bytes_stored,
                updated_at = now()
            """,
            source_id,
            cursor,
            bytes_stored,
        )

    async def stored_bytes(self) -> int:
        jurisdiction = jurisdiction_for(self.home_region) if self.home_region else None
        row = await self._conn.fetchrow(
            """
            SELECT coalesce(sum(octet_length(text)), 0) AS n
            FROM zenas_pool.documents
            WHERE ($1::text IS NULL OR jurisdiction = $1)
            """,
            jurisdiction,
        )
        return int(row["n"])


def _source_from_row(row) -> SourceSpec:
    return SourceSpec(
        source_id=row["source_id"],
        name=row["name"],
        region=row["region"],
        jurisdiction=row["jurisdiction"],
        licence=row["licence"],
        terms=row["terms"],
        terms_url=row["terms_url"],
        bulk_use=row["bulk_use"],
        store_full_text=row["store_full_text"],
        canonical_url=row["canonical_url"],
        notes=row["notes"],
        adapter="",
        enabled=bool(_row_field(row, "enabled", True)),
        key_name=str(_row_field(row, "key_name", "") or ""),
        key_required=bool(_row_field(row, "key_required", False)),
    )


def _bind_version(
    doc: LegalDocument,
    chunks: list[Chunk],
    plan: VersionPlan,
    taken: set[str],
) -> None:
    """Apply a born-closed end date, and keep the version id unique."""
    if plan.effective_to is not None:
        doc.effective_to = plan.effective_to
        if doc.status == "in_force":
            doc.status = "superseded"
    version_id = doc.version_id or version_id_for(
        doc.id, doc.content_hash, doc.effective_from, doc.known_from
    )
    if version_id in taken:
        base = version_id
        suffix = 2
        while f"{base}:{suffix}" in taken:
            suffix += 1
        version_id = f"{base}:{suffix}"
    if version_id != doc.version_id:
        doc.version_id = version_id
        for chunk in chunks:
            chunk.version_id = version_id
            chunk.chunk_id = f"{version_id}:{chunk.ordinal}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
