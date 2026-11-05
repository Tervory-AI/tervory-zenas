"""scan.static — injection patterns, Semgrep when the image provides it.

Rule packs are vendored. A scan does not download rules.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

TOOL = "scan.static"
_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
_MAX_LINE = 5000

_RULES: tuple[tuple[str, str, str, tuple[str, ...], re.Pattern[str]], ...] = (
    (
        "python-sql-format",
        "critical",
        "SQL is built with string formatting.",
        (".py",),
        re.compile(r"""\.execute\(\s*(?:f["']|["'][^"'\n]*["']\s*(?:%|\+|\.format\())"""),
    ),
    (
        "javascript-sql-concat",
        "critical",
        "SQL is built by joining strings.",
        (".js", ".ts", ".tsx", ".jsx"),
        re.compile(r"""(?:query|execute)\(\s*(?:`[^`\n]*\$\{|["'][^"'\n]*["']\s*\+)"""),
    ),
)
_SEMGREP_SEVERITY = {"ERROR": "critical", "WARNING": "high", "INFO": "low"}


def _rules_path() -> Path | None:
    here = Path(__file__).resolve().parent
    local = here / "semgrep.yml"
    if local.is_file():
        return local
    for parent in here.parents:
        shared = parent / "agents" / "radix" / "scanlib" / "semgrep.yml"
        if shared.is_file():
            return shared
    return None


def system_tools() -> bool:
    return os.environ.get("RADIX_USE_SYSTEM_TOOLS", "").strip().lower() in {"1", "true", "yes"}


def semgrep_bin() -> str | None:
    if not system_tools():
        return None
    override = os.environ.get("SEMGREP_BIN", "").strip()
    if override:
        return override
    vendored = Path("/opt/semgrep/bin/semgrep")
    if vendored.is_file():
        return str(vendored)
    return shutil.which("semgrep")


def _tool_env() -> dict[str, str]:
    env = os.environ.copy()
    env["http_proxy"] = "http://127.0.0.1:9"
    env["https_proxy"] = "http://127.0.0.1:9"
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    env["SEMGREP_SEND_METRICS"] = "off"
    env["SEMGREP_ENABLE_VERSION_CHECK"] = "0"
    return env


def _suffix(path: str) -> str:
    return Path(path).suffix.lower()


def scan_files(files: list[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    seen: set[tuple[str, int, str]] = set()
    for path, text in files:
        suffix = _suffix(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > _MAX_LINE:
                continue
            for rule, severity, message, suffixes, pattern in _RULES:
                if suffix not in suffixes or pattern.search(line) is None:
                    continue
                key = (path, lineno, rule)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "tool": TOOL,
                        "severity": severity,
                        "file": path,
                        "line": lineno,
                        "rule": rule,
                        "fix": message,
                    }
                )
    findings.sort(key=lambda item: (item["file"], item["line"], item["rule"]))
    return findings


def iter_source_files(root: Path) -> list[tuple[str, str]]:
    suffixes = {suffix for _rule, _sev, _msg, items, _pat in _RULES for suffix in items}
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.stat().st_size > 1_000_000:
            continue
        rel = path.relative_to(root).as_posix()
        found.append((rel, path.read_text(encoding="utf-8", errors="replace")))
    return found


def _relative(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def _from_semgrep(report: dict, root: Path) -> list[dict]:
    findings: list[dict] = []
    seen: set[tuple[str, int, str]] = set()
    for item in report.get("results") or []:
        if not isinstance(item, dict):
            continue
        check = str(item.get("check_id") or "")
        rule = check.split(".")[-1]
        extra = item.get("extra") or {}
        severity = _SEMGREP_SEVERITY.get(str(extra.get("severity") or "").upper(), "high")
        start = item.get("start") or {}
        line = int(start.get("line") or 0)
        rel = _relative(str(item.get("path") or ""), root)
        message = str(extra.get("message") or "Static analysis finding.")
        key = (rel, line, rule)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            {
                "tool": TOOL,
                "severity": severity,
                "file": rel,
                "line": line,
                "rule": rule,
                "fix": message,
            }
        )
    findings.sort(key=lambda item: (item["file"], item["line"], item["rule"]))
    return findings


def _run_semgrep(binary: str, root: Path) -> list[dict] | None:
    rules = _rules_path()
    if rules is None:
        return None
    report = root.parent / "semgrep-report.json"
    argv = [
        binary,
        "scan",
        "--config",
        str(rules),
        "--json",
        "--quiet",
        "--metrics=off",
        "--disable-version-check",
        "--no-git-ignore",
        str(root),
    ]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=60,
            env=_tool_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        if completed.returncode not in (0, 1):
            return None
        raw = report.read_text(encoding="utf-8") if report.is_file() else completed.stdout.decode("utf-8", errors="replace")
    finally:
        if report.exists():
            report.unlink()
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return _from_semgrep(parsed, root)


def scan_tree(root: Path) -> list[dict]:
    binary = semgrep_bin()
    if binary:
        found = _run_semgrep(binary, root)
        if found is not None:
            return found
    return scan_files(iter_source_files(root))


def _primary(findings: list[dict]) -> dict:
    if not findings:
        return {"file": "", "line": 0, "rule": "", "severity": "", "critical": 0}
    first = findings[0]
    critical = sum(1 for item in findings if item["severity"] == "critical")
    return {
        "file": first["file"],
        "line": first["line"],
        "rule": first["rule"],
        "severity": first["severity"],
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
