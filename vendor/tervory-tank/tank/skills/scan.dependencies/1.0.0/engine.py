"""scan.dependencies — offline advisory match for npm and Python packages.

The database is an OSV export baked into the image. pip-audit and npm audit
call a live service, so a scan does not run them.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

TOOL = "scan.dependencies"
_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
_REQ = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*([A-Za-z0-9.*+-]+)"
)


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
    for source in (affected.get("database_specific") or {}, doc.get("database_specific") or {}):
        if isinstance(source, dict):
            value = source.get("severity")
            if isinstance(value, str) and value.strip():
                return _severity(value)
    return "high"


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
    text = text.lstrip("=<>~^ ")
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
    return tuple(parts) if parts else None


def _lt(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    width = max(len(left), len(right))
    a = left + (0,) * (width - len(left))
    b = right + (0,) * (width - len(right))
    return a < b


def _in_range(version: tuple[int, ...], events: list[dict]) -> tuple[bool, str]:
    introduced: tuple[int, ...] | None = None
    fixed = ""
    fixed_version: tuple[int, ...] | None = None
    for event in events:
        if "introduced" in event and introduced is None:
            introduced = parse_version(str(event.get("introduced") or "0"))
        if "fixed" in event and fixed_version is None:
            fixed = str(event.get("fixed") or "")
            fixed_version = parse_version(fixed)
    if introduced is not None and _lt(version, introduced):
        return False, ""
    if fixed_version is not None and not _lt(version, fixed_version):
        return False, ""
    return True, fixed


def load_advisories() -> list[dict]:
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
            advisory = str(doc.get("id") or "")
            for affected in doc.get("affected") or []:
                if not isinstance(affected, dict):
                    continue
                package = affected.get("package") or {}
                name = str(package.get("name") or "").casefold()
                ecosystem = str(package.get("ecosystem") or "")
                key = (ecosystem.casefold(), name, advisory)
                if not name or not advisory or key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "ecosystem": ecosystem,
                        "package": name,
                        "advisory": advisory,
                        "severity": _osv_severity(doc, affected),
                        "ranges": [
                            item.get("events") or []
                            for item in (affected.get("ranges") or [])
                            if isinstance(item, dict)
                        ],
                    }
                )
    return rows


def _match(ecosystem: str, package: str, version_text: str, advisories: list[dict]) -> list[dict]:
    version = parse_version(version_text)
    if version is None:
        return []
    want_eco = ecosystem.casefold()
    want_name = package.casefold()
    found: list[dict] = []
    for row in advisories:
        if row["ecosystem"].casefold() != want_eco or row["package"] != want_name:
            continue
        for events in row["ranges"]:
            hit, fixed = _in_range(version, events)
            if hit:
                found.append(
                    {
                        "package": want_name,
                        "version": version_text.lstrip("=<>~^ "),
                        "advisory": row["advisory"],
                        "severity": row["severity"],
                        "fixed": fixed,
                    }
                )
                break
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
    return rows


def scan_files(files: list[tuple[str, str]]) -> list[dict]:
    advisories = load_advisories()
    findings: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for ecosystem, package, version, path, line in _manifests(files):
        for hit in _match(ecosystem, package, version, advisories):
            key = (path, hit["package"], hit["advisory"])
            if key in seen:
                continue
            seen.add(key)
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
        ):
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
