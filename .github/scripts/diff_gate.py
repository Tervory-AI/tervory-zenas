#!/usr/bin/env python3
"""Diff gate (founder, 6 Nov 2026): block bad diffs before they reach the merge queue.

Checks only what a pull request adds or changes against its merge-base with main:
  - type and lint suppressions (# noqa, # type: ignore, @ts-ignore, @ts-nocheck,
    @ts-expect-error, eslint-disable);
  - Python eval(), exec() and pickle.loads() calls (found with ast, so model.eval() is fine);
  - wildcard CORS and Pydantic extra="allow";
  - protected files: workflows, scanner configs, auth, seal checks, dependency
    manifests, and RLS/policy/grant lines in schema.sql;
  - container risks in Dockerfiles and compose files: privileged, Docker socket, host
    network or PID, added capabilities, unconfined profiles, host paths, and removed
    hardening (read_only, cap_drop, no-new-privileges);
  - weakened tests: fewer test functions or asserts in an existing test file, or a new
    skip, .only, or xfail without strict=True.

A protected-file change or a removed test passes only with FOUNDER_UNLOCK=1, which CI sets
when the founder (and nobody else) has applied the "founder-unlock" label. Nothing else
unlocks the gate. Warnings (file size, files touched, large rewrites) never block.

Usage: diff_gate.py [BASE] [HEAD]   (defaults: merge-base of origin/main and HEAD; HEAD)
Exit 0 = pass, 1 = blocked.
"""
from __future__ import annotations

import ast
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

CODE = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
SKIP_CONTENT = (".github/scripts/diff_gate.py", "tests/test_diff_gate.py")

SUPPRESSIONS = [
    (re.compile(r"#\s*noqa\b"), "lint suppression (# noqa)"),
    (re.compile(r"#\s*type:\s*ignore\b"), "type suppression (# type: ignore)"),
    (re.compile(r"@ts-(?:ignore|nocheck|expect-error)\b"), "TypeScript suppression"),
    (re.compile(r"eslint-disable"), "ESLint suppression"),
]
CORS = re.compile(r"""(allow_origins\s*=\s*\[\s*["']\*["']\s*\]|Access-Control-Allow-Origin["']?\s*[:,=]\s*["']\*["'])""")
EXTRA_ALLOW = re.compile(r"""extra\s*=\s*["']allow["']""")

PROTECTED = [
    ".github/workflows/*", ".gitleaks.toml", ".gitleaksignore", ".semgrep*", "**/.semgrep*",
    "**/auth.py", "auth/*", "**/auth/*", "ops/seal-check.sh", "tests/test_room_seal.py",
    ".github/scripts/diff_gate.py",
]
MANIFESTS = {"requirements.txt", "package.json", "package-lock.json", "pyproject.toml",
             "poetry.lock", "pnpm-lock.yaml", "yarn.lock", "uv.lock"}
SCHEMA_RISK = re.compile(r"ROW LEVEL SECURITY|\bPOLICY\b|\bGRANT\b|\bREVOKE\b", re.I)
CONTAINER_FILES = ["Dockerfile*", "**/Dockerfile*", "docker-compose*.yml", "**/docker-compose*.yml"]
CONTAINER_RISK = [
    (re.compile(r"privileged\s*:\s*true", re.I), "privileged container"),
    (re.compile(r"docker\.sock"), "Docker socket mounted"),
    (re.compile(r"network_mode\s*:\s*[\"']?host", re.I), "host network"),
    (re.compile(r"\bpid\s*:\s*[\"']?host", re.I), "host PID namespace"),
    (re.compile(r"^\s*cap_add\s*:", re.I), "added Linux capabilities"),
    (re.compile(r"unconfined", re.I), "security profile disabled"),
    (re.compile(r"^\s*-\s*[\"']?/(?:etc|root|home|proc|sys|var/lib/docker)(?:/|:)", re.I), "host path mounted"),
]
SKIP_ADDED = re.compile(r"pytest\.mark\.skip\b|pytest\.skip\(|\b(?:it|describe|test)\.only\(|\btest\.skip\(|\bit\.skip\(")
XFAIL = re.compile(r"pytest\.mark\.xfail\b|pytest\.xfail\(")


@dataclass
class Report:
    blocks: list[str] = field(default_factory=list)
    unlockable: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def show(rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def matches(path: str, patterns: list[str]) -> bool:
    p = PurePosixPath(path)
    return any(fnmatch.fnmatchcase(path, pat) or (pat.startswith("**/") and fnmatch.fnmatchcase(p.name, pat[3:]))
               for pat in patterns)


def is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in MANIFESTS or fnmatch.fnmatchcase(name, "requirements*.txt")


def is_test(path: str) -> bool:
    name = PurePosixPath(path).name
    if ".test." in name or ".spec." in name:
        return True
    return name.startswith("test_") and (name.endswith(".py") or path.startswith("tests/") or "/tests/" in path)


def added_lines(base: str, head: str, path: str) -> dict[int, str]:
    out: dict[int, str] = {}
    line = 0
    for raw in git("diff", "-U0", "--no-color", base, head, "--", path).splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if m:
            line = int(m.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            out[line] = raw[1:]
            line += 1
    return out


def removed_lines(base: str, head: str, path: str) -> list[str]:
    return [r[1:] for r in git("diff", "-U0", "--no-color", base, head, "--", path).splitlines()
            if r.startswith("-") and not r.startswith("---")]


def dangerous_calls(source: str, lines: set[int]) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.lineno not in lines:
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in {"eval", "exec"}:
            found.append((node.lineno, f"{f.id}()"))
        elif isinstance(f, ast.Attribute) and f.attr == "loads" and isinstance(f.value, ast.Name) and f.value.id == "pickle":
            found.append((node.lineno, "pickle.loads()"))
    return found


def count_tests(source: str | None, path: str) -> tuple[int, int]:
    if not source:
        return 0, 0
    if path.endswith(".py"):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return 0, 0
        tests = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"))
        asserts = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Assert))
        asserts += sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr.startswith("assert"))
        return tests, asserts
    tests = len(re.findall(r"\b(?:it|test)\s*\(", source))
    asserts = len(re.findall(r"\bexpect\s*\(|\bassert\w*\s*[.(]", source))
    return tests, asserts


def check(base: str, head: str) -> Report:
    rep = Report()
    status = git("diff", "--name-status", "--no-renames", base, head).splitlines()
    for row in status:
        code, path = row.split("\t", 1)
        rep.files.append(path)
        kind = code[0]
        if path.startswith("vendor/"):
            continue  # vendored copy of tervory-tank: checked in tervory-tank itself
        if matches(path, PROTECTED) or is_manifest(path):
            rep.unlockable.append(f"{path}: protected file {'deleted' if kind == 'D' else 'changed'}")
        if kind == "D":
            if is_test(path):
                rep.unlockable.append(f"{path}: test file deleted")
            continue
        if path in SKIP_CONTENT:
            continue
        added = added_lines(base, head, path)
        if PurePosixPath(path).name == "schema.sql":
            for n, text in added.items():
                if SCHEMA_RISK.search(text):
                    rep.unlockable.append(f"{path}:{n}: RLS, policy or grant line changed")
            for text in removed_lines(base, head, path):
                if SCHEMA_RISK.search(text):
                    rep.unlockable.append(f"{path}: RLS, policy or grant line removed")
                    break
        if matches(path, CONTAINER_FILES):
            for n, text in added.items():
                for rx, why in CONTAINER_RISK:
                    if rx.search(text):
                        rep.blocks.append(f"{path}:{n}: {why}")
            for text in removed_lines(base, head, path):
                if re.search(r"read_only\s*:\s*true|cap_drop|no-new-privileges", text):
                    rep.unlockable.append(f"{path}: container hardening line removed")
                    break
        if not path.endswith(CODE):
            continue
        for n, text in added.items():
            for rx, why in SUPPRESSIONS:
                if rx.search(text):
                    rep.blocks.append(f"{path}:{n}: {why}")
            if CORS.search(text):
                rep.blocks.append(f"{path}:{n}: wildcard CORS")
            if path.endswith(".py") and EXTRA_ALLOW.search(text):
                rep.blocks.append(f'{path}:{n}: Pydantic extra="allow"')
        new = show(head, path) or ""
        if path.endswith(".py") and not is_test(path):
            for n, call in dangerous_calls(new, set(added)):
                rep.blocks.append(f"{path}:{n}: {call} call")
        if is_test(path):
            for n, text in added.items():
                if SKIP_ADDED.search(text):
                    rep.blocks.append(f"{path}:{n}: test skipped or focused (.only)")
                if XFAIL.search(text) and "strict=True" not in text:
                    rep.blocks.append(f"{path}:{n}: xfail without strict=True")
            if kind == "M":
                t0, a0 = count_tests(show(base, path), path)
                t1, a1 = count_tests(new, path)
                if t1 < t0:
                    rep.unlockable.append(f"{path}: test functions {t0} -> {t1}")
                if a1 < a0:
                    rep.unlockable.append(f"{path}: asserts {a0} -> {a1}")
        lines = new.count("\n")
        if lines > 250 and kind == "A":
            rep.warnings.append(f"{path}: new file has {lines} lines (cap 250)")
        if kind == "M":
            old = show(base, path) or ""
            old_n = max(old.count("\n"), 1)
            gone = len(removed_lines(base, head, path))
            if old_n > 40 and gone / old_n > 0.6:
                rep.warnings.append(f"{path}: {gone} of {old_n} lines rewritten")
    if len(rep.files) > 25:
        rep.warnings.append(f"{len(rep.files)} files touched; consider smaller PRs")
    return rep


def receipt(rep: Report, unlocked: bool) -> tuple[str, int]:
    out = ["## Diff gate", f"Files touched: {len(rep.files)}"]
    touched = {"schema": any(p.endswith("schema.sql") for p in rep.files),
               "auth": any(matches(p, ["**/auth.py", "auth/*", "**/auth/*"]) for p in rep.files),
               "seal": any(p in {"ops/seal-check.sh", "tests/test_room_seal.py"} for p in rep.files)}
    out.append("Schema touched: {schema} · Auth touched: {auth} · Seal touched: {seal}".format(
        **{k: "yes" if v else "no" for k, v in touched.items()}))
    for b in rep.blocks:
        out.append(f"- BLOCK {b}")
    for u in rep.unlockable:
        out.append(f"- {'UNLOCKED' if unlocked else 'BLOCK (founder-unlock)'} {u}")
    for w in rep.warnings:
        out.append(f"- warn {w}")
    failed = bool(rep.blocks) or (bool(rep.unlockable) and not unlocked)
    out.append("Result: blocked" if failed else "Result: passed")
    return "\n".join(out), 1 if failed else 0


def main(argv: list[str]) -> int:
    head = argv[2] if len(argv) > 2 else "HEAD"
    base = argv[1] if len(argv) > 1 else git("merge-base", "origin/main", head).strip()
    rep = check(base, head)
    text, code = receipt(rep, os.environ.get("FOUNDER_UNLOCK") == "1")
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
