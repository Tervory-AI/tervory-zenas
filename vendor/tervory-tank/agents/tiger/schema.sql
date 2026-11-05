-- G5. Applied by watch.ensure_tables in the validated Tiger room schema.
-- Additive and idempotent; no production migration is run by the builder.
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS alert_new_product boolean NOT NULL DEFAULT true;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS alert_below_cost boolean NOT NULL DEFAULT true;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS alert_market_gap boolean NOT NULL DEFAULT true;

-- Baselines and condition transitions commit with the ordinary alert/mail rows.
CREATE TABLE IF NOT EXISTS alert_state (
    tenant_id uuid NOT NULL,
    key text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (tenant_id, key)
);

-- Tiger T1: identifier evidence and durable command/job binding. Additive only.
CREATE TABLE IF NOT EXISTS agent_tiger.matches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    sku text NOT NULL,
    url text NOT NULL,
    method text NOT NULL CHECK (method IN ('gtin','brand_mpn')),
    identifiers jsonb NOT NULL,
    seen_on text NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now(),
    state text NOT NULL CHECK (state IN ('suggested','confirmed','rejected')),
    UNIQUE (tenant_id,sku,url)
);
CREATE INDEX IF NOT EXISTS tiger_matches_product ON agent_tiger.matches(tenant_id,sku,state);
CREATE TABLE IF NOT EXISTS agent_tiger.match_runs (
    tenant_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    job_id uuid,
    ledger_id uuid NOT NULL,
    PRIMARY KEY (tenant_id,operation_id)
);
ALTER TABLE agent_tiger.match_runs ADD COLUMN IF NOT EXISTS progress jsonb;
ALTER TABLE agent_tiger.match_runs ADD COLUMN IF NOT EXISTS cursor jsonb;
CREATE TABLE IF NOT EXISTS agent_tiger.match_sightings (
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    root_job_id uuid NOT NULL,
    match_id uuid NOT NULL REFERENCES agent_tiger.matches(id),
    evidence jsonb NOT NULL,
    PRIMARY KEY (tenant_id,job_id,match_id)
);
CREATE INDEX IF NOT EXISTS tiger_sightings_run ON agent_tiger.match_sightings(tenant_id,root_job_id);
-- Public host counters contain no catalogue, tenant identifiers or page content.
-- A shared counter prevents concurrent jobs/tenants exceeding a site's allowance.
CREATE TABLE IF NOT EXISTS agent_tiger.discovery_limits (
    site text PRIMARY KEY,
    day date NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')::date,
    requests integer NOT NULL DEFAULT 0,
    last_at timestamptz,
    human_check boolean NOT NULL DEFAULT false
);
-- Join the platform rollout exactly as the other room-owned tables do.
DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['matches','match_runs','match_sightings'] LOOP
        IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='tank' AND c.relname='command_threads' AND c.relrowsecurity) THEN
            IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid=('agent_tiger.'||tab)::regclass) THEN
                EXECUTE format('ALTER TABLE agent_tiger.%I ENABLE ROW LEVEL SECURITY',tab);
            END IF;
            IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE oid=('agent_tiger.'||tab)::regclass) THEN
                EXECUTE format('ALTER TABLE agent_tiger.%I FORCE ROW LEVEL SECURITY',tab);
            END IF;
        ELSE
            IF (SELECT relforcerowsecurity FROM pg_class WHERE oid=('agent_tiger.'||tab)::regclass) THEN
                EXECUTE format('ALTER TABLE agent_tiger.%I NO FORCE ROW LEVEL SECURITY',tab);
            END IF;
            IF (SELECT relrowsecurity FROM pg_class WHERE oid=('agent_tiger.'||tab)::regclass) THEN
                EXECUTE format('ALTER TABLE agent_tiger.%I DISABLE ROW LEVEL SECURITY',tab);
            END IF;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='agent_tiger'
                       AND tablename=tab AND policyname='tenant_access') THEN
            EXECUTE format('CREATE POLICY tenant_access ON agent_tiger.%I USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',tab);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='agent_tiger'
                       AND tablename=tab AND policyname='tenant_boundary') THEN
            EXECUTE format('CREATE POLICY tenant_boundary ON agent_tiger.%I AS RESTRICTIVE USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',tab);
        END IF;
    END LOOP;
END
$$;
DO $$
DECLARE tab text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='agent_tiger') THEN
        FOREACH tab IN ARRAY ARRAY['matches','match_runs','match_sightings','discovery_limits'] LOOP
            IF NOT has_table_privilege('agent_tiger','agent_tiger.'||tab,'SELECT')
               OR NOT has_table_privilege('agent_tiger','agent_tiger.'||tab,'INSERT')
               OR NOT has_table_privilege('agent_tiger','agent_tiger.'||tab,'UPDATE') THEN
                EXECUTE format('GRANT SELECT,INSERT,UPDATE ON agent_tiger.%I TO agent_tiger',tab);
            END IF;
        END LOOP;
    END IF;
END $$;

-- BEGIN GENERATED AGENT MEMORY (hermes/record_schema.py)
CREATE TABLE IF NOT EXISTS agent_tiger.memory_records (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    agent text NOT NULL DEFAULT 'tiger' CHECK (agent = 'tiger'),
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
CREATE INDEX IF NOT EXISTS memory_records_scope ON agent_tiger.memory_records (tenant_id, tier, created_at, id);
CREATE INDEX IF NOT EXISTS memory_records_encoder ON agent_tiger.memory_records (tenant_id, embed_model);
CREATE TABLE IF NOT EXISTS agent_tiger.memory_index_jobs (
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
CREATE INDEX IF NOT EXISTS memory_index_jobs_due ON agent_tiger.memory_index_jobs (run_at);
CREATE TABLE IF NOT EXISTS agent_tiger.memory_publish_policy (
    tenant_id uuid PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    identifiers jsonb NOT NULL DEFAULT '{}',
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_tiger.memory_publish_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    actor text NOT NULL,
    happened_at timestamptz NOT NULL DEFAULT now(),
    source_row uuid NOT NULL,
    result text NOT NULL CHECK (result IN ('disabled','missing','refused','published','already_published')),
    published_row uuid
);
-- END GENERATED AGENT MEMORY
