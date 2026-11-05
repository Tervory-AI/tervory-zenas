-- Market Lab baseline feeds.
-- Additive and idempotent. Postgres applies this file as-is.
-- SQLite ATTACHes the database file as market_lab, skips CREATE SCHEMA
-- (SQLite has no schemas except ATTACH names), and rewrites index
-- statements because it rejects a schema-qualified table in CREATE INDEX.

CREATE SCHEMA IF NOT EXISTS market_lab;

CREATE TABLE IF NOT EXISTS market_lab.feed_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    licence TEXT NOT NULL,
    terms_checked_at TEXT NOT NULL,
    cadence TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    key_name TEXT NOT NULL DEFAULT '',
    key_required INTEGER NOT NULL DEFAULT 0
);

-- Existing databases created before key_name. Additive and idempotent.
ALTER TABLE market_lab.feed_sources ADD COLUMN IF NOT EXISTS key_name TEXT NOT NULL DEFAULT '';
ALTER TABLE market_lab.feed_sources ADD COLUMN IF NOT EXISTS key_required INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS market_lab.series (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    ref TEXT NOT NULL,
    title TEXT NOT NULL,
    unit TEXT NOT NULL,
    geo_level TEXT NOT NULL CHECK (geo_level IN ('national', 'state', 'county', 'port', 'metro')),
    geo TEXT NOT NULL,
    frequency TEXT NOT NULL,
    UNIQUE (source_id, ref)
);

CREATE TABLE IF NOT EXISTS market_lab.points (
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value NUMERIC NOT NULL,
    fetched_at TEXT NOT NULL,
    revision TEXT NOT NULL,
    PRIMARY KEY (series_id, date, revision)
);

CREATE TABLE IF NOT EXISTS market_lab.events (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    geo TEXT NOT NULL,
    date TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_lab.fetch_runs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT NOT NULL,
    rows INTEGER NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS points_series_idx ON market_lab.points (series_id);
CREATE INDEX IF NOT EXISTS events_source_idx ON market_lab.events (source_id, date);
CREATE INDEX IF NOT EXISTS fetch_runs_source_idx ON market_lab.fetch_runs (source_id, started);

-- Provenance and quarantine. Existing rows keep their numbers. New writes fill these.
ALTER TABLE market_lab.series ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT '';
ALTER TABLE market_lab.series ADD COLUMN IF NOT EXISTS region TEXT NOT NULL DEFAULT '';
ALTER TABLE market_lab.series ADD COLUMN IF NOT EXISTS jump_limit REAL NOT NULL DEFAULT 10;

ALTER TABLE market_lab.points ADD COLUMN IF NOT EXISTS source_id TEXT NOT NULL DEFAULT '';
ALTER TABLE market_lab.points ADD COLUMN IF NOT EXISTS region TEXT NOT NULL DEFAULT '';
ALTER TABLE market_lab.points ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT '';
ALTER TABLE market_lab.points ADD COLUMN IF NOT EXISTS unit TEXT NOT NULL DEFAULT '';
ALTER TABLE market_lab.points ADD COLUMN IF NOT EXISTS batch_id TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS market_lab.batches (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    series_id TEXT NOT NULL,
    region TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    jump_limit REAL NOT NULL DEFAULT 10,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_lab.batch_points (
    batch_id TEXT NOT NULL,
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value TEXT NOT NULL,
    source_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    region TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit TEXT NOT NULL,
    revision TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, series_id, date, revision)
);

CREATE TABLE IF NOT EXISTS market_lab.official_values (
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
);

CREATE INDEX IF NOT EXISTS batches_status_idx ON market_lab.batches (status, series_id);
