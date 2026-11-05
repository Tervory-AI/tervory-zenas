"""SQLite connection and schema apply. schema.sql stays valid PostgreSQL."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_INDEX = re.compile(
    r"CREATE INDEX IF NOT EXISTS (\w+) ON market_lab\.(\w+) \((.+)\)\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
# Postgres accepts ADD COLUMN IF NOT EXISTS. This SQLite build does not.
_ADD_IF = re.compile(r"\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b", re.IGNORECASE)


def connect(target: str) -> sqlite3.Connection:
    if target.startswith("postgres://") or target.startswith("postgresql://"):
        raise SystemExit(
            "FEEDS_DATABASE_URL points at Postgres. Apply schema.sql with the "
            "platform database tools. The feeds runner uses a sqlite file so "
            "tests need no database server."
        )
    if target.startswith("sqlite:///"):
        target = target[len("sqlite:///") :]
    # The file is ATTACHed as market_lab so schema.sql can qualify every table.
    # The main database stays empty; unqualified reads then find market_lab.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if target == ":memory:":
        conn.execute("ATTACH DATABASE ':memory:' AS market_lab")
    else:
        path = Path(target)
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        conn.execute("ATTACH DATABASE ? AS market_lab", (str(path),))
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in _statements(script):
        rewritten = _for_sqlite(statement)
        if rewritten is None:
            continue
        try:
            conn.execute(rewritten)
        except sqlite3.OperationalError as exc:
            # The column is already on a table created with the new definition.
            if "duplicate column name" not in str(exc).lower():
                raise
    conn.commit()


def _statements(script: str) -> list[str]:
    found: list[str] = []
    for raw in script.split(";"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("--")]
        text = "\n".join(lines).strip()
        if text:
            found.append(text)
    return found


def _for_sqlite(statement: str) -> str | None:
    if statement.upper().startswith("CREATE SCHEMA"):
        return None
    if statement.upper().startswith("ALTER TABLE"):
        return _ADD_IF.sub("ADD COLUMN", statement)
    match = _INDEX.match(statement)
    if match is None:
        return statement
    name, table, columns = match.groups()
    return f"CREATE INDEX IF NOT EXISTS market_lab.{name} ON {table} ({columns.strip()})"
