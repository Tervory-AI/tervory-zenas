"""Run the free scanners; adapt their reports to CI policy, never detect issues here."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def commit_sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise ValueError("Expected a full commit SHA")
    return git("rev-parse", "--verify", value + "^{commit}")


def trusted_main_ancestor(value: str) -> None:
    git("merge-base", "--is-ancestor", value, "refs/remotes/origin/main")



QUEUE_REF = re.compile(r"refs/heads/gh-readonly-queue/main/pr-[1-9][0-9]*-([a-f0-9]{40})")


def main_contains(value: str) -> bool:
    try:
        trusted_main_ancestor(value)
        return True
    except subprocess.CalledProcessError as error:
        if error.returncode != 1:
            raise
        return False


def verify_queue_base(group: dict, head: str, base: str) -> None:
    """Trust only fetched GitHub queue merges on a first-parent chain to main.

    A later queue entry can use an earlier, still speculative entry as its base.
    Being a descendant of main alone does not authenticate such a commit.
    """
    head_ref = group["head_ref"]
    match = QUEUE_REF.fullmatch(head_ref) if isinstance(head_ref, str) else None
    if not match:
        raise ValueError("Expected the event's GitHub queue ref")
    remote_ref = "refs/remotes/origin/" + head_ref.removeprefix("refs/heads/")
    if git("rev-parse", "--verify", remote_ref + "^{commit}") != head:
        raise ValueError("Queue ref must match the checked-out event head")
    refs = set()
    for row in git("for-each-ref", "--format=%(refname) %(objectname)",
                   "refs/remotes/origin/gh-readonly-queue/main/").splitlines():
        name, sha = row.split()
        queue = QUEUE_REF.fullmatch(name.replace("refs/remotes/origin/", "refs/heads/", 1))
        if queue:
            refs.add((sha, queue[1]))
    parents = git("rev-list", "--parents", "-n", "1", head).split()
    if len(parents) != 3 or parents[1] != match[1]:
        raise ValueError("Queue head must merge onto the base encoded by its ref")
    current, saw_base = head, False
    while not main_contains(current):
        parents = git("rev-list", "--parents", "-n", "1", current).split()
        if len(parents) != 3 or (current, parents[1]) not in refs:
            raise ValueError("Unverifiable speculative queue merge ancestry")
        saw_base = saw_base or current == base
        current = parents[1]
    if not saw_base:
        trusted_main_ancestor(base)


def event_comparison_base(event_name: str, event: dict) -> str | None:
    """Bind incremental scans to the checked-out event, never a moving fallback."""
    if not isinstance(event, dict):
        raise ValueError("Invalid event object")
    if event_name in {"workflow_dispatch", "schedule"}:
        return None  # Intended whole audits must not compare HEAD to origin/main.
    head = git("rev-parse", "HEAD")
    if event_name == "pull_request":
        pr = event["pull_request"]
        if pr["base"]["ref"] != "main":
            raise ValueError("PR must target trusted main")
        advertised = commit_sha(pr["base"]["sha"])
        expected_head = commit_sha(pr["head"]["sha"])
        parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
        if len(parents) != 3 or parents[0] != head or parents[2] != expected_head:
            raise ValueError("Expected the event PR's synthetic merge checkout")
        actual_base = parents[1]
        git("merge-base", "--is-ancestor", advertised, actual_base)
        trusted_main_ancestor(actual_base)
        return actual_base
    if event_name == "merge_group":
        group = event["merge_group"]
        if group["base_ref"] != "refs/heads/main" or commit_sha(group["head_sha"]) != head:
            raise ValueError("Expected the event's merge group on main")
        base = commit_sha(group["base_sha"])
    elif event_name == "push":
        if event["ref"] != "refs/heads/main" or commit_sha(event["after"]) != head:
            raise ValueError("Expected the event's main push checkout")
        if event["before"] == "0" * 40:
            return None
        base = commit_sha(event["before"])
    else:
        raise ValueError("Unsupported security event")
    if base == head:
        raise ValueError("Incremental event cannot use its own head as base")
    git("merge-base", "--is-ancestor", base, head)
    if event_name == "merge_group":
        verify_queue_base(group, head, base)
    else:
        trusted_main_ancestor(base)
    return base


def run(args: list[str], allowed: tuple[int, ...] = (0,)) -> int:
    # Scanner diagnostics can contain source text. Only sanitized JSON is retained.
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=900, check=False)
    if result.returncode not in allowed:
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode}); scan is incomplete")
    return result.returncode


def write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def annotation(kind: str, text: str, path: str = "", line: int | None = None) -> None:
    """Escape workflow command syntax; callers supply only permitted metadata."""
    def escape(value: str) -> str:
        return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    properties = []
    if path:
        properties.append("file=" + escape(path).replace(":", "%3A").replace(",", "%2C"))
    if line is not None:
        properties.append(f"line={int(line)}")
    suffix = " " + ",".join(properties) if properties else ""
    print(f"::{kind}{suffix}::{escape(text)}")


def finding_summary(tool: str, findings: list[dict]) -> None:
    """Put the complete metadata list first, ahead of GitHub's annotation cap."""
    if not findings:
        return
    lines = []
    if tool == "OSV":
        groups = {}
        for row in findings:
            key = (row["package"], row["version"], row["path"], row["ecosystem"])
            group = groups.setdefault(key, {"fixes": set(), "advisories": set()})
            group["fixes"].update(row["fixed_versions"] or ["unavailable"])
            group["advisories"].add(row["id"])
        for (package, version, path, _), group in sorted(groups.items()):
            lines.append(f"{package} {version} ({path}) -> {','.join(sorted(group['fixes']))}, "
                         f"{len(group['advisories'])} advisories")
    elif tool == "Semgrep":
        groups = {}
        for row in findings:
            groups.setdefault(row["rule"], set()).add((row["path"], row["line"]))
        for rule, locations in sorted(groups.items()):
            lines.append(f"{rule}: " + ", ".join(f"{path}:{line}" for path, line in sorted(locations)))
    elif tool == "Gitleaks":
        locations = {(row["RuleID"], row["File"], row["StartLine"]) for row in findings}
        lines = [f"{rule}: {path}:{line}" for rule, path, line in sorted(locations)]
    else:
        raise ValueError("Unknown summary tool")
    annotation("error", tool + "\n" + "\n".join(lines))


def semgrep_error(error: dict) -> dict:
    # Tagged error variants can carry skipped source tokens. Retain only the tag.
    kind = error.get("type", "UnknownError")
    if isinstance(kind, list):
        kind = kind[0] if kind else "UnknownError"
    if not isinstance(kind, str):
        kind = "UnknownError"
    path = error.get("path") or ""
    if not isinstance(path, str):
        path = ""
    # Semgrep 1.182.1 represents registry download failures as SemgrepError.
    # Inspect its stable prefix for classification, but never retain/emit the message.
    download_failed = str(error.get("message", "")).lower().startswith(
        "failed to download configuration")
    fatal = (str(error.get("level", "")).lower() == "fatal"
             or kind in {"Fatal error", "FatalError", "RuleDownloadError", "ConfigDownloadError"}
             or download_failed)
    return {"type": kind, "path": path, "fatal": fatal}


def leaks(base: str | None, temp: Path, output: Path) -> bool:
    # PR #374: Claude-reviewed fingerprints and one exact public CI dummy value.
    config = Path(".gitleaks.toml").resolve()
    ignore = Path(".gitleaksignore").resolve()
    raw = temp / "gitleaks.json"
    code = run(["gitleaks", "git", ".", "--no-banner", "--redact=100",
                "--config", str(config), "--gitleaks-ignore-path", str(ignore),
                "--ignore-gitleaks-allow", "--log-opts", f"{base}..HEAD" if base else "HEAD",
                "--report-format", "json", "--report-path", str(raw)], (0, 1))
    findings = json.loads(raw.read_text())
    if not isinstance(findings, list):
        raise ValueError("Invalid Gitleaks report")
    # Do not retain matches, secrets, commit messages, authors or email addresses.
    safe = [{k: row[k] for k in ("RuleID", "File", "StartLine", "Commit")}
            for row in findings]
    write(output / "gitleaks.json", safe)
    finding_summary("Gitleaks", safe)
    for finding in safe:
        annotation("error", finding["RuleID"], finding["File"], finding["StartLine"])
    return code == 1 or bool(safe)


def semgrep(base: str | None, temp: Path, output: Path) -> bool:
    raw = temp / "semgrep.json"
    # The pinned CLI rejects --config auto with metrics off before writing JSON.
    args = ["semgrep", "scan", "--config", "auto", "--metrics", "auto",
            "--disable-version-check", "--disable-nosem", "--no-git-ignore",
            "--json", "--output", str(raw)]
    if base:
        args += ["--baseline-commit", base]
    missing_report = False
    try:
        # Read findings/errors even when the CLI returns nonzero. In particular,
        # exit 3 denotes target parse errors, which are notices under this policy.
        code = run([*args, "."], tuple(range(256)))
        if not raw.is_file():
            missing_report = True
            annotation("notice", f"SemgrepReportMissingExit{code}", str(raw))
            raise RuntimeError("Semgrep did not create its JSON report")
        report = json.loads(raw.read_text())
    except FileNotFoundError as error:
        annotation("notice", "SemgrepExecutableMissing", error.filename or "semgrep")
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # A missing-report notice above already identifies the path and exit code.
        if not missing_report:
            annotation("notice", type(error).__name__)
        raise
    findings = [{"rule": row["check_id"], "path": row["path"],
                 "line": row["start"]["line"], "severity": row["extra"]["severity"]}
                for row in report["results"]]
    errors = [semgrep_error(error) for error in report["errors"]]
    # Fatal process/configuration exits and a missing report still fail closed.
    # 0 = success, 1 = findings, 3 = target parsing errors in the pinned CLI.
    if (code not in (0, 1, 3) or (code == 3 and not errors)) and not any(e["fatal"] for e in errors):
        errors.append({"type": "SemgrepProcessError", "path": "", "fatal": True})
    fatal_count = sum(error["fatal"] for error in errors)
    write(output / "semgrep.json", {"findings": findings, "errors": errors,
                                    "error_count": len(errors), "fatal_error_count": fatal_count,
                                    "non_fatal_error_count": len(errors) - fatal_count,
                                    "exit_code": code})
    finding_summary("Semgrep", findings)
    for finding in findings:
        annotation("error", f"{finding['rule']} {finding['severity']}",
                   finding["path"], finding["line"])
    for error in errors:
        annotation("notice", error["type"], error["path"])
    if fatal_count:
        raise RuntimeError("Semgrep scan is incomplete")
    # ERROR is the legacy spelling of HIGH; non-fatal notices do not alter this gate.
    return any(row["severity"] in {"ERROR", "HIGH", "CRITICAL"} for row in findings)


def lockfiles(ref: str) -> list[str]:
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "-z", ref])
    return [path for path in paths.decode().split("\0") if path and
            (Path(path).name == "package-lock.json" or
             fnmatch.fnmatch(Path(path).name, "*requirements*.txt") or
             fnmatch.fnmatch(Path(path).name, "*requirements*.lock"))]


def osv_report(ref: str, temp: Path) -> tuple[dict, list[str], Path]:
    root = temp / ref
    root.mkdir()
    paths = lockfiles(ref)
    if not paths:
        raise ValueError("No dependency inputs found; scan is incomplete")
    args = ["osv-scanner", "scan", "source", "--no-resolve", "--all-packages",
            "--all-vulns", "--no-ignore", "--format", "json"]
    config = temp / "osv.toml"
    config.write_text("")  # Do not allow a repo config to suppress vulnerabilities.
    raw = temp / f"osv-{ref}.json"
    args += ["--config", str(config), "--output-file", str(raw)]
    for name in paths:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output(["git", "show", f"{ref}:{name}"]))
        kind = "package-lock.json" if target.name == "package-lock.json" else "requirements.txt"
        args += ["--lockfile", f"{kind}:{target}"]
    run(args, (0, 1))  # 1 means findings; all infrastructure/parse failures block.
    report = json.loads(raw.read_text())
    if "results" not in report or not isinstance(report["results"], (list, type(None))):
        raise ValueError("Invalid OSV report")
    return report, paths, root


def dependency_line(path: Path, package: dict) -> int:
    """Locate the reported pin without retaining or printing file contents."""
    text = path.read_text()
    if path.name == "package-lock.json":
        # OSV's JSON omits line numbers. Locate the matching package object in
        # npm v1 dependencies or v2/v3 packages, including nested installations.
        decoder = json.JSONDecoder()
        for match in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:\s*(?=\{)', text):
            key = json.loads('"' + match[1] + '"')
            entry, _ = decoder.raw_decode(text, match.end())
            name = entry.get("name", key.rsplit("node_modules/", 1)[-1])
            if name == package["name"] and entry.get("version") == package["version"]:
                return text.count("\n", 0, match.start()) + 1
    else:
        # PEP 503 canonical names; extras and hashes do not change the pin's line.
        normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
        for number, line in enumerate(text.splitlines(), 1):
            match = re.match(r"\s*([A-Za-z0-9_.-]+)(?:\[[^]]+\])?\s*===?\s*([^\s;\\#]+)", line)
            if (match and normalize(match[1]) == normalize(package["name"])
                    and match[2] == package["version"]):
                return number
    # Some manifest entries are ranges, not exact pins. Anchor those at the file.
    return 1


def osv_fixed_versions(vuln: dict, package: dict) -> list[str]:
    # These are OSV's declared fixes, not a replacement vulnerability matcher.
    # Keep all release branches; never claim that a lexically first fix is safe.
    def name(value: str) -> str:
        return re.sub(r"[-_.]+", "-", value).lower() if package["ecosystem"] == "PyPI" else value

    return sorted({event["fixed"] for affected in vuln.get("affected", [])
                   if name(affected.get("package", {}).get("name", "")) == name(package["name"])
                   and affected.get("package", {}).get("ecosystem") == package["ecosystem"]
                   for affected_range in affected.get("ranges", [])
                   if affected_range.get("type") != "GIT"
                   for event in affected_range.get("events", []) if event.get("fixed")})


def osv_findings(report: dict, root: Path) -> dict[tuple[str, ...], dict]:
    findings = {}
    for source in report["results"] or []:
        path = str(Path(source["source"]["path"]).relative_to(root))
        for item in source["packages"]:
            package = item["package"]
            for vuln in item.get("vulnerabilities", []):
                key = (path, package["ecosystem"], package["name"], package["version"], vuln["id"])
                # OSV computes CVSS. Do not implement our own score calculator.
                scores = [group["max_severity"] for group in item.get("groups", [])
                          if vuln["id"] in group["ids"] and group.get("max_severity")]
                score = max((float(s) for s in scores), default=None)
                findings[key] = dict(zip(("path", "ecosystem", "package", "version", "id"), key))
                findings[key]["cvss"] = score
                findings[key]["line"] = dependency_line(root / path, package)
                findings[key]["fixed_versions"] = osv_fixed_versions(vuln, package)
    return findings


def osv(base: str | None, temp: Path, output: Path) -> bool:
    head = git("rev-parse", "HEAD")
    report, paths, root = osv_report(head, temp)
    current = osv_findings(report, root)
    previous = {}
    if base and base != head:
        old, _, old_root = osv_report(base, temp)
        previous = osv_findings(old, old_root)
    elif base:
        previous = current
    new = [row for key, row in current.items() if key not in previous]
    write(output / "osv.json", {"lockfiles": paths, "findings": new,
                                "total_findings": len(current), "baseline": base})
    finding_summary("OSV", new)
    for finding in new:
        fixes = ",".join(finding["fixed_versions"]) or "unavailable"
        annotation("error", f"{finding['package']} {finding['version']} {finding['id']} fixed-in {fixes}",
                   finding["path"], finding["line"])
    # PR/push: every newly introduced vulnerable version blocks, at any severity.
    # Weekly: high/critical (CVSS >= 7); unknown scores also block for review.
    return bool(new) if base else any(row["cvss"] is None or row["cvss"] >= 7 for row in new)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=("gitleaks", "semgrep", "osv"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--base", help="Explicit comparison commit; omit for whole audit")
    selection.add_argument("--event-context", action="store_true", help="Verify and select the GitHub event's comparison base")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    status = {"tool": args.tool, "head": git("rev-parse", "HEAD"), "base": None}
    try:
        base = None
        if args.event_context:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            base = event_comparison_base(os.environ["GITHUB_EVENT_NAME"], event)
        elif args.base and set(args.base) != {"0"}:
            base = git("rev-parse", "--verify", f"{args.base}^{{commit}}")
        status["base"] = base
        # A new-main push with an all-zero before SHA gets a full scan.
        with tempfile.TemporaryDirectory(prefix="security-", dir=args.output.parent) as directory:
            failed = {"gitleaks": leaks, "semgrep": semgrep, "osv": osv}[args.tool](
                base, Path(directory), args.output)
        status["result"] = "findings" if failed else "clean"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        # No exception text: a tool/library error can echo source or credentials.
        status["result"] = "incomplete"
        status["error_type"] = type(error).__name__
        failed = True
    write(args.output / f"{args.tool}-status.json", status)
    print(f"{args.tool}: {status['result']}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
