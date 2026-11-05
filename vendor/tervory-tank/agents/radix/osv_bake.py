"""Build-time only: turn the official OSV exports into /opt/osv/osv.sqlite.

The Dockerfile's osv stage fetches <ecosystem>/all.zip from the public OSV bucket
(https://osv-vulnerabilities.storage.googleapis.com) with ADD, then runs this
script on the local files. This script opens no socket. Radix at runtime only
reads the finished sqlite file, read-only, and has no route to the internet.

Usage: python osv_bake.py OUT.sqlite PyPI=/osv/PyPI.zip npm=/osv/npm.zip
The build fails (exit 1) when an export looks truncated or the #47 advisories
are missing, so a bad download never ships as "no findings".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanlib.dependencies import _row  # noqa: E402

# Floors well under today's counts (PyPI ~26k records, npm ~230k incl. MAL-), so
# only a broken or truncated download trips them.
FLOORS = {"pypi": 5_000, "npm": 20_000}
MUST_HAVE = {"pypi": "GHSA-8q59-q68h-6hv4", "npm": "GHSA-xvch-5gv4-984h"}
SCHEMA = """
CREATE TABLE advisories (
  eco TEXT NOT NULL, ecosystem TEXT NOT NULL, package TEXT NOT NULL, advisory TEXT NOT NULL,
  aliases TEXT NOT NULL, severity TEXT NOT NULL, ranges TEXT NOT NULL, versions TEXT NOT NULL
);
CREATE TABLE meta (ecosystem TEXT PRIMARY KEY, records INTEGER, rows INTEGER, sha256 TEXT, baked_at TEXT);
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def bake(out: Path, sources: dict[str, Path], floors: dict[str, int] | None = None,
         must_have: dict[str, str] | None = None) -> dict[str, dict]:
    floors = FLOORS if floors is None else floors
    must_have = MUST_HAVE if must_have is None else must_have
    if out.exists():
        out.unlink()
    conn = sqlite3.connect(out)
    conn.executescript(SCHEMA)
    report: dict[str, dict] = {}
    baked_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ecosystem, path in sources.items():
        eco = ecosystem.casefold()
        records = rows = 0
        ids: set[str] = set()
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.endswith(".json"):
                    continue
                try:
                    doc = json.loads(archive.read(info))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(doc, dict):
                    continue
                records += 1
                for affected in doc.get("affected") or []:
                    if not isinstance(affected, dict):
                        continue
                    if str((affected.get("package") or {}).get("ecosystem") or "").casefold() != eco:
                        continue
                    row = _row(doc, affected)
                    if row is None:
                        continue
                    # Explicit version lists are only kept when no usable range exists (size).
                    versions = [] if row["ranges"] else row["versions"]
                    conn.execute(
                        "INSERT INTO advisories VALUES (?,?,?,?,?,?,?,?)",
                        (eco, row["ecosystem"], row["package"], row["advisory"],
                         json.dumps(row["aliases"]), row["severity"],
                         json.dumps(row["ranges"], separators=(",", ":")),
                         json.dumps(versions, separators=(",", ":"))),
                    )
                    ids.add(row["advisory"])
                    rows += 1
        if records < floors.get(eco, 1):
            raise SystemExit(f"OSV {ecosystem}: only {records} records, export looks truncated")
        if must_have.get(eco) and must_have[eco] not in ids:
            raise SystemExit(f"OSV {ecosystem}: {must_have[eco]} missing, refusing to bake")
        digest = _sha256(path)
        conn.execute("INSERT INTO meta VALUES (?,?,?,?,?)", (ecosystem, records, rows, digest, baked_at))
        report[ecosystem] = {"records": records, "rows": rows, "sha256": digest, "baked_at": baked_at}
    conn.execute("CREATE INDEX advisories_pkg ON advisories (eco, package)")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    return report


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[0])
    sources = {}
    for item in argv[1:]:
        name, _, raw = item.partition("=")
        sources[name] = Path(raw)
    report = bake(out, sources)
    print(json.dumps({"out": str(out), "bytes": out.stat().st_size, "ecosystems": report}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
