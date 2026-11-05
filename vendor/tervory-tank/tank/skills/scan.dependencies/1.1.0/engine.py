"""scan.dependencies — offline advisory match for npm and Python packages.

The database is an OSV export baked into the image. pip-audit and npm audit
call a live service, so a scan does not run them.

Two kinds of baked data are read, both offline:
- OSV JSON files (the #47 fixtures under scanlib/osv, and any *.json in RADIX_OSV_DIR);
- osv.sqlite in RADIX_OSV_DIR, built at image build by radix/osv_bake.py from the
  official OSV PyPI and npm exports. Only the packages a scan names are read from it,
  so memory stays small.
Manifests read: requirements*.txt, package.json, package-lock.json, and pinned
installs in Dockerfiles (pip install name==version, npm install name@version).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
from pathlib import Path

TOOL = "scan.dependencies"
DB_NAME = "osv.sqlite"
_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
_REQ = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*([A-Za-z0-9.*+-]+)"
)
_NPM_PIN = re.compile(r"^((?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*)@v?(\d[0-9A-Za-z.+-]*)$", re.I)
_PIP_CMD = re.compile(r"^pip[0-9.]*$")
_ARG_DEFAULT = re.compile(r"^\s*(?:ARG|ENV)\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$", re.M)
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_PRE = re.compile(r"(a|b|c|rc|alpha|beta|pre|preview|dev|-)", re.I)


def _severity(raw: str) -> str:
    text = raw.strip().lower()
    if text in {"critical", "high", "medium", "low"}:
        return text
    if text in {"moderate", "warning"}:
        return "medium"
    if text in {"error", "important"}:
        return "high"
    return "low"


def _osv_severity(doc: dict, affected: dict) -> str:
    if str(doc.get("id") or "").startswith("MAL-"):
        return "critical"
    for source in (affected.get("database_specific") or {}, doc.get("database_specific") or {}):
        if isinstance(source, dict):
            value = source.get("severity")
            if isinstance(value, str) and value.strip():
                return _severity(value)
    return "high"


def normalize(ecosystem: str, name: str) -> str:
    text = name.strip().casefold()
    if ecosystem.casefold() == "pypi":
        return re.sub(r"[-_.]+", "-", text)
    return text


def advisory_dirs() -> list[Path]:
    here = Path(__file__).resolve().parent
    found: list[Path] = []
    local = here / "osv"
    if local.is_dir():
        found.append(local)
    raw = os.environ.get("RADIX_OSV_DIR", "").strip()
    if raw:
        extra = Path(raw)
        if extra.is_dir() and extra not in found:
            found.append(extra)
    for parent in here.parents:
        shared = parent / "agents" / "radix" / "scanlib" / "osv"
        if shared.is_dir() and shared not in found:
            found.append(shared)
            break
    return found


def parse_version(raw: str) -> tuple[int, ...] | None:
    text = raw.strip()
    if not text or text in {"*", "latest", "main", "master"}:
        return None
    if "," in text:
        return None
    text = text.lstrip("=<>~^v ")
    parts: list[int] = []
    for piece in text.split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
        if digits != piece:
            break
    return tuple(parts) if parts else None


def version_key(raw: str) -> tuple | None:
    """Comparable key: release numbers, then pre-release (0) < final (1) < post (2)."""
    release = parse_version(raw)
    if release is None:
        return None
    text = raw.strip().lstrip("=<>~^v ").split("+", 1)[0]
    head = ".".join(str(part) for part in release)
    rest = text[len(head):] if text.startswith(head) else ""
    stage = 1
    if rest:
        lowered = rest.lower().lstrip(".")
        if lowered.startswith("post") or lowered.startswith("-post"):
            stage = 2
        elif _PRE.match(lowered):
            stage = 0
    padded = release + (0,) * (8 - len(release)) if len(release) < 8 else release
    return (padded, stage, rest.lower())


def _in_range(version: str | tuple[int, ...], events: list[dict]) -> tuple[bool, str]:
    """OSV evaluation: walk events in version order; introduced opens, fixed/last_affected close."""
    if isinstance(version, tuple):
        version = ".".join(str(part) for part in version)
    mine = version_key(version)
    if mine is None:
        return False, ""
    ordered: list[tuple[tuple, int, str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        for kind, rank in (("introduced", 0), ("fixed", 1), ("last_affected", 2)):
            if kind in event:
                value = str(event.get(kind) or "0")
                key = (((0,) * 8, -1, "") if value == "0" else version_key(value))
                if key is not None:
                    ordered.append((key, rank, kind, value))
    ordered.sort(key=lambda item: (item[0], item[1]))
    affected = False
    fixed = ""
    for key, _rank, kind, value in ordered:
        if kind == "introduced" and mine >= key:
            affected = True
            fixed = ""
        elif kind == "fixed":
            if mine >= key:
                affected = False
            elif affected and not fixed:
                fixed = value
        elif kind == "last_affected" and mine > key:
            affected = False
    return affected, (fixed if affected else "")


def _row(doc: dict, affected: dict) -> dict | None:
    package = affected.get("package") or {}
    ecosystem = str(package.get("ecosystem") or "")
    name = normalize(ecosystem, str(package.get("name") or ""))
    advisory = str(doc.get("id") or "")
    if not name or not advisory or doc.get("withdrawn"):
        return None
    return {
        "ecosystem": ecosystem,
        "package": name,
        "advisory": advisory,
        "aliases": [str(item) for item in doc.get("aliases") or []],
        "severity": _osv_severity(doc, affected),
        "ranges": [
            item.get("events") or []
            for item in (affected.get("ranges") or [])
            if isinstance(item, dict) and item.get("type") in (None, "SEMVER", "ECOSYSTEM")
        ],
        "versions": [str(item) for item in affected.get("versions") or []],
    }


def load_advisories() -> list[dict]:
    """The OSV JSON files (small: fixtures and any extra JSON). osv.sqlite is read per package."""
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for folder in advisory_dirs():
        for path in sorted(folder.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            for affected in doc.get("affected") or []:
                if not isinstance(affected, dict):
                    continue
                row = _row(doc, affected)
                if row is None:
                    continue
                key = (row["ecosystem"].casefold(), row["package"], row["advisory"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    return rows


def databases() -> list[Path]:
    return [folder / DB_NAME for folder in advisory_dirs() if (folder / DB_NAME).is_file()]


def _from_databases(wanted: set[tuple[str, str]]) -> list[dict]:
    rows: list[dict] = []
    if not wanted:
        return rows
    for path in databases():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for ecosystem, name in sorted(wanted):
                cur = conn.execute(
                    "SELECT ecosystem, package, advisory, aliases, severity, ranges, versions "
                    "FROM advisories WHERE eco = ? AND package = ?",
                    (ecosystem.casefold(), name),
                )
                for eco, pkg, advisory, aliases, severity, ranges, versions in cur:
                    rows.append({
                        "ecosystem": eco, "package": pkg, "advisory": advisory,
                        "aliases": json.loads(aliases), "severity": severity,
                        "ranges": json.loads(ranges), "versions": json.loads(versions),
                    })
        finally:
            conn.close()
    return rows


def _match(ecosystem: str, package: str, version_text: str, advisories: list[dict]) -> list[dict]:
    clean = version_text.strip().lstrip("=<>~^v ")
    if parse_version(version_text) is None:
        return []
    want_eco = ecosystem.casefold()
    want_name = normalize(ecosystem, package)
    found: list[dict] = []
    for row in advisories:
        if row["ecosystem"].casefold() != want_eco or row["package"] != want_name:
            continue
        hit, fixed = False, ""
        if clean in row.get("versions", []):
            hit = True
        for events in row["ranges"]:
            in_range, at = _in_range(clean, events)
            if in_range:
                hit, fixed = True, at
                break
        if hit:
            found.append(
                {
                    "package": want_name,
                    "version": clean,
                    "advisory": row["advisory"],
                    "aliases": row.get("aliases", []),
                    "severity": row["severity"],
                    "fixed": fixed,
                }
            )
    return found


def _packages_from_package_json(text: str) -> list[tuple[str, str, int]]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(doc, dict):
        return []
    rows: list[tuple[str, str, int]] = []
    lines = text.splitlines()
    for section in ("dependencies", "devDependencies"):
        block = doc.get(section) or {}
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            if not isinstance(spec, str):
                continue
            line_no = 1
            needle = f'"{name}"'
            for index, line in enumerate(lines, start=1):
                if needle in line:
                    line_no = index
                    break
            rows.append((str(name), spec, line_no))
    return rows


def _packages_from_lock(text: str) -> list[tuple[str, str, int]]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(doc, dict):
        return []
    rows: list[tuple[str, str, int]] = []
    packages = doc.get("packages")
    if isinstance(packages, dict):
        for key, meta in packages.items():
            if not isinstance(meta, dict) or not str(key).startswith("node_modules/"):
                continue
            version = meta.get("version")
            if isinstance(version, str):
                name = str(key).split("node_modules/")[-1]
                rows.append((name, version, 1))
    deps = doc.get("dependencies")
    if isinstance(deps, dict):
        for name, meta in deps.items():
            if isinstance(meta, dict) and isinstance(meta.get("version"), str):
                rows.append((str(name), str(meta["version"]), 1))
    return rows


def _packages_from_requirements(text: str) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue
        match = _REQ.match(line)
        if match is None:
            continue
        rows.append((match.group(1), match.group(2), lineno))
    return rows


def is_dockerfile(path: str) -> bool:
    name = Path(path).name.casefold()
    return name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile")


def _docker_runs(text: str) -> list[tuple[str, int]]:
    """RUN commands with backslash continuations joined, each with its first line number."""
    out: list[tuple[str, int]] = []
    buf: list[str] = []
    start = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not buf:
            if not line.upper().startswith("RUN "):
                continue
            start = lineno
            line = line[4:]
        if line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        out.append((" ".join(buf), start))
        buf = []
    if buf:
        out.append((" ".join(buf), start))
    return out


def _packages_from_dockerfile(text: str) -> list[tuple[str, str, str, int]]:
    """Pinned installs only: pip install name==version, npm/yarn/pnpm add name@version.

    ${NAME} is filled from an ARG or ENV default in the same file; anything else unresolved is skipped.
    """
    rows: list[tuple[str, str, str, int]] = []
    defaults: dict[str, str] = {}
    for match in _ARG_DEFAULT.finditer(text):
        defaults.setdefault(match.group(1), match.group(2).strip("\"'"))
    for command, lineno in _docker_runs(text):
        command = _VAR.sub(lambda m: defaults.get(m.group(1) or m.group(2), m.group(0)), command)
        for part in re.split(r"&&|\|\||;|\|", command):
            try:
                words = shlex.split(part)
            except ValueError:
                continue
            for index, word in enumerate(words[:-1]):
                tool = Path(word).name
                if words[index + 1] not in {"install", "i", "add"}:
                    continue
                if _PIP_CMD.match(tool):
                    pattern, ecosystem = _REQ, "PyPI"
                elif tool in {"npm", "yarn", "pnpm"}:
                    pattern, ecosystem = _NPM_PIN, "npm"
                else:
                    continue
                for arg in words[index + 2:]:
                    match = pattern.match(arg)
                    if match and "$" not in arg:
                        rows.append((ecosystem, match.group(1), match.group(2), lineno))
                break
    return rows


def _manifests(files: list[tuple[str, str]]) -> list[tuple[str, str, str, str, int]]:
    """Return ecosystem, package, version, file, line."""
    rows: list[tuple[str, str, str, str, int]] = []
    locks = {(path, text) for path, text in files if path.endswith("package-lock.json")}
    locked_names: set[str] = set()
    for path, text in locks:
        for name, version, line in _packages_from_lock(text):
            locked_names.add(name.casefold())
            rows.append(("npm", name, version, path, line))
    for path, text in files:
        if path.endswith("package-lock.json"):
            continue
        if path.endswith("package.json"):
            for name, spec, line in _packages_from_package_json(text):
                if name.casefold() in locked_names:
                    continue
                rows.append(("npm", name, spec, path, line))
        elif Path(path).name.startswith("requirements") and path.endswith(".txt"):
            for name, version, line in _packages_from_requirements(text):
                rows.append(("PyPI", name, version, path, line))
        elif is_dockerfile(path):
            for ecosystem, name, version, line in _packages_from_dockerfile(text):
                rows.append((ecosystem, name, version, path, line))
    return rows


def scan_files(files: list[tuple[str, str]]) -> list[dict]:
    manifests = _manifests(files)
    wanted = {(eco, normalize(eco, name)) for eco, name, _version, _path, _line in manifests}
    advisories = [
        row for row in load_advisories()
        if (row["ecosystem"], row["package"]) in wanted
    ] + _from_databases(wanted)
    findings: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for ecosystem, package, version, path, line in manifests:
        hits = _match(ecosystem, package, version, advisories)
        # GHSA first, so a PYSEC or CVE alias of the same advisory is not reported twice.
        hits.sort(key=lambda item: (not item["advisory"].startswith("GHSA-"), item["advisory"]))
        for hit in hits:
            names = {hit["advisory"], *hit["aliases"]}
            if any((path, hit["package"], name) in seen for name in names):
                continue
            seen.update((path, hit["package"], name) for name in names)
            fixed = hit["fixed"]
            findings.append(
                {
                    "tool": TOOL,
                    "severity": hit["severity"],
                    "file": path,
                    "line": line,
                    "rule": hit["advisory"],
                    "fix": fixed,
                    "package": hit["package"],
                    "version": hit["version"],
                    "advisory": hit["advisory"],
                }
            )
    findings.sort(key=lambda item: (item["file"], item["line"], item["rule"]))
    return findings


def iter_manifests(root: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        name = path.name
        if name not in {"package.json", "package-lock.json"} and not (
            name.startswith("requirements") and name.endswith(".txt")
        ) and not is_dockerfile(name):
            continue
        if path.stat().st_size > 2_000_000:
            continue
        rel = path.relative_to(root).as_posix()
        found.append((rel, path.read_text(encoding="utf-8", errors="replace")))
    return found


def scan_tree(root: Path) -> list[dict]:
    return scan_files(iter_manifests(root))


def _primary(findings: list[dict]) -> dict:
    if not findings:
        return {
            "package": "",
            "version": "",
            "advisory": "",
            "severity": "",
            "fixed": "",
            "critical": 0,
        }
    ranked = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    first = sorted(findings, key=lambda item: (ranked.get(item["severity"], 9), item["advisory"]))[0]
    critical = sum(1 for item in findings if item["severity"] == "critical")
    return {
        "package": first["package"],
        "version": first["version"],
        "advisory": first["advisory"],
        "severity": first["severity"],
        "fixed": first["fix"],
        "critical": critical,
    }


def run_skill(payload: dict) -> dict:
    files: list[tuple[str, str]] = []
    for item in payload.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        text = item.get("text")
        if path and isinstance(text, str):
            files.append((path, text))
    public = _primary(scan_files(files))
    public["leaked"] = False
    return public
