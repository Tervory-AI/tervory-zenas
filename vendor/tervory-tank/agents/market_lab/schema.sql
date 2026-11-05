-- Market Lab box. Additive and idempotent. Applied by the agent on start.
-- Predictions are append-only: the application inserts a new row to resolve
-- or correct one. It never updates a row that was already inserted.

CREATE SCHEMA IF NOT EXISTS agent_market_lab;

-- Tenant-selected public feeds, checked by the existing scheduled-job worker.
CREATE TABLE IF NOT EXISTS agent_market_lab.feed_links (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    url text NOT NULL,
    label text NOT NULL DEFAULT '',
    cadence text NOT NULL CHECK (cadence IN ('daily', 'weekly')),
    next_check timestamptz NOT NULL DEFAULT now(),
    last_checked timestamptz,
    status text NOT NULL DEFAULT 'not_checked' CHECK (status IN ('not_checked', 'checked', 'failed')),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, url)
);

CREATE TABLE IF NOT EXISTS agent_market_lab.series (
    id          text PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    source      text NOT NULL,
    ref         text NOT NULL,
    unit        text NOT NULL,
    engine      text NOT NULL
);

CREATE INDEX IF NOT EXISTS series_tenant_idx
    ON agent_market_lab.series (tenant_id, source);

CREATE TABLE IF NOT EXISTS agent_market_lab.points (
    series_id   text NOT NULL,
    date        text NOT NULL,
    value       numeric NOT NULL,
    fetched_at  text NOT NULL,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS agent_market_lab.events (
    series_id   text NOT NULL,
    date        text NOT NULL,
    kind        text NOT NULL,
    size        numeric NOT NULL,
    PRIMARY KEY (series_id, date, kind)
);

CREATE TABLE IF NOT EXISTS agent_market_lab.opportunities (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    made_at       timestamptz NOT NULL,
    area          text NOT NULL,
    segment       text NOT NULL,
    demand_idx    numeric NOT NULL,
    supply_idx    numeric NOT NULL,
    gap           numeric NOT NULL,
    saturation    numeric NOT NULL,
    reachability  numeric NOT NULL,
    trend         jsonb NOT NULL,
    rank          integer NOT NULL,
    evidence      jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS opportunities_tenant_idx
    ON agent_market_lab.opportunities (tenant_id, made_at);

CREATE TABLE IF NOT EXISTS agent_market_lab.predictions (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL,
    made_at           timestamptz NOT NULL,
    statement         text NOT NULL,
    metric_series_id  text NOT NULL,
    comparator        text NOT NULL,
    target            numeric NOT NULL,
    resolve_by        timestamptz NOT NULL,
    rationale         text NOT NULL,
    sources           jsonb NOT NULL,
    outcome           text,
    resolved_at       timestamptz,
    score             numeric
);

-- Gate 2 adds metadata without changing historical prediction rows.
ALTER TABLE agent_market_lab.predictions ADD COLUMN IF NOT EXISTS horizon_seconds bigint;
ALTER TABLE agent_market_lab.predictions ADD COLUMN IF NOT EXISTS confidence double precision
    CHECK (confidence >= 0 AND confidence <= 1 AND confidence NOT IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8));
ALTER TABLE agent_market_lab.predictions ADD COLUMN IF NOT EXISTS realised_value numeric;
ALTER TABLE agent_market_lab.predictions ADD COLUMN IF NOT EXISTS realised_date date;
ALTER TABLE agent_market_lab.predictions ADD COLUMN IF NOT EXISTS kind text;
ALTER TABLE agent_market_lab.predictions ADD COLUMN IF NOT EXISTS feed text;

CREATE INDEX IF NOT EXISTS predictions_tenant_idx
    ON agent_market_lab.predictions (tenant_id, resolve_by);

CREATE TABLE IF NOT EXISTS agent_market_lab.reports (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    made_at     timestamptz NOT NULL,
    question    text NOT NULL,
    answer      text NOT NULL,
    citations   jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS reports_tenant_idx
    ON agent_market_lab.reports (tenant_id, made_at);

-- ML6 report/channel claims remain in this room. Sending is never reclaimed.
CREATE TABLE IF NOT EXISTS agent_market_lab.brief_deliveries (
    tenant_id uuid NOT NULL,
    report_id uuid NOT NULL REFERENCES agent_market_lab.reports(id),
    channel text NOT NULL CHECK (channel IN ('email','webhook')),
    status text NOT NULL CHECK (status IN ('pending','sending','sent','uncertain','suppressed','held')),
    reason text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, report_id, channel)
);
CREATE INDEX IF NOT EXISTS brief_deliveries_pending
    ON agent_market_lab.brief_deliveries (tenant_id, channel, report_id) WHERE status='pending';

-- Provenance on every stored point. A batch stays out of the pool until it passes.
ALTER TABLE agent_market_lab.series ADD COLUMN IF NOT EXISTS region text NOT NULL DEFAULT '';
ALTER TABLE agent_market_lab.series ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT '';
ALTER TABLE agent_market_lab.series ADD COLUMN IF NOT EXISTS jump_limit numeric NOT NULL DEFAULT 10;

ALTER TABLE agent_market_lab.points ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT '';
ALTER TABLE agent_market_lab.points ADD COLUMN IF NOT EXISTS region text NOT NULL DEFAULT '';
ALTER TABLE agent_market_lab.points ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT '';
ALTER TABLE agent_market_lab.points ADD COLUMN IF NOT EXISTS unit text NOT NULL DEFAULT '';
ALTER TABLE agent_market_lab.points ADD COLUMN IF NOT EXISTS batch_id text NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS agent_market_lab.batches (
    id          text PRIMARY KEY,
    tenant_id   uuid NOT NULL,
    series_id   text NOT NULL,
    source      text NOT NULL,
    fetched_at  text NOT NULL,
    region      text NOT NULL,
    currency    text NOT NULL,
    unit        text NOT NULL,
    status      text NOT NULL,
    reason      text NOT NULL DEFAULT '',
    jump_limit  numeric NOT NULL DEFAULT 10,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS batches_tenant_idx
    ON agent_market_lab.batches (tenant_id, status);

CREATE TABLE IF NOT EXISTS agent_market_lab.batch_points (
    batch_id    text NOT NULL,
    tenant_id   uuid NOT NULL,
    series_id   text NOT NULL,
    date        text NOT NULL,
    value       numeric NOT NULL,
    source      text NOT NULL,
    fetched_at  text NOT NULL,
    region      text NOT NULL,
    currency    text NOT NULL,
    unit        text NOT NULL,
    PRIMARY KEY (batch_id, series_id, date)
);

CREATE TABLE IF NOT EXISTS agent_market_lab.official_values (
    tenant_id   uuid NOT NULL,
    series_id   text NOT NULL,
    date        text NOT NULL,
    value       numeric NOT NULL,
    PRIMARY KEY (tenant_id, series_id, date)
);

-- Immutable, tenant-owned source documents. Fixture documents are never served.
CREATE TABLE IF NOT EXISTS agent_market_lab.feed_documents (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    source_id text NOT NULL,
    fetched_at timestamptz NOT NULL,
    fixture boolean NOT NULL DEFAULT false,
    content jsonb NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE TABLE IF NOT EXISTS agent_market_lab.feed_checks (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    source_id text NOT NULL,
    checked_at timestamptz NOT NULL,
    fixture boolean NOT NULL DEFAULT false,
    rows integer NOT NULL CHECK (rows >= 0),
    note text NOT NULL,
    document_id uuid,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, document_id) REFERENCES agent_market_lab.feed_documents (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS feed_checks_tenant_source_idx
    ON agent_market_lab.feed_checks (tenant_id, source_id, checked_at DESC);

-- A warning freezes the prediction and the evidence used to issue it.
CREATE TABLE IF NOT EXISTS agent_market_lab.warning_pairs (
    tenant_id uuid NOT NULL, pair_key text NOT NULL, document_id uuid NOT NULL,
    summary jsonb NOT NULL, updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, pair_key)
);
CREATE TABLE IF NOT EXISTS agent_market_lab.warnings (
    tenant_id uuid NOT NULL, id uuid NOT NULL, pair_key text NOT NULL,
    event_date date NOT NULL, prediction_id uuid NOT NULL, document_id uuid NOT NULL,
    downstream_id text NOT NULL, config jsonb NOT NULL, baseline jsonb NOT NULL,
    window_start date NOT NULL, window_end date NOT NULL, created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, pair_key, event_date)
);
CREATE TABLE IF NOT EXISTS agent_market_lab.warning_resolutions (
    tenant_id uuid NOT NULL, warning_id uuid NOT NULL, prediction_id uuid NOT NULL,
    document_id uuid NOT NULL, outcome text NOT NULL, score numeric,
    resolved_at timestamptz NOT NULL, PRIMARY KEY (tenant_id, warning_id),
    FOREIGN KEY (tenant_id, warning_id) REFERENCES agent_market_lab.warnings (tenant_id, id)
);
CREATE TABLE IF NOT EXISTS agent_market_lab.warning_training (
    tenant_id uuid NOT NULL, id uuid NOT NULL, warning_id uuid NOT NULL,
    kind text NOT NULL, actor text NOT NULL, input jsonb NOT NULL, output jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, warning_id) REFERENCES agent_market_lab.warnings (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS warning_training_pending
    ON agent_market_lab.warning_training (tenant_id, created_at) WHERE delivered_at IS NULL;

-- Room-owned document memory. Deletion removes this row and its guarded series.
CREATE TABLE IF NOT EXISTS agent_market_lab.uploaded_files (
    tenant_id uuid NOT NULL, id uuid NOT NULL, content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id,id)
);

-- H7: append-only observations of delivery state, never inferred past sends.
CREATE TABLE IF NOT EXISTS agent_market_lab.brief_delivery_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id uuid NOT NULL,
    report_id uuid NOT NULL,
    channel text NOT NULL,
    status text NOT NULL,
    reason text,
    happened_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,report_id,channel)
      REFERENCES agent_market_lab.brief_deliveries(tenant_id,report_id,channel)
);
CREATE INDEX IF NOT EXISTS brief_history_scope
    ON agent_market_lab.brief_delivery_history(tenant_id,report_id,happened_at,id);

-- BEGIN GENERATED AGENT MEMORY (hermes/record_schema.py)
CREATE TABLE IF NOT EXISTS agent_market_lab.memory_records (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    agent text NOT NULL DEFAULT 'market_lab' CHECK (agent = 'market_lab'),
    claim text NOT NULL CHECK (length(claim) BETWEEN 1 AND 8000),
    evidence jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence) = 'array'),
    confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    last_verified timestamptz,
    embed_model text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    tier text NOT NULL DEFAULT 'client' CHECK (tier IN ('client','platform')),
    kind text NOT NULL DEFAULT 'fact',
    meta jsonb NOT NULL DEFAULT '{}',
    seen integer NOT NULL DEFAULT 1,
    CHECK (tier <> 'platform' OR tenant_id = '00000000-0000-0000-0000-000000000001'::uuid)
);
CREATE INDEX IF NOT EXISTS memory_records_scope ON agent_market_lab.memory_records (tenant_id, tier, created_at, id);
CREATE INDEX IF NOT EXISTS memory_records_encoder ON agent_market_lab.memory_records (tenant_id, embed_model);
CREATE TABLE IF NOT EXISTS agent_market_lab.memory_index_jobs (
    row_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    target_model text NOT NULL,
    operation text NOT NULL DEFAULT 'upsert' CHECK (operation IN ('upsert','delete')),
    attempts integer NOT NULL DEFAULT 0,
    run_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, row_id, target_model, operation)
);
CREATE INDEX IF NOT EXISTS memory_index_jobs_due ON agent_market_lab.memory_index_jobs (run_at);
CREATE TABLE IF NOT EXISTS agent_market_lab.memory_publish_policy (
    tenant_id uuid PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    identifiers jsonb NOT NULL DEFAULT '{}',
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_market_lab.memory_publish_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    actor text NOT NULL,
    happened_at timestamptz NOT NULL DEFAULT now(),
    source_row uuid NOT NULL,
    result text NOT NULL CHECK (result IN ('disabled','missing','refused','published','already_published')),
    published_row uuid
);
-- END GENERATED AGENT MEMORY
