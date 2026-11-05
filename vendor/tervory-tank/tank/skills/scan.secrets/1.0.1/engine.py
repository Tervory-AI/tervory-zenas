"""scan.secrets — find credentials in code that was handed to Radix.

Gitleaks 8.28.0 runs when the image asks for system tools. Otherwise the same
fixture rule, plus a few well-known patterns, run in process. The secret text
is never returned, stored, or logged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

TOOL = "scan.secrets"
FIXTURE_RULE = "tervory-fixture-key"
FIXTURE_TYPE = "fixture-key"
_MAX_LINE = 5000
_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
_TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".env", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".txt", ".md", ".sh", ".rb", ".go", ".rs",
    ".java", ".xml", ".properties", ".tf", ".php", ".cs", ".sql",
}

_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (FIXTURE_RULE, FIXTURE_TYPE, re.compile(r"tvfy-example-fixture-key-[0-9]{4}")),
    ("aws-access-key", "aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-pat", "github-token", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("slack-token", "slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key", "private-key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
)


def _config_path() -> Path | None:
    here = Path(__file__).resolve().parent
    local = here / "gitleaks.toml"
    if local.is_file():
        return local
    for parent in here.parents:
        shared = parent / "agents" / "radix" / "scanlib" / "gitleaks.toml"
        if shared.is_file():
            return shared
    return None


def system_tools() -> bool:
    return os.environ.get("RADIX_USE_SYSTEM_TOOLS", "").strip().lower() in {"1", "true", "yes"}


def gitleaks_bin() -> str | None:
    if not system_tools():
        return None
    override = os.environ.get("GITLEAKS_BIN", "").strip()
    if override:
        return override
    return shutil.which("gitleaks")


def _tool_env() -> dict[str, str]:
    env = os.environ.copy()
    # A blackhole proxy so the scanner cannot leave the machine if it tries.
    env["http_proxy"] = "http://127.0.0.1:9"
    env["https_proxy"] = "http://127.0.0.1:9"
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    return env


def redact(value: str, secrets: list[str]) -> str:
    out = value
    for secret in secrets:
        if secret and secret != "REDACTED":
            out = out.replace(secret, "***")
    return out


def _relative(path: str, root: Path | None) -> str:
    if root is None:
        return path.replace("\\", "/")
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def _finding(path: str, line: int, rule: str, secret_type: str) -> dict:
    fix = "Remove the secret and rotate it."
    return {
        "tool": TOOL,
        "severity": "critical",
        "file": path,
        "line": line,
        "rule": rule,
        "type": secret_type,
        "fix": fix,
    }


def scan_files(files: list[tuple[str, str]]) -> tuple[list[dict], list[str]]:
    findings: list[dict] = []
    secrets: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for path, text in files:
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > _MAX_LINE:
                continue
            for rule, secret_type, pattern in _RULES:
                match = pattern.search(line)
                if match is None:
                    continue
                key = (path, lineno, rule)
                if key in seen:
                    continue
                seen.add(key)
                secrets.append(match.group(0))
                findings.append(_finding(path, lineno, rule, secret_type))
    findings.sort(key=lambda item: (item["file"], item["line"], item["rule"]))
    return findings, secrets


def iter_text_files(root: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in {".env", "Dockerfile"}:
            if "." in path.name:
                continue
        if path.stat().st_size > 1_000_000:
            continue
        data = path.read_bytes()
        if b"\0" in data[:1024]:
            continue
        rel = path.relative_to(root).as_posix()
        found.append((rel, data.decode("utf-8", errors="replace")))
    return found


def _from_gitleaks(report: list[dict], root: Path) -> tuple[list[dict], list[str]]:
    findings: list[dict] = []
    secrets: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for item in report:
        rule = str(item.get("RuleID") or "")
        if not rule:
            continue
        secret = str(item.get("Secret") or "")
        match = str(item.get("Match") or "")
        for value in (secret, match):
            if value and value != "REDACTED":
                secrets.append(value)
        rel = _relative(str(item.get("File") or ""), root)
        if any(part in _SKIP_PARTS for part in Path(rel).parts):
            continue
        line = int(item.get("StartLine") or 0)
        secret_type = FIXTURE_TYPE if rule == FIXTURE_RULE else rule
        key = (rel, line, rule)
        if key in seen:
            continue
        seen.add(key)
        findings.append(_finding(rel, line, rule, secret_type))
    findings.sort(key=lambda item: (item["file"], item["line"], item["rule"]))
    return findings, secrets


def _run_gitleaks(binary: str, root: Path) -> tuple[list[dict], list[str]] | None:
    config = _config_path()
    if config is None:
        return None
    report = root.parent / "gitleaks-report.json"
    argv = [
        binary,
        "detect",
        "--no-git",
        "--no-banner",
        "--redact",
        "--log-level",
        "error",
        "--source",
        str(root),
        "--config",
        str(config),
        "--report-format",
        "json",
        "--report-path",
        str(report),
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
        if completed.returncode not in (0, 1) or not report.is_file():
            return None
        raw = report.read_text(encoding="utf-8")
    finally:
        if report.exists():
            report.unlink()
    try:
        parsed = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return _from_gitleaks(parsed, root)


def scan_tree(root: Path) -> tuple[list[dict], list[str]]:
    binary = gitleaks_bin()
    if binary:
        found = _run_gitleaks(binary, root)
        if found is not None:
            return found
    return scan_files(iter_text_files(root))


def _primary(findings: list[dict]) -> dict:
    if not findings:
        return {"file": "", "line": 0, "rule": "", "type": "", "critical": 0}
    first = findings[0]
    critical = sum(1 for item in findings if item["severity"] == "critical")
    return {
        "file": first["file"],
        "line": first["line"],
        "rule": first["rule"],
        "type": first["type"],
        "critical": critical,
    }


def _leaked(public: dict, secrets: list[str]) -> bool:
    blob = json.dumps(public, sort_keys=True)
    return any(secret and secret != "REDACTED" and secret in blob for secret in secrets)


def run_skill(payload: dict) -> dict:
    files: list[tuple[str, str]] = []
    for item in payload.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        text = item.get("text")
        if path and isinstance(text, str):
            files.append((path, text))
    findings, secrets = scan_files(files)
    public = _primary(findings)
    public["leaked"] = _leaked(public, secrets)
    if public["leaked"]:
        blob = json.dumps(public)
        for secret in secrets:
            blob = redact(blob, [secret])
        public = json.loads(blob)
        public["leaked"] = True
    return public
