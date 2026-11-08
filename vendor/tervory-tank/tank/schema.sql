-- Tank schema. Applied by Tank on every start; every statement is idempotent.
-- Build Pack v2, "Tank — the centrepiece": ledger, policy, evals, tasks.

CREATE SCHEMA IF NOT EXISTS tank;

-- One row per model call. Never updated after insert except outcome/feedback.
CREATE TABLE IF NOT EXISTS tank.ledger (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ts              timestamptz NOT NULL DEFAULT now(),
    tenant_id       uuid NOT NULL,
    region          text NOT NULL DEFAULT 'us-east',
    agent           text NOT NULL,            -- tier0 | base | tiger | market_lab | radix | tisan | zenas | orchestrator
    skill           text,                     -- Skala skill id@version, null for Tier 0
    task_type       text NOT NULL,            -- policy key, e.g. tier0.answer
    rung            smallint NOT NULL DEFAULT 1,  -- 1 frontier, 2 owned model, 3 code, 4 cache
    model           text NOT NULL,            -- LiteLLM model_name actually used
    priority        text NOT NULL DEFAULT 'standard',  -- urgent | standard | batch
    tokens_in       integer NOT NULL DEFAULT 0,
    tokens_out      integer NOT NULL DEFAULT 0,
    cost_usd        numeric(12,6) NOT NULL DEFAULT 0,
    latency_ms      integer,
    cache_hit       boolean NOT NULL DEFAULT false,
    eval_score      numeric(5,4),             -- null unless sampled by the eval harness
    outcome         text NOT NULL DEFAULT 'delivered'  -- delivered | failed | unverified
);
CREATE INDEX IF NOT EXISTS ledger_tenant_ts ON tank.ledger (tenant_id, ts DESC);
CREATE INDEX IF NOT EXISTS ledger_task_ts   ON tank.ledger (task_type, ts DESC);

-- Human verdict on a delivered answer. This column is what outcome-based billing,
-- the eval harness and the graduation loop all read. Null = nobody has said yet.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS feedback      text;         -- accepted | rejected
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS feedback_note text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS feedback_at   timestamptz;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS feedback_by   text;         -- user id of who said so
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS task_id       uuid;         -- set when the call ran a task
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS error         text;         -- why a failed call failed: upstream status and a short message

-- Routing policy. Data, not code: change a row, not a deploy.
CREATE TABLE IF NOT EXISTS tank.policy (
    task_type       text NOT NULL,
    tenant_id       uuid,                     -- null = default for every tenant
    rung            smallint NOT NULL DEFAULT 1,
    model           text NOT NULL,            -- LiteLLM model_name
    priority        text NOT NULL DEFAULT 'standard',
    batch_eligible  boolean NOT NULL DEFAULT false,
    cache_scope     text NOT NULL DEFAULT 'none',   -- none | tenant | platform
    max_tokens_out  integer NOT NULL DEFAULT 1024,
    system_prompt   text,
    eval_threshold  numeric(5,4) NOT NULL DEFAULT 0.97,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
-- Graduation: a cheaper candidate runs in the shadow of the current model on a share of calls.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS candidate_model text;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS shadow_rate numeric(4,3) NOT NULL DEFAULT 0;   -- 0..1 of calls shadowed
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS previous_model text;

-- One shadow comparison per sampled call. agreement 0..1: how close the candidate came to the primary.
CREATE TABLE IF NOT EXISTS tank.shadow (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ledger_id       uuid NOT NULL,            -- the primary call
    tenant_id       uuid NOT NULL,
    task_type       text NOT NULL,
    primary_model   text NOT NULL,
    candidate_model text NOT NULL,
    primary_content text NOT NULL,
    candidate_content text,
    candidate_cost_usd numeric(12,6) NOT NULL DEFAULT 0,
    primary_cost_usd   numeric(12,6) NOT NULL DEFAULT 0,
    agreement       numeric(5,4),             -- null until judged
    method          text,                     -- json | judge | failed
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shadow_task ON tank.shadow (task_type, created_at DESC);

-- Promotions and demotions, so the ladder has a history.
CREATE TABLE IF NOT EXISTS tank.policy_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type       text NOT NULL,
    action          text NOT NULL,            -- promote | candidate | rollback
    from_model      text,
    to_model        text,
    by_user         text,
    detail          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- One policy row per task type per tenant; a null tenant is the platform default.
CREATE UNIQUE INDEX IF NOT EXISTS policy_task_tenant
    ON tank.policy (task_type, COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- What was asked and what came back, one row per model call. This is the learning corpus:
-- feedback turns rows here into golden cases; the graduation loop replays them against candidates.
CREATE TABLE IF NOT EXISTS tank.transcripts (
    ledger_id       uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    task_type       text NOT NULL,
    messages        jsonb NOT NULL,           -- as sent; agent chat includes its policy system prompt
    content         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transcripts_task ON tank.transcripts (task_type, created_at DESC);

-- Golden cases per task type for the eval harness (build order step 5).
CREATE TABLE IF NOT EXISTS tank.evals (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type       text NOT NULL,
    input           jsonb NOT NULL,
    expected        jsonb NOT NULL,
    tolerance       text NOT NULL DEFAULT 'exact',  -- exact | contains | semantic
    source          text NOT NULL DEFAULT 'manual', -- manual | tester_feedback
    created_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tank.evals ADD COLUMN IF NOT EXISTS tenant_id uuid;          -- who the case came from (null = platform)
ALTER TABLE tank.evals ADD COLUMN IF NOT EXISTS ledger_id uuid;          -- the call it was made from
ALTER TABLE tank.evals ADD COLUMN IF NOT EXISTS verdict text NOT NULL DEFAULT 'accepted';  -- accepted | rejected
ALTER TABLE tank.evals ADD COLUMN IF NOT EXISTS note text;
CREATE UNIQUE INDEX IF NOT EXISTS evals_ledger ON tank.evals (ledger_id) WHERE ledger_id IS NOT NULL;

-- A unit of work a person asked for. The board in the Workspace is a view of this table.
-- Today a task is one model call; later a task fans out to an agent run of many calls,
-- all stamped with this task_id in the ledger.
CREATE TABLE IF NOT EXISTS tank.tasks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    created_by      text,                     -- user id of who asked
    agent           text NOT NULL DEFAULT 'tier0',
    task_type       text NOT NULL DEFAULT 'tier0.answer',
    title           text NOT NULL,
    input           jsonb NOT NULL,           -- the messages as submitted
    status          text NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
    result          text,
    error           text,
    ledger_id       uuid,                     -- the call that produced the result
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz
);
CREATE INDEX IF NOT EXISTS tasks_tenant_created ON tank.tasks (tenant_id, created_at DESC);

-- Tier 0 defaults. Haiku for everything that is not a judgment call.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('tier0.answer',   1, 'haiku',  'urgent',   'platform', 1024,
   'You are Tervory, an assistant for entrepreneurs. Answer plainly and briefly. If the request needs research, data work, price monitoring, security checks, a build, or legal documents, say which of those it is rather than attempting it.'),
  ('tier0.draft',    1, 'haiku',  'urgent',   'none',     2048,
   'You draft short business writing (emails, messages, summaries) in plain, direct English. No preamble.'),
  ('route.request',  1, 'haiku',  'urgent',   'none',     64,
   'Classify the request into exactly one of: tier0, base, tiger, market_lab, radix, tisan, zenas. Reply with the label only.'),
  ('reason.brief',   1, 'sonnet', 'standard', 'none',     4096,
   'You write concise, well-structured analysis for a business owner. Lead with the conclusion.')
ON CONFLICT DO NOTHING;

-- The judge: scores whether two answers say the same thing. Platform-only, never billed to a client.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('tank.judge', 1, 'haiku', 'batch', 'none', 64,
   'You compare two answers to the same request. Reply with one integer 0-100: how closely the second answer matches the first in conclusion and key facts. 100 = same conclusion and facts; 0 = contradicts or misses the point. Reply with the number only.')
ON CONFLICT DO NOTHING;

-- First graduation candidate: reason.brief from Sonnet to Haiku, every call shadowed until promoted.
UPDATE tank.policy SET candidate_model = 'haiku', shadow_rate = 1.0
WHERE task_type = 'reason.brief' AND tenant_id IS NULL AND model = 'sonnet' AND candidate_model IS NULL;

-- ---------- Skala: the skills registry (Phase 1 = this table + the skills/ folder in Git) ----------
CREATE TABLE IF NOT EXISTS tank.skills (
    id              text NOT NULL,
    version         text NOT NULL,
    agent           text NOT NULL,
    rung            smallint NOT NULL DEFAULT 1,
    task_type       text,                     -- policy row the skill routes through (model skills only)
    description     text NOT NULL DEFAULT '',
    state           text NOT NULL DEFAULT 'candidate',  -- candidate | staging | approved | retired
    eval_threshold  numeric(5,4) NOT NULL DEFAULT 0.95,
    cases           integer NOT NULL DEFAULT 0,
    last_eval_score numeric(5,4),
    last_eval_at    timestamptz,
    models_validated jsonb NOT NULL DEFAULT '[]'::jsonb,
    approved_by     text,
    approved_at     timestamptz,
    present         boolean NOT NULL DEFAULT true,   -- false when the package has gone from disk
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS tank.skill_evals (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        text NOT NULL,
    version         text NOT NULL,
    score           numeric(5,4) NOT NULL,
    passed          integer NOT NULL,
    total           integer NOT NULL,
    cost_usd        numeric(12,6) NOT NULL DEFAULT 0,
    details         jsonb NOT NULL,
    ran_by          text,
    ran_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS skill_evals_skill ON tank.skill_evals (skill_id, version, ran_at DESC);

-- ---------- Base: enriched records ----------
CREATE SCHEMA IF NOT EXISTS base;
CREATE TABLE IF NOT EXISTS base.records (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    task_id         uuid,                     -- the Tank task that ran the batch
    batch           text,                     -- a label the client gave the upload
    raw             jsonb NOT NULL,           -- the input record as received
    profile         jsonb NOT NULL DEFAULT '{}'::jsonb,  -- field -> {value, source, confidence}
    passes          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- pass -> {skill, ms, note}
    confidence      smallint NOT NULL DEFAULT 0,         -- 0-100 overall (pass 7 owns this later)
    status          text NOT NULL DEFAULT 'queued',      -- queued | running | done | failed
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS base_records_tenant ON base.records (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS base_records_task   ON base.records (task_id);

-- Policy rows for the model steps in Base (pass 2c extract, pass 5 business data).
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('base.extract', 1, 'haiku', 'batch', 'tenant', 600,
   'You extract structured facts from website text and reply with strict JSON only.'),
  ('base.business_data', 1, 'haiku', 'batch', 'tenant', 600,
   'You estimate the size and shape of a small business from sparse signals, conservatively, and reply with strict JSON only.')
ON CONFLICT (task_type, COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid))
DO UPDATE SET max_tokens_out = GREATEST(tank.policy.max_tokens_out, EXCLUDED.max_tokens_out);
-- (the cap only ever goes up from the repo; an operator's higher setting is kept)

-- Per-agent Tank keys. The raw key stays in TANK_KEY_<AGENT>. This table stores SHA-256 hex only.
-- Database roles are separate from this file. The agent-roles service creates login
-- role agent_<name> from AGENT_DB_PASSWORD_<AGENT>, with USAGE and CREATE on schema
-- agent_<name> only. The password is never stored here. See agents/_chassis/chassis/roles.py.
CREATE TABLE IF NOT EXISTS tank.agent_keys (
    agent           text PRIMARY KEY,
    key_hash        text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Founder console. Booked revenue is outcomes × price book until billing exists.
-- A null tenant_id on the price book is the default for every tenant.
CREATE TABLE IF NOT EXISTS tank.price_book (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid,
    outcome_type    text NOT NULL,
    unit_price      numeric(12,4) NOT NULL,
    currency        text NOT NULL DEFAULT 'USD',
    effective_from  timestamptz NOT NULL DEFAULT now(),
    effective_to    timestamptz,
    note            text
);
CREATE INDEX IF NOT EXISTS price_book_type_from ON tank.price_book (outcome_type, effective_from DESC);

-- One row per billable outcome. ref is idempotent for backfills (one base.record_enriched per done record).
CREATE TABLE IF NOT EXISTS tank.outcomes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ts              timestamptz NOT NULL DEFAULT now(),
    tenant_id       uuid NOT NULL,
    agent           text NOT NULL,
    outcome_type    text NOT NULL,
    quantity        numeric(12,4) NOT NULL DEFAULT 1,
    ref             text,
    ledger_ids      uuid[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS outcomes_tenant_ts ON tank.outcomes (tenant_id, ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS outcomes_type_ref ON tank.outcomes (outcome_type, ref) WHERE ref IS NOT NULL;

-- Nightly rollup. History stays here after the ledger is pruned.
-- model is stored so a route can lose proven status later without the original calls.
CREATE TABLE IF NOT EXISTS tank.econ_daily (
    day             date NOT NULL,
    tenant_id       uuid NOT NULL,
    agent           text NOT NULL,
    task_type       text NOT NULL,
    rung            smallint NOT NULL,
    model           text NOT NULL DEFAULT '',
    calls           integer NOT NULL DEFAULT 0,
    cost_usd        numeric(14,6) NOT NULL DEFAULT 0,
    outcomes        numeric(14,4) NOT NULL DEFAULT 0,
    booked_usd      numeric(14,4) NOT NULL DEFAULT 0,
    cache_hits      integer NOT NULL DEFAULT 0,
    proven_calls    integer NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tenant_id, agent, task_type, rung, model)
);

-- Evidence that a route is proven for a task type. Recomputed from evals, shadow runs and client feedback.
CREATE TABLE IF NOT EXISTS tank.route_evidence (
    task_type           text NOT NULL,
    model               text NOT NULL,
    rung                smallint NOT NULL,
    eval_score          numeric(5,4),
    shadow_agreement    numeric(5,4),
    shadow_n            integer NOT NULL DEFAULT 0,
    acceptance_rate     numeric(5,4),
    judged              integer NOT NULL DEFAULT 0,
    rejected            integer NOT NULL DEFAULT 0,
    proven              boolean NOT NULL DEFAULT false,
    proven_at           timestamptz,
    lost_at             timestamptz,
    flag                text,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_type, model, rung)
);

-- Policy bars for "proven". Defaults match the graduation view (30 samples, agreement at eval_threshold).
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS min_shadow_samples integer NOT NULL DEFAULT 30;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS max_reject_rate numeric(5,4) NOT NULL DEFAULT 0.05;

-- The hourly rollup counts recent client verdicts. This index keeps that off a full ledger scan.
CREATE INDEX IF NOT EXISTS ledger_feedback ON tank.ledger (feedback);

-- Serving-route fields the focus list reads. The rollup writes them; the page does not recompute them.
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS candidate_model text;
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS candidate_shadow_n integer NOT NULL DEFAULT 0;
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS candidate_agreement numeric(5,4);
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS min_shadow_samples integer NOT NULL DEFAULT 30;
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS eval_threshold numeric(5,4);
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS is_serving boolean NOT NULL DEFAULT false;

-- Shadow pair costs, replaced by the hourly rollup. Graduations read this instead of scanning tank.shadow.
CREATE TABLE IF NOT EXISTS tank.shadow_pair (
    task_type        text NOT NULL,
    primary_model    text NOT NULL,
    candidate_model  text NOT NULL,
    n                integer NOT NULL DEFAULT 0,
    agreement        numeric(8,6),
    prim_cost        numeric(14,6),
    cand_cost        numeric(14,6),
    PRIMARY KEY (task_type, primary_model, candidate_model)
);

-- Founder console rollup reads the ledger by time (ts >= window start) across every tenant.
CREATE INDEX IF NOT EXISTS ledger_ts ON tank.ledger (ts);

-- Two prices for the same outcome and effective date: the one saved last wins.
ALTER TABLE tank.price_book ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

-- Market Lab's research skill (C9) routes through this row; without it Tank answers 404 "no policy".
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('market.research', 1, 'sol', 'standard', 'tenant', 2048,
   'You answer market questions briefly. Every claim carries a source URL and a date from the material given; say "unknown" rather than guess.')
ON CONFLICT DO NOTHING;

-- ---------- 14 Oct 2026: work moves to the funded providers (OpenAI, Google, xAI) by API ----------
-- One-time switch, recorded in policy_log so it never runs twice and never overrides a later
-- operator choice (e.g. moving rows back to Anthropic after the top-up via PUT /v1/policy or rollback).
-- previous_model keeps the Anthropic name, so POST .../rollback returns a row to it in one call.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM tank.policy_log WHERE action = 'switch' AND detail->>'id' = '2026-10-14-funded-providers') THEN
    INSERT INTO tank.policy_log (task_type, action, from_model, to_model, by_user, detail)
    SELECT task_type, 'switch', model,
           CASE WHEN task_type = 'tank.judge' THEN 'flash-lite'
                WHEN model = 'haiku' THEN 'luna' WHEN model = 'sonnet' THEN 'sol' ELSE 'astra' END,
           'claude', '{"id": "2026-10-14-funded-providers"}'::jsonb
    FROM tank.policy WHERE tenant_id IS NULL AND model IN ('haiku', 'sonnet', 'opus');

    -- The judge sits on a different provider from the models it grades.
    UPDATE tank.policy SET previous_model = model, model = 'flash-lite', updated_at = now()
    WHERE tenant_id IS NULL AND task_type = 'tank.judge' AND model IN ('haiku', 'sonnet', 'opus');

    UPDATE tank.policy SET previous_model = model,
           model = CASE model WHEN 'haiku' THEN 'luna' WHEN 'sonnet' THEN 'sol' ELSE 'astra' END,
           updated_at = now()
    WHERE tenant_id IS NULL AND model IN ('haiku', 'sonnet', 'opus');

    UPDATE tank.policy SET candidate_model =
           CASE candidate_model WHEN 'haiku' THEN 'luna' WHEN 'sonnet' THEN 'sol' ELSE 'astra' END
    WHERE tenant_id IS NULL AND candidate_model IN ('haiku', 'sonnet', 'opus');

    -- Marker row, so the block is a no-op from now on even if no rows matched.
    INSERT INTO tank.policy_log (task_type, action, by_user, detail)
    VALUES ('*', 'switch', 'claude', '{"id": "2026-10-14-funded-providers", "marker": true}'::jsonb);
  END IF;
END
$$;

-- ---------- 14 Oct 2026 (later): Gemini carries the work until the OpenAI account is ready ----------
-- Founder decision: "just use Gemini for now". One-time and logged, same pattern as the switch above.
-- To move back to OpenAI later: PUT /v1/policy/<task_type> with model luna/sol, or a new logged block.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM tank.policy_log WHERE action = 'switch' AND detail->>'id' = '2026-10-14-gemini-only') THEN
    INSERT INTO tank.policy_log (task_type, action, from_model, to_model, by_user, detail)
    SELECT task_type, 'switch', model,
           CASE WHEN task_type = 'tank.judge' THEN 'flash' WHEN model = 'luna' THEN 'flash-lite' ELSE 'argon' END,
           'claude', '{"id": "2026-10-14-gemini-only"}'::jsonb
    FROM tank.policy
    WHERE tenant_id IS NULL AND (model IN ('luna', 'sol', 'astra') OR task_type = 'tank.judge');

    UPDATE tank.policy SET model = CASE model WHEN 'luna' THEN 'flash-lite' ELSE 'argon' END, updated_at = now()
    WHERE tenant_id IS NULL AND model IN ('luna', 'sol', 'astra') AND task_type <> 'tank.judge';

    -- The judge stays a different model from the ones it grades.
    UPDATE tank.policy SET model = 'flash', updated_at = now()
    WHERE tenant_id IS NULL AND task_type = 'tank.judge';

    UPDATE tank.policy SET candidate_model = 'flash-lite'
    WHERE tenant_id IS NULL AND candidate_model = 'luna';

    INSERT INTO tank.policy_log (task_type, action, by_user, detail)
    VALUES ('*', 'switch', 'claude', '{"id": "2026-10-14-gemini-only", "marker": true}'::jsonb);
  END IF;
END
$$;

-- Zenas scope gate (Z1). Model is Gemini Flash-Lite while Gemini carries the work.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('zenas.scope', 1, 'flash-lite', 'urgent', 'tenant', 400,
   'You classify a request for Zenas. Reply with strict JSON only: {"area":"<id>","jurisdiction":"us|za|both"}. Use an allowed area id or "none". Do not give legal advice. Do not help with criminal law.')
ON CONFLICT DO NOTHING;

-- ---------- 14 Oct 2026: platform rows start at the cheapest model of their tier ----------
-- With cheapest-available fallbacks in litellm/config.yaml, each row names the cheapest model and the
-- gateway walks up the price list to the first provider with funds. The judge keeps its own model.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM tank.policy_log WHERE action = 'switch' AND detail->>'id' = '2026-10-14-cheapest-available') THEN
    INSERT INTO tank.policy_log (task_type, action, from_model, to_model, by_user, detail)
    SELECT task_type, 'switch', model, CASE model WHEN 'flash-lite' THEN 'luna' ELSE 'grok-top' END,
           'claude', '{"id": "2026-10-14-cheapest-available"}'::jsonb
    FROM tank.policy WHERE tenant_id IS NULL AND task_type <> 'tank.judge' AND model IN ('flash-lite', 'argon');
    UPDATE tank.policy SET model = CASE model WHEN 'flash-lite' THEN 'luna' ELSE 'grok-top' END, updated_at = now()
    WHERE tenant_id IS NULL AND task_type <> 'tank.judge' AND model IN ('flash-lite', 'argon');
    INSERT INTO tank.policy_log (task_type, action, by_user, detail)
    VALUES ('*', 'switch', 'claude', '{"id": "2026-10-14-cheapest-available", "marker": true}'::jsonb);
  END IF;
END
$$;

-- ---------- B3: Base records move to the Base room ----------
-- One copy, then a log row. The old table stays in place. A trigger makes it
-- read-only; Claude retires it later. Every column is kept.
CREATE SCHEMA IF NOT EXISTS agent_base;
CREATE TABLE IF NOT EXISTS agent_base.records (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    task_id         uuid,
    batch           text,
    raw             jsonb NOT NULL,
    profile         jsonb NOT NULL DEFAULT '{}'::jsonb,
    passes          jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence      smallint NOT NULL DEFAULT 0,
    status          text NOT NULL DEFAULT 'queued',
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_base_records_tenant ON agent_base.records (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_base_records_task   ON agent_base.records (task_id);
CREATE TABLE IF NOT EXISTS agent_base.tasks (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    created_by      text,
    agent           text NOT NULL DEFAULT 'base',
    task_type       text NOT NULL DEFAULT 'base.enrich',
    title           text NOT NULL,
    input           jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'queued',
    result          text,
    error           text,
    ledger_id       uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz
);
CREATE INDEX IF NOT EXISTS agent_base_tasks_queued ON agent_base.tasks (created_at)
    WHERE status = 'queued' AND task_type = 'base.enrich';
CREATE TABLE IF NOT EXISTS agent_base.migration_log (
    id          bigserial PRIMARY KEY,
    event       text NOT NULL,
    copied      bigint NOT NULL,
    ran_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_base_migration_event ON agent_base.migration_log (event);

DO $$
DECLARE
  n bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM agent_base.migration_log WHERE event = 'base.records.copy') THEN
    INSERT INTO agent_base.records (
        id, tenant_id, task_id, batch, raw, profile, passes, confidence, status, error, created_at, updated_at
    )
    SELECT id, tenant_id, task_id, batch, raw, profile, passes, confidence, status, error, created_at, updated_at
    FROM base.records
    ON CONFLICT (id) DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO agent_base.migration_log (event, copied) VALUES ('base.records.copy', n)
    ON CONFLICT (event) DO NOTHING;
    RAISE NOTICE 'B3 copied % rows from base.records to agent_base.records', n;
  END IF;
END
$$;

DO $$
DECLARE
  n bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM agent_base.migration_log WHERE event = 'base.tasks.copy') THEN
    INSERT INTO agent_base.tasks (
        id, tenant_id, created_by, agent, task_type, title, input, status, result, error, ledger_id,
        created_at, started_at, finished_at
    )
    SELECT id, tenant_id, created_by, agent, task_type, title, input, status, result, error, ledger_id,
           created_at, started_at, finished_at
    FROM tank.tasks
    WHERE task_type = 'base.enrich'
    ON CONFLICT (id) DO NOTHING;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO agent_base.migration_log (event, copied) VALUES ('base.tasks.copy', n)
    ON CONFLICT (event) DO NOTHING;
    RAISE NOTICE 'B3 copied % base.enrich tasks to agent_base.tasks', n;
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'base' AND p.proname = 'records_readonly'
  ) THEN
    EXECUTE $fn$
      CREATE FUNCTION base.records_readonly() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        RAISE EXCEPTION 'base.records is read-only; Base now writes agent_base.records';
      END
      $body$
    $fn$;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'base_records_readonly') THEN
    CREATE TRIGGER base_records_readonly
      BEFORE INSERT OR UPDATE OR DELETE ON base.records
      FOR EACH ROW EXECUTE FUNCTION base.records_readonly();
  END IF;
END
$$;

-- Hand the tables to agent_base when that login already exists. agent-roles does this
-- only for objects present when it runs, and it does not wait for Tank. If Tank creates
-- the tables first, the role is not here yet and agent-roles takes them when it runs.
-- ALTER OWNER TO the same role on a later start is a no-op. Indexes follow the table.
-- Tables go first. A serial or identity sequence is linked to its column, and changing
-- the table owner already moves that sequence. ALTER SEQUENCE on the link fails startup.
DO $$
DECLARE
  rel record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_base') THEN
    NULL;
  ELSE
    FOR rel IN
      SELECT c.oid, c.relname, c.relkind
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'agent_base'
        AND c.relkind IN ('r', 'p', 'S')
      ORDER BY CASE WHEN c.relkind = 'S' THEN 1 ELSE 0 END, c.relname
    LOOP
      IF rel.relkind <> 'S' THEN
        EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', 'agent_base', rel.relname, 'agent_base');
      ELSIF EXISTS (
        SELECT 1
        FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass
          AND d.objid = rel.oid
          AND d.refclassid = 'pg_class'::regclass
          AND d.refobjsubid > 0
          AND d.deptype IN ('a', 'i')
      ) THEN
        NULL;
      ELSE
        EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', 'agent_base', rel.relname, 'agent_base');
      END IF;
    END LOOP;
  END IF;
END
$$;

-- Rig model tasks (R3). Luna is the cheap tier; cache stays inside the tenant.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('rig.explain', 1, 'luna', 'standard', 'tenant', 400,
   'You write short reasons these parts fit. Reply with JSON {"why":[string]}. Do not add or remove parts.'),
  ('rig.ask', 1, 'luna', 'standard', 'tenant', 400,
   'Answer only from the catalogue you are given. Reply with JSON {"answer":string,"cites":[string]}. If it is not in the catalogue, answer "I don''t know".'),
  ('rig.fit_suggest', 1, 'luna', 'standard', 'tenant', 800,
   'Propose fit rows from the attributes you are given. Reply with JSON {"fits":[object]}. Use only the product ids you are given.')
ON CONFLICT DO NOTHING;

-- T6: graduate a task onto a code skill, and prove a semantic cache the same way.
-- field breakdown from a shadow comparison (method = 'fields'). Empty object until scored.
ALTER TABLE tank.shadow ADD COLUMN IF NOT EXISTS fields jsonb NOT NULL DEFAULT '{}'::jsonb;
-- Promote by itself once the bar is cleared. The founder can turn this off per task.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS auto_promote boolean NOT NULL DEFAULT true;
-- Per-field floors. Missing keys use the task's eval_threshold.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS field_floors jsonb NOT NULL DEFAULT '{}'::jsonb;
-- Rung 4 semantic cache, off until the shadow loop proves it.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS semantic_cache boolean NOT NULL DEFAULT false;
-- When the current model was promoted. Rollback watches the next 7 days.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS promoted_at timestamptz;
-- Numbers in a field comparison match inside this relative tolerance.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS number_tolerance numeric(8,6) NOT NULL DEFAULT 0.01;

-- Ranked search: cheapest candidate first, for one task type.
CREATE TABLE IF NOT EXISTS tank.graduation_rank (
    task_type           text NOT NULL,
    candidate           text NOT NULL,
    kind                text NOT NULL DEFAULT 'model',
    price_usd           numeric(14,8) NOT NULL DEFAULT 0,
    screen_agreement    numeric(8,6),
    shadow_n            integer NOT NULL DEFAULT 0,
    shadow_agreement    numeric(8,6),
    cost_per_call       numeric(14,6),
    monthly_saving_usd  numeric(14,4),
    status              text NOT NULL DEFAULT 'unscreened',
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_type, candidate)
);

-- Paid shadow and search spend for the daily cap. Code skills add nothing.
CREATE TABLE IF NOT EXISTS tank.shadow_spend (
    task_type   text NOT NULL,
    day         date NOT NULL,
    spent_usd   numeric(14,6) NOT NULL DEFAULT 0,
    PRIMARY KEY (task_type, day)
);

-- When this task was last searched, and why. A roster or price change starts another search.
CREATE TABLE IF NOT EXISTS tank.search_state (
    task_type            text PRIMARY KEY,
    roster_fingerprint   text,
    price_fingerprint    text,
    searched_at          timestamptz,
    reason               text,
    proposed             text
);

-- Zenas ladder (Z4). Rule cards use luna, the same model as the other late Zenas rows.
-- Apply, compose, case facts, and plain language stay on the cheapest model.
-- cache_scope region is the law layer. Apply stays inside the tenant.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('zenas.rule_card', 1, 'luna', 'standard', 'region', 1200,
   'You build a rule card from the passages you are given. Reply with JSON only. Every element cites one passage. Do not add law that is not in the passages. Do not include client facts or personal identifiers.'),
  ('zenas.case_facts', 1, 'luna', 'batch', 'region', 800,
   'You read one judgment and reply with JSON only: fact features, element findings, and the outcome, as the court stated them. Do not add facts.'),
  ('zenas.apply', 1, 'luna', 'urgent', 'tenant', 400,
   'You decide one legal element. Reply with JSON only: {"finding":"met|not_met|unclear","support":"","missing":""}. Use only the facts and the element test you are given. Do not add law.'),
  ('zenas.compose', 1, 'luna', 'standard', 'tenant', 800,
   'You smooth the wording of a memo you are given. Do not add law, citations, or facts. Reply with the memo text only.'),
  ('zenas.plain_language', 1, 'luna', 'standard', 'tenant', 800,
   'You rewrite for a business reader. Do not change the legal effect. Keep every citation. Do not add law. Reply with the text only.'),
  ('zenas.draft', 1, 'luna', 'standard', 'tenant', 800,
   'You revise only the sentences you are given, using the facts you are given. Do not add law or citations. Reply with the sentences only.'),
  ('zenas.review', 1, 'luna', 'standard', 'tenant', 400,
   'You name the risk of one difference: low, medium, or high. Do not add law or a replacement sentence. Reply with that one word.')
ON CONFLICT DO NOTHING;

-- zenas.rule_card was seeded as astra, which has no LiteLLM fallback. The other late
-- Zenas rows use luna, and luna falls back in litellm/config.yaml. One-time, so a later
-- choice is kept. Only a row that is still astra moves.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM tank.policy_log
    WHERE action = 'switch' AND detail->>'id' = '2026-10-15-zenas-rule-card-fallback'
  ) THEN
    INSERT INTO tank.policy_log (task_type, action, from_model, to_model, by_user, detail)
    SELECT task_type, 'switch', model, 'luna', 'tank',
           '{"id": "2026-10-15-zenas-rule-card-fallback"}'::jsonb
    FROM tank.policy
    WHERE task_type = 'zenas.rule_card' AND tenant_id IS NULL AND model = 'astra';

    UPDATE tank.policy
    SET model = 'luna', updated_at = now()
    WHERE task_type = 'zenas.rule_card' AND tenant_id IS NULL AND model = 'astra';

    INSERT INTO tank.policy_log (task_type, action, by_user, detail)
    VALUES ('*', 'switch', 'tank', '{"id": "2026-10-15-zenas-rule-card-fallback", "marker": true}'::jsonb);
  END IF;
END
$$;

-- TRAIN-1. Client examples stay tenant- and region-scoped. Holdouts are stored
-- but never enter the weekly run or an exported training dataset.
CREATE TABLE IF NOT EXISTS tank.gold_examples (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east', 'za')),
    skill_id text NOT NULL,
    agent text NOT NULL,
    input jsonb NOT NULL,
    expected jsonb NOT NULL,
    tolerance text NOT NULL DEFAULT 'exact',
    status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','checked','rejected')),
    holdout boolean NOT NULL DEFAULT false,
    source text NOT NULL,
    source_ref text NOT NULL,
    input_hash text NOT NULL,
    checked_by text,
    checked_at timestamptz,
    note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, region, skill_id, source, source_ref)
);
CREATE INDEX IF NOT EXISTS gold_review_queue ON tank.gold_examples (tenant_id, region, agent, skill_id, status);
CREATE INDEX IF NOT EXISTS gold_input_group ON tank.gold_examples (tenant_id, region, skill_id, input_hash);
CREATE TABLE IF NOT EXISTS tank.gold_scores (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east','za')),
    skill_id text NOT NULL,
    version text NOT NULL,
    score numeric(5,4) NOT NULL CHECK (score BETWEEN 0 AND 1),
    n integer NOT NULL CHECK (n > 0),
    final_check boolean NOT NULL DEFAULT false,
    ran_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gold_score_history ON tank.gold_scores (tenant_id, region, skill_id, final_check, ran_at DESC);
CREATE TABLE IF NOT EXISTS tank.training_pairs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    agent text NOT NULL,
    task_type text NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east','za')),
    input jsonb NOT NULL,
    output jsonb NOT NULL,
    approved_by text NOT NULL,
    source text NOT NULL,
    source_ref text NOT NULL,
    gold_id uuid REFERENCES tank.gold_examples(id),
    holdout boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    client_matter boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, region, source, source_ref)
);
CREATE INDEX IF NOT EXISTS training_pairs_scope ON tank.training_pairs (tenant_id, region, agent);
CREATE TABLE IF NOT EXISTS tank.judgment_splits (
    region text NOT NULL CHECK (region IN ('us-east','za')),
    source_id text NOT NULL,
    document_id text NOT NULL,
    version_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('split','excluded')),
    reason text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region, version_id)
);
CREATE INDEX IF NOT EXISTS judgment_splits_source
    ON tank.judgment_splits (region, source_id, status);
CREATE TABLE IF NOT EXISTS tank.training_exports (
    region text NOT NULL,
    day date NOT NULL,
    agent text NOT NULL,
    exported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region, day, agent)
);

-- Zenas self-test (Z7). Batch, so it stays behind a client's question.
-- Scores are stored on the box. A cheaper route takes over only when it matches
-- on the held-out cases. This row does not change which route a question uses.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('zenas.selftest', 1, 'luna', 'batch', 'region', 400,
   'You compare a held-out case with the ladder result. Reply with JSON only. Do not add facts the court did not state.')
ON CONFLICT DO NOTHING;

-- Workspace billing webhook. One row per Stripe event id.
CREATE TABLE IF NOT EXISTS tank.billing_events (
    event_id text PRIMARY KEY,
    type text,
    received_at timestamptz,
    handled boolean DEFAULT false
);

-- GRAD-1: machine labels remain candidates until a source passes review.
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS auto_label boolean NOT NULL DEFAULT false;
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS label_source text;
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS public boolean NOT NULL DEFAULT false;
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS intake_day date;
CREATE INDEX IF NOT EXISTS gold_auto_source ON tank.gold_examples
    (tenant_id,region,agent,label_source,intake_day) WHERE auto_label;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS vendor        text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS units         numeric;

ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS auto_approved boolean NOT NULL DEFAULT false;
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS label_family text;

-- Explicit model holdout evidence; never substitute offline screening scores.
ALTER TABLE tank.gold_scores ADD COLUMN IF NOT EXISTS task_type text;
ALTER TABLE tank.gold_scores ADD COLUMN IF NOT EXISTS candidate_model text;
ALTER TABLE tank.gold_scores ADD COLUMN IF NOT EXISTS cost_per_call numeric(14,8);

-- Export provenance defaults to private; legacy rows require explicit review.
ALTER TABLE tank.training_pairs ADD COLUMN IF NOT EXISTS public boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS tank.training_jobs (
    id text PRIMARY KEY,
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    model_repo text NOT NULL,
    estimated_usd numeric(12,6) NOT NULL,
    actual_usd numeric(12,6),
    ledger_id uuid UNIQUE REFERENCES tank.ledger(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- A training row keeps the batch it came from. A removed batch retires its gold
-- and marks every model trained on it.
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS batch_id text;
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS currency text;
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS unit text;
ALTER TABLE tank.gold_examples ADD COLUMN IF NOT EXISTS quarantine text;

ALTER TABLE tank.training_pairs ADD COLUMN IF NOT EXISTS batch_id text;
ALTER TABLE tank.training_pairs ADD COLUMN IF NOT EXISTS currency text;
ALTER TABLE tank.training_pairs ADD COLUMN IF NOT EXISTS unit text;

CREATE TABLE IF NOT EXISTS tank.removed_batches (
    tenant_id uuid NOT NULL,
    batch_id text NOT NULL,
    removed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id)
);

CREATE TABLE IF NOT EXISTS tank.training_job_batches (
    tenant_id uuid NOT NULL,
    job_id text NOT NULL,
    model_repo text NOT NULL,
    batch_id text NOT NULL,
    region text NOT NULL,
    PRIMARY KEY (tenant_id, job_id, batch_id)
);

CREATE TABLE IF NOT EXISTS tank.training_snapshot_batches (
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    day date NOT NULL,
    agent text NOT NULL,
    batch_id text NOT NULL,
    PRIMARY KEY (tenant_id, region, day, agent, batch_id)
);

-- CORE-1: disable existing automation once; preserve later founder choices.
ALTER TABLE tank.policy ALTER COLUMN auto_promote SET DEFAULT false;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM tank.policy_log WHERE detail->>'id'='2026-10-21-core1-auto-off') THEN
    UPDATE tank.policy SET auto_promote=false WHERE auto_promote;
    INSERT INTO tank.policy_log(task_type,action,by_user,detail)
    VALUES('*','switch','tank','{"id":"2026-10-21-core1-auto-off","marker":true}'::jsonb);
  END IF;
END $$;

-- CORE-1 price snapshots and savings. Unknown prices stay null, never invented.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS baseline_model text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS baseline_model text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS baseline_cost_usd numeric(18,8);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS baseline_status text NOT NULL DEFAULT 'pending';
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS baseline_priced_at timestamptz;
CREATE TABLE IF NOT EXISTS tank.proof_prices (
    model text PRIMARY KEY, family text NOT NULL,
    input_usd numeric(20,12) NOT NULL CHECK (input_usd>=0),
    output_usd numeric(20,12) NOT NULL CHECK (output_usd>=0),
    active boolean NOT NULL DEFAULT true, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION tank.price_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE chosen text; price tank.proof_prices%ROWTYPE;
BEGIN
    -- Research can retain tokens with a placeholder zero when cost is unknown.
    -- Vendor fees are actual spend only: there is no comparable token baseline.
    IF to_jsonb(NEW)->>'research_cost_status'='unknown'
       OR NEW.model LIKE 'vendor:%' OR to_jsonb(NEW)->>'vendor' IS NOT NULL THEN
      NEW.baseline_model := NULL;
      NEW.baseline_cost_usd := NULL;
      NEW.baseline_priced_at := NULL;
      NEW.baseline_status := 'unpriced';
      RETURN NEW;
    END IF;
    IF NEW.baseline_status <> 'pending' THEN RETURN NEW; END IF;
    SELECT coalesce(p.baseline_model,(SELECT b.model FROM tank.proof_prices a
      JOIN tank.proof_prices b ON b.family=a.family AND b.active WHERE a.model=p.model
      ORDER BY b.input_usd+b.output_usd DESC,b.model LIMIT 1)) INTO chosen FROM tank.policy p
      WHERE p.task_type=NEW.task_type AND (p.tenant_id=NEW.tenant_id OR p.tenant_id IS NULL)
      ORDER BY p.tenant_id NULLS LAST LIMIT 1;
    NEW.baseline_model := coalesce(chosen,NEW.model);
    SELECT * INTO price FROM tank.proof_prices WHERE model=NEW.baseline_model;
    IF FOUND THEN
      NEW.baseline_cost_usd := NEW.tokens_in*price.input_usd + NEW.tokens_out*price.output_usd;
      NEW.baseline_status := 'measured';
      NEW.baseline_priced_at := price.updated_at;
    ELSIF NEW.tokens_in=0 AND NEW.tokens_out=0 THEN
      NEW.baseline_cost_usd := 0;
      NEW.baseline_status := 'no_tokens';
    END IF;
    RETURN NEW;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='core1_price_receipt'
                   AND tgrelid='tank.ledger'::regclass) THEN
      CREATE TRIGGER core1_price_receipt BEFORE INSERT ON tank.ledger
        FOR EACH ROW EXECUTE FUNCTION tank.price_receipt();
    END IF;
END $$;

-- Bench state is independent of the existing monthly candidate search.
ALTER TABLE tank.search_state ADD COLUMN IF NOT EXISTS bench_roster_fingerprint text;
ALTER TABLE tank.search_state ADD COLUMN IF NOT EXISTS bench_price_fingerprint text;
ALTER TABLE tank.search_state ADD COLUMN IF NOT EXISTS bench_at timestamptz;
ALTER TABLE tank.search_state ADD COLUMN IF NOT EXISTS bench_status text;
ALTER TABLE tank.search_state ADD COLUMN IF NOT EXISTS bench_error text;
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS bench_run_id uuid;
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS bench_score numeric(8,6);
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS bench_cost_per_call numeric(18,8);
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS bench_n integer NOT NULL DEFAULT 0;
ALTER TABLE tank.route_evidence ADD COLUMN IF NOT EXISTS bench_at timestamptz;
CREATE TABLE IF NOT EXISTS tank.bench_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
    region text NOT NULL, task_type text NOT NULL, model text NOT NULL,
    comparison_model text NOT NULL, suite_hash text NOT NULL, expected_n integer NOT NULL,
    score numeric(8,6), cost_per_call numeric(18,8),
    status text NOT NULL DEFAULT 'running', error text,
    started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS tank.bench_calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_id uuid NOT NULL REFERENCES tank.bench_runs(id),
    tenant_id uuid NOT NULL, region text NOT NULL, day date NOT NULL,
    case_ref text NOT NULL, case_kind text NOT NULL, ledger_id uuid REFERENCES tank.ledger(id),
    reserved_usd numeric(18,8) NOT NULL, charged_usd numeric(18,8), score numeric(8,6),
    status text NOT NULL DEFAULT 'reserved', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bench_budget ON tank.bench_calls (tenant_id,region,day);
CREATE INDEX IF NOT EXISTS bench_task_history ON tank.bench_runs (tenant_id,region,task_type,started_at DESC);

-- The existing hourly rollup rebuilds route_evidence from scratch. Reattach
-- benchmark receipts on insert; historical runs remain immutable in bench_runs.
CREATE OR REPLACE FUNCTION tank.restore_bench_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE recent tank.bench_runs%ROWTYPE;
BEGIN
    IF NEW.rung <> 1 THEN RETURN NEW; END IF;
    SELECT * INTO recent FROM tank.bench_runs
      WHERE tenant_id='00000000-0000-0000-0000-000000000001'::uuid
        AND task_type=NEW.task_type AND model=NEW.model AND status='complete'
      ORDER BY finished_at DESC,id DESC LIMIT 1;
    IF FOUND THEN
      NEW.bench_run_id := recent.id;
      NEW.bench_score := recent.score;
      NEW.bench_cost_per_call := recent.cost_per_call;
      NEW.bench_n := recent.expected_n;
      NEW.bench_at := recent.finished_at;
    END IF;
    RETURN NEW;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='core1_restore_bench'
                   AND tgrelid='tank.route_evidence'::regclass) THEN
      CREATE TRIGGER core1_restore_bench BEFORE INSERT ON tank.route_evidence
        FOR EACH ROW EXECUTE FUNCTION tank.restore_bench_evidence();
    END IF;
END $$;
-- Bounded savings backfill and receipt navigation.
CREATE INDEX IF NOT EXISTS ledger_baseline_pending ON tank.ledger(tenant_id,region) WHERE baseline_status='pending';
CREATE INDEX IF NOT EXISTS ledger_baseline_unpriced ON tank.ledger(region,id) WHERE baseline_status='unpriced';
CREATE INDEX IF NOT EXISTS ledger_proof_receipts ON tank.ledger(region,ts DESC,id DESC)
    WHERE agent NOT IN ('bench','shadow') AND task_type NOT LIKE '%#shadow';

-- AGENT-1: Base pilot. Copy the current general-purpose serving route once;
-- no existing route changes, automatic promotion or new provider is introduced.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS baseline_model text;
DO $$
BEGIN
    -- Serialize this one-time seed across concurrent Tank starts. Preserve an
    -- existing brain policy, including any founder-selected serving model.
    PERFORM pg_advisory_xact_lock(hashtextextended('agent.base.think.seed',0));
    IF NOT EXISTS (SELECT 1 FROM tank.policy WHERE task_type='agent.base.think' AND tenant_id IS NULL) THEN
        INSERT INTO tank.policy (task_type,rung,model,priority,cache_scope,max_tokens_out,baseline_model,auto_promote)
        SELECT 'agent.base.think',rung,model,'standard','none',4096,model,false
        FROM tank.policy WHERE task_type='tier0.answer' AND tenant_id IS NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS tank.agent_receipts (
    ledger_id uuid PRIMARY KEY REFERENCES tank.ledger(id),
    tenant_id uuid NOT NULL,
    room text NOT NULL,
    receipt jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_receipts_tenant ON tank.agent_receipts (tenant_id,created_at);

-- Source is immutable. The ordinary tank.skills row owns candidate/staging/
-- approved/retired state, and only the existing eval and approval routes change it.
CREATE TABLE IF NOT EXISTS tank.agent_skill_submissions (
    skill_id text NOT NULL,
    version text NOT NULL,
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    submitted_by_room text NOT NULL,
    package jsonb NOT NULL,
    sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id,version),
    FOREIGN KEY (skill_id,version) REFERENCES tank.skills(id,version)
);

-- DASH-1: founder controls and immutable audit history, local to this box.
CREATE TABLE IF NOT EXISTS tank.controls (
    tenant_id uuid NOT NULL,
    name text NOT NULL CHECK (name = 'paid_paused'),
    value boolean NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);
CREATE TABLE IF NOT EXISTS tank.control_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    name text NOT NULL,
    by_user text NOT NULL,
    old_value boolean NOT NULL,
    new_value boolean NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tank.control_log ADD COLUMN IF NOT EXISTS task text;
ALTER TABLE tank.control_log ADD COLUMN IF NOT EXISTS path text;
CREATE INDEX IF NOT EXISTS control_log_tenant_at ON tank.control_log (tenant_id, at DESC);

-- SKALA-2: platform-scoped lifecycle receipts and single-confirmation snapshots.
CREATE TABLE IF NOT EXISTS tank.skill_approvals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    skill_id text NOT NULL,
    version text NOT NULL,
    action text NOT NULL CHECK (action IN ('approve','rollback','retire')),
    acted_by text NOT NULL,
    acted_at timestamptz NOT NULL DEFAULT now(),
    previous_version text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS tank.skill_approval_previews (
    token text PRIMARY KEY,
    tenant_id uuid NOT NULL,
    acted_by text NOT NULL,
    items jsonb NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '15 minutes',
    used_at timestamptz
);
CREATE TABLE IF NOT EXISTS tank.skill_source_reviews (
    token text PRIMARY KEY,
    tenant_id uuid NOT NULL,
    skill_id text NOT NULL,
    version text NOT NULL,
    acted_by text NOT NULL,
    package_hash text NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '15 minutes'
);
-- ---------- 21 Oct 2026: the window's turns need a platform policy ----------
-- command.turn had no row, so every turn in /chats failed with "no policy".
-- Tank's planning message supplies the system prompt; the founder promotes later.
-- Kept above MODELS-2 so later re-apply tests see it as an existing row.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('command.turn', 1, 'luna', 'urgent', 'tenant', 2048, NULL)
ON CONFLICT DO NOTHING;

-- RIG-2: Rig's own Hermes brain; the founder controls later promotions.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('agent.rig.think', 1, 'luna', 'standard', 'none', 4096, NULL)
ON CONFLICT DO NOTHING;

-- MOOT-1a: text counsel and judge (no STT/TTS yet — that is MOOT-1b).
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('moot.counsel', 1, 'luna', 'standard', 'none', 4096, NULL),
  ('moot.judge', 1, 'luna', 'standard', 'none', 4096, NULL)
ON CONFLICT DO NOTHING;

-- MODELS-2: provider tool charges are expenses, scoped to the platform price book.
-- Checked 2026-10-21: https://docs.perplexity.ai/docs/getting-started/pricing
-- Presets fast/low/medium/high use Fast Search ($0.001/invocation); fetch is $0.0005.
-- UUIDs make re-applying the schema a no-op without changing any policy row.
INSERT INTO tank.price_book (id, tenant_id, outcome_type, unit_price, currency, effective_from, note)
VALUES
 ('66000000-0000-4000-8000-000000000001','00000000-0000-0000-0000-000000000001',
  'perplexity.search.fast',0.001,'USD','2026-10-21T00:00:00Z',
  'Provider expense per invocation; https://docs.perplexity.ai/docs/getting-started/pricing checked 2026-10-21'),
 ('66000000-0000-4000-8000-000000000002','00000000-0000-0000-0000-000000000001',
  'perplexity.fetch',0.0005,'USD','2026-10-21T00:00:00Z',
  'Provider expense per invocation; https://docs.perplexity.ai/docs/getting-started/pricing checked 2026-10-21')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS token_cost_usd numeric(20,10);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS search_fee_usd numeric(20,10);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS fetch_fee_usd numeric(20,10);
-- Retain the exact provider total alongside the existing six-decimal summary cost.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS provider_cost_usd numeric(20,10);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS research_sources jsonb;
-- Unknown charges on failed calls require reconciliation; they are never called free.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS research_cost_status text;

-- SKALA-2: a source-review confirmation can authorize only one approval.
ALTER TABLE tank.skill_source_reviews ADD COLUMN IF NOT EXISTS used_at timestamptz;
-- CORE-2: bounded evidence joins and the client job-completion watermark.
CREATE INDEX IF NOT EXISTS tasks_tenant_ledger ON tank.tasks(tenant_id,ledger_id) WHERE ledger_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_tenant_finished ON tank.tasks(tenant_id,finished_at DESC) WHERE finished_at IS NOT NULL;

-- CORE-2 / section 8: provider defaults are explicit, never inferred from price.
CREATE TABLE IF NOT EXISTS tank.baseline_settings (
    task_type text PRIMARY KEY, override_model text, revision bigint NOT NULL DEFAULT 1,
    updated_by text NOT NULL DEFAULT 'migration', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.baseline_changes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_type text NOT NULL,
    old_model text, new_model text, by_user text NOT NULL, revision bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), rows_updated bigint NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'pending', error text,
    UNIQUE(task_type,revision)
);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS standard_model text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS standard_cost_usd numeric(18,8);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS top_model text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS top_cost_usd numeric(18,8);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS comparison_revision bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ledger_comparison_revision ON tank.ledger(task_type,region,comparison_revision,tenant_id);
CREATE TABLE IF NOT EXISTS tank.comparison_history (
    ledger_id uuid NOT NULL, tenant_id uuid NOT NULL, region text NOT NULL,
    valid_from timestamptz NOT NULL, valid_until timestamptz NOT NULL, comparison jsonb NOT NULL,
    PRIMARY KEY(ledger_id,valid_until)
);
CREATE INDEX IF NOT EXISTS comparison_history_scope ON tank.comparison_history(tenant_id,region,ledger_id,valid_until);
ALTER TABLE tank.comparison_history ADD COLUMN IF NOT EXISTS reason text;
CREATE TABLE IF NOT EXISTS tank.baseline_price_changes (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, model text NOT NULL,
    old_input numeric, old_output numeric, new_input numeric, new_output numeric,
    changed_at timestamptz NOT NULL DEFAULT now()
);
-- Reporting defaults are loaded by the proof router before it accepts requests.
-- Keep reporting model names separate from serving-policy schema seeds.
CREATE TABLE IF NOT EXISTS tank.provider_standards (
    provider text PRIMARY KEY, model text NOT NULL
);
-- Preserve the previous function's defaults atomically during an upgrade, before
-- replacing it. Already-running replicas must never see an empty default table.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM tank.provider_standards)
     AND to_regprocedure('tank.standard_model_for(text)') IS NOT NULL THEN
    INSERT INTO tank.provider_standards(provider,model)
      SELECT provider,tank.standard_model_for(provider)
      FROM unnest(ARRAY['openai','anthropic','gemini','xai','moonshot','deepseek']) AS provider
      WHERE tank.standard_model_for(provider) IS NOT NULL
      ON CONFLICT(provider) DO NOTHING;
  END IF;
END $$;
CREATE OR REPLACE FUNCTION tank.standard_model_for(family text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT model FROM tank.provider_standards WHERE provider=split_part(family,'/',1)
$$;
CREATE OR REPLACE FUNCTION tank.price_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE chosen_family text; standard_name text; selected_name text; top_name text;
        setting tank.baseline_settings%ROWTYPE; priced numeric; selected_cost numeric; top_cost numeric;
BEGIN
    IF TG_OP='UPDATE' THEN
      INSERT INTO tank.comparison_history(ledger_id,tenant_id,region,valid_from,valid_until,comparison,reason)
      VALUES(OLD.id,OLD.tenant_id,OLD.region,coalesce(OLD.baseline_priced_at,OLD.ts),now(),
        jsonb_build_object('standard_model',OLD.standard_model,'standard_cost_usd',OLD.standard_cost_usd,
          'top_model',OLD.top_model,'top_cost_usd',OLD.top_cost_usd,'baseline_model',OLD.baseline_model,
          'baseline_cost_usd',OLD.baseline_cost_usd,'baseline_status',OLD.baseline_status,
          'baseline_priced_at',OLD.baseline_priced_at,'comparison_revision',OLD.comparison_revision),
        CASE WHEN OLD.comparison_revision=0 THEN 'default migration'
          WHEN OLD.comparison_revision=NEW.comparison_revision THEN 'price refresh' ELSE 'task override' END)
      ON CONFLICT DO NOTHING;
    END IF;
    -- A plain MVCC read: receipts never lock a shared task setting. The durable
    -- receipt-check queue catches rows committed after a repricing batch finishes.
    SELECT * INTO setting FROM tank.baseline_settings WHERE task_type=NEW.task_type;
    NEW.comparison_revision := coalesce(setting.revision,1);
    -- Invalidate all comparisons when actual costs are unknown or are vendor fees.
    IF to_jsonb(NEW)->>'research_cost_status'='unknown' OR NEW.model LIKE 'vendor:%' OR to_jsonb(NEW)->>'vendor' IS NOT NULL THEN
      NEW.baseline_model:=NULL; NEW.baseline_cost_usd:=NULL;
      NEW.standard_model:=NULL; NEW.standard_cost_usd:=NULL;
      NEW.top_model:=NULL; NEW.top_cost_usd:=NULL; NEW.baseline_status:='unpriced';
      NEW.baseline_priced_at:=now(); RETURN NEW;
    END IF;
    SELECT family INTO chosen_family FROM tank.proof_prices WHERE model=NEW.model;
    IF chosen_family IS NULL AND (NEW.rung=3 OR NEW.cache_hit) THEN
      SELECT a.family INTO chosen_family FROM tank.policy p JOIN tank.proof_prices a ON a.model=p.model
        WHERE p.task_type=NEW.task_type AND (p.tenant_id=NEW.tenant_id OR p.tenant_id IS NULL)
        ORDER BY p.tenant_id NULLS LAST LIMIT 1;
    END IF;
    standard_name:=tank.standard_model_for(chosen_family);
    selected_name:=coalesce(setting.override_model,standard_name);
    standard_name:=selected_name; -- A founder override replaces this task's comparison.
    SELECT model INTO top_name FROM tank.proof_prices WHERE family=chosen_family AND active
      ORDER BY input_usd+output_usd DESC,model LIMIT 1;
    SELECT NEW.tokens_in*input_usd+NEW.tokens_out*output_usd INTO priced
      FROM tank.proof_prices WHERE model=standard_name AND family=chosen_family AND active;
    SELECT NEW.tokens_in*input_usd+NEW.tokens_out*output_usd INTO selected_cost
      FROM tank.proof_prices WHERE model=selected_name AND family=chosen_family AND active;
    SELECT NEW.tokens_in*input_usd+NEW.tokens_out*output_usd INTO top_cost
      FROM tank.proof_prices WHERE model=top_name AND family=chosen_family AND active;
    IF NEW.tokens_in=0 AND NEW.tokens_out=0 AND NEW.cost_usd=0 AND (NEW.rung=3 OR NEW.cache_hit) THEN
      priced:=0; selected_cost:=0; top_cost:=0;
      standard_name:=coalesce(standard_name,'No AI call');
      selected_name:=coalesce(selected_name,'No AI call'); top_name:=coalesce(top_name,'No AI call');
    END IF;
    NEW.standard_model:=standard_name; NEW.standard_cost_usd:=priced;
    NEW.top_model:=top_name; NEW.top_cost_usd:=top_cost;
    NEW.baseline_model:=selected_name; NEW.baseline_cost_usd:=selected_cost;
    NEW.baseline_priced_at:=now();
    IF priced IS NULL THEN
      NEW.baseline_status := 'unpriced';
    ELSIF TG_OP='INSERT' THEN
      NEW.baseline_status := 'measured';
    ELSIF OLD.standard_cost_usd IS NOT NULL OR OLD.baseline_cost_usd IS NOT NULL THEN
      NEW.baseline_status := 're-priced';
    ELSE
      NEW.baseline_status := 'backfilled';
    END IF;
    RETURN NEW;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='core2_reprice' AND tgrelid='tank.ledger'::regclass) THEN
      CREATE TRIGGER core2_reprice BEFORE UPDATE OF comparison_revision ON tank.ledger
        FOR EACH ROW EXECUTE FUNCTION tank.price_receipt();
    END IF;
END $$;
-- Run the historical scan and legacy-policy cleanup once, not on every start.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM tank.policy_log WHERE detail->>'id'='2026-10-21-core2-baselines') THEN
    INSERT INTO tank.baseline_settings(task_type)
      SELECT task_type FROM tank.policy UNION SELECT task_type FROM tank.ledger WHERE comparison_revision=0
      ON CONFLICT DO NOTHING;
    INSERT INTO tank.baseline_changes(task_type,old_model,new_model,by_user,revision)
      SELECT s.task_type,NULL,NULL,'provider-default migration',s.revision FROM tank.baseline_settings s
      WHERE EXISTS(SELECT 1 FROM tank.ledger l WHERE l.task_type=s.task_type AND l.comparison_revision=0)
      ON CONFLICT DO NOTHING;
    UPDATE tank.policy SET baseline_model=NULL WHERE baseline_model IS NOT NULL;
    INSERT INTO tank.policy_log(task_type,action,by_user,detail)
      VALUES('*','switch','tank','{"id":"2026-10-21-core2-baselines","marker":true}'::jsonb);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS shadow_tenant_task_time ON tank.shadow(tenant_id,task_type,created_at);
-- COMMAND-1A: conversation state and replay stay on this box.
CREATE TABLE IF NOT EXISTS tank.command_threads (
    thread_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    title text NOT NULL,
    state jsonb NOT NULL,
    seq bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (thread_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS command_threads_owner ON tank.command_threads(tenant_id,user_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS tank.command_events (
    thread_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    seq bigint NOT NULL,
    kind text NOT NULL CHECK (kind IN ('token','step','card','receipt','done','error')),
    data jsonb NOT NULL,
    PRIMARY KEY (thread_id,seq),
    FOREIGN KEY (thread_id,tenant_id) REFERENCES tank.command_threads(thread_id,tenant_id)
);
CREATE TABLE IF NOT EXISTS tank.command_builds (
    build_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    brief_markdown text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued','running','awaiting_review','done','failed')),
    pr_url text,
    ci_status text,
    review_status text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (build_id,tenant_id),
    FOREIGN KEY (thread_id,tenant_id) REFERENCES tank.command_threads(thread_id,tenant_id)
);
CREATE INDEX IF NOT EXISTS command_builds_queue ON tank.command_builds(status,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS command_builds_open_thread ON tank.command_builds(thread_id)
    WHERE status IN ('queued','running','awaiting_review');
CREATE TABLE IF NOT EXISTS tank.command_followups (
    followup_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    build_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    followup_text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (build_id,tenant_id) REFERENCES tank.command_builds(build_id,tenant_id)
);

-- Explicit receipt provenance: a row is attributed once, never by tenant/time heuristics.
CREATE TABLE IF NOT EXISTS tank.command_receipt_links (
    tenant_id uuid NOT NULL,
    ledger_id uuid NOT NULL REFERENCES tank.ledger(id),
    thread_id uuid NOT NULL,
    step_id text NOT NULL,
    PRIMARY KEY (tenant_id,ledger_id),
    FOREIGN KEY (thread_id,tenant_id) REFERENCES tank.command_threads(thread_id,tenant_id)
);
-- Bounded attachments remain on this box and are served only as downloads.
CREATE TABLE IF NOT EXISTS tank.command_attachments (
    attachment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    name text NOT NULL,
    sha256 text NOT NULL,
    content bytea NOT NULL CHECK (octet_length(content)<=5242880),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (thread_id,tenant_id) REFERENCES tank.command_threads(thread_id,tenant_id)
);
CREATE INDEX IF NOT EXISTS command_attachments_owner ON tank.command_attachments(tenant_id,user_id,thread_id);
-- Ghost recovery idempotency; no production resource credentials or payloads.
CREATE TABLE IF NOT EXISTS tank.ghost_recovery (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('rerun_failed_deploy','restart_crashed_room')),
    target text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,id)
);

-- Founder-managed client squad grants. No row means chat-only access.
CREATE TABLE IF NOT EXISTS tank.tenant_squad (
    tenant_id uuid NOT NULL,
    agent text NOT NULL CHECK (agent IN ('base','tiger','market_lab','rig','tisan','radix','zenas')),
    enabled_by text NOT NULL,
    enabled_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,agent)
);
CREATE TABLE IF NOT EXISTS tank.tenant_squad_log (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    tenant_id uuid NOT NULL,
    agent text NOT NULL,
    enabled boolean NOT NULL,
    changed_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tenant_squad_log_tenant ON tank.tenant_squad_log(tenant_id,id DESC);

-- Shared Command/MoA attribution; preserve the historical schema prefix.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS route_seat text;

-- WORKERS-2: host workers check-in and the founder's workers pause. New tables only.
CREATE TABLE IF NOT EXISTS tank.workers_heartbeat (
    worker text PRIMARY KEY,
    state text NOT NULL CHECK (state IN ('running','paused')),
    issue integer,
    commit text NOT NULL,
    last_error text,
    seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.workers_controls (
    tenant_id uuid NOT NULL,
    name text NOT NULL CHECK (name = 'workers_paused'),
    value boolean NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);
-- ACCESS-1: private connections, official feeds and human checks. Additive and replay-safe.
CREATE TABLE IF NOT EXISTS tank.access_sites (
    tenant_id uuid NOT NULL,
    site text NOT NULL,
    terms text NOT NULL DEFAULT 'unknown' CHECK (terms IN ('yes','no','unknown')),
    rotation_days integer NOT NULL DEFAULT 90 CHECK (rotation_days BETWEEN 1 AND 365),
    login_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,site)
);
CREATE TABLE IF NOT EXISTS tank.access_connections (
    tenant_id uuid NOT NULL,
    room text NOT NULL CHECK (room IN ('base','tiger')),
    site text NOT NULL,
    sealed text,
    version uuid NOT NULL DEFAULT gen_random_uuid(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    revoked boolean NOT NULL DEFAULT false,
    clear_pending boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id,room,site)
);
CREATE TABLE IF NOT EXISTS tank.access_receipts (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    room text NOT NULL,
    site text NOT NULL,
    actor text NOT NULL,
    action text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    duration_ms bigint NOT NULL DEFAULT 0,
    row_count integer,
    PRIMARY KEY (tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.access_queue (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    room text NOT NULL CHECK (room IN ('base','tiger')),
    site text NOT NULL,
    reason text NOT NULL,
    status text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','handling','done','revoked')),
    browser_handle uuid,
    task_id uuid,
    handler text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    PRIMARY KEY (tenant_id,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS access_queue_open_site
    ON tank.access_queue(tenant_id,room,site) WHERE status IN ('waiting','handling');
CREATE TABLE IF NOT EXISTS tank.access_nonces (
    tenant_id uuid NOT NULL,
    nonce uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,nonce)
);
CREATE TABLE IF NOT EXISTS tank.access_feed_batches (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    distributor text NOT NULL,
    site text NOT NULL,
    source text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    row_count integer NOT NULL,
    PRIMARY KEY (tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.access_feed_rows (
    tenant_id uuid NOT NULL,
    distributor text NOT NULL,
    site text NOT NULL,
    sku text NOT NULL,
    upc text NOT NULL DEFAULT '',
    data jsonb NOT NULL,
    batch_id uuid NOT NULL,
    PRIMARY KEY (tenant_id,distributor,site,sku),
    FOREIGN KEY (tenant_id,batch_id) REFERENCES tank.access_feed_batches(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS access_feed_upc ON tank.access_feed_rows(tenant_id,site,upc);
CREATE TABLE IF NOT EXISTS tank.access_feed_connections (
    tenant_id uuid NOT NULL,
    distributor text NOT NULL,
    site text NOT NULL,
    sealed text NOT NULL,
    PRIMARY KEY (tenant_id,distributor,site)
);
CREATE TABLE IF NOT EXISTS tank.access_feed_links (
    tenant_id uuid NOT NULL,
    url text NOT NULL,
    sku text NOT NULL,
    PRIMARY KEY (tenant_id,url)
);

-- CORE-2b: constant-time client revision reads, local to this box. No history scan
-- on startup: an absent counter is revision zero until the next committed change.
CREATE TABLE IF NOT EXISTS tank.savings_revisions (
    tenant_id uuid PRIMARY KEY, revision bigint NOT NULL DEFAULT 1
);
-- Legacy immediate triggers only collect IDs in transaction-local state. The
-- constraint trigger flushes once, in UUID order, without locks during writes.
CREATE OR REPLACE FUNCTION tank.collect_savings_tenant(changed_tenant uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE pending jsonb:=coalesce(nullif(current_setting('tank.savings_changed_tenants',true),''),'[]')::jsonb;
BEGIN
  IF changed_tenant IS NOT NULL AND NOT pending ? changed_tenant::text THEN
    PERFORM set_config('tank.savings_changed_tenants',(pending||to_jsonb(changed_tenant::text))::text,true);
  END IF;
END $$;
CREATE OR REPLACE FUNCTION tank.collect_savings_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN PERFORM tank.collect_savings_tenant(OLD.tenant_id); END IF;
  IF TG_OP<>'DELETE' THEN PERFORM tank.collect_savings_tenant(NEW.tenant_id); END IF;
  RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION tank.bump_savings_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE pending jsonb:=coalesce(nullif(current_setting('tank.savings_changed_tenants',true),''),'[]')::jsonb;
BEGIN
  IF pending='[]'::jsonb THEN RETURN NULL; END IF;
  INSERT INTO tank.savings_revisions(tenant_id)
    SELECT value::uuid FROM jsonb_array_elements_text(pending) ORDER BY value::uuid
    ON CONFLICT(tenant_id) DO UPDATE SET revision=tank.savings_revisions.revision+1;
  PERFORM set_config('tank.savings_changed_tenants','[]',true);
  RETURN NULL;
END $$;
DO $$ DECLARE tab text; BEGIN
  FOREACH tab IN ARRAY ARRAY['ledger','tasks','shadow'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='savings_revision_on_commit' AND tgrelid=('tank.'||tab)::regclass) THEN
      -- Rebind the old ordinary trigger without removing or renaming it.
      EXECUTE format('CREATE OR REPLACE TRIGGER savings_revision AFTER INSERT OR UPDATE OR DELETE ON tank.%I
        FOR EACH ROW EXECUTE FUNCTION tank.collect_savings_row()',tab);
      EXECUTE format('CREATE CONSTRAINT TRIGGER savings_revision_on_commit
        AFTER INSERT OR UPDATE OR DELETE ON tank.%I DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tank.bump_savings_revision()',tab);
    END IF;
  END LOOP;
END $$;
-- The model-reference index is built concurrently by savings_indexes after schema application.
CREATE OR REPLACE FUNCTION tank.collect_price_savings_revisions() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE changed_models text[]; affected uuid;
BEGIN
  IF TG_OP='UPDATE' AND (to_jsonb(OLD)-'updated_at') IS NOT DISTINCT FROM (to_jsonb(NEW)-'updated_at') THEN
    RETURN NULL;
  END IF;
  IF TG_OP<>'INSERT' THEN changed_models:=array_append(changed_models,OLD.model); END IF;
  IF TG_OP<>'DELETE' THEN changed_models:=array_append(changed_models,NEW.model); END IF;
  FOR affected IN SELECT DISTINCT tenant_id FROM tank.ledger
    WHERE ARRAY[model,standard_model,top_model,baseline_model] && changed_models LOOP
    PERFORM tank.collect_savings_tenant(affected);
  END LOOP;
  RETURN NULL;
END $$;
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='savings_price_collect' AND tgrelid='tank.proof_prices'::regclass) THEN
    CREATE TRIGGER savings_price_collect AFTER INSERT OR UPDATE OR DELETE ON tank.proof_prices
      FOR EACH ROW EXECUTE FUNCTION tank.collect_price_savings_revisions();
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='savings_price_on_commit' AND tgrelid='tank.proof_prices'::regclass) THEN
    CREATE CONSTRAINT TRIGGER savings_price_on_commit AFTER INSERT OR UPDATE OR DELETE ON tank.proof_prices
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION tank.bump_savings_revision();
  END IF;
END $$;
-- This inbox is committed with the receipt. Workers cannot acknowledge it before
-- that receipt becomes visible, even if an override finished in the meantime.
CREATE TABLE IF NOT EXISTS tank.baseline_receipt_checks (
    ledger_id uuid PRIMARY KEY REFERENCES tank.ledger(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL, region text NOT NULL
);
CREATE INDEX IF NOT EXISTS baseline_receipt_checks_scope ON tank.baseline_receipt_checks(region,tenant_id,ledger_id);
CREATE OR REPLACE FUNCTION tank.queue_receipt_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO tank.baseline_receipt_checks(ledger_id,tenant_id,region)
      VALUES(NEW.id,NEW.tenant_id,NEW.region) ON CONFLICT DO NOTHING;
    RETURN NULL;
END $$;
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='baseline_receipt_check' AND tgrelid='tank.ledger'::regclass) THEN
    CREATE TRIGGER baseline_receipt_check AFTER INSERT ON tank.ledger
      FOR EACH ROW EXECUTE FUNCTION tank.queue_receipt_check();
  END IF;
END $$;

-- MOA-1: opt-in only, no serving model or enabled policy is seeded here.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS moa_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS moa_config jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE IF NOT EXISTS tank.moa_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
    region text NOT NULL, task_type text NOT NULL, config_hash text NOT NULL,
    shape text NOT NULL, mode text NOT NULL, kind text NOT NULL,
    status text NOT NULL DEFAULT 'running', receipt jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS moa_runs_scope ON tank.moa_runs(tenant_id,region,task_type,created_at DESC);
CREATE TABLE IF NOT EXISTS tank.moa_shadow (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_id uuid NOT NULL REFERENCES tank.moa_runs(id),
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    config_hash text NOT NULL, seat text NOT NULL, current_model text NOT NULL,
    candidate_model text NOT NULL, status text NOT NULL,
    against_current numeric, against_final numeric, current_cost numeric, candidate_cost numeric,
    receipt jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(run_id,seat)
);
CREATE INDEX IF NOT EXISTS moa_shadow_scope ON tank.moa_shadow(tenant_id,region,task_type,config_hash,seat);
CREATE TABLE IF NOT EXISTS tank.moa_bench (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
    region text NOT NULL, task_type text NOT NULL, config_hash text NOT NULL,
    suite_hash text NOT NULL, shape text NOT NULL, mode text NOT NULL,
    status text NOT NULL, score numeric, cost_per_call numeric, samples integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS moa_bench_scope ON tank.moa_bench(tenant_id,region,task_type,config_hash,created_at DESC);
-- Seat receipts inherit the original task's baseline, never an arbitrary seat alias.
-- Trigger names run alphabetically; this follows CORE-1's existing baseline snapshot.
CREATE OR REPLACE FUNCTION tank.moa_baseline_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE chosen text; rate tank.proof_prices%ROWTYPE;
BEGIN
    IF NEW.route_seat IS NULL OR NEW.task_type NOT LIKE '%#moa.%' THEN RETURN NEW; END IF;
    SELECT p.baseline_model INTO chosen FROM tank.policy p
      WHERE p.task_type=split_part(NEW.task_type,'#moa.',1)
        AND (p.tenant_id=NEW.tenant_id OR p.tenant_id IS NULL)
      ORDER BY p.tenant_id NULLS LAST LIMIT 1;
    IF chosen IS NOT NULL THEN
      SELECT * INTO rate FROM tank.proof_prices WHERE model=chosen;
      NEW.baseline_model := chosen;
      NEW.baseline_cost_usd := NEW.tokens_in*rate.input_usd+NEW.tokens_out*rate.output_usd;
      IF rate.model IS NULL THEN
        NEW.baseline_status := 'unpriced';
      ELSE
        NEW.baseline_status := 'measured';
      END IF;
      NEW.baseline_priced_at := rate.updated_at;
    END IF;
    RETURN NEW;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='zz_moa_baseline'
                   AND tgrelid='tank.ledger'::regclass) THEN
        CREATE TRIGGER zz_moa_baseline BEFORE INSERT ON tank.ledger
            FOR EACH ROW EXECUTE FUNCTION tank.moa_baseline_snapshot();
    END IF;
END $$;

-- RIG-3: use the existing ACCESS feed connections, batches and receipts.
-- Rig connection names use the rig: prefix to preserve Tiger's bindings.
ALTER TABLE tank.access_feed_connections ADD COLUMN IF NOT EXISTS rig_refresh_at timestamptz;
ALTER TABLE tank.access_feed_batches ADD COLUMN IF NOT EXISTS rig_summary jsonb;

-- RIG-3: confirmed mappings for uploads and scheduled feeds, no source amounts or credentials.
CREATE TABLE IF NOT EXISTS tank.rig_feed_settings (
    tenant_id uuid NOT NULL,
    distributor text NOT NULL,
    site text NOT NULL,
    settings jsonb NOT NULL,
    PRIMARY KEY (tenant_id,distributor,site)
);

-- Short-lived field-name previews bind confirmation to the reviewing administrator.
CREATE TABLE IF NOT EXISTS tank.rig_feed_previews (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    user_id text NOT NULL,
    preview jsonb NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '15 minutes',
    PRIMARY KEY (tenant_id,id)
);

-- RIG-5F: operator-configured GPU limits and durable reservations. No default spend.
CREATE TABLE IF NOT EXISTS tank.rig_forge_limits (
    tenant_id uuid NOT NULL,
    method text NOT NULL CHECK (method IN ('photogrammetry','trellis2')),
    enabled boolean NOT NULL DEFAULT false,
    image text NOT NULL CHECK (image ~ '^[a-z0-9][a-z0-9./_-]+@sha256:[a-f0-9]{64}$'),
    flavor text NOT NULL CHECK (flavor IN ('t4-small','a10g-small','a10g-large','a100-large','h100')),
    hour_usd numeric(12,6) NOT NULL CHECK (hour_usd > 0),
    billing_quantum_seconds integer NOT NULL CHECK (billing_quantum_seconds BETWEEN 1 AND 3600),
    max_seconds integer NOT NULL CHECK (max_seconds BETWEEN 60 AND 3600),
    max_job_usd numeric(12,6) NOT NULL CHECK (max_job_usd > 0),
    total_budget_usd numeric(12,6) NOT NULL CHECK (total_budget_usd >= max_job_usd),
    PRIMARY KEY (tenant_id, method)
);
CREATE TABLE IF NOT EXISTS tank.rig_forge_jobs (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    method text NOT NULL CHECK (method IN ('photogrammetry','trellis2')),
    request jsonb NOT NULL,
    grant_payload jsonb NOT NULL,
    reserved_usd numeric(12,6) NOT NULL CHECK (reserved_usd > 0),
    state text NOT NULL CHECK (state IN ('reserved','claimed','complete')),
    receipt jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,id)
);
-- End RIG-5F.

-- ONE-DOOR Part B: foreground ladder, tenant cache, learning windows and batches.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS escalation_model text;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS confidence_floor numeric(5,4) NOT NULL DEFAULT 0.8;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS acceptance_window_s integer NOT NULL DEFAULT 86400;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS matter_matching boolean NOT NULL DEFAULT false;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS max_cost_usd numeric(12,6) NOT NULL DEFAULT 1;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS escalation jsonb NOT NULL DEFAULT '[]';
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS accept_after timestamptz;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS batch boolean NOT NULL DEFAULT false;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS batch_id uuid;
ALTER TABLE tank.training_pairs ADD COLUMN IF NOT EXISTS polarity text NOT NULL DEFAULT 'positive';
CREATE TABLE IF NOT EXISTS tank.route_cache (
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    task_type text NOT NULL,
    context_hash text NOT NULL,
    input_hash text NOT NULL,
    semantic_form text NOT NULL,
    vector jsonb NOT NULL,
    ledger_id uuid NOT NULL REFERENCES tank.ledger(id),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id,region,task_type,context_hash,input_hash)
);
CREATE INDEX IF NOT EXISTS route_cache_expiry ON tank.route_cache(expires_at);
CREATE TABLE IF NOT EXISTS tank.batches (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    task_type text NOT NULL,
    model text NOT NULL,
    count integer NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    provider text NOT NULL,
    provider_id text,
    input_file_id text,
    submitted_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    estimated_cost_usd numeric(18,8) NOT NULL DEFAULT 0,
    actual_cost_usd numeric(18,8) NOT NULL DEFAULT 0,
    error text,
    caller text,
    UNIQUE (tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.batch_items (
    batch_id uuid NOT NULL REFERENCES tank.batches(id),
    tenant_id uuid NOT NULL,
    item_id uuid NOT NULL,
    request jsonb NOT NULL,
    policy jsonb NOT NULL,
    prices jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    attempts integer NOT NULL DEFAULT 0,
    reserved_usd numeric(18,8) NOT NULL DEFAULT 0,
    ledger_id uuid REFERENCES tank.ledger(id),
    error text,
    PRIMARY KEY (batch_id,item_id)
);
CREATE INDEX IF NOT EXISTS batches_pending ON tank.batches(status,created_at);
CREATE TABLE IF NOT EXISTS tank.policy_seeds (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
DO $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('one-door-ladder-v1',0));
    IF NOT EXISTS (SELECT 1 FROM tank.policy_seeds WHERE name='one-door-ladder-v1') THEN
        -- Luna is the existing funded cheap roster model. Never approve code here.
        UPDATE tank.policy SET escalation_model='luna',rung=3,cache_scope='tenant',updated_at=now()
        WHERE tenant_id IS NULL AND task_type IN ('base.extract','base.business_data','agent.base.think');
        UPDATE tank.policy SET model=CASE WHEN EXISTS (
            SELECT 1 FROM tank.skills WHERE id='base.extract' AND version='2.0.0' AND state='approved'
        ) THEN 'skill:base.extract@2.0.0' ELSE 'luna' END
        WHERE tenant_id IS NULL AND task_type='base.extract';
        UPDATE tank.policy SET model=CASE WHEN EXISTS (
            SELECT 1 FROM tank.skills WHERE id='base.business_data' AND version='2.0.0' AND state='approved'
        ) THEN 'skill:base.business_data@2.0.0' ELSE 'luna' END,confidence_floor=0.4
        WHERE tenant_id IS NULL AND task_type='base.business_data';
        INSERT INTO tank.policy(task_type,rung,model,escalation_model,cache_scope,confidence_floor)
        VALUES ('base.normalize',3,CASE WHEN EXISTS (
            SELECT 1 FROM tank.skills WHERE id='base.normalize' AND version='1.0.0' AND state='approved'
        ) THEN 'skill:base.normalize@1.0.0' ELSE 'luna' END,'luna','tenant',0.8)
        ON CONFLICT DO NOTHING;
        -- Existing funded roster entry; no new provider or key. Matter keys are
        -- mandatory for semantic reuse; no reference means a semantic miss.
        UPDATE tank.policy SET model='astra',rung=1,semantic_cache=true,matter_matching=true,
            cache_scope='tenant',updated_at=now()
        WHERE tenant_id IS NULL AND task_type IN
          ('zenas.scope','zenas.rule_card','zenas.case_facts','zenas.apply','zenas.compose',
           'zenas.plain_language','zenas.draft','zenas.review','zenas.selftest');
        UPDATE tank.policy SET batch_eligible=true WHERE tenant_id IS NULL
          AND task_type IN ('base.extract','base.business_data','zenas.case_facts','zenas.rule_card','zenas.selftest');
        INSERT INTO tank.policy_seeds(name) VALUES('one-door-ladder-v1');
    END IF;
END $$;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS batch_baseline_cost_usd numeric(18,8);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS batch_discount_usd numeric(18,8);
ALTER TABLE tank.batches ADD COLUMN IF NOT EXISTS request_hash text NOT NULL DEFAULT '';
ALTER TABLE tank.batch_items ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT 0;

-- Repair only the three original platform seed pins on boxes that already ran
-- v1. Never approve a package, alter tenant overrides or overwrite later choices.
DO $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('one-door-approved-seeds-v2',0));
    IF NOT EXISTS (SELECT 1 FROM tank.policy_seeds WHERE name='one-door-approved-seeds-v2') THEN
        UPDATE tank.policy p SET model='luna',updated_at=now()
        FROM (VALUES ('base.normalize','1.0.0'),('base.extract','2.0.0'),
                     ('base.business_data','2.0.0')) AS seed(skill_id,version)
        WHERE p.tenant_id IS NULL AND p.task_type=seed.skill_id
          AND p.rung=3 AND p.escalation_model='luna'
          AND p.model='skill:'||seed.skill_id||'@'||seed.version
          AND NOT EXISTS (SELECT 1 FROM tank.skills s
            WHERE s.id=seed.skill_id AND s.version=seed.version AND s.state='approved');
        INSERT INTO tank.policy_seeds(name) VALUES('one-door-approved-seeds-v2');
    END IF;
END $$;

-- BASE-WORKS: immutable public reference snapshots and private client results.
CREATE TABLE IF NOT EXISTS tank.base_reference_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL CHECK(owner_id='00000000-0000-0000-0000-000000000001'),
    published_on date NOT NULL UNIQUE,
    source_url text NOT NULL,
    sha256 text NOT NULL,
    checked_at timestamptz NOT NULL,
    adopted boolean NOT NULL DEFAULT false,
    row_count integer NOT NULL CHECK(row_count > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.base_reference_entries (
    file_id uuid NOT NULL REFERENCES tank.base_reference_files(id),
    licence_key text NOT NULL,
    licence_number text NOT NULL,
    state text NOT NULL,
    licence_type text NOT NULL,
    name text NOT NULL,
    address jsonb NOT NULL,
    legacy_record_id uuid,
    raw jsonb,
    PRIMARY KEY(file_id,licence_key),
    CHECK (legacy_record_id IS NOT NULL OR raw IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS base_reference_selection ON tank.base_reference_entries(file_id,state,licence_type);
CREATE TABLE IF NOT EXISTS tank.base_reference_checks (
    owner_id uuid PRIMARY KEY CHECK(owner_id='00000000-0000-0000-0000-000000000001'),
    checked_at timestamptz NOT NULL,
    status text NOT NULL,
    message text
);
-- Independent source stops; legacy rows still describe ATF until next checked.
ALTER TABLE tank.base_reference_checks ADD COLUMN IF NOT EXISTS source_checks jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE IF NOT EXISTS tank.base_campaigns (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL,
    name text NOT NULL,
    file_id uuid REFERENCES tank.base_reference_files(id),
    selection jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,id),
    UNIQUE(tenant_id,request_id)
);
CREATE TABLE IF NOT EXISTS tank.base_campaign_states (
    tenant_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    state text NOT NULL,
    ordinal integer NOT NULL,
    batch text NOT NULL,
    task_id uuid,
    status text NOT NULL DEFAULT 'waiting',
    records_count integer NOT NULL,
    csv_records jsonb,
    started_at timestamptz,
    finished_at timestamptz,
    error text,
    PRIMARY KEY(tenant_id,campaign_id,state),
    FOREIGN KEY(tenant_id,campaign_id) REFERENCES tank.base_campaigns(tenant_id,id),
    UNIQUE(tenant_id,batch)
);
CREATE TABLE IF NOT EXISTS tank.base_record_results (
    tenant_id uuid NOT NULL,
    record_id uuid NOT NULL,
    task_id uuid,
    batch text,
    licence_key text,
    state text NOT NULL,
    licence_type text NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    fields jsonb NOT NULL,
    source_revision text NOT NULL,
    attempted_at timestamptz NOT NULL,
    checked_at timestamptz NOT NULL,
    PRIMARY KEY(tenant_id,record_id)
);
CREATE INDEX IF NOT EXISTS base_results_dimensions ON tank.base_record_results(tenant_id,state,licence_type,checked_at);
CREATE TABLE IF NOT EXISTS tank.base_ledger_links (
    tenant_id uuid NOT NULL,
    record_id uuid NOT NULL,
    ledger_id uuid NOT NULL REFERENCES tank.ledger(id),
    PRIMARY KEY(tenant_id,record_id,ledger_id)
);
CREATE TABLE IF NOT EXISTS tank.base_apollo_imports (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    sha256 text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,id),
    UNIQUE(tenant_id,sha256)
);
CREATE TABLE IF NOT EXISTS tank.base_apollo_records (
    tenant_id uuid NOT NULL,
    import_id uuid NOT NULL,
    licence_key text NOT NULL,
    fields jsonb NOT NULL,
    PRIMARY KEY(tenant_id,import_id,licence_key),
    FOREIGN KEY(tenant_id,import_id) REFERENCES tank.base_apollo_imports(tenant_id,id)
);
ALTER TABLE tank.base_apollo_records ADD COLUMN IF NOT EXISTS base_snapshot jsonb;

-- Immutable sampled evidence; a checked CSV only adds human verdicts.
CREATE TABLE IF NOT EXISTS tank.base_audits (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    campaign_id uuid NOT NULL,
    seed bigint NOT NULL CHECK(seed >= 0),
    sampling_method text NOT NULL DEFAULT 'python_random_sample_v1',
    population_size integer NOT NULL CHECK(population_size >= 200),
    population_sha256 text NOT NULL,
    records jsonb NOT NULL CHECK(jsonb_typeof(records)='array' AND jsonb_array_length(records)=200),
    verdicts jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(verdicts)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL,
    reviewed_at timestamptz,
    reviewed_by text,
    PRIMARY KEY(tenant_id,id),
    FOREIGN KEY(tenant_id,campaign_id) REFERENCES tank.base_campaigns(tenant_id,id),
    UNIQUE(tenant_id,campaign_id,seed)
);

-- Explicit field checks are additive; legacy record verdicts stay unchanged.
ALTER TABLE tank.base_audits ADD COLUMN IF NOT EXISTS field_verdicts jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Job 6 (F8): Base source reliability from reviewed audits. Platform-wide counts only:
-- source kind, field name and numbers; never a tenant, record, URL or value.
CREATE TABLE IF NOT EXISTS tank.base_source_reliability (
    source text NOT NULL,
    field text NOT NULL,
    correct integer NOT NULL CHECK(correct >= 0),
    wrong integer NOT NULL CHECK(wrong >= 0),
    checked integer NOT NULL CHECK(checked = correct + wrong AND checked > 0),
    accuracy double precision NOT NULL CHECK(accuracy BETWEEN 0 AND 1),
    wilson_lower double precision CHECK(wilson_lower BETWEEN 0 AND 1),
    audits integer NOT NULL CHECK(audits > 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(source, field)
);
-- Additive ownership for branches that already installed the initial score table.
ALTER TABLE tank.base_source_reliability ADD COLUMN IF NOT EXISTS owner_id uuid NOT NULL
    DEFAULT '00000000-0000-0000-0000-000000000001'
    CHECK (owner_id='00000000-0000-0000-0000-000000000001'::uuid);

-- Founder decision: retain NOT NULL; a not_set amount is storage only, never spend.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='tank' AND table_name='price_book' AND column_name='unit_price'
          AND numeric_precision=18 AND numeric_scale=8
    ) THEN
        ALTER TABLE tank.price_book ALTER COLUMN unit_price TYPE numeric(18,8);
    END IF;
END $$;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='tank' AND table_name='ledger' AND column_name='cost_usd'
          AND numeric_precision=18 AND numeric_scale=8
    ) THEN
        ALTER TABLE tank.ledger ALTER COLUMN cost_usd TYPE numeric(18,8);
    END IF;
END $$;
ALTER TABLE tank.price_book ADD COLUMN IF NOT EXISTS price_status text NOT NULL DEFAULT 'set' CHECK(price_status IN ('set','not_set'));
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS cost_status text NOT NULL DEFAULT 'set' CHECK(cost_status IN ('set','not_set'));
ALTER TABLE tank.econ_daily ADD COLUMN IF NOT EXISTS unpriced_calls integer NOT NULL DEFAULT 0;
ALTER TABLE tank.price_book ADD COLUMN IF NOT EXISTS free_pass boolean NOT NULL DEFAULT false;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS vendor_cost_status text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS vendor_unit_price numeric(20,10);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS vendor_price_id uuid;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS vendor_list_cost_usd numeric(20,10);
INSERT INTO tank.price_book(id,tenant_id,outcome_type,unit_price,price_status,currency,effective_from,note)
VALUES
 ('ba5e0000-0000-4000-8000-000000000001','00000000-0000-0000-0000-000000000001','apollo.contact',0,'not_set','USD','2026-10-01','Expense per contact; price not set. Founder-supplied CSV comparison requires no API call.'),
 ('ba5e0000-0000-4000-8000-000000000002','00000000-0000-0000-0000-000000000001','brave.search.query',0,'not_set','USD','2026-10-01','Expense per successful query; price not set.'),
 ('ba5e0000-0000-4000-8000-000000000003','00000000-0000-0000-0000-000000000001','google.places.request',0,'not_set','USD','2026-10-01','Expense per successful Places request; price not set.'),
 ('ba5e0000-0000-4000-8000-000000000004','00000000-0000-0000-0000-000000000001','context.dev.credit',0.000792,'set','USD','2026-10-21','Pro list price: $99 / 125000 credits; https://www.context.dev/pricing checked 2026-10-21. Free pass only when founder explicitly sets it.')
ON CONFLICT(id) DO NOTHING;

-- Count duplicate source rows without changing the platform-owned originals.
ALTER TABLE tank.base_reference_files ADD COLUMN IF NOT EXISTS duplicate_count integer NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0);

-- Product plugin metadata; credential plaintext is never persisted here.
CREATE TABLE IF NOT EXISTS tank.vault_items (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL, vault text NOT NULL, kind text NOT NULL,
    name text NOT NULL, metadata jsonb NOT NULL, sealed text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, vault, kind, name)
);


-- Founder decision, 2026-10-27: 99% agreement over 200 checked samples.
-- Change only the complete old default pair, leaving customized bars intact.
-- A marker protects later founder edits (including a deliberate return to the
-- old values) when startup reapplies this file. No policy choice is reset.
ALTER TABLE tank.policy ALTER COLUMN eval_threshold SET DEFAULT 0.99;
ALTER TABLE tank.policy ALTER COLUMN min_shadow_samples SET DEFAULT 200;
ALTER TABLE tank.route_evidence ALTER COLUMN min_shadow_samples SET DEFAULT 200;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tank.policy_seeds WHERE name='graduation-99-200-v1') THEN
        UPDATE tank.policy SET eval_threshold=0.99, min_shadow_samples=200
        WHERE eval_threshold=0.97 AND min_shadow_samples=30;
        INSERT INTO tank.policy_seeds(name) VALUES('graduation-99-200-v1');
    END IF;
END $$;

-- Chats use Tank's tenant-sealed exact cache, never semantic matching.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tank.policy_seeds WHERE name='command-exact-cache-v1') THEN
        UPDATE tank.policy SET cache_scope='tenant', semantic_cache=false, updated_at=now()
        WHERE task_type='command.turn';
        INSERT INTO tank.policy_seeds(name) VALUES('command-exact-cache-v1');
    END IF;
END $$;

-- Local semantic cache shadow evidence. No existing serving policy is changed.
CREATE TABLE IF NOT EXISTS tank.semantic_observations (
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    task_type text NOT NULL,
    ledger_id uuid NOT NULL REFERENCES tank.ledger(id),
    policy_hash text NOT NULL,
    encoder_id text NOT NULL,
    input_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('hit','miss','guarded','unavailable')),
    source_id uuid REFERENCES tank.ledger(id),
    candidate_content text,
    similarity double precision,
    agreement double precision CHECK (agreement BETWEEN 0 AND 1),
    primary_cost_usd numeric(18,8) NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,ledger_id)
);
CREATE INDEX IF NOT EXISTS semantic_observations_evidence
    ON tank.semantic_observations(tenant_id,region,task_type,policy_hash,encoder_id,input_hash);
CREATE TABLE IF NOT EXISTS tank.semantic_approvals (
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    task_type text NOT NULL,
    policy_hash text NOT NULL,
    encoder_id text NOT NULL,
    approved boolean NOT NULL DEFAULT false,
    approved_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,region,task_type,policy_hash,encoder_id)
);

-- RouteLLM is an opt-in shadow experiment. No policy is enabled by migration.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS routellm_weak_model text;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS routellm_threshold double precision
    CHECK (routellm_threshold >= 0 AND routellm_threshold <= 1);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_model text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_score double precision;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_threshold double precision;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_weak_model text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_config text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_status text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_primary_cost_known boolean;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_model_cost_usd numeric(12,6);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_parent_id uuid;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS routellm_sample_tenant uuid;
ALTER TABLE tank.shadow ADD COLUMN IF NOT EXISTS routellm_config text;
ALTER TABLE tank.shadow ADD COLUMN IF NOT EXISTS candidate_ledger_id uuid;

-- Case-study report identities. Values stay in their existing evidence stores.
CREATE TABLE IF NOT EXISTS tank.case_study_clients (
    tenant_id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.case_study_reports (
    tenant_id uuid NOT NULL REFERENCES tank.case_study_clients(tenant_id),
    product text NOT NULL CHECK(product IN ('base','tiger','market_lab','rig','zenas','trigger','tisan','radix','autumbra')),
    template_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,product)
);
CREATE OR REPLACE FUNCTION tank.register_case_study_product() RETURNS trigger AS $$
DECLARE report_product text;
BEGIN
    IF TG_NARGS>0 THEN
        report_product := TG_ARGV[0];
    ELSE
        report_product := NEW.agent;
    END IF;
    report_product := CASE report_product WHEN 'ghost' THEN 'autumbra' WHEN 'market-lab' THEN 'market_lab' ELSE report_product END;
    IF report_product IN ('base','tiger','market_lab','rig','zenas','trigger','tisan','radix','autumbra') THEN
        INSERT INTO tank.case_study_clients(tenant_id) VALUES(NEW.tenant_id) ON CONFLICT DO NOTHING;
        INSERT INTO tank.case_study_reports(tenant_id,product) VALUES(NEW.tenant_id,report_product) ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, tank;
DO $$ BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='case_study_product_added' AND tgrelid='tank.tenant_squad'::regclass) THEN
        CREATE TRIGGER case_study_product_added AFTER INSERT OR UPDATE OF agent ON tank.tenant_squad
            FOR EACH ROW EXECUTE FUNCTION tank.register_case_study_product();
    END IF;
    -- Trigger and Autumbra have no squad-grant entry on today's main. Existing
    -- product activity still registers its report atomically, without granting
    -- new tools or copying the receipt into a parallel reporting store.
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='case_study_product_used' AND tgrelid='tank.ledger'::regclass) THEN
        CREATE TRIGGER case_study_product_used AFTER INSERT ON tank.ledger
            FOR EACH ROW EXECUTE FUNCTION tank.register_case_study_product();
    END IF;
    IF to_regclass('agent_trigger.voices') IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM pg_trigger WHERE tgname='case_study_trigger_provisioned' AND tgrelid=to_regclass('agent_trigger.voices')) THEN
        CREATE TRIGGER case_study_trigger_provisioned AFTER INSERT ON agent_trigger.voices
            FOR EACH ROW EXECUTE FUNCTION tank.register_case_study_product('trigger');
    END IF;
END $$;
-- Existing grants receive the same registration once; no report metrics are copied.
INSERT INTO tank.case_study_clients(tenant_id) SELECT DISTINCT tenant_id FROM tank.tenant_squad ON CONFLICT DO NOTHING;
INSERT INTO tank.case_study_reports(tenant_id,product)
    SELECT tenant_id,CASE WHEN agent='ghost' THEN 'autumbra' ELSE agent END FROM tank.tenant_squad
    WHERE agent IN ('base','tiger','market_lab','rig','zenas','trigger','tisan','radix','autumbra','ghost') ON CONFLICT DO NOTHING;
INSERT INTO tank.case_study_clients(tenant_id)
    SELECT DISTINCT tenant_id FROM tank.ledger
    WHERE agent IN ('base','tiger','market_lab','market-lab','rig','zenas','trigger','tisan','radix','autumbra','ghost') ON CONFLICT DO NOTHING;
INSERT INTO tank.case_study_reports(tenant_id,product)
    SELECT DISTINCT tenant_id,CASE agent WHEN 'ghost' THEN 'autumbra' WHEN 'market-lab' THEN 'market_lab' ELSE agent END
    FROM tank.ledger WHERE agent IN ('base','tiger','market_lab','market-lab','rig','zenas','trigger','tisan','radix','autumbra','ghost') ON CONFLICT DO NOTHING;
DO $$ BEGIN
    IF to_regclass('agent_trigger.voices') IS NOT NULL THEN
        INSERT INTO tank.case_study_clients(tenant_id) SELECT tenant_id FROM agent_trigger.voices ON CONFLICT DO NOTHING;
        INSERT INTO tank.case_study_reports(tenant_id,product) SELECT tenant_id,'trigger' FROM agent_trigger.voices ON CONFLICT DO NOTHING;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS shadow_routellm_tenant_task ON tank.shadow(tenant_id,task_type,routellm_config)
    WHERE routellm_config IS NOT NULL;

-- Trigger first build. Leave all existing and tenant-specific policies untouched.
INSERT INTO tank.policy(task_type,rung,model,cache_scope,max_tokens_out,eval_threshold,min_shadow_samples,auto_promote)
VALUES ('trigger.write',1,'luna','tenant',3000,0.99,200,false),
       ('trigger.check',3,'code','none',0,0.99,200,false)
ON CONFLICT DO NOTHING;
-- trigger.check never calls /v1/route; its adapter executes code and records zero cost.

-- Trigger second build: tenant-private writing/feedback provenance.
CREATE TABLE IF NOT EXISTS tank.trigger_writes (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    brief text NOT NULL,
    draft text NOT NULL,
    checker_results jsonb NOT NULL,
    feedback_status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    write_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('accepted','changes_requested','rejected')),
    changes text NOT NULL DEFAULT '',
    user_id text NOT NULL,
    operation_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,write_id) REFERENCES tank.trigger_writes(tenant_id,id),
    UNIQUE (tenant_id,operation_id)
);

-- Explicitly deferred requests. Queue claims and all receipts remain tenant/box scoped.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS sent_at timestamptz;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS offpeak_job_id uuid;
CREATE TABLE IF NOT EXISTS tank.offpeak_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    request jsonb NOT NULL,
    policy jsonb NOT NULL,
    caller text,
    task_id uuid,
    model_override text,
    status text NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','dispatching','batch','completed','failed','uncertain')),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    finished_at timestamptz,
    result jsonb,
    error text,
    command_context jsonb,
    notified boolean NOT NULL DEFAULT false,
    UNIQUE(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS offpeak_pending ON tank.offpeak_jobs(region,status,created_at);
CREATE INDEX IF NOT EXISTS offpeak_task ON tank.offpeak_jobs(tenant_id,task_id);
ALTER TABLE tank.offpeak_jobs ADD COLUMN IF NOT EXISTS checked_at timestamptz;


-- B3: transactional model-change outbox. Registry/eval work belongs to the
-- platform tenant; policy_tenant_id identifies an optional tenant override.
CREATE TABLE IF NOT EXISTS tank.skill_revalidations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    policy_tenant_id uuid,
    task_type text NOT NULL,
    old_model text NOT NULL,
    model text NOT NULL,
    policy jsonb NOT NULL,
    skill_id text NOT NULL,
    version text NOT NULL,
    queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    eval_id uuid,
    error text
);
CREATE INDEX IF NOT EXISTS skill_revalidations_pending
    ON tank.skill_revalidations(tenant_id, queued_at, id) WHERE completed_at IS NULL;
ALTER TABLE tank.skill_evals ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE tank.skill_evals ADD COLUMN IF NOT EXISTS revalidation_id uuid;
CREATE UNIQUE INDEX IF NOT EXISTS skill_evals_revalidation_once
    ON tank.skill_evals(revalidation_id) WHERE revalidation_id IS NOT NULL;

-- Routing data: null fallbacks inherit the gateway chain. Holds never serve early.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS fallbacks text[];
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS held_policy jsonb;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS held_swap_id uuid;
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS held_at timestamptz;
ALTER TABLE tank.skill_revalidations ADD COLUMN IF NOT EXISTS swap_id uuid;
CREATE INDEX IF NOT EXISTS skill_revalidations_swap
    ON tank.skill_revalidations(tenant_id,swap_id);

CREATE OR REPLACE FUNCTION tank.queue_skill_revalidations() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE serving tank.policy%ROWTYPE;
        requested jsonb;
        swap uuid;
BEGIN
    IF current_setting('tank.policy_swap_apply',true) = 'on' THEN
        NEW.held_policy := NULL;
        NEW.held_swap_id := NULL;
        NEW.held_at := NULL;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        serving := OLD;
    ELSE
        -- An upsert's UPDATE trigger owns existing rows; do not queue twice.
        IF EXISTS (SELECT 1 FROM tank.policy WHERE task_type=NEW.task_type
                   AND tenant_id IS NOT DISTINCT FROM NEW.tenant_id) THEN
            RETURN NEW;
        END IF;
        SELECT * INTO serving FROM tank.policy
          WHERE task_type=NEW.task_type AND tenant_id IS NULL;
    END IF;
    NEW.held_policy := NULL;
    NEW.held_swap_id := NULL;
    NEW.held_at := NULL;
    IF serving.model IS NULL OR serving.model IS NOT DISTINCT FROM NEW.model
       OR NOT EXISTS (SELECT 1 FROM tank.skills s
                      WHERE s.task_type=NEW.task_type AND s.state='approved') THEN
        RETURN NEW;
    END IF;
    swap := gen_random_uuid();
    requested := to_jsonb(NEW);
    INSERT INTO tank.skill_revalidations
      (tenant_id,policy_tenant_id,task_type,old_model,model,policy,skill_id,version,swap_id)
    SELECT '00000000-0000-0000-0000-000000000001'::uuid,
           NEW.tenant_id,NEW.task_type,serving.model,NEW.model,requested,s.id,s.version,swap
      FROM tank.skills s WHERE s.task_type=NEW.task_type AND s.state='approved';
    INSERT INTO tank.policy_log(task_type,action,from_model,to_model,by_user,detail)
    VALUES (NEW.task_type,'held',serving.model,NEW.model,'tank',
            jsonb_build_object('swap_id',swap,'tenant_id',NEW.tenant_id));
    serving.tenant_id := NEW.tenant_id;
    serving.task_type := NEW.task_type;
    NEW := serving;
    NEW.held_policy := requested;
    NEW.held_swap_id := swap;
    NEW.held_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER policy_skill_revalidation
    BEFORE INSERT OR UPDATE OF model ON tank.policy
    FOR EACH ROW EXECUTE FUNCTION tank.queue_skill_revalidations();

-- Provider prompt caching: cache read/write tokens reported by the provider,
-- already included in tokens_in. Additive; existing rows read as zero.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS cache_read_tokens integer NOT NULL DEFAULT 0;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS cache_write_tokens integer NOT NULL DEFAULT 0;

-- CLI-1: bearer credentials are never persisted; scope remains local to this box.
CREATE TABLE IF NOT EXISTS tank.personal_tokens (
    token_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east','za')),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS personal_tokens_owner ON tank.personal_tokens(tenant_id,user_id);
-- Follow an existing RLS rollout; do not turn it on for a legacy installation.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        ALTER TABLE tank.personal_tokens ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.personal_tokens FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.personal_tokens'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.personal_tokens FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.personal_tokens'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.personal_tokens AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END
$$;

-- CLI device sign-in (#496): the CLI asks for a device code, a signed-in Workspace
-- user approves it, and the box issues an org-scoped credential in personal_tokens.
-- Additive and idempotent. Every secret is stored only as a SHA-256 hash.
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS device_name text CHECK (device_name IS NULL OR length(device_name) BETWEEN 1 AND 100);
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS last_used_at timestamptz;
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS refresh_hash text CHECK (refresh_hash IS NULL OR refresh_hash ~ '^[0-9a-f]{64}$');
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS refresh_expires_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS personal_tokens_refresh ON tank.personal_tokens(refresh_hash) WHERE refresh_hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS tank.device_authorizations (
    device_hash text PRIMARY KEY CHECK (device_hash ~ '^[0-9a-f]{64}$'),
    user_code text NOT NULL UNIQUE CHECK (user_code ~ '^[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}$'),
    region text NOT NULL CHECK (region IN ('us-east','za')),
    device_name text NOT NULL CHECK (length(device_name) BETWEEN 1 AND 100),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied','consumed')),
    -- Set only by a signed-in Workspace approval; NULL while pending or denied.
    tenant_id uuid,
    user_id text,
    token_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    decided_at timestamptz,
    last_polled_at timestamptz,
    CHECK (status NOT IN ('approved','consumed') OR (tenant_id IS NOT NULL AND user_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS device_authorizations_expiry ON tank.device_authorizations(expires_at);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        ALTER TABLE tank.device_authorizations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.device_authorizations FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.device_authorizations'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.device_authorizations FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.device_authorizations'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.device_authorizations AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END
$$;

-- Tisan plans structure only; accepted Trigger writes supply every text slot.
INSERT INTO tank.policy(task_type,rung,model,cache_scope,max_tokens_out,eval_threshold,min_shadow_samples,auto_promote)
VALUES ('tisan.plan',1,'luna','tenant',3000,0.99,200,false)
ON CONFLICT DO NOTHING;

-- Code mode starts with the existing chat policy, including tenant-specific rows.
-- A new task name, not a new provider choice. Code input must never enter the route cache.
INSERT INTO tank.policy
SELECT (jsonb_populate_record(NULL::tank.policy,
        to_jsonb(p) || '{"task_type":"command.code","cache_scope":"none"}'::jsonb)).*
FROM tank.policy p WHERE p.task_type='command.turn'
ON CONFLICT (task_type, (COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid)))
DO UPDATE SET cache_scope='none' WHERE tank.policy.cache_scope IS DISTINCT FROM 'none';
-- IDENTITY-ZA: existing token format/lifetimes remain unchanged. Trusted role
-- snapshot is set only by verified OIDC issuance; refresh/revoke preserve the row.
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS identity_tenant_admin boolean NOT NULL DEFAULT false;
ALTER TABLE tank.device_authorizations ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT 'workspace'
    CHECK (provider IN ('workspace', 'zitadel'));

-- B4 saved Base lists: immutable tenant-owned record/evidence snapshots.
CREATE TABLE IF NOT EXISTS tank.base_saved_lists (
    list_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    filters jsonb NOT NULL,
    record_ids uuid[] NOT NULL,
    records jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS base_saved_lists_tenant ON tank.base_saved_lists(tenant_id,created_at DESC);
-- Follow personal_tokens: join an existing RLS rollout, never start one.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        ALTER TABLE tank.base_saved_lists ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.base_saved_lists FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.base_saved_lists'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.base_saved_lists FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.base_saved_lists'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.base_saved_lists AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END
$$;

-- Trigger W1 provenance remains in the existing tenant-private tables.
ALTER TABLE tank.trigger_writes ADD COLUMN IF NOT EXISTS format text;
ALTER TABLE tank.trigger_writes ADD COLUMN IF NOT EXISTS accepted_write_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE tank.trigger_writes ADD COLUMN IF NOT EXISTS write_context jsonb NOT NULL DEFAULT '{}';
ALTER TABLE tank.trigger_writes ADD COLUMN IF NOT EXISTS parent_write_id uuid;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='tank.trigger_writes'::regclass AND conname='trigger_parent_tenant') THEN
        ALTER TABLE tank.trigger_writes ADD CONSTRAINT trigger_parent_tenant FOREIGN KEY (tenant_id,parent_write_id) REFERENCES tank.trigger_writes(tenant_id,id);
    END IF;
    -- Match personal_tokens: follow an existing rollout, never enable legacy RLS.
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        ALTER TABLE tank.trigger_writes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.trigger_writes FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_writes'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.trigger_writes FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_writes'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.trigger_writes AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END
$$;
CREATE INDEX IF NOT EXISTS trigger_writes_format_recent ON tank.trigger_writes(tenant_id,format,created_at DESC);

-- MCP G1: reuse CLI credentials, with a distinct audience and public-client binding.
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS audience text NOT NULL DEFAULT 'cli'
    CHECK (audience IN ('cli','mcp'));
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS client_id text;

-- Public OAuth client metadata, not tenant-owned records or credentials.
CREATE TABLE IF NOT EXISTS tank.oauth_clients (
    client_id text PRIMARY KEY,
    client_name text NOT NULL CHECK (length(client_name) BETWEEN 1 AND 100),
    redirect_uris jsonb NOT NULL,
    source_address_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oauth_clients_source ON tank.oauth_clients(source_address_hash,created_at);

-- The code envelope supplies only a tenant scope; its hash must match before use.
CREATE TABLE IF NOT EXISTS tank.oauth_codes (
    code_hash text PRIMARY KEY CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    region text NOT NULL CHECK (region='us-east'),
    client_id text NOT NULL,
    client_name text NOT NULL,
    redirect_uri text NOT NULL,
    challenge text NOT NULL,
    resource text NOT NULL CHECK (resource='https://app.tervory.com/mcp'),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz
);
CREATE INDEX IF NOT EXISTS oauth_codes_tenant ON tank.oauth_codes(tenant_id,expires_at);
-- Retain the issued grant reference so an authorization-code replay revokes it.
ALTER TABLE tank.oauth_codes ADD COLUMN IF NOT EXISTS token_id uuid REFERENCES tank.personal_tokens(token_id);

CREATE TABLE IF NOT EXISTS tank.mcp_sessions (
    session_hash text PRIMARY KEY CHECK (session_hash ~ '^[0-9a-f]{64}$'),
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    token_id uuid NOT NULL REFERENCES tank.personal_tokens(token_id),
    client_id text NOT NULL,
    thread_id uuid NOT NULL REFERENCES tank.command_threads(thread_id),
    initialized boolean NOT NULL DEFAULT false,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '30 days'
);
CREATE INDEX IF NOT EXISTS mcp_sessions_owner ON tank.mcp_sessions(tenant_id,token_id);

-- Match personal_tokens exactly: staged installations stay staged. Enforced
-- installations get both the permissive policy and the restrictive boundary.
DO $$
DECLARE tab text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        FOREACH tab IN ARRAY ARRAY['oauth_codes','mcp_sessions'] LOOP
            EXECUTE format('ALTER TABLE tank.%I ENABLE ROW LEVEL SECURITY',tab);
            EXECUTE format('ALTER TABLE tank.%I FORCE ROW LEVEL SECURITY',tab);
            IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=format('tank.%I',tab)::regclass AND polname='tenant_access') THEN
                EXECUTE format($policy$CREATE POLICY tenant_access ON tank.%I FOR ALL TO PUBLIC
                    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)$policy$,tab);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=format('tank.%I',tab)::regclass AND polname='tenant_boundary') THEN
                EXECUTE format($policy$CREATE POLICY tenant_boundary ON tank.%I AS RESTRICTIVE FOR ALL TO PUBLIC
                    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)$policy$,tab);
            END IF;
        END LOOP;
    END IF;
END
$$;


-- G3: founder-owned MCP approval registry (global, deliberately no tenant/RLS).
CREATE TABLE IF NOT EXISTS tank.mcp_servers (
    url text PRIMARY KEY,
    name text NOT NULL,
    allowed_tools jsonb NOT NULL CHECK (jsonb_typeof(allowed_tools)='object'),
    approved_by text NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT now()
);
-- Existing vault row/RLS scope protects cached discovery and sealed OAuth state.
-- Plugin status is deliberately unchanged; mcp_state has its own lifecycle.
ALTER TABLE tank.vault_items ADD COLUMN IF NOT EXISTS mcp_state text NOT NULL DEFAULT 'not_approved'
    CHECK (mcp_state IN ('not_approved','needs_sign_in','connected','sign_in_failed'));
ALTER TABLE tank.vault_items ADD COLUMN IF NOT EXISTS mcp_tools jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE tank.vault_items ADD COLUMN IF NOT EXISTS mcp_discovered_at timestamptz;

-- ML3 alert preferences and delivery claims join an existing RLS rollout.
CREATE TABLE IF NOT EXISTS tank.market_lab_alerts (
    tenant_id uuid PRIMARY KEY,
    email_enabled boolean NOT NULL DEFAULT false,
    email_since timestamptz,
    webhook_url text,
    webhook_sealed text,
    webhook_since timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.market_lab_deliveries (
    tenant_id uuid NOT NULL,
    warning_id uuid NOT NULL,
    channel text NOT NULL CHECK (channel IN ('email','webhook')),
    status text NOT NULL CHECK (status IN ('pending','sending','sent','uncertain','suppressed')),
    letter jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, warning_id, channel)
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        ALTER TABLE tank.market_lab_alerts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.market_lab_alerts FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.market_lab_alerts'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.market_lab_alerts FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.market_lab_alerts'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.market_lab_alerts AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        ALTER TABLE tank.market_lab_deliveries ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.market_lab_deliveries FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.market_lab_deliveries'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.market_lab_deliveries FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.market_lab_deliveries'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.market_lab_deliveries AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END
$$;

-- Trigger B1: tenant-private evidence and conversations. Founder-approved
-- retention is 12 months after the last message (2026-11-03).
CREATE TABLE IF NOT EXISTS tank.trigger_bot_sources (
 tenant_id uuid NOT NULL, id uuid NOT NULL, kind text NOT NULL,
 title text NOT NULL, url text, content text NOT NULL DEFAULT '',
 status text NOT NULL DEFAULT 'pending', checked_at timestamptz,
 next_check timestamptz NOT NULL DEFAULT now(), operation_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,operation_id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_bot_passages (
 tenant_id uuid NOT NULL, id uuid NOT NULL, source_id uuid NOT NULL,
 text text NOT NULL, url text, title text NOT NULL, kind text NOT NULL,
 sku text, product_id text, question text, approved_answer text,
 search tsvector GENERATED ALWAYS AS (to_tsvector('english',text)) STORED,
 PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,source_id) REFERENCES tank.trigger_bot_sources(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS trigger_bot_search ON tank.trigger_bot_passages USING gin(search);
CREATE TABLE IF NOT EXISTS tank.trigger_bot_conversations (
 tenant_id uuid NOT NULL, id uuid NOT NULL, visitor_id uuid NOT NULL,
 channel text NOT NULL DEFAULT 'web' CHECK(channel='web'),
 status text NOT NULL DEFAULT 'bot' CHECK(status IN ('bot','handed_over','closed')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_bot_messages (
 tenant_id uuid NOT NULL, id uuid NOT NULL, conversation_id uuid NOT NULL,
 question text NOT NULL, answer text, retrieved uuid[] NOT NULL DEFAULT '{}',
 cited uuid[] NOT NULL DEFAULT '{}', outcome text NOT NULL, feedback text,
 ledger_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,conversation_id) REFERENCES tank.trigger_bot_conversations(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_bot_drafts (
 tenant_id uuid NOT NULL, id uuid NOT NULL, conversation_id uuid,
 kind text NOT NULL, data jsonb NOT NULL, state text NOT NULL DEFAULT 'draft',
 operation_id uuid, result jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_bot_config (
 tenant_id uuid PRIMARY KEY, enabled boolean NOT NULL DEFAULT false,
 handover_email text, origins jsonb NOT NULL DEFAULT '[]',
 operation_id uuid, updated_at timestamptz NOT NULL DEFAULT now()
);
-- Founder retention decision, 2026-11-03: last message + 12 calendar months.
CREATE INDEX IF NOT EXISTS trigger_bot_message_age ON tank.trigger_bot_messages(tenant_id,conversation_id,created_at DESC);
CREATE INDEX IF NOT EXISTS trigger_bot_draft_conversation ON tank.trigger_bot_drafts(tenant_id,conversation_id);
CREATE INDEX IF NOT EXISTS trigger_bot_retention_runs ON tank.ledger(tenant_id,ts DESC)
 WHERE task_type='trigger.bot.retention';
CREATE TABLE IF NOT EXISTS tank.trigger_bot_embed_keys (
 tenant_id uuid NOT NULL, key_hash text NOT NULL, region text NOT NULL,
 origins jsonb NOT NULL, revoked boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,key_hash)
);
CREATE TABLE IF NOT EXISTS tank.trigger_bot_limits (
 tenant_id uuid NOT NULL, window_id bigint NOT NULL, route text NOT NULL,
 client_hash bytea NOT NULL, hits integer NOT NULL, PRIMARY KEY(tenant_id,window_id,route,client_hash)
);
CREATE TABLE IF NOT EXISTS tank.trigger_bot_windows (
 tenant_id uuid NOT NULL, window_id bigint NOT NULL, salt bytea NOT NULL,
 PRIMARY KEY(tenant_id,window_id)
);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='tank.trigger_bot_limits'::regclass
                  AND conname='trigger_bot_limits_window_fk') THEN
  -- Clean legacy orphans once, before enforcing the window relationship.
  DELETE FROM tank.trigger_bot_limits l
   WHERE NOT EXISTS (SELECT 1 FROM tank.trigger_bot_windows w
                     WHERE w.tenant_id=l.tenant_id AND w.window_id=l.window_id);
  ALTER TABLE tank.trigger_bot_limits ADD CONSTRAINT trigger_bot_limits_window_fk
   FOREIGN KEY (tenant_id,window_id)
   REFERENCES tank.trigger_bot_windows(tenant_id,window_id) ON DELETE CASCADE;
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='tank.trigger_bot_limits'::regclass
                  AND conname='trigger_bot_limits_hits_positive') THEN
  ALTER TABLE tank.trigger_bot_limits ADD CONSTRAINT trigger_bot_limits_hits_positive CHECK (hits>0);
 END IF;
END $$;
-- Same conditional ENABLE/FORCE + permissive/restrictive boundary as personal_tokens.
DO $$ DECLARE tab text; BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  FOREACH tab IN ARRAY ARRAY['trigger_bot_sources','trigger_bot_passages','trigger_bot_conversations',
   'trigger_bot_messages','trigger_bot_drafts','trigger_bot_config','trigger_bot_embed_keys',
   'trigger_bot_limits','trigger_bot_windows'] LOOP
   EXECUTE format('ALTER TABLE tank.%I ENABLE ROW LEVEL SECURITY',tab);
   EXECUTE format('ALTER TABLE tank.%I FORCE ROW LEVEL SECURITY',tab);
   IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=format('tank.%I',tab)::regclass AND polname='tenant_access') THEN
    EXECUTE format('CREATE POLICY tenant_access ON tank.%I FOR ALL TO PUBLIC USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',tab);
   END IF;
   IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=format('tank.%I',tab)::regclass AND polname='tenant_boundary') THEN
    EXECUTE format('CREATE POLICY tenant_boundary ON tank.%I AS RESTRICTIVE FOR ALL TO PUBLIC USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',tab);
   END IF;
  END LOOP;
 END IF;
END $$;
-- Reuse the currently configured default writing route, not a new provider.
INSERT INTO tank.policy(task_type,tenant_id,rung,model,priority,batch_eligible,cache_scope,max_tokens_out,eval_threshold,min_shadow_samples,auto_promote)
SELECT 'trigger.bot.answer',NULL,rung,model,priority,false,'none',max_tokens_out,eval_threshold,min_shadow_samples,false
FROM tank.policy WHERE task_type='trigger.write' AND tenant_id IS NULL
ON CONFLICT (task_type, (COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid))) DO NOTHING;
ALTER TABLE tank.trigger_bot_passages ADD COLUMN IF NOT EXISTS checked_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE tank.trigger_bot_sources ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

-- Website-bound keys are revoked with their source; legacy keys remain nullable.
ALTER TABLE tank.trigger_bot_embed_keys ADD COLUMN IF NOT EXISTS source_id uuid;
-- Trigger M1: private posts, channel connections and confirmed replies.
CREATE TABLE IF NOT EXISTS tank.trigger_posts (
 tenant_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(),
 write_id uuid, user_id text NOT NULL, operation_id uuid NOT NULL,
 channel text NOT NULL CHECK(channel IN ('gbp','facebook','instagram','linkedin','x')),
 format text NOT NULL, text text NOT NULL, report jsonb NOT NULL, voice_revision jsonb,
 revision integer NOT NULL DEFAULT 1, approved_revision integer,
 state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','approved','scheduled','published','failed','cancelled')),
 planned_at timestamptz, time_zone text, location text, connection_generation uuid,
 delivery text NOT NULL DEFAULT 'pending' CHECK(delivery IN ('pending','inflight','uncertain','done')),
 attempt_id uuid, attempts integer NOT NULL DEFAULT 0, retryable boolean NOT NULL DEFAULT false,
 retry_at timestamptz, lease_until timestamptz, platform_error text,
 platform_id text, platform_url text, published_at timestamptz, ledger_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,operation_id),
 FOREIGN KEY(tenant_id,write_id) REFERENCES tank.trigger_writes(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS trigger_posts_due ON tank.trigger_posts(planned_at,retry_at)
 WHERE state IN ('scheduled','failed');
ALTER TABLE tank.trigger_posts ADD COLUMN IF NOT EXISTS claimed_at timestamptz;
CREATE TABLE IF NOT EXISTS tank.trigger_post_events (
 tenant_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(), post_id uuid NOT NULL,
 revision integer NOT NULL, state text NOT NULL, operation_id uuid NOT NULL,
 ledger_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,operation_id),
 FOREIGN KEY(tenant_id,post_id) REFERENCES tank.trigger_posts(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_gbp_connections (
 tenant_id uuid PRIMARY KEY, generation uuid NOT NULL, sealed text,
 status text NOT NULL CHECK(status IN ('connected','disconnecting','disconnected')),
 location text, location_title text, reviews_checked_at timestamptz,
 user_id text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.trigger_gbp_states (
 tenant_id uuid NOT NULL, state_hash text NOT NULL, user_id text NOT NULL,
 generation uuid NOT NULL, sealed text NOT NULL, expires_at timestamptz NOT NULL,
 consumed_at timestamptz, PRIMARY KEY(tenant_id,state_hash)
);
CREATE TABLE IF NOT EXISTS tank.trigger_gbp_reviews (
 tenant_id uuid NOT NULL, location text NOT NULL, review_id text NOT NULL,
 rating text NOT NULL, text text NOT NULL, updated_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,location,review_id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_gbp_replies (
 tenant_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(),
 location text NOT NULL, review_id text NOT NULL, generation uuid NOT NULL, write_id uuid NOT NULL,
 text text NOT NULL, report jsonb NOT NULL, revision integer NOT NULL DEFAULT 1,
 state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','sending','sent','failed')),
 sent_hash text, platform_error text, sent_at timestamptz, ledger_id uuid,
 PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,location,review_id),
 FOREIGN KEY(tenant_id,location,review_id) REFERENCES tank.trigger_gbp_reviews(tenant_id,location,review_id),
 FOREIGN KEY(tenant_id,write_id) REFERENCES tank.trigger_writes(tenant_id,id)
);
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_posts ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_posts FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_posts'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_posts FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_posts'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_posts AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_post_events ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_post_events FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_post_events'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_post_events FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_post_events'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_post_events AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_gbp_connections ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_gbp_connections FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_connections'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_gbp_connections FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_connections'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_gbp_connections AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_gbp_states ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_gbp_states FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_states'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_gbp_states FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_states'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_gbp_states AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_gbp_reviews ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_gbp_reviews FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_reviews'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_gbp_reviews FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_reviews'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_gbp_reviews AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_gbp_replies ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_gbp_replies FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_replies'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_gbp_replies FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_gbp_replies'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_gbp_replies AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;

-- A result can release only the currently held swap, under the same row lock.
CREATE OR REPLACE FUNCTION tank.finish_policy_swap(p_task text,p_tenant uuid,p_swap uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE serving tank.policy%ROWTYPE;
        requested tank.policy%ROWTYPE;
        failed boolean;
        pending boolean;
        previous_flag text;
BEGIN
    SELECT * INTO serving FROM tank.policy WHERE task_type=p_task
      AND tenant_id IS NOT DISTINCT FROM p_tenant FOR UPDATE;
    IF p_swap IS NULL OR serving.held_swap_id IS DISTINCT FROM p_swap THEN
        RETURN;
    END IF;
    SELECT bool_or(error IS NOT NULL),bool_or(completed_at IS NULL)
      INTO failed,pending FROM tank.skill_revalidations
      WHERE tenant_id='00000000-0000-0000-0000-000000000001'::uuid AND swap_id=p_swap;
    IF NOT coalesce(failed,false) AND coalesce(pending,true) THEN
        RETURN;
    END IF;
    requested := jsonb_populate_record(NULL::tank.policy,serving.held_policy);
    IF coalesce(failed,false) THEN
        UPDATE tank.policy SET held_policy=NULL,held_swap_id=NULL,held_at=NULL
          WHERE task_type=p_task AND tenant_id IS NOT DISTINCT FROM p_tenant;
        INSERT INTO tank.policy_log(task_type,action,from_model,to_model,by_user,detail)
        VALUES (p_task,'hold_failed',serving.model,requested.model,'tank',
                jsonb_build_object('swap_id',p_swap,'tenant_id',p_tenant));
        RETURN;
    END IF;
    previous_flag := current_setting('tank.policy_swap_apply',true);
    PERFORM set_config('tank.policy_swap_apply','on',true);
    UPDATE tank.policy SET
            rung=requested.rung,
            model=requested.model,
            priority=requested.priority,
            batch_eligible=requested.batch_eligible,
            cache_scope=requested.cache_scope,
            max_tokens_out=requested.max_tokens_out,
            system_prompt=requested.system_prompt,
            eval_threshold=requested.eval_threshold,
            updated_at=requested.updated_at,
            candidate_model=requested.candidate_model,
            shadow_rate=requested.shadow_rate,
            previous_model=serving.model,
            min_shadow_samples=requested.min_shadow_samples,
            max_reject_rate=requested.max_reject_rate,
            auto_promote=requested.auto_promote,
            field_floors=requested.field_floors,
            semantic_cache=requested.semantic_cache,
            promoted_at=requested.promoted_at,
            number_tolerance=requested.number_tolerance,
            baseline_model=requested.baseline_model,
            moa_enabled=requested.moa_enabled,
            moa_config=requested.moa_config,
            escalation_model=requested.escalation_model,
            confidence_floor=requested.confidence_floor,
            acceptance_window_s=requested.acceptance_window_s,
            matter_matching=requested.matter_matching,
            max_cost_usd=requested.max_cost_usd,
            routellm_weak_model=requested.routellm_weak_model,
            routellm_threshold=requested.routellm_threshold,
            fallbacks=requested.fallbacks,
            held_policy=NULL,held_swap_id=NULL,held_at=NULL
      WHERE task_type=p_task AND tenant_id IS NOT DISTINCT FROM p_tenant;
    PERFORM set_config('tank.policy_swap_apply',coalesce(previous_flag,''),true);
    INSERT INTO tank.policy_log(task_type,action,from_model,to_model,by_user,detail)
    VALUES (p_task,'released',serving.model,requested.model,'tank',
            jsonb_build_object('swap_id',p_swap,'tenant_id',p_tenant));
END;
$$;

-- Tisan T3: client requests and immutable revision/sign-off events, never publication.
CREATE TABLE IF NOT EXISTS tank.tisan_requests (
    tenant_id uuid NOT NULL,
    request_id uuid NOT NULL,
    actor text NOT NULL,
    data jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('requested','changes_requested','accepted')),
    build integer CHECK (build > 0),
    "commit" text CHECK ("commit" ~ '^[0-9a-f]{40}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,request_id)
);
CREATE TABLE IF NOT EXISTS tank.tisan_request_events (
    tenant_id uuid NOT NULL,
    event_id uuid NOT NULL,
    request_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('requested','changes_requested','accepted')),
    actor text NOT NULL,
    build integer CHECK (build > 0),
    "commit" text CHECK ("commit" ~ '^[0-9a-f]{40}$'),
    text text CHECK (length(text) BETWEEN 1 AND 4000),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,event_id),
    FOREIGN KEY (tenant_id,request_id) REFERENCES tank.tisan_requests(tenant_id,request_id)
);
CREATE INDEX IF NOT EXISTS tisan_requests_tenant ON tank.tisan_requests(tenant_id,created_at DESC);
CREATE INDEX IF NOT EXISTS tisan_request_events_request ON tank.tisan_request_events(tenant_id,request_id,created_at);
-- Follow personal_tokens exactly: join an existing rollout, never enable legacy RLS.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        ALTER TABLE tank.tisan_requests ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.tisan_requests FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.tisan_requests'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.tisan_requests FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.tisan_requests'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.tisan_requests AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        ALTER TABLE tank.tisan_request_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.tisan_request_events FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.tisan_request_events'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.tisan_request_events FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.tisan_request_events'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.tisan_request_events AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END
$$;

-- First-party plugin installs: tenant records only, never credentials.
CREATE TABLE IF NOT EXISTS tank.plugin_installs (
    tenant_id uuid NOT NULL,
    plugin_id text NOT NULL,
    version text NOT NULL,
    installed_by text NOT NULL,
    installed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,plugin_id,version)
);
-- Follow an existing RLS rollout; do not turn it on for a legacy installation.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        ALTER TABLE tank.plugin_installs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.plugin_installs FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.plugin_installs'::regclass AND polname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.plugin_installs FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.plugin_installs'::regclass AND polname='tenant_boundary') THEN
            CREATE POLICY tenant_boundary ON tank.plugin_installs AS RESTRICTIVE FOR ALL TO PUBLIC
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END
$$;
-- Trigger M2: tenant-private Facebook connection and one-use consent states.
CREATE TABLE IF NOT EXISTS tank.trigger_meta_connections (
 tenant_id uuid PRIMARY KEY, generation uuid NOT NULL, sealed text,
 status text NOT NULL CHECK(status IN ('connected','disconnecting','disconnected')),
 page_id text, page_name text, user_id text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.trigger_meta_states (
 tenant_id uuid NOT NULL, state_hash text NOT NULL, user_id text NOT NULL,
 generation uuid NOT NULL, sealed text NOT NULL, expires_at timestamptz NOT NULL,
 consumed_at timestamptz, PRIMARY KEY(tenant_id,state_hash)
);
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_meta_connections ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_meta_connections FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_meta_connections'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_meta_connections FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_meta_connections'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_meta_connections AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_meta_states ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_meta_states FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_meta_states'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_meta_states FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_meta_states'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_meta_states AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;

-- Trigger B3: WhatsApp conversation identities, durable inbound claims and free-tier budget.
-- Preserve the legacy web-only channel check; identify the delivery transport additively.
ALTER TABLE tank.trigger_bot_conversations ADD COLUMN IF NOT EXISTS transport
 text NOT NULL DEFAULT 'web' CHECK(transport IN ('web','whatsapp'));
ALTER TABLE tank.trigger_bot_conversations ADD COLUMN IF NOT EXISTS whatsapp_sealed text;
CREATE TABLE IF NOT EXISTS tank.trigger_whatsapp_months (
 tenant_id uuid NOT NULL, phone_number_id text NOT NULL, month date NOT NULL,
 timezone text NOT NULL, count integer NOT NULL CHECK(count BETWEEN 1 AND 1000),
 PRIMARY KEY(tenant_id,phone_number_id,month)
);
-- Rebinding a business phone to another tenant cannot replenish this month's budget.
CREATE UNIQUE INDEX IF NOT EXISTS trigger_whatsapp_phone_month
 ON tank.trigger_whatsapp_months(phone_number_id,month);
CREATE TABLE IF NOT EXISTS tank.trigger_whatsapp_events (
 tenant_id uuid NOT NULL, event_hash text NOT NULL, conversation_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'claimed' CHECK(state IN ('claimed','sending','sent','uncertain','handed_over')),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,event_hash),
 FOREIGN KEY(tenant_id,conversation_id) REFERENCES tank.trigger_bot_conversations(tenant_id,id) ON DELETE CASCADE
);
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_whatsapp_months ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_whatsapp_months FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_whatsapp_months'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_whatsapp_months FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_whatsapp_months'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_whatsapp_months AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.trigger_whatsapp_events ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.trigger_whatsapp_events FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_whatsapp_events'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.trigger_whatsapp_events FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.trigger_whatsapp_events'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.trigger_whatsapp_events AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;

-- I6: own-repository scan provenance and per-app critical observations.

CREATE TABLE IF NOT EXISTS tank.radix_deploy_scans (
 tenant_id uuid NOT NULL, scan_id uuid NOT NULL, commit_sha text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), processed boolean NOT NULL DEFAULT false,
 complete boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,scan_id), CHECK(commit_sha ~ '^[a-f0-9]{40}$')
);
CREATE TABLE IF NOT EXISTS tank.radix_deploy_findings (
 tenant_id uuid NOT NULL, id uuid NOT NULL, scan_id uuid NOT NULL, app text NOT NULL,
 commit_sha text NOT NULL, finding jsonb NOT NULL, resolved_at timestamptz,
 override_user text, override_at timestamptz, ledger_id uuid,
 PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,scan_id) REFERENCES tank.radix_deploy_scans(tenant_id,scan_id)
);
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.radix_deploy_scans ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.radix_deploy_scans FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.radix_deploy_scans'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.radix_deploy_scans FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.radix_deploy_scans'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.radix_deploy_scans AS RESTRICTIVE FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.radix_deploy_findings ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.radix_deploy_findings FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.radix_deploy_findings'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.radix_deploy_findings FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.radix_deploy_findings'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.radix_deploy_findings AS RESTRICTIVE FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;

-- Trigger B2: existing tenant RLS and retention cover the console/widget fields.
ALTER TABLE tank.trigger_bot_config ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}';
ALTER TABLE tank.trigger_bot_messages ADD COLUMN IF NOT EXISTS person text;

-- BEGIN trigger (new blocks for trigger go here)
-- END trigger
-- BEGIN base (new blocks for base go here)
-- END base
-- BEGIN tiger (new blocks for tiger go here)
CREATE TABLE IF NOT EXISTS tank.tiger_savings (
 tenant_id uuid NOT NULL, id uuid NOT NULL, action text NOT NULL,
 component boolean NOT NULL DEFAULT false, receipt jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS tiger_savings_history ON tank.tiger_savings(tenant_id,created_at DESC) WHERE NOT component;
CREATE TABLE IF NOT EXISTS tank.tiger_extract_cache (
 tenant_id uuid NOT NULL, key text NOT NULL, output jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,key)
);
DO $$
DECLARE relation text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='tank' AND c.relname='ledger' AND c.relrowsecurity) THEN RETURN; END IF;
 FOREACH relation IN ARRAY ARRAY['tiger_savings','tiger_extract_cache'] LOOP
  EXECUTE format('ALTER TABLE tank.%I ENABLE ROW LEVEL SECURITY', relation);
  EXECUTE format('ALTER TABLE tank.%I FORCE ROW LEVEL SECURITY', relation);
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='tank' AND tablename=relation AND policyname='tenant_access') THEN
   EXECUTE format('CREATE POLICY tenant_access ON tank.%I FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',relation);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='tank' AND tablename=relation AND policyname='tenant_boundary') THEN
   EXECUTE format('CREATE POLICY tenant_boundary ON tank.%I AS RESTRICTIVE FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',relation);
  END IF;
 END LOOP;
END $$;
-- END tiger
-- BEGIN market_lab (new blocks for market_lab go here)
-- END market_lab
-- BEGIN rig (new blocks for rig go here)
-- Rig store-room lifecycle registry. No store catalogue or contact data here.
CREATE TABLE IF NOT EXISTS tank.rig_rooms (
    tenant_id uuid PRIMARY KEY,
    coolify_uuid text,
    state text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- END rig
-- BEGIN tisan (new blocks for tisan go here)
-- END tisan
-- BEGIN radix (new blocks for radix go here)
-- END radix
-- BEGIN zenas (new blocks for zenas go here)
-- END zenas
-- BEGIN mcp (new blocks for mcp go here)
-- END mcp
-- BEGIN skala (new blocks for skala go here)
-- END skala
-- BEGIN worker (new blocks for worker go here)
-- END worker
-- BEGIN platform (new blocks for platform go here)
CREATE TABLE IF NOT EXISTS tank.engine_state (
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    model text NOT NULL, revision text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,region,task_type)
);
CREATE TABLE IF NOT EXISTS tank.engine_candidates (
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    model text NOT NULL, checkpoint text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,region,task_type,model)
);
CREATE TABLE IF NOT EXISTS tank.engine_proofs (
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    model text NOT NULL, revision text NOT NULL, input_hash text NOT NULL,
    ledger_id uuid NOT NULL, candidate_ledger_id uuid NOT NULL, agreement numeric NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,region,task_type,model,revision,input_hash)
);
CREATE TABLE IF NOT EXISTS tank.engine_graduations (
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    model text NOT NULL, revision text NOT NULL, previous_model text NOT NULL,
    samples integer NOT NULL, agreement numeric NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,region,task_type,model,revision)
);
ALTER TABLE tank.engine_proofs ADD COLUMN IF NOT EXISTS quality jsonb NOT NULL DEFAULT '{}';
ALTER TABLE tank.shadow ADD COLUMN IF NOT EXISTS quality jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS tank.engine_judges (
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    model text NOT NULL, revision text NOT NULL, passed boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,region,task_type,model,revision)
);
CREATE TABLE IF NOT EXISTS tank.engine_evals (
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    model text NOT NULL, revision text NOT NULL, agreement numeric NOT NULL,
    samples integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,region,task_type,model,revision)
);
CREATE TABLE IF NOT EXISTS tank.engine_receipts (
    tenant_id uuid NOT NULL, region text NOT NULL, task_type text NOT NULL,
    ledger_id uuid NOT NULL, detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,ledger_id)
);
ALTER TABLE tank.training_pairs ADD COLUMN IF NOT EXISTS teacher_model text;

-- Home is assigned at onboarding; changing it requires a separate data migration.
CREATE TABLE IF NOT EXISTS tank.tenants (
    tenant_id uuid PRIMARY KEY,
    home_region text NOT NULL CHECK (home_region IN ('us-east','za')),
    onboarded_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS feedback_kind text
    CHECK (feedback_kind IN ('useful','not_useful','wrong'));
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS revalidation_id uuid;
CREATE TABLE IF NOT EXISTS tank.roster_observations (
    tenant_id uuid NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east','za')),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,region)
);
CREATE TABLE IF NOT EXISTS tank.platform_releases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east','za')),
    sha text NOT NULL CHECK (sha ~ '^[0-9a-f]{40}$'),
    observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS platform_releases_region
    ON tank.platform_releases(tenant_id,region,observed_at DESC);
CREATE TABLE IF NOT EXISTS tank.founder_morning_reports (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east','za')),
    report_day date NOT NULL,
    report jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('sending','sent','uncertain')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(tenant_id,region,report_day)
);
-- Join an existing tenant-RLS rollout; never enable it on legacy tables.
DO $$
DECLARE relation text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
        FOREACH relation IN ARRAY ARRAY['tenants','roster_observations','platform_releases','founder_morning_reports',
            'engine_state','engine_candidates','engine_proofs','engine_graduations','engine_evals','engine_receipts','engine_judges']
        LOOP
            EXECUTE format('ALTER TABLE tank.%I ENABLE ROW LEVEL SECURITY',relation);
            EXECUTE format('ALTER TABLE tank.%I FORCE ROW LEVEL SECURITY',relation);
            IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=format('tank.%I',relation)::regclass AND polname='tenant_access') THEN
                EXECUTE format('CREATE POLICY tenant_access ON tank.%I FOR ALL TO PUBLIC
                    USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)
                    WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',relation);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=format('tank.%I',relation)::regclass AND polname='tenant_boundary') THEN
                EXECUTE format('CREATE POLICY tenant_boundary ON tank.%I AS RESTRICTIVE FOR ALL TO PUBLIC
                    USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)
                    WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',relation);
            END IF;
        END LOOP;
    END IF;
END;
$$;

-- An onboarding assignment must never silently move an existing tenant.
CREATE OR REPLACE FUNCTION tank.tenant_has_data(wanted uuid) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE relation record; has_row boolean;
BEGIN
    FOR relation IN
        SELECT n.nspname,c.relname,a.atttypid='uuid'::regtype AS is_uuid
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE c.relkind IN ('r','p') AND a.attname='tenant_id' AND NOT a.attisdropped
        AND a.atttypid IN ('uuid'::regtype,'text'::regtype,'varchar'::regtype)
        AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
        AND c.oid <> 'tank.tenants'::regclass
    LOOP
        IF relation.is_uuid THEN
            EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I WHERE tenant_id=$1)',
                           relation.nspname,relation.relname) INTO has_row USING wanted;
        ELSE
            EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I WHERE tenant_id=$1)',
                           relation.nspname,relation.relname) INTO has_row USING wanted::text;
        END IF;
        IF has_row THEN RETURN true; END IF;
    END LOOP;
    RETURN false;
END;
$$;

-- Paid ladder steps: tenant preferences default to Ask, independent of master stop.
CREATE TABLE IF NOT EXISTS tank.paid_steps (
    step_id text PRIMARY KEY,
    product text NOT NULL CHECK(product IN ('base','tiger','market_lab','rig','tisan','radix','zenas','trigger','autumbra','model')),
    kind text NOT NULL,
    source text NOT NULL
);
CREATE TABLE IF NOT EXISTS tank.paid_step_settings (
    tenant_id uuid NOT NULL,
    step_id text NOT NULL REFERENCES tank.paid_steps(step_id),
    mode text NOT NULL CHECK(mode IN ('ask','auto')),
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,step_id)
);
CREATE TABLE IF NOT EXISTS tank.paid_step_settings_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    step_id text NOT NULL REFERENCES tank.paid_steps(step_id),
    mode text NOT NULL CHECK(mode IN ('ask','auto')),
    by_user text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tank.paid_step_approvals (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    step_id text NOT NULL REFERENCES tank.paid_steps(step_id),
    request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
    state text NOT NULL CHECK(state IN ('pending','approved','denied')),
    authorization_mode text NOT NULL CHECK(authorization_mode IN ('pending','approved','automatic','denied')),
    user_id text,
    thread_id uuid,
    decided_by text,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    UNIQUE(tenant_id,run_id,step_id),
    CHECK(state <> 'denied' OR (decided_by IS NOT NULL AND length(btrim(reason)) > 0))
);
CREATE INDEX IF NOT EXISTS paid_step_pending ON tank.paid_step_approvals(tenant_id,created_at) WHERE state='pending';
CREATE TABLE IF NOT EXISTS tank.paid_step_calls (
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    call_key text NOT NULL CHECK(call_key ~ '^[a-f0-9]{64}$'),
    request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
    state text NOT NULL CHECK(state IN ('executing','completed')),
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,run_id,call_key)
);
DO $$
DECLARE relation text;
BEGIN
 -- Match the existing rollout: additive tables inherit RLS when the platform
 -- boundary is enabled; this feature does not enable the opt-in rollout itself.
 IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='tank' AND c.relname='ledger' AND c.relrowsecurity) THEN
  RETURN;
 END IF;
 FOREACH relation IN ARRAY ARRAY['paid_step_settings','paid_step_settings_log','paid_step_approvals','paid_step_calls'] LOOP
  EXECUTE format('ALTER TABLE tank.%I ENABLE ROW LEVEL SECURITY', relation);
  EXECUTE format('ALTER TABLE tank.%I FORCE ROW LEVEL SECURITY', relation);
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='tank' AND tablename=relation AND policyname='tenant_access') THEN
   EXECUTE format('CREATE POLICY tenant_access ON tank.%I FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',relation);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='tank' AND tablename=relation AND policyname='tenant_boundary') THEN
   EXECUTE format('CREATE POLICY tenant_boundary ON tank.%I AS RESTRICTIVE FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)',relation);
  END IF;
 END LOOP;
END $$;
ALTER TABLE tank.paid_step_approvals ADD COLUMN IF NOT EXISTS continuation jsonb;
ALTER TABLE tank.paid_step_approvals ADD COLUMN IF NOT EXISTS resume_status text CHECK(resume_status IN ('waiting','running','completed','failed'));
ALTER TABLE tank.paid_step_approvals ADD COLUMN IF NOT EXISTS resume_started_at timestamptz;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS paid_step_id text;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS paid_step_approval_id uuid;
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS paid_step_mode text CHECK(paid_step_mode IN ('approved','automatic'));
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS paid_steps jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Keep the originating private turn separate from its administrator decision card.
ALTER TABLE tank.paid_step_approvals ADD COLUMN IF NOT EXISTS delivery_thread_id uuid;

-- Human-readable route metadata accompanies stable switch identities.
ALTER TABLE tank.paid_steps ADD COLUMN IF NOT EXISTS task_type text;
ALTER TABLE tank.paid_steps ADD COLUMN IF NOT EXISTS model text;
-- END PAID LADDER STEPS
-- DECISION C1 is default-off; enabling it also requires a positive per-task TTL.
ALTER TABLE tank.policy ADD COLUMN IF NOT EXISTS cache_ttl_s integer NOT NULL DEFAULT 0 CHECK (cache_ttl_s BETWEEN 0 AND 2592000);
-- END platform

-- BEGIN mobile
CREATE TABLE IF NOT EXISTS tank.mobile_codes (
    tenant_id uuid NOT NULL,
    code_hash text NOT NULL,
    challenge text NOT NULL,
    identity jsonb NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
    PRIMARY KEY (tenant_id, code_hash)
);
CREATE TABLE IF NOT EXISTS tank.mobile_sessions (
    tenant_id uuid NOT NULL,
    token_hash text NOT NULL,
    user_id text NOT NULL,
    identity jsonb NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '7 days',
    revoked boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, token_hash)
);
CREATE TABLE IF NOT EXISTS tank.mobile_devices (
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    token_hash text NOT NULL UNIQUE,
    token text NOT NULL,
    platform text NOT NULL CHECK (platform IN ('ios','android')),
    session_hash text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, token_hash),
    FOREIGN KEY (tenant_id, session_hash) REFERENCES tank.mobile_sessions(tenant_id, token_hash)
);
CREATE INDEX IF NOT EXISTS mobile_devices_user ON tank.mobile_devices(tenant_id,user_id);
CREATE TABLE IF NOT EXISTS tank.mobile_drafts (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    share_id text NOT NULL,
    payload jsonb NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours',
    PRIMARY KEY (tenant_id,id),
    UNIQUE (tenant_id,user_id,share_id)
);
DO $$
DECLARE mobile_table text;
BEGIN
 -- Follow an existing RLS rollout; do not turn it on for a legacy installation.
 IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  RETURN;
 END IF;
 FOREACH mobile_table IN ARRAY ARRAY['mobile_codes','mobile_sessions','mobile_devices','mobile_drafts'] LOOP
  EXECUTE format('ALTER TABLE tank.%I ENABLE ROW LEVEL SECURITY', mobile_table);
  EXECUTE format('ALTER TABLE tank.%I FORCE ROW LEVEL SECURITY', mobile_table);
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=to_regclass('tank.' || mobile_table) AND polname='tenant_access') THEN
   EXECUTE format('CREATE POLICY tenant_access ON tank.%I FOR ALL TO PUBLIC USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)', mobile_table);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=to_regclass('tank.' || mobile_table) AND polname='tenant_boundary') THEN
   EXECUTE format('CREATE POLICY tenant_boundary ON tank.%I AS RESTRICTIVE FOR ALL TO PUBLIC USING (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=NULLIF(current_setting(''app.tenant_id'',true),'''')::uuid)', mobile_table);
  END IF;
 END LOOP;
END $$;
-- END mobile
-- BEGIN trigger_signin
ALTER TABLE tank.command_threads ADD COLUMN IF NOT EXISTS trigger_signin_issuer text NOT NULL DEFAULT '';
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS trigger_signin boolean NOT NULL DEFAULT false;
ALTER TABLE tank.device_authorizations ADD COLUMN IF NOT EXISTS trigger_signin boolean NOT NULL DEFAULT false;
ALTER TABLE tank.oauth_codes ADD COLUMN IF NOT EXISTS trigger_signin boolean NOT NULL DEFAULT false;
ALTER TABLE tank.personal_tokens ADD COLUMN IF NOT EXISTS trigger_signin_issuer text;

CREATE TABLE IF NOT EXISTS tank.trigger_signin_config (
    region text PRIMARY KEY CHECK (region = 'us-east'),
    issuer text NOT NULL UNIQUE,
    client_id text NOT NULL,
    workspace_client_id text NOT NULL,
    project_id text NOT NULL
);
CREATE TABLE IF NOT EXISTS tank.trigger_signin_organisations (
    region text NOT NULL REFERENCES tank.trigger_signin_config(region),
    org_id text NOT NULL,
    tenant_id uuid NOT NULL,
    PRIMARY KEY (region,org_id),
    UNIQUE (region,tenant_id)
);
CREATE TABLE IF NOT EXISTS tank.trigger_signin_founders (
    region text PRIMARY KEY REFERENCES tank.trigger_signin_config(region),
    subject text NOT NULL
);
-- END trigger_signin

-- BEGIN opli
CREATE TABLE IF NOT EXISTS tank.opli_mcp_policy (
    tenant_id uuid NOT NULL,
    server_id text NOT NULL,
    blocked boolean NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id,server_id)
);
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.opli_mcp_policy ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.opli_mcp_policy FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.opli_mcp_policy'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.opli_mcp_policy FOR ALL TO PUBLIC
     USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
     WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.opli_mcp_policy'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.opli_mcp_policy AS RESTRICTIVE FOR ALL TO PUBLIC
     USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
     WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END;
$$;
-- END opli

-- BEGIN workspace
-- Workspace code-only comparisons keep hypothetical tokens separate from usage.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS comparison_tokens_in integer CHECK (comparison_tokens_in >= 0);
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS comparison_tokens_out integer CHECK (comparison_tokens_out >= 0);
CREATE OR REPLACE FUNCTION tank.price_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE chosen_family text; standard_name text; selected_name text; top_name text;
        setting tank.baseline_settings%ROWTYPE; priced numeric; selected_cost numeric; top_cost numeric;
BEGIN
    IF TG_OP='UPDATE' THEN
      INSERT INTO tank.comparison_history(ledger_id,tenant_id,region,valid_from,valid_until,comparison,reason)
      VALUES(OLD.id,OLD.tenant_id,OLD.region,coalesce(OLD.baseline_priced_at,OLD.ts),now(),
        jsonb_build_object('standard_model',OLD.standard_model,'standard_cost_usd',OLD.standard_cost_usd,
          'top_model',OLD.top_model,'top_cost_usd',OLD.top_cost_usd,'baseline_model',OLD.baseline_model,
          'baseline_cost_usd',OLD.baseline_cost_usd,'baseline_status',OLD.baseline_status,
          'baseline_priced_at',OLD.baseline_priced_at,'comparison_revision',OLD.comparison_revision),
        CASE WHEN OLD.comparison_revision=0 THEN 'default migration'
          WHEN OLD.comparison_revision=NEW.comparison_revision THEN 'price refresh' ELSE 'task override' END)
      ON CONFLICT DO NOTHING;
    END IF;
    -- A plain MVCC read: receipts never lock a shared task setting. The durable
    -- receipt-check queue catches rows committed after a repricing batch finishes.
    SELECT * INTO setting FROM tank.baseline_settings WHERE task_type=NEW.task_type;
    NEW.comparison_revision := coalesce(setting.revision,1);
    -- Invalidate all comparisons when actual costs are unknown or are vendor fees.
    IF to_jsonb(NEW)->>'research_cost_status'='unknown' OR NEW.model LIKE 'vendor:%' OR to_jsonb(NEW)->>'vendor' IS NOT NULL THEN
      NEW.baseline_model:=NULL; NEW.baseline_cost_usd:=NULL;
      NEW.standard_model:=NULL; NEW.standard_cost_usd:=NULL;
      NEW.top_model:=NULL; NEW.top_cost_usd:=NULL; NEW.baseline_status:='unpriced';
      NEW.baseline_priced_at:=now(); RETURN NEW;
    END IF;
    SELECT family INTO chosen_family FROM tank.proof_prices WHERE model=NEW.model;
    IF chosen_family IS NULL AND (NEW.rung=3 OR NEW.cache_hit) THEN
      SELECT a.family INTO chosen_family FROM tank.policy p JOIN tank.proof_prices a ON a.model=p.model
        WHERE p.task_type=NEW.task_type AND (p.tenant_id=NEW.tenant_id OR p.tenant_id IS NULL)
        ORDER BY p.tenant_id NULLS LAST LIMIT 1;
    END IF;
    standard_name:=tank.standard_model_for(chosen_family);
    selected_name:=coalesce(setting.override_model,standard_name);
    standard_name:=selected_name; -- A founder override replaces this task's comparison.
    SELECT model INTO top_name FROM tank.proof_prices WHERE family=chosen_family AND active
      ORDER BY input_usd+output_usd DESC,model LIMIT 1;
    SELECT coalesce(NEW.comparison_tokens_in,NEW.tokens_in)*input_usd+coalesce(NEW.comparison_tokens_out,NEW.tokens_out)*output_usd INTO priced
      FROM tank.proof_prices WHERE model=standard_name AND family=chosen_family AND active;
    SELECT coalesce(NEW.comparison_tokens_in,NEW.tokens_in)*input_usd+coalesce(NEW.comparison_tokens_out,NEW.tokens_out)*output_usd INTO selected_cost
      FROM tank.proof_prices WHERE model=selected_name AND family=chosen_family AND active;
    SELECT coalesce(NEW.comparison_tokens_in,NEW.tokens_in)*input_usd+coalesce(NEW.comparison_tokens_out,NEW.tokens_out)*output_usd INTO top_cost
      FROM tank.proof_prices WHERE model=top_name AND family=chosen_family AND active;
    IF coalesce(NEW.comparison_tokens_in,NEW.tokens_in)=0 AND coalesce(NEW.comparison_tokens_out,NEW.tokens_out)=0 AND NEW.cost_usd=0 AND (NEW.rung=3 OR NEW.cache_hit) THEN
      priced:=0; selected_cost:=0; top_cost:=0;
      standard_name:=coalesce(standard_name,'No AI call');
      selected_name:=coalesce(selected_name,'No AI call'); top_name:=coalesce(top_name,'No AI call');
    END IF;
    NEW.standard_model:=standard_name; NEW.standard_cost_usd:=priced;
    NEW.top_model:=top_name; NEW.top_cost_usd:=top_cost;
    NEW.baseline_model:=selected_name; NEW.baseline_cost_usd:=selected_cost;
    NEW.baseline_priced_at:=now();
    IF priced IS NULL THEN
      NEW.baseline_status := 'unpriced';
    ELSIF NEW.comparison_tokens_in IS NOT NULL OR NEW.comparison_tokens_out IS NOT NULL THEN
      NEW.baseline_status := 'estimated';
    ELSIF TG_OP='INSERT' THEN
      NEW.baseline_status := 'measured';
    ELSIF OLD.standard_cost_usd IS NOT NULL OR OLD.baseline_cost_usd IS NOT NULL THEN
      NEW.baseline_status := 're-priced';
    ELSE
      NEW.baseline_status := 'backfilled';
    END IF;
    RETURN NEW;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='workspace_price_comparison' AND tgrelid='tank.ledger'::regclass) THEN
      CREATE TRIGGER workspace_price_comparison BEFORE UPDATE OF comparison_tokens_in,comparison_tokens_out ON tank.ledger
        FOR EACH ROW EXECUTE FUNCTION tank.price_receipt();
    END IF;
END $$;

-- One completion-memory attempt per real task, independent of deletable memory.
CREATE TABLE IF NOT EXISTS tank.workspace_task_memory (
    task_id uuid PRIMARY KEY REFERENCES tank.tasks(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL,
    user_id text NOT NULL CHECK(length(btrim(user_id)) > 0),
    state text NOT NULL CHECK(state IN ('pending','done','unavailable','denied')),
    result jsonb NOT NULL DEFAULT '{}'::jsonb
);
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='tank' AND c.relname='command_threads' AND c.relrowsecurity) THEN
        ALTER TABLE tank.workspace_task_memory ENABLE ROW LEVEL SECURITY;
        ALTER TABLE tank.workspace_task_memory FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='tank' AND tablename='workspace_task_memory' AND policyname='tenant_access') THEN
            CREATE POLICY tenant_access ON tank.workspace_task_memory FOR ALL TO PUBLIC
            USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
            WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
        END IF;
    END IF;
END $$;

-- END workspace

-- BEGIN tervor
CREATE TABLE IF NOT EXISTS tank.tervor_models (
    tenant_id uuid NOT NULL,
    id text NOT NULL,
    product text NOT NULL,
    version text NOT NULL,
    manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,id),
    UNIQUE (tenant_id,product,version)
);
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.tervor_models ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.tervor_models FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.tervor_models'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.tervor_models FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='tank.tervor_models'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.tervor_models AS RESTRICTIVE FOR ALL TO PUBLIC
    USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END;
$$;
-- END tervor

-- BEGIN moot_media
-- Confidential Moot media ownership (Mux ids stay Tank-side; signed playback only).
CREATE TABLE IF NOT EXISTS tank.moot_media (
    upload_id       text PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    user_id         text NOT NULL,
    hearing_id      uuid NOT NULL,
    asset_id        text,
    playback_ids    jsonb NOT NULL DEFAULT '[]'::jsonb,
    passthrough     text NOT NULL DEFAULT '',
    idempotency_key text,
    region          text NOT NULL DEFAULT 'us-east',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS moot_media_tenant_hearing ON tank.moot_media (tenant_id, hearing_id, created_at DESC);
CREATE INDEX IF NOT EXISTS moot_media_asset ON tank.moot_media (asset_id) WHERE asset_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS moot_media_idempotency
    ON tank.moot_media (tenant_id, user_id, hearing_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Durable pre-provider upload intent; uncertain attempts continue to consume quota.
CREATE TABLE IF NOT EXISTS tank.moot_upload_requests (
    request_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    user_id text NOT NULL,
    hearing_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    input_hash text NOT NULL CHECK (input_hash ~ '^[a-f0-9]{64}$'),
    upload_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,user_id,hearing_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS moot_upload_request_budget
    ON tank.moot_upload_requests(tenant_id,user_id,created_at);
-- END moot_media
-- BEGIN autumbra
CREATE SCHEMA IF NOT EXISTS agent_ghost;
-- Counterfactual tokens reuse the shared comparison columns, never billed usage.
ALTER TABLE tank.ledger ADD COLUMN IF NOT EXISTS autumbra_reference_hash text;
-- END autumbra

-- BEGIN GENERATED AGENT MEMORY (hermes/record_schema.py)
CREATE SCHEMA IF NOT EXISTS agent_tank;
CREATE TABLE IF NOT EXISTS agent_tank.memory_records (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    agent text NOT NULL DEFAULT 'tank' CHECK (agent = 'tank'),
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
CREATE INDEX IF NOT EXISTS memory_records_scope ON agent_tank.memory_records (tenant_id, tier, created_at, id);
CREATE INDEX IF NOT EXISTS memory_records_encoder ON agent_tank.memory_records (tenant_id, embed_model);
CREATE TABLE IF NOT EXISTS agent_tank.memory_index_jobs (
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
CREATE INDEX IF NOT EXISTS memory_index_jobs_due ON agent_tank.memory_index_jobs (run_at);
CREATE TABLE IF NOT EXISTS agent_tank.memory_publish_policy (
    tenant_id uuid PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    identifiers jsonb NOT NULL DEFAULT '{}',
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_tank.memory_publish_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    actor text NOT NULL,
    happened_at timestamptz NOT NULL DEFAULT now(),
    source_row uuid NOT NULL,
    result text NOT NULL CHECK (result IN ('disabled','missing','refused','published','already_published')),
    published_row uuid
);
-- END GENERATED AGENT MEMORY
