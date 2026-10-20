import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.zenas.pool.store import (
    CITATION_CHUNK_SQL,
    CITATION_DOCUMENT_SQL,
    INSERT_SOURCE_SQL,
    TEXT_CHUNK_SQL,
    schema_statements,
)

_ALLOWED = ("CREATE SCHEMA", "CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX")


def _allowed_statement(upper: str) -> bool:
    if upper.startswith(_ALLOWED):
        return True
    # The only ALTER this schema may use: add a column on a table that already exists.
    compact = " ".join(upper.split())
    return compact.startswith("ALTER TABLE ") and " ADD COLUMN IF NOT EXISTS " in f" {compact} "


def test_schema_is_additive():
    statements = schema_statements()
    assert statements
    for statement in statements:
        upper = statement.upper()
        assert "IF NOT EXISTS" in upper
        assert _allowed_statement(upper)
        # last_update is a column name, not an UPDATE statement.
        folded = re.sub(r"\bLAST_UPDATE\b", "LAST_TS", upper)
        for banned in ("DROP ", "TRUNCATE", "RENAME ", "DELETE ", "UPDATE "):
            assert banned not in folded


def test_court_and_treatment_columns_are_additive():
    text = Path(__file__).resolve().parents[1].joinpath("agents/zenas/pool/schema.sql").read_text(encoding="utf-8")
    for column in (
        "court",
        "division",
        "seat",
        "bench_size",
        "judges",
        "neutral_citation",
        "report_citation",
        "appeal",
        "appeal_from",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column} " in text
    for column in ("treatment", "evidence", "paragraph"):
        assert f"ADD COLUMN IF NOT EXISTS {column} " in text
    assert "DEFAULT 'referred to'" in text


def test_queries_are_scoped_by_jurisdiction():
    for sql in (CITATION_CHUNK_SQL, CITATION_DOCUMENT_SQL, TEXT_CHUNK_SQL):
        assert "jurisdiction = $1" in sql
        assert "effective_from IS NOT NULL" in sql
    assert "ON CONFLICT" in INSERT_SOURCE_SQL
    assert "zenas_pool.sources" in INSERT_SOURCE_SQL
