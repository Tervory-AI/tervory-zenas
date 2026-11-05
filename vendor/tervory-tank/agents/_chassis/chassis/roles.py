"""Database role for one agent. Idempotent. The password is never logged.

The agent-roles service runs this from AGENT_DB_PASSWORD_<AGENT>. The role can
use only schema agent_<name>. It is not a superuser and cannot read tank or base.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_AGENT = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_PREFIX = "AGENT_DB_PASSWORD_"

# Extra schemas an agent owns besides agent_<name>. Shared public law for one
# region, not tenant data. Created here because the agent role cannot create schemas.
EXTRA_SCHEMAS: dict[str, tuple[str, ...]] = {"zenas": ("zenas_pool",)}

_ROLE_OPTIONS = (
    "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
)


class RoleProvisionError(RuntimeError):
    """Provisioning failed. The message does not include the password or the SQL."""


def role_name(agent: str) -> str:
    if _AGENT.fullmatch(agent) is None:
        raise ValueError("agent name must be lowercase letters, digits, and underscores")
    return f"agent_{agent}"


def quote_literal(value: str) -> str:
    if "\x00" in value:
        raise ValueError("password contains a null")
    return "'" + value.replace("'", "''") + "'"


def passwords_from_env(env: Mapping[str, str]) -> list[tuple[str, str]]:
    """(agent id, password) for every non-empty AGENT_DB_PASSWORD_<AGENT>."""
    found: list[tuple[str, str]] = []
    for key in sorted(env):
        if not key.startswith(_PREFIX):
            continue
        raw = env[key].strip()
        if not raw:
            continue
        agent = key[len(_PREFIX):].lower()
        if _AGENT.fullmatch(agent) is None:
            continue
        found.append((agent, raw))
    return found


def _tag(seed: str, secret: str) -> str:
    tag = seed
    while f"${tag}$" in secret:
        tag += "x"
    return tag


def _ownership(name: str) -> str:
    """Give the role every table and sequence already in its schema.

    C6 created those objects as the superuser. ALTER ... OWNER TO the same role
    on a later start is a no-op. Indexes follow the table.
    """
    return f"""
DO $own$
DECLARE
  rel record;
BEGIN
  FOR rel IN
    SELECT c.relname, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = '{name}'
      AND c.relkind IN ('r', 'p', 'S')
  LOOP
    IF rel.relkind = 'S' THEN
      EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', '{name}', rel.relname, '{name}');
    ELSE
      EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', '{name}', rel.relname, '{name}');
    END IF;
  END LOOP;
END
$own$;
""".strip()


def _extra_schemas(agent: str, name: str) -> list[str]:
    out: list[str] = []
    for schema in EXTRA_SCHEMAS.get(agent, ()):
        out += [
            f"CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION {name}",
            f"ALTER SCHEMA {schema} OWNER TO {name}",
            f"GRANT USAGE, CREATE ON SCHEMA {schema} TO {name}",
        ]
    return out


def _guarded_revoke(schema: str, name: str) -> str:
    """REVOKE is skipped when that schema does not exist yet. Tank may still be starting."""
    tag = f"revoke{schema}"
    return f"""
DO ${tag}$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{schema}') THEN
    EXECUTE 'REVOKE ALL ON SCHEMA {schema} FROM {name}';
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {name}';
  END IF;
END
${tag}$;
""".strip()


def statements_for(agent: str, password: str) -> list[str]:
    """SQL that creates or updates one agent role. Safe to run on every start."""
    if not password or not password.strip():
        raise ValueError("password is empty")
    name = role_name(agent)
    secret = password.strip()
    literal = quote_literal(secret)
    tag = _tag("pw", secret)
    return [
        f"CREATE SCHEMA IF NOT EXISTS {name}",
        f"""
DO ${tag}$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
    CREATE ROLE {name} {_ROLE_OPTIONS} PASSWORD {literal};
  ELSE
    ALTER ROLE {name} WITH {_ROLE_OPTIONS} PASSWORD {literal};
  END IF;
END
${tag}$;
""".strip(),
        f"GRANT USAGE, CREATE ON SCHEMA {name} TO {name}",
        _ownership(name),
        *_extra_schemas(agent, name),
        _guarded_revoke("tank", name),
        _guarded_revoke("base", name),
        f"REVOKE ALL ON SCHEMA public FROM {name}",
        f"ALTER ROLE {name} SET search_path = {name}",
    ]


async def apply_roles(conn: Any, env: Mapping[str, str]) -> list[str]:
    """Run statements_for for every configured agent. Returns the agent ids."""
    applied: list[str] = []
    for agent, password in passwords_from_env(env):
        for statement in statements_for(agent, password):
            try:
                await conn.execute(statement)
            except Exception:
                raise RoleProvisionError(f"could not provision {role_name(agent)}") from None
        applied.append(agent)
    return applied
