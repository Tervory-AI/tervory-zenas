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

-- Learning register. A source row records jurisdiction, source, licence, terms status, and last update.
-- No source text is stored here. not_cleared is not ingested. last_update stays empty until a later load.
-- A golden-case row is an empty slot. correction is for a lawyer later. Questions are not invented here.
-- Criminal matters are refused by the register and are not inserted here.

CREATE TABLE IF NOT EXISTS agent_zenas.learning_sources (
    source_id      text PRIMARY KEY,
    jurisdiction   text NOT NULL,
    source         text NOT NULL,
    licence        text NOT NULL,
    terms_status   text NOT NULL,
    last_update    timestamptz,
    CONSTRAINT learning_sources_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za')),
    CONSTRAINT learning_sources_terms_chk CHECK (terms_status IN ('cleared', 'not_cleared'))
);

ALTER TABLE agent_zenas.learning_sources ADD COLUMN IF NOT EXISTS jurisdiction text;
ALTER TABLE agent_zenas.learning_sources ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE agent_zenas.learning_sources ADD COLUMN IF NOT EXISTS licence text;
ALTER TABLE agent_zenas.learning_sources ADD COLUMN IF NOT EXISTS terms_status text;
ALTER TABLE agent_zenas.learning_sources ADD COLUMN IF NOT EXISTS last_update timestamptz;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.conname = 'learning_sources_jurisdiction_chk' AND n.nspname = 'agent_zenas') THEN ALTER TABLE agent_zenas.learning_sources ADD CONSTRAINT learning_sources_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za')); END IF; END $$;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.conname = 'learning_sources_terms_chk' AND n.nspname = 'agent_zenas') THEN ALTER TABLE agent_zenas.learning_sources ADD CONSTRAINT learning_sources_terms_chk CHECK (terms_status IN ('cleared', 'not_cleared')); END IF; END $$;

CREATE INDEX IF NOT EXISTS learning_sources_jurisdiction_idx
    ON agent_zenas.learning_sources (jurisdiction);

CREATE TABLE IF NOT EXISTS agent_zenas.golden_cases (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    jurisdiction    text NOT NULL,
    slot            integer NOT NULL,
    question        text NOT NULL DEFAULT '',
    answer          text NOT NULL DEFAULT '',
    correction      text NOT NULL DEFAULT '',
    corrected_by    text NOT NULL DEFAULT '',
    admission_no    text NOT NULL DEFAULT '',
    corrected_at    timestamptz,
    CONSTRAINT golden_cases_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za')),
    CONSTRAINT golden_cases_slot_chk CHECK (slot >= 1 AND slot <= 20)
);

ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS jurisdiction text;
ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS slot integer;
ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS question text;
ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS answer text;
ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS correction text;
ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS corrected_by text;
ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS admission_no text;
ALTER TABLE agent_zenas.golden_cases ADD COLUMN IF NOT EXISTS corrected_at timestamptz;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.conname = 'golden_cases_jurisdiction_chk' AND n.nspname = 'agent_zenas') THEN ALTER TABLE agent_zenas.golden_cases ADD CONSTRAINT golden_cases_jurisdiction_chk CHECK (jurisdiction IN ('us', 'za')); END IF; END $$;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE c.conname = 'golden_cases_slot_chk' AND n.nspname = 'agent_zenas') THEN ALTER TABLE agent_zenas.golden_cases ADD CONSTRAINT golden_cases_slot_chk CHECK (slot >= 1 AND slot <= 20); END IF; END $$;

CREATE UNIQUE INDEX IF NOT EXISTS golden_cases_slot_idx
    ON agent_zenas.golden_cases (jurisdiction, slot);


-- Seed. ON CONFLICT DO NOTHING so a later lawyer correction is kept.
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('govinfo-uscode', 'us', 'United States Code (GovInfo)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('ecfr', 'us', 'Code of Federal Regulations (eCFR)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('federal-register', 'us', 'Federal Register', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('cfr-19', 'us', '19 CFR (customs)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('usitc-htsus', 'us', 'Harmonized Tariff Schedule of the United States (USITC)', 'Public domain. U.S. government work, 17 U.S.C. § 105. Enacted schedule published by the U.S. International Trade Commission, 19 U.S.C. § 1202.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('ear-15-cfr', 'us', 'EAR (15 CFR 730-774)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('itar-22-cfr', 'us', 'ITAR (22 CFR 120-130)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('ofac-31-cfr', 'us', 'OFAC (31 CFR 500s)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('cogsa', 'us', 'COGSA (46 USC 30701 note)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('carmack', 'us', 'Carmack (49 USC 14706)', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('uncitral-cisg', 'us', 'CISG (UNCITRAL publication)', 'United Nations / UNCITRAL. Official publication text is stored from the public ebook.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('uncitral-model-law', 'us', 'UNCITRAL model laws', 'United Nations / UNCITRAL. Official publication text is stored from the public ebook.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('courtlistener', 'us', 'CourtListener (Free Law Project)', 'Opinion text: public domain (edicts of government). CourtListener bulk files: free of known copyright restrictions (Free Law Project). Criminal matters are not stored.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('cap', 'us', 'Caselaw Access Project', 'Unrestricted as of 2024-03-01. Harvard ended the commercial-use limits on this digitization. Criminal matters are not stored.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('frcp', 'us', 'Federal Rules of Civil Procedure', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('fre', 'us', 'Federal Rules of Evidence', 'Public domain. U.S. government work, 17 U.S.C. § 105.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('local-rules', 'us', 'Local rules of the districts the panel practises in', 'Unknown. The panel has not named the districts.', 'not_cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('delaware-code', 'us', 'Delaware General Corporation Law', 'Public domain. Enacted statute text is an edict of government. Publisher annotations and unofficial commentary are not stored.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('ny-laws', 'us', 'New York enacted commercial statutes', 'Public domain. Enacted statute text is an edict of government. Publisher annotations and unofficial commentary are not stored.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('texas-statutes', 'us', 'Texas enacted commercial statutes', 'Public domain. Enacted statute text is an edict of government. Publisher annotations and unofficial commentary are not stored.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('ucc-enacted', 'us', 'UCC Articles 1, 2 and 9 as enacted by Delaware, New York and Texas', 'Public domain. Enacted statute text is an edict of government. Publisher annotations and unofficial commentary are not stored. The official ALI text is a different source and is not stored.', 'cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.learning_sources
    (source_id, jurisdiction, source, licence, terms_status)
VALUES ('ali-ucc-official', 'us', 'UCC official text and comments', 'Copyright American Law Institute and Uniform Law Commission. The official text and comments are not stored.', 'not_cleared')
ON CONFLICT (source_id) DO NOTHING;
INSERT INTO agent_zenas.golden_cases (jurisdiction, slot)
VALUES
    ('us', 1),
    ('us', 2),
    ('us', 3),
    ('us', 4),
    ('us', 5),
    ('us', 6),
    ('us', 7),
    ('us', 8),
    ('us', 9),
    ('us', 10),
    ('us', 11),
    ('us', 12),
    ('us', 13),
    ('us', 14),
    ('us', 15),
    ('us', 16),
    ('us', 17),
    ('us', 18),
    ('us', 19),
    ('us', 20),
    ('za', 1),
    ('za', 2),
    ('za', 3),
    ('za', 4),
    ('za', 5),
    ('za', 6),
    ('za', 7),
    ('za', 8),
    ('za', 9),
    ('za', 10),
    ('za', 11),
    ('za', 12),
    ('za', 13),
    ('za', 14),
    ('za', 15),
    ('za', 16),
    ('za', 17),
    ('za', 18),
    ('za', 19),
    ('za', 20)
ON CONFLICT (jurisdiction, slot) DO NOTHING;

-- Z7. Active is its own column: the status check already on a live database
-- lists draft, lawyer_approved, and stale, and a check is not widened here.
-- reviewed is false until a person signs. holdout cases are not reused.
ALTER TABLE agent_zenas.documents ADD COLUMN IF NOT EXISTS reviewed boolean NOT NULL DEFAULT false;
ALTER TABLE agent_zenas.rule_cards ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT false;
ALTER TABLE agent_zenas.worked_matters ADD COLUMN IF NOT EXISTS holdout boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS agent_zenas.review_settings (
    region text PRIMARY KEY,
    review text NOT NULL DEFAULT 'off',
    CONSTRAINT review_settings_review_chk CHECK (review IN ('off', 'on')),
    CONSTRAINT review_settings_region_chk CHECK (region IN ('us-east', 'za'))
);

CREATE TABLE IF NOT EXISTS agent_zenas.selftest_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region text NOT NULL,
    kind text NOT NULL,
    area text NOT NULL DEFAULT '',
    jurisdiction text NOT NULL DEFAULT '',
    cases integer NOT NULL DEFAULT 0,
    outcome_agreement numeric(6,2) NOT NULL DEFAULT 0,
    element_agreement numeric(6,2) NOT NULL DEFAULT 0,
    citations_valid numeric(6,2) NOT NULL DEFAULT 0,
    cost_per_case numeric(12,6) NOT NULL DEFAULT 0,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT selftest_region_chk CHECK (region IN ('us-east', 'za')),
    CONSTRAINT selftest_kind_chk CHECK (kind IN ('holdout', 'legalbench'))
);

-- Z8. A cite-check failure is logged and retried the next night. No judgment text.
CREATE TABLE IF NOT EXISTS agent_zenas.convert_failures (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region       text NOT NULL,
    pool_doc_id  text NOT NULL,
    reason       text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT convert_failures_region_chk CHECK (region IN ('us-east', 'za'))
);

CREATE UNIQUE INDEX IF NOT EXISTS convert_failures_doc_idx
    ON agent_zenas.convert_failures (region, pool_doc_id);
