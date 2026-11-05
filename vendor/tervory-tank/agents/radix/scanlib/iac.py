"""scan.iac — Checkov's bundled IaC checks, without downloads or severity guesses."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TOOL = "scan.iac"
_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}


class Findings(list):
    """Keep execution evidence beside findings, including an unavailable binary."""

    def __init__(self, rows=(), *, status="not run"):
        super().__init__(rows)
        self.status = status


def checkov_bin() -> str | None:
    if os.environ.get("RADIX_USE_SYSTEM_TOOLS", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    override = os.environ.get("CHECKOV_BIN", "").strip()
    if override:
        return override
    vendored = Path("/opt/checkov/bin/checkov")
    return str(vendored) if vendored.is_file() else shutil.which("checkov")


def _tool_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env[name] = "http://127.0.0.1:9"
    env.update(NO_PROXY="", no_proxy="", SEMGREP_SEND_METRICS="off", SEMGREP_ENABLE_VERSION_CHECK="0")
    # Never allow inherited platform settings to enable a cloud integration.
    for name in list(env):
        if name.startswith(("BC_", "PRISMA_", "CHECKOV_")):
            env.pop(name)
    env.update(CKV_SKIP_UPDATE_CHECK="1", ENABLE_MODULES_FOREACH="false")
    return env


def _reports(parsed) -> list[dict]:
    reports = parsed if isinstance(parsed, list) else [parsed]
    if not reports or any(not isinstance(report, dict) or not isinstance(report.get("results"), dict)
                          for report in reports):
        raise ValueError("invalid Checkov report")
    return reports


def scan_tree(root: Path) -> Findings:
    binary = checkov_bin()
    if not binary:
        return Findings()
    root = root.resolve()
    # A handed-over .checkov.yml must not enable downloads or load external Python checks.
    with tempfile.TemporaryDirectory(prefix="radix-checkov-") as work:
        config = Path(work) / "config.yml"
        config.write_text("{}\n", encoding="utf-8")
        argv = [binary, "--directory", str(root), "--config-file", str(config),
                "--skip-download", "--download-external-modules", "false",
                "--skip-framework", "sca_package", "sca_image", "secrets",
                "--output", "json", "--quiet", "--compact"]
        for part in sorted(_SKIP_PARTS):
            argv.extend(["--skip-path", r"(^|/)" + part.replace(".", r"\.") + r"(/|$)"])
        try:
            completed = subprocess.run(argv, capture_output=True, timeout=120,
                                       env=_tool_env(), cwd=work, check=False)
            if completed.returncode not in (0, 1):
                return Findings()
            reports = _reports(json.loads(completed.stdout))
            rows = []
            for report in reports:
                results = report["results"]
                if results.get("parsing_errors"):
                    return Findings()
                for item in results.get("failed_checks", []):
                    path = str(item.get("file_abs_path") or "")
                    if path:
                        rel = Path(path).resolve().relative_to(root).as_posix()
                    else:
                        rel = str(item["file_path"]).lstrip("/")
                    if ".." in Path(rel).parts or any(part in _SKIP_PARTS for part in Path(rel).parts):
                        continue
                    lines = item.get("file_line_range") or [0]
                    rows.append({"tool": TOOL, "severity": "unknown", "file": rel,
                                 "line": int(lines[0]), "rule": str(item["check_id"]),
                                 "fix": str(item.get("check_name") or "")})
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
            return Findings()
    rows.sort(key=lambda row: (row["file"], row["line"], row["rule"]))
    return Findings(rows, status="run")


def run_skill(payload: dict) -> dict:
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list) or not files or len(files) > 20:
        return {"refused": True, "status": "not run", "findings": [], "critical": 0}
    with tempfile.TemporaryDirectory(prefix="radix-iac-eval-") as work:
        root = Path(work) / "tree"
        root.mkdir()
        for item in files:
            if not isinstance(item, dict):
                return {"refused": True, "status": "not run", "findings": [], "critical": 0}
            path, text = item.get("path"), item.get("text")
            if (not isinstance(path, str) or not path or Path(path).is_absolute()
                    or ".." in Path(path).parts or not isinstance(text, str) or len(text) > 200_000):
                return {"refused": True, "status": "not run", "findings": [], "critical": 0}
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        found = scan_tree(root)
    first = found[0] if found else {}
    return {"status": found.status, "findings": list(found), "rules": sorted({row["rule"] for row in found}),
            "file": first.get("file", ""), "line": first.get("line", 0), "rule": first.get("rule", ""),
            "severity": first.get("severity", ""), "critical": 0}
