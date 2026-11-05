"""tank/schema.sql is applied on every Tank start, so every statement must be safe to re-run."""
import pathlib
import re

SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "tank" / "schema.sql"

_FORBIDDEN = re.compile(r"\b(DROP|RENAME)\b", re.IGNORECASE)


def _split_statements(sql: str) -> list[str]:
    """Split on semicolons that are not inside string literals. Line comments are dropped."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_str = False
    while i < n:
        c = sql[i]
        if in_str:
            buf.append(c)
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append(sql[i + 1])
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if sql.startswith("$$", i):
            # Dollar-quoted body (a DO block): copy it whole; its semicolons are not statement ends.
            end = sql.find("$$", i + 2)
            end = n if end < 0 else end + 2
            buf.append(sql[i:end])
            i = end
            continue
        if c == "'":
            in_str = True
            buf.append(c)
            i += 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            buf.append(" ")
            continue
        if c == ";":
            statements.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def _idempotent(statement: str) -> bool:
    if re.match(r"^DO\s+\$\$", statement, re.IGNORECASE):
        # A one-time data change must guard itself, e.g. IF NOT EXISTS (marker in tank.policy_log).
        if re.search(r"\bIF\s+NOT\s+EXISTS\b", statement, re.IGNORECASE):
            return True
        # Or it only inserts, and every insert skips rows it already wrote.
        inserts = re.findall(r"\bINSERT\b.*?(?:;|END\s+IF)", statement, re.IGNORECASE | re.DOTALL)
        return bool(inserts) and all(re.search(r"\bON\s+CONFLICT\b", i, re.IGNORECASE) for i in inserts) \
            and not re.search(r"\b(UPDATE|DELETE|DROP|TRUNCATE|ALTER)\b", statement, re.IGNORECASE)
    head = re.match(r"^(CREATE|ALTER|INSERT|UPDATE)\b", statement, re.IGNORECASE)
    if head is None:
        return False
    kind = head.group(1).upper()
    if kind == "CREATE":
        if re.match(r"^CREATE\s+OR\s+REPLACE\s+FUNCTION\b", statement, re.IGNORECASE):
            return True  # Same signature/body replaces itself; exercised twice on Postgres.
        if re.match(r"^CREATE\s+OR\s+REPLACE\s+TRIGGER\b", statement, re.IGNORECASE):
            return True  # PostgreSQL 14+: replaces the same trigger; exercised twice on Postgres.
        return re.search(r"\bIF\s+NOT\s+EXISTS\b", statement, re.IGNORECASE) is not None
    if kind == "ALTER":
        if re.fullmatch(r"ALTER\s+TABLE\s+tank\.policy\s+ALTER\s+COLUMN\s+auto_promote\s+SET\s+DEFAULT\s+false", statement, re.IGNORECASE):
            return True  # CORE-1 explicitly changes this default, without changing the column.
        if re.fullmatch(r"ALTER\s+TABLE\s+tank\.(?:policy\s+ALTER\s+COLUMN\s+eval_threshold\s+SET\s+DEFAULT\s+0\.99|(?:policy|route_evidence)\s+ALTER\s+COLUMN\s+min_shadow_samples\s+SET\s+DEFAULT\s+200)", statement, re.IGNORECASE):
            return True  # Founder graduation defaults; reapplication does not rewrite rows.
        return re.search(r"\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b", statement, re.IGNORECASE) is not None
    if kind == "INSERT":
        return re.search(r"\bON\s+CONFLICT\b", statement, re.IGNORECASE) is not None
    return True


def test_schema_statements_are_idempotent() -> None:
    statements = _split_statements(SCHEMA.read_text(encoding="utf-8"))
    assert len(statements) >= 20, "schema parser returned too few statements"
    bad: list[str] = []
    for statement in statements:
        forbidden = _FORBIDDEN.search(statement)
        if forbidden:
            bad.append(f"{forbidden.group(1).upper()} in: {statement[:120]}")
            continue
        if not _idempotent(statement):
            bad.append(f"not idempotent-shaped: {statement[:120]}")
    assert not bad, "\n".join(bad)


def test_idempotence_shapes_still_reject_unprotected_ddl():
    assert not _idempotent("ALTER TABLE tank.policy ALTER COLUMN model TYPE integer")
    assert not _idempotent("CREATE FUNCTION tank.unsafe() RETURNS void LANGUAGE sql AS 'SELECT 1'")
    assert not _idempotent("CREATE TABLE tank.unsafe (id integer)")
    assert not _idempotent("ALTER TABLE tank.policy ADD COLUMN unsafe text")
