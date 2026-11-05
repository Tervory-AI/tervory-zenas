"""One generator for every agent's additive memory schema. No data migration."""

import re
from pathlib import Path

PLATFORM_TENANT = "00000000-0000-0000-0000-000000000001"
AGENTS = (
    "tank",
    "base",
    "tiger",
    "market_lab",
    "zenas",
    "radix",
    "tisan",
    "rig",
    "ghost",
    "trigger",
    "template",
)
BEGIN = "-- BEGIN GENERATED AGENT MEMORY (hermes/record_schema.py)"
END = "-- END GENERATED AGENT MEMORY"


def schema_for(agent: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", agent):
        raise ValueError("Invalid agent")
    return "agent_" + agent


def schema_sql(agent: str, *, include_schema: bool = True) -> str:
    s = schema_for(agent)
    prefix = f"CREATE SCHEMA IF NOT EXISTS {s};\n" if include_schema else ""
    return (
        prefix
        + f"""CREATE TABLE IF NOT EXISTS {s}.memory_records (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    agent text NOT NULL DEFAULT '{agent}' CHECK (agent = '{agent}'),
    claim text NOT NULL CHECK (length(claim) BETWEEN 1 AND 8000),
    evidence jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence) = 'array'),
    confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    last_verified timestamptz,
    embed_model text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    tier text NOT NULL DEFAULT 'client' CHECK (tier IN ('client','platform')),
    kind text NOT NULL DEFAULT 'fact',
    meta jsonb NOT NULL DEFAULT '{{}}',
    seen integer NOT NULL DEFAULT 1,
    CHECK (tier <> 'platform' OR tenant_id = '{PLATFORM_TENANT}'::uuid)
);
CREATE INDEX IF NOT EXISTS memory_records_scope ON {s}.memory_records (tenant_id, tier, created_at, id);
CREATE INDEX IF NOT EXISTS memory_records_encoder ON {s}.memory_records (tenant_id, embed_model);
CREATE TABLE IF NOT EXISTS {s}.memory_index_jobs (
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
CREATE INDEX IF NOT EXISTS memory_index_jobs_due ON {s}.memory_index_jobs (run_at);
CREATE TABLE IF NOT EXISTS {s}.memory_publish_policy (
    tenant_id uuid PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    identifiers jsonb NOT NULL DEFAULT '{{}}',
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {s}.memory_publish_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    actor text NOT NULL,
    happened_at timestamptz NOT NULL DEFAULT now(),
    source_row uuid NOT NULL,
    result text NOT NULL CHECK (result IN ('disabled','missing','refused','published','already_published')),
    published_row uuid
);
"""
    )


def generated_block(agent: str) -> str:
    # Agent chassis provisions its schema. Restricted agent roles cannot CREATE
    # SCHEMA even with IF NOT EXISTS; Tank's startup creates agent_tank itself.
    return BEGIN + "\n" + schema_sql(agent, include_schema=agent == "tank") + END + "\n"


def update_files(root: Path) -> None:
    for agent in AGENTS:
        path = root / (
            "tank/schema.sql"
            if agent == "tank"
            else f"agents/{'_template' if agent == 'template' else agent}/schema.sql"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_text() if path.exists() else ""
        previous = previous.split(BEGIN)[0].rstrip()
        path.write_text(
            previous + ("\n\n" if previous else "") + generated_block(agent)
        )


if __name__ == "__main__":
    update_files(Path(__file__).resolve().parents[1])
