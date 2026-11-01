
-- MOOT durable text hearing: additive, tenant/region-scoped, no fixture records.
CREATE TABLE IF NOT EXISTS agent_zenas.moot_hearings (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    region text NOT NULL CHECK (region IN ('us-east', 'za')),
    revision bigint NOT NULL CHECK (revision >= 0),
    state jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS moot_hearings_tenant_region_idx
    ON agent_zenas.moot_hearings(tenant_id, region, updated_at DESC);
CREATE TABLE IF NOT EXISTS agent_zenas.moot_hearing_requests (
    tenant_id uuid NOT NULL,
    hearing_id uuid NOT NULL REFERENCES agent_zenas.moot_hearings(id),
    actor_id text NOT NULL,
    request_id uuid NOT NULL,
    fingerprint text NOT NULL,
    result_revision bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hearing_id, actor_id, request_id)
);
CREATE TABLE IF NOT EXISTS agent_zenas.moot_hearing_events (
    tenant_id uuid NOT NULL,
    hearing_id uuid NOT NULL REFERENCES agent_zenas.moot_hearings(id),
    revision bigint NOT NULL,
    actor_id text NOT NULL,
    request_id uuid NOT NULL,
    kind text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, hearing_id, revision)
);
CREATE TABLE IF NOT EXISTS agent_zenas.moot_hearing_learning (
    tenant_id uuid NOT NULL,
    hearing_id uuid NOT NULL REFERENCES agent_zenas.moot_hearings(id),
    turn_number integer NOT NULL,
    candidate jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending_review' CHECK (status IN ('pending_review', 'accepted', 'rejected')),
    reviewed_by text,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, hearing_id, turn_number)
);
