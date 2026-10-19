"""Apply the Zenas schema on a real Postgres: empty, twice, and after main.

Tank went down when a schema change was not idempotent on a database that
already had the main schema and the agent roles. This test is that check.
It runs when ZENAS_SCHEMA_DATABASE_URL points at a superuser database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "agents" / "_chassis"))

from chassis.roles import statements_for  # noqa: E402
from test_schema import _split_statements  # noqa: E402

ZENAS = ROOT / "agents" / "zenas" / "schema.sql"
TANK = ROOT / "tank" / "schema.sql"
URL = os.environ.get("ZENAS_SCHEMA_DATABASE_URL", "")
PASSWORD = "zenas_schema_placeholder"


def _statements(path: Path) -> list[str]:
    return _split_statements(path.read_text(encoding="utf-8"))


async def _admin():
    asyncpg = pytest.importorskip("asyncpg")
    if not URL:
        pytest.skip("ZENAS_SCHEMA_DATABASE_URL is not set")
    return await asyncpg.connect(URL)


async def _fresh(admin, name: str):
    await admin.execute(
        f"""
        SELECT pg_terminate_backend(pid) FROM pg_stat_activity
        WHERE datname = '{name}' AND pid <> pg_backend_pid()
        """
    )
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    await admin.execute(f'CREATE DATABASE "{name}"')
    base = URL.rsplit("/", 1)[0]
    asyncpg = pytest.importorskip("asyncpg")
    return await asyncpg.connect(f"{base}/{name}")


async def _apply(conn, statements: list[str]) -> None:
    for statement in statements:
        await conn.execute(statement)


def _guarded_ownership(schema: str) -> str:
    """Own tables, and own a sequence only when it does not belong to a column.

    chassis.roles alters every sequence. Postgres refuses that for a sequence
    linked to a serial or identity column (agent_base.migration_log_id_seq).
    Tank's own ownership block already skips those links. The Zenas schema
    creates no sequences; this is the same guard, used only so the test can
    put the roles in place on a database where main has already run.
    """
    return f"""
DO $own_safe$
DECLARE
  rel record;
BEGIN
  FOR rel IN
    SELECT c.oid, c.relname, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = '{schema}'
      AND c.relkind IN ('r', 'p', 'S')
  LOOP
    IF rel.relkind <> 'S' THEN
      EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', '{schema}', rel.relname, '{schema}');
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
      EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', '{schema}', rel.relname, '{schema}');
    END IF;
  END LOOP;
END
$own_safe$;
""".strip()


async def _apply_roles(conn, agent: str) -> None:
    schema = f"agent_{agent}"
    for statement in statements_for(agent, PASSWORD):
        if "ALTER SEQUENCE" in statement:
            await conn.execute(_guarded_ownership(schema))
        else:
            await conn.execute(statement)


@pytest.mark.asyncio
async def _assert_tenant_home(conn) -> None:
    meta = await conn.fetchrow(
        """
        SELECT is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'agent_zenas'
          AND table_name = 'tenants'
          AND column_name = 'home_region'
        """
    )
    assert meta is not None
    assert meta["is_nullable"] == "NO"
    assert meta["column_default"] is None
    rule = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conname = 'tenants_home_region_chk'
        """
    )
    assert rule is not None
    assert "'za'" in rule and "'us-east'" in rule
    import uuid

    asyncpg = pytest.importorskip("asyncpg")
    kept = uuid.uuid4()
    await conn.execute(
        "INSERT INTO agent_zenas.tenants (tenant_id, home_region) VALUES ($1, 'us-east')",
        kept,
    )
    with pytest.raises(asyncpg.NotNullViolationError):
        await conn.execute("INSERT INTO agent_zenas.tenants (tenant_id) VALUES ($1)", uuid.uuid4())
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO agent_zenas.tenants (tenant_id, home_region) VALUES ($1, 'eu')",
            uuid.uuid4(),
        )
    await conn.execute(
        "INSERT INTO agent_zenas.tenants (tenant_id, home_region) VALUES ($1, 'za')",
        uuid.uuid4(),
    )


async def _assert_draft_library(conn) -> None:
    """clauses and playbooks exist, name their region, and survive a second apply."""
    import uuid

    asyncpg = pytest.importorskip("asyncpg")
    for name in ("clauses", "playbooks"):
        found = await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'agent_zenas' AND table_name = $1
            """,
            name,
        )
        assert int(found) == 1
        meta = await conn.fetchrow(
            """
            SELECT is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'agent_zenas' AND table_name = $1 AND column_name = 'region'
            """,
            name,
        )
        assert meta is not None
        assert meta["is_nullable"] == "NO"
        assert meta["column_default"] is None
    with pytest.raises(asyncpg.NotNullViolationError):
        await conn.execute(
            """
            INSERT INTO agent_zenas.clauses
                (jurisdiction, template, kind, text, approved_by, admission_no)
            VALUES ('us', 'nda', 'parties', 'The parties are named.', 'Ada', '1')
            """
        )
    await conn.execute(
        """
        INSERT INTO agent_zenas.clauses
            (region, jurisdiction, template, kind, text, approved_by, admission_no)
        VALUES ('us-east', 'us', 'nda', 'parties', 'The parties are named.', 'Ada', '1')
        """
    )
    await conn.execute(
        """
        INSERT INTO agent_zenas.playbooks (region, tenant_id, name, template, positions)
        VALUES ('us-east', $1, 'NDA playbook', 'nda', '[]'::jsonb)
        """,
        uuid.uuid4(),
    )


async def _assert_material_type(conn) -> None:
    """material_type has no default. Only the two named kinds are stored."""
    asyncpg = pytest.importorskip("asyncpg")
    meta = await conn.fetchrow(
        """
        SELECT is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'agent_zenas'
          AND table_name = 'worked_matters'
          AND column_name = 'material_type'
        """
    )
    assert meta is not None
    assert meta["is_nullable"] == "YES"
    assert meta["column_default"] is None
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO agent_zenas.worked_matters (region, source, jurisdiction, material_type)
            VALUES ('us-east', 'case', 'us', 'secret')
            """
        )
    await conn.execute(
        """
        INSERT INTO agent_zenas.worked_matters (region, source, jurisdiction, material_type)
        VALUES ('us-east', 'case', 'us', 'public_record')
        """
    )


async def _assert_z7(conn) -> None:
    """reviewed, active, and holdout exist, and the new tables survive a second apply."""
    for table, column in (
        ("documents", "reviewed"),
        ("rule_cards", "active"),
        ("worked_matters", "holdout"),
    ):
        meta = await conn.fetchrow(
            """
            SELECT is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'agent_zenas' AND table_name = $1 AND column_name = $2
            """,
            table,
            column,
        )
        assert meta is not None
        assert meta["is_nullable"] == "NO"
        assert "false" in str(meta["column_default"]).lower()
    for name in ("review_settings", "selftest_runs"):
        found = await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'agent_zenas' AND table_name = $1
            """,
            name,
        )
        assert int(found) == 1
    await conn.execute(
        """
        INSERT INTO agent_zenas.review_settings (region, review)
        VALUES ('us-east', 'off')
        ON CONFLICT (region) DO NOTHING
        """
    )
    await conn.execute(
        """
        INSERT INTO agent_zenas.selftest_runs (region, kind, area, jurisdiction, cases)
        VALUES ('za', 'holdout', 'contracts', 'za', 1)
        """
    )


def test_home_region_column_has_no_default() -> None:
    column = [
        line
        for line in ZENAS.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("home_region")
    ]
    assert column == ["    home_region  text NOT NULL,"]


@pytest.mark.asyncio
async def test_zenas_schema_on_empty_twice_and_on_main_with_roles() -> None:
    text = ZENAS.read_text(encoding="utf-8").lower()
    assert "alter sequence" not in text
    assert "serial" not in text
    assert "generated always" not in text
    admin = await _admin()
    empty = None
    main = None
    try:
        empty = await _fresh(admin, "zenas_schema_empty")
        zenas = _statements(ZENAS)
        await _apply(empty, zenas)
        await _apply(empty, zenas)
        present = await empty.fetchval(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'agent_zenas'
              AND table_name IN (
                'matters', 'rule_cards', 'worked_matters', 'training_pairs',
                'ladder_runs', 'memory_notes'
              )
            """
        )
        assert int(present) == 6
        sequences = await empty.fetchval(
            """
            SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'agent_zenas' AND c.relkind = 'S'
            """
        )
        assert int(sequences) == 0
        await _assert_tenant_home(empty)
        await _assert_draft_library(empty)
        await _assert_material_type(empty)
        await _assert_z7(empty)
        await _apply(empty, zenas)
        kept = await empty.fetchval("SELECT count(*) FROM agent_zenas.tenants")
        assert int(kept) == 2
        assert int(await empty.fetchval("SELECT count(*) FROM agent_zenas.review_settings")) == 1
        assert int(await empty.fetchval("SELECT count(*) FROM agent_zenas.selftest_runs")) == 1
        assert int(await empty.fetchval("SELECT count(*) FROM agent_zenas.clauses")) == 1
        assert int(await empty.fetchval("SELECT count(*) FROM agent_zenas.playbooks")) == 1
        assert int(await empty.fetchval("SELECT count(*) FROM agent_zenas.worked_matters")) == 1

        main = await _fresh(admin, "zenas_schema_main")
        await _apply(main, _statements(TANK))
        for agent in ("base", "zenas"):
            await _apply_roles(main, agent)
        await _apply(main, zenas)
        await _apply(main, zenas)
        again = await main.fetchval(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'agent_zenas' AND table_name = 'rule_cards'
            """
        )
        assert int(again) == 1
        role = await main.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'agent_zenas'")
        assert role == 1
        linked = await main.fetchval(
            """
            SELECT count(*) FROM pg_class seq
            JOIN pg_namespace n ON n.oid = seq.relnamespace
            JOIN pg_depend d ON d.objid = seq.oid
            WHERE n.nspname = 'agent_base'
              AND seq.relname = 'migration_log_id_seq'
              AND d.deptype IN ('a', 'i')
            """
        )
        assert int(linked) == 1
        zenas_sequences = await main.fetchval(
            """
            SELECT count(*) FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'agent_zenas' AND c.relkind = 'S'
            """
        )
        assert int(zenas_sequences) == 0
        await _assert_tenant_home(main)
        await _assert_draft_library(main)
        await _assert_material_type(main)
        await _assert_z7(main)
        await _apply(main, zenas)
        kept_main = await main.fetchval("SELECT count(*) FROM agent_zenas.tenants")
        assert int(kept_main) == 2
        assert int(await main.fetchval("SELECT count(*) FROM agent_zenas.review_settings")) == 1
        assert int(await main.fetchval("SELECT count(*) FROM agent_zenas.selftest_runs")) == 1
        assert int(await main.fetchval("SELECT count(*) FROM agent_zenas.clauses")) == 1
        assert int(await main.fetchval("SELECT count(*) FROM agent_zenas.playbooks")) == 1
        assert int(await main.fetchval("SELECT count(*) FROM agent_zenas.worked_matters")) == 1
    finally:
        if empty is not None:
            await empty.close()
        if main is not None:
            await main.close()
        await admin.execute("DROP DATABASE IF EXISTS zenas_schema_empty")
        await admin.execute("DROP DATABASE IF EXISTS zenas_schema_main")
        await admin.close()
