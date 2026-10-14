-- Zenas box. Additive and idempotent. Applied by the agent on start.
-- Document bodies keep placeholders. The identifier itself is only in pii.ciphertext.
-- The knowledge pool has its own schema and is not created here.

CREATE SCHEMA IF NOT EXISTS agent_zenas;

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
