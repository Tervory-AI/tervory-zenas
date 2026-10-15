-- Zenas knowledge pool. Applied by the pool package, not by Tank.
-- Shared public law for one region. Not tenant data. Every statement is idempotent.
-- Claude wires this into the Zenas box after the chassis (C6) lands. Do not DROP or rename.

CREATE SCHEMA IF NOT EXISTS zenas_pool;

-- Recorded before any text is stored. Unknown or forbidden bulk use never gets a document row.
CREATE TABLE IF NOT EXISTS zenas_pool.sources (
    source_id        text PRIMARY KEY,
    name             text NOT NULL,
    region           text NOT NULL,             -- us-east | za. Same code, one region per deployment.
    jurisdiction     text NOT NULL,             -- us | za
    licence          text NOT NULL,
    terms            text NOT NULL,
    terms_url        text NOT NULL DEFAULT '',
    bulk_use         text NOT NULL,             -- allowed | forbidden | unknown
    store_full_text  boolean NOT NULL,
    canonical_url    text NOT NULL DEFAULT '',
    notes            text NOT NULL DEFAULT '',
    recorded_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per version of an instrument. The open wording is not inserted twice.
-- The same wording may return later (A, then B, then A). That is a new row.
CREATE TABLE IF NOT EXISTS zenas_pool.documents (
    version_id       text PRIMARY KEY,
    document_id      text NOT NULL,
    source_id        text NOT NULL REFERENCES zenas_pool.sources (source_id),
    jurisdiction     text NOT NULL,
    level            text NOT NULL,             -- federal | state | national | provincial | international
    body             text NOT NULL,             -- issuing body
    kind             text NOT NULL,             -- statute | regulation | case | court_rule | treaty | form | guidance
    citation         text NOT NULL,
    citation_key     text NOT NULL,
    title            text NOT NULL,
    doc_date         date,
    effective_from   date,                   -- official start, when the source states one
    effective_to     date,
    known_from       date,                   -- first seen. Floor for as-at when effective_from is unknown.
    status           text NOT NULL,
    url              text NOT NULL DEFAULT '',
    licence          text NOT NULL,
    text             text NOT NULL,
    content_hash     text NOT NULL,
    sections         jsonb NOT NULL DEFAULT '[]'::jsonb,
    captured_at      timestamptz NOT NULL DEFAULT now()
);

-- A database that created this table before known_from existed still gains the column.
ALTER TABLE zenas_pool.documents ADD COLUMN IF NOT EXISTS known_from date;

-- New name so a database that already created documents_hash
-- (document_id, content_hash) still gains the wider key.
-- IF NOT EXISTS would not change the old index.
CREATE UNIQUE INDEX IF NOT EXISTS documents_hash_span
    ON zenas_pool.documents (document_id, content_hash, effective_from, known_from);

CREATE INDEX IF NOT EXISTS documents_citation
    ON zenas_pool.documents (jurisdiction, citation_key);

CREATE INDEX IF NOT EXISTS documents_as_at
    ON zenas_pool.documents (document_id, effective_from, effective_to);

CREATE INDEX IF NOT EXISTS documents_fts
    ON zenas_pool.documents
    USING gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(text, '')));

-- Structure-aware pieces. A section is never split by length.
CREATE TABLE IF NOT EXISTS zenas_pool.chunks (
    chunk_id         text PRIMARY KEY,
    version_id       text NOT NULL REFERENCES zenas_pool.documents (version_id),
    document_id      text NOT NULL,
    jurisdiction     text NOT NULL,
    path             text NOT NULL DEFAULT '',
    heading          text NOT NULL DEFAULT '',
    text             text NOT NULL,
    citation         text NOT NULL,
    citation_key     text NOT NULL,
    ordinal          integer NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_citation
    ON zenas_pool.chunks (jurisdiction, citation_key);

CREATE INDEX IF NOT EXISTS chunks_version
    ON zenas_pool.chunks (version_id, ordinal);

CREATE INDEX IF NOT EXISTS chunks_fts
    ON zenas_pool.chunks
    USING gin (to_tsvector('english', coalesce(heading, '') || ' ' || coalesce(text, '')));

-- Pointers only. Vectors live in Qdrant collection law_<jurisdiction> when Hermes (C7) is wired.
CREATE TABLE IF NOT EXISTS zenas_pool.embeddings (
    chunk_id         text PRIMARY KEY REFERENCES zenas_pool.chunks (chunk_id),
    model            text NOT NULL,
    dim              integer NOT NULL,
    qdrant_collection text NOT NULL
);

-- Link-only sources (copyrighted model texts, terms that forbid bulk use).
CREATE TABLE IF NOT EXISTS zenas_pool.links (
    source_id        text PRIMARY KEY REFERENCES zenas_pool.sources (source_id),
    url              text NOT NULL,
    reason           text NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zenas_pool.updates (
    source_id        text PRIMARY KEY REFERENCES zenas_pool.sources (source_id),
    last_update      timestamptz,
    last_status      text NOT NULL DEFAULT 'never',
    documents        integer NOT NULL DEFAULT 0,
    detail           text NOT NULL DEFAULT ''
);

-- Founder switch and the env-var name. The key value is never a column.
ALTER TABLE zenas_pool.sources ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true;
ALTER TABLE zenas_pool.sources ADD COLUMN IF NOT EXISTS key_name text NOT NULL DEFAULT '';
ALTER TABLE zenas_pool.sources ADD COLUMN IF NOT EXISTS key_required boolean NOT NULL DEFAULT false;

-- Citation graph. to_document is empty until the cited instrument is in this region.
CREATE TABLE IF NOT EXISTS zenas_pool.cites (
    from_version     text NOT NULL,
    citation_text    text NOT NULL,
    to_document      text NOT NULL DEFAULT '',
    resolved         boolean NOT NULL DEFAULT false,
    PRIMARY KEY (from_version, citation_text)
);

CREATE INDEX IF NOT EXISTS cites_to_document
    ON zenas_pool.cites (to_document);

-- How far a resumable load got. A second run continues from cursor.
CREATE TABLE IF NOT EXISTS zenas_pool.checkpoints (
    source_id        text PRIMARY KEY,
    cursor           text NOT NULL DEFAULT '',
    bytes_stored     bigint NOT NULL DEFAULT 0,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
