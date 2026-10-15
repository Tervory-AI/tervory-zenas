-- Zenas box. Additive and idempotent. Applied by the agent on start.
-- Document bodies keep placeholders. The identifier itself is only in pii.ciphertext.
-- The knowledge pool has its own schema and is not created here.

CREATE SCHEMA IF NOT EXISTS agent_zenas;

-- One row per tenant. home_region is the source of truth for where that tenant's
-- rows live. A home_region on a request is only a cross-check against this column.
-- No default: an insert must name za or us-east.
CREATE TABLE IF NOT EXISTS agent_zenas.tenants (
    tenant_id    uuid PRIMARY KEY,
    home_region  text NOT NULL,
    CONSTRAINT tenants_home_region_chk CHECK (home_region IN ('za', 'us-east'))
);

CREATE TABLE IF NOT EXISTS agent_zenas.matters (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL,
    jurisdiction  text NOT NULL,
    area          text NOT NULL,
    title         text NOT NULL,
    parties       jsonb NOT NULL DEFAULT '[]'::jsonb,
    status        text NOT NULL DEFAULT 'open',
    created_by    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT matters_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za', 'both')),
    CONSTRAINT matters_status_chk CHECK (status IN ('open', 'closed'))
);

CREATE INDEX IF NOT EXISTS matters_tenant_idx
    ON agent_zenas.matters (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_zenas.documents (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    matter_id   uuid NOT NULL,
    tenant_id   uuid NOT NULL,
    kind        text NOT NULL,
    version     integer NOT NULL,
    body        text NOT NULL,
    sources     jsonb NOT NULL DEFAULT '[]'::jsonb,
    state       text NOT NULL DEFAULT 'draft',
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT documents_state_chk CHECK (state IN ('draft', 'in_review', 'released', 'rejected'))
);

CREATE INDEX IF NOT EXISTS documents_tenant_matter_idx
    ON agent_zenas.documents (tenant_id, matter_id, kind, version);

CREATE TABLE IF NOT EXISTS agent_zenas.reviews (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL,
    tenant_id     uuid NOT NULL,
    reviewer      text NOT NULL,
    jurisdiction  text NOT NULL,
    admission_no  text NOT NULL,
    decision      text NOT NULL,
    changes       text NOT NULL DEFAULT '',
    ts            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reviews_tenant_document_idx
    ON agent_zenas.reviews (tenant_id, document_id, ts DESC);

CREATE TABLE IF NOT EXISTS agent_zenas.consents (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL,
    user_id          text NOT NULL,
    jurisdiction     text NOT NULL,
    wording_version  text NOT NULL,
    text_hash        text NOT NULL,
    ts               timestamptz NOT NULL DEFAULT now(),
    ip               text NOT NULL
);

CREATE INDEX IF NOT EXISTS consents_tenant_user_idx
    ON agent_zenas.consents (tenant_id, user_id, wording_version, ts DESC);

CREATE TABLE IF NOT EXISTS agent_zenas.pii (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    matter_id   uuid NOT NULL,
    tenant_id   uuid NOT NULL,
    kind        text NOT NULL,
    ciphertext  text NOT NULL
);

CREATE INDEX IF NOT EXISTS pii_tenant_matter_idx
    ON agent_zenas.pii (tenant_id, matter_id);

-- Legal ladder. Rule cards are law, shared by every tenant of this region.
-- A card with a tenant_id is tied to that tenant and is not returned to anyone else.
-- Worked matters, training pairs, and memory notes for a tenant stay in that tenant's home region.
-- Decided cases are public record: tenant_id is null and the text is the judgment as published.

CREATE TABLE IF NOT EXISTS agent_zenas.rule_cards (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region          text NOT NULL,
    tenant_id       uuid,
    jurisdiction    text NOT NULL,
    question        text NOT NULL,
    question_key    text NOT NULL,
    elements        jsonb NOT NULL DEFAULT '[]'::jsonb,
    exceptions      jsonb NOT NULL DEFAULT '[]'::jsonb,
    defences        jsonb NOT NULL DEFAULT '[]'::jsonb,
    remedies        jsonb NOT NULL DEFAULT '[]'::jsonb,
    sources         jsonb NOT NULL DEFAULT '[]'::jsonb,
    as_at           date,
    status          text NOT NULL DEFAULT 'draft',
    approved_by     text,
    admission_no    text,
    model_used      text,
    cost_usd        numeric(12,6) NOT NULL DEFAULT 0,
    decision_table  jsonb NOT NULL DEFAULT '[]'::jsonb,
    edit_count      integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rule_cards_status_chk CHECK (status IN ('draft', 'lawyer_approved', 'stale')),
    CONSTRAINT rule_cards_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za', 'both')),
    CONSTRAINT rule_cards_region_chk CHECK (region IN ('us-east', 'za'))
);

CREATE INDEX IF NOT EXISTS rule_cards_lookup_idx
    ON agent_zenas.rule_cards (region, jurisdiction, question_key, status);

CREATE TABLE IF NOT EXISTS agent_zenas.worked_matters (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region              text NOT NULL,
    tenant_id           uuid,
    source              text NOT NULL,
    jurisdiction        text NOT NULL,
    area                text NOT NULL DEFAULT '',
    rule_card_ids       jsonb NOT NULL DEFAULT '[]'::jsonb,
    fact_pattern        jsonb NOT NULL DEFAULT '{}'::jsonb,
    element_findings    jsonb NOT NULL DEFAULT '[]'::jsonb,
    outcome             text NOT NULL DEFAULT '',
    lawyer_edits        text NOT NULL DEFAULT '',
    as_at               date,
    pool_doc_id         text,
    pool_version_id     text,
    embedding           jsonb NOT NULL DEFAULT '[]'::jsonb,
    released_matter_id  uuid,
    material_type       text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT worked_source_chk CHECK (source IN ('case', 'released')),
    CONSTRAINT worked_region_chk CHECK (region IN ('us-east', 'za')),
    CONSTRAINT worked_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za', 'both')),
    CONSTRAINT worked_material_type_chk CHECK (material_type IS NULL OR material_type IN ('public_record', 'client_work'))
);

CREATE UNIQUE INDEX IF NOT EXISTS worked_case_once_idx
    ON agent_zenas.worked_matters (region, pool_doc_id)
    WHERE source = 'case' AND pool_doc_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS worked_region_tenant_idx
    ON agent_zenas.worked_matters (region, tenant_id);

CREATE TABLE IF NOT EXISTS agent_zenas.training_pairs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region        text NOT NULL,
    tenant_id     uuid,
    source        text NOT NULL,
    worked_id     uuid,
    request       text NOT NULL DEFAULT '',
    findings      jsonb NOT NULL DEFAULT '[]'::jsonb,
    outcome       text NOT NULL DEFAULT '',
    memo          text NOT NULL DEFAULT '',
    lawyer_edits  text NOT NULL DEFAULT '',
    as_at         date,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT training_source_chk CHECK (source IN ('case', 'released')),
    CONSTRAINT training_region_chk CHECK (region IN ('us-east', 'za'))
);

CREATE INDEX IF NOT EXISTS training_region_tenant_idx
    ON agent_zenas.training_pairs (region, tenant_id);

CREATE TABLE IF NOT EXISTS agent_zenas.ladder_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region        text NOT NULL,
    tenant_id     uuid NOT NULL,
    kind          text NOT NULL,
    question_key  text NOT NULL DEFAULT '',
    cost_usd      numeric(12,6) NOT NULL DEFAULT 0,
    reused        integer NOT NULL DEFAULT 0,
    coded         integer NOT NULL DEFAULT 0,
    modeled       integer NOT NULL DEFAULT 0,
    elements      integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ladder_runs_region_chk CHECK (region IN ('us-east', 'za'))
);

CREATE INDEX IF NOT EXISTS ladder_runs_tenant_idx
    ON agent_zenas.ladder_runs (region, tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_zenas.memory_notes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region      text NOT NULL,
    tenant_id   uuid NOT NULL,
    kind        text NOT NULL DEFAULT '',
    body        text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT memory_notes_region_chk CHECK (region IN ('us-east', 'za'))
);

CREATE INDEX IF NOT EXISTS memory_notes_tenant_idx
    ON agent_zenas.memory_notes (region, tenant_id, created_at DESC);

-- Panel-approved clause text for one region. Empty until a lawyer adds a row.
-- region has no default: the box that holds the clause must name it.
CREATE TABLE IF NOT EXISTS agent_zenas.clauses (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region              text NOT NULL,
    jurisdiction        text NOT NULL,
    template            text NOT NULL,
    kind                text NOT NULL,
    text                text NOT NULL,
    fallback_positions  jsonb NOT NULL DEFAULT '[]'::jsonb,
    notes               text NOT NULL DEFAULT '',
    approved_by         text NOT NULL,
    admission_no        text NOT NULL,
    status              text NOT NULL DEFAULT 'lawyer_approved',
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT clauses_region_chk CHECK (region IN ('us-east', 'za')),
    CONSTRAINT clauses_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za', 'both')),
    CONSTRAINT clauses_status_chk CHECK (status = 'lawyer_approved')
);

CREATE INDEX IF NOT EXISTS clauses_lookup_idx
    ON agent_zenas.clauses (region, template, jurisdiction, kind);

-- A tenant's preferred positions. Another tenant's playbook is not readable here.
CREATE TABLE IF NOT EXISTS agent_zenas.playbooks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region      text NOT NULL,
    tenant_id   uuid NOT NULL,
    name        text NOT NULL,
    template    text NOT NULL,
    positions   jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT playbooks_region_chk CHECK (region IN ('us-east', 'za'))
);

CREATE INDEX IF NOT EXISTS playbooks_tenant_idx
    ON agent_zenas.playbooks (region, tenant_id, template);

-- material_type is public_record or client_work. No default: unknown stays null and is not shared.
ALTER TABLE agent_zenas.worked_matters ADD COLUMN IF NOT EXISTS material_type text;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.conname = 'worked_material_type_chk' AND n.nspname = 'agent_zenas') THEN ALTER TABLE agent_zenas.worked_matters ADD CONSTRAINT worked_material_type_chk CHECK (material_type IS NULL OR material_type IN ('public_record', 'client_work')); END IF; END $$;
