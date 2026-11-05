"""Reject mutable build/CI installs; report locations without printing commands."""

from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DIGEST = re.compile(r"@sha256:[a-f0-9]{64}$")
PYTHON_PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[^]]+\])?==[^\s,;*<>=]+(?:\s*;[^\n]+)?$")


def archive_requirement(requirement: str) -> bool:
    match = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\s+@\s+(\S+)", requirement)
    if not match:
        return False
    url = urlsplit(match[1])
    return (
        url.scheme in ("https", "file") and not url.username and not url.password
        and not url.query and not url.fragment
        and (bool(url.hostname) if url.scheme == "https" else not url.netloc and url.path.startswith("/"))
        and url.path.endswith((".whl", ".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".zip"))
    )


def logical_lines(text: str):
    pending, first = "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not pending:
            first = number
        if line.lstrip().startswith("#"):
            continue
        pending += line.strip().removesuffix("\\") + " "
        if line.rstrip().endswith("\\"):
            continue
        yield first, pending.strip()
        pending = ""
    if pending:
        yield first, pending.strip()


def lock_errors(text: str) -> list[tuple[int, str]]:
    errors = []
    for number, line in logical_lines(text):
        if not line:
            continue
        requirement = re.split(r"\s+--hash=", line, maxsplit=1)[0]
        hashes = re.findall(r"--hash=([^\s]+)", line)
        if not (PYTHON_PIN.fullmatch(requirement) or archive_requirement(requirement)) or not hashes or any(
            not re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in hashes
        ):
            errors.append((number, "Python lock entry needs an exact version and SHA256 hashes"))
    return errors


def enabled_flag(command: str, flag: str) -> bool:
    try:
        args = shlex.split(command, comments=True)
    except ValueError:
        return False
    values = [arg for arg in args if arg == flag or arg.startswith(flag + "=") or arg == "--no-" + flag[2:]]
    return bool(values) and values[-1] in {flag, flag + "=true"}


def install_errors(text: str) -> list[tuple[int, str]]:
    errors = []
    for number, line in logical_lines(text):
        if not line:
            continue
        # Commands may be chained in a Docker RUN or a YAML run block.
        for command in re.split(r"&&|;|\|\|", line):
            pip = re.search(
                r"(?:\bpython[\d.]*\s+-m\s+pip|\bpip3?)\s+(?:.*?\s+)?install\b(.*)", command,
            )
            if pip:
                try:
                    args = shlex.split(pip[1], comments=True)
                except ValueError:
                    errors.append((number, "Cannot verify Python install arguments"))
                    continue
                files = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg in ("-r", "--requirement")]
                files += [arg.split("=", 1)[1] for arg in args if arg.startswith("--requirement=")]
                files += [arg[2:] for arg in args if arg.startswith("-r") and arg != "-r"]
                if "--require-hashes" not in args or not files or any(
                    not path.endswith(".lock") or "$" in path for path in files
                ):
                    errors.append((number, "Python install must use --require-hashes and a requirements lock"))
            metadata_only = (re.search(r"\bnpm\b.*\bupdate\b", command)
                             and enabled_flag(command, "--package-lock-only") and enabled_flag(command, "--ignore-scripts"))
            if re.search(r"\bnpm\b.*\b(?:install|i|add|update|upgrade)\b", command) and not metadata_only:
                errors.append((number, "Node packages must be installed with npm ci from a committed lock"))
            if re.search(r"\b(?:yarn|pnpm)\s+(?:install|add|update)\b", command):
                errors.append((number, "Use the committed npm lock and npm ci"))
            if re.search(r"\bnpm\b.*\bci\b", command):
                if not enabled_flag(command, "--ignore-scripts"):
                    errors.append((number, "npm ci must disable lifecycle scripts with --ignore-scripts"))
    return errors


def python_command_errors(text: str) -> list[tuple[int, str]]:
    """Inspect literal subprocess/command-helper calls without executing Python."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [(1, "Cannot inspect Python command arguments")]
    errors = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in {"run", "check_call", "check_output", "Popen", "system", "command", "_run"}:
            continue
        argument = node.args[0]
        command = None
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            command = argument.value
        elif isinstance(argument, (ast.List, ast.Tuple)):
            command = " ".join(shlex.quote(value.value) if isinstance(value, ast.Constant) and isinstance(value.value, str)
                               else "$DYNAMIC" for value in argument.elts)
        if command:
            errors.extend((node.lineno, reason) for _, reason in install_errors(command))
    return errors


def referenced_lock_errors(root: Path, path: Path, paths: list[Path]) -> list[tuple[int, str]]:
    """A literal .lock argument must name a committed file (including Docker COPY renames)."""
    errors = []
    text = path.read_text()
    committed = {item.relative_to(root).as_posix() for item in paths if item.is_file()}
    aliases = {}
    if path.name.startswith("Dockerfile"):
        for _, line in logical_lines(text):
            if not re.match(r"COPY\s", line, re.I):
                continue
            try:
                args = [arg for arg in shlex.split(line)[1:] if not arg.startswith("--")]
            except ValueError:
                continue
            if len(args) < 2:
                continue
            for source in args[:-1]:
                if not source.endswith(".lock"):
                    continue
                found = any(name == source or name.endswith("/" + source) for name in committed)
                dest = args[-1]
                if dest.endswith("/") or len(args) > 2:
                    dest += ("" if dest.endswith("/") else "/") + Path(source).name
                aliases[dest] = found
                aliases[Path(dest).name] = found
    for number, line in logical_lines(text):
        if not re.search(r"\bpip3?\b.*\binstall\b", line):
            continue
        for reference in re.findall(r"(?:--requirement[= ]+|-r\s*)([^\s;&]+)", line):
            reference = reference.strip("\"'")
            if not reference.endswith(".lock") or "$" in reference:
                continue  # install_errors gives the more specific message.
            candidates = [root / reference, path.parent / reference]
            directories = re.findall(r"working-directory:\s*([\w./-]+)", text)
            candidates.extend(root / directory / reference for directory in directories)
            found = any(candidate.is_relative_to(root) and candidate.relative_to(root).as_posix() in committed for candidate in candidates)
            if not found and not aliases.get(reference):
                errors.append((number, "Python install references a lock not committed in this repository"))
    return errors


def docker_errors(text: str) -> list[tuple[int, str]]:
    errors, stages = [], set()
    for number, line in logical_lines(text):
        match = re.match(r"FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
        if not match:
            continue
        base, alias = match.groups()
        if base.lower() not in stages and base.lower() != "scratch" and not DIGEST.search(base):
            errors.append((number, "External Docker base image must be pinned by @sha256"))
        if alias:
            stages.add(alias.lower())
    return errors + install_errors(text)


def node_lock_errors(package: dict, lock: dict) -> list[str]:
    errors = []
    if lock.get("lockfileVersion", 0) < 2 or not isinstance(lock.get("packages"), dict):
        return ["Node lock must contain package versions and integrity hashes"]
    root = lock["packages"].get("", {})
    for group in ("dependencies", "devDependencies", "optionalDependencies"):
        if package.get(group, {}) != root.get(group, {}):
            errors.append("Node lock does not match package.json")
    for name, item in lock["packages"].items():
        if not name or item.get("link"):
            continue
        integrity = item.get("integrity", "")
        try:
            algorithm, digest = integrity.split("-", 1)
            size = {"sha256": 32, "sha384": 48, "sha512": 64}[algorithm]
            valid = len(base64.b64decode(digest, validate=True)) == size
        except (ValueError, KeyError):
            valid = False
        if not item.get("version") or not valid:
            errors.append(f"Node lock package {name} needs a version and integrity hash")
    return errors


def check(root: Path, paths: list[Path]) -> list[str]:
    errors = []
    for path in paths:
        relative = path.relative_to(root)
        if not path.is_file():
            continue
        if path.name.startswith("Dockerfile"):
            errors.extend(f"{relative}:{line}: {reason}" for line, reason in docker_errors(path.read_text()))
        elif ".github/workflows/" in relative.as_posix() and path.suffix in (".yml", ".yaml"):
            errors.extend(f"{relative}:{line}: {reason}" for line, reason in install_errors(path.read_text()))
        elif path.suffix == ".py":
            errors.extend(f"{relative}:{line}: {reason}" for line, reason in python_command_errors(path.read_text()))
        elif path.suffix == ".sh" or path.name == "Makefile":
            errors.extend(f"{relative}:{line}: {reason}" for line, reason in install_errors(path.read_text()))
        if path.name.startswith("Dockerfile") or path.suffix in {".sh", ".yml", ".yaml"} or path.name == "Makefile":
            errors.extend(f"{relative}:{line}: {reason}" for line, reason in referenced_lock_errors(root, path, paths))
        if "requirements" in path.name and path.suffix == ".lock":
            errors.extend(f"{relative}:{line}: {reason}" for line, reason in lock_errors(path.read_text()))
        elif "requirements" in path.name and path.suffix in (".txt", ".in"):
            if not path.with_suffix(".lock").is_file():
                errors.append(f"{relative}: missing adjacent requirements lock")
        if path.name == "package.json":
            try:
                for script in json.loads(path.read_text()).get("scripts", {}).values():
                    errors.extend(f"{relative}: package script: {reason}" for _, reason in install_errors(script))
            except (ValueError, AttributeError, TypeError):
                errors.append(f"{relative}: Cannot parse package scripts")
            lock = path.with_name("package-lock.json")
            if not lock.is_file():
                errors.append(f"{relative}: missing package-lock.json")
            else:
                try:
                    reasons = node_lock_errors(json.loads(path.read_text()), json.loads(lock.read_text()))
                except (ValueError, TypeError):
                    reasons = ["Cannot parse Node manifest/lock"]
                errors.extend(f"{relative}: {reason}" for reason in reasons)
    return errors


def signature(root: Path, error: str) -> tuple[str, str, str]:
    """Line shifts do not grandfather a new mutable command or a changed lock entry."""
    parts = error.split(":", 2)
    path = root / parts[0]
    if len(parts) == 3 and parts[1].isdigit():
        number, reason = int(parts[1]), parts[2].strip()
        line = dict(logical_lines(path.read_text())).get(number, "")
        if path.suffix == ".py":
            try:
                node = next(node for node in ast.walk(ast.parse(path.read_text()))
                            if isinstance(node, ast.Call) and node.lineno == number)
                line = ast.dump(node, include_attributes=False)
            except (SyntaxError, StopIteration):
                line = path.read_text()
    else:
        reason = ":".join(parts[1:]).strip()
        # Missing/stale manifests and invalid Node locks are whole-file violations.
        line = path.read_text()
        if path.name == "package.json" and path.with_name("package-lock.json").exists():
            line += path.with_name("package-lock.json").read_text()
    return parts[0], reason, hashlib.sha256(line.encode()).hexdigest()


def new_errors(root: Path, paths: list[Path], base: str) -> tuple[list[str], int]:
    """Compare with the trusted PR base, never a mutable allowlist in the PR."""
    base = subprocess.check_output(["git", "rev-parse", "--verify", base + "^{commit}"], cwd=root).decode().strip()
    with tempfile.TemporaryDirectory() as directory:
        baseline = Path(directory)
        names = subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", base], cwd=root).decode().split("\0")
        # Only copy files consumed by the policy, not application data or Git internals.
        selected = [name for name in names if name and policy_file(Path(name))]
        for name in selected:
            target = baseline / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(["git", "show", f"{base}:{name}"], cwd=root))
        previous = Counter(signature(baseline, error) for error in check(baseline, [baseline / name for name in selected]))
        errors = check(root, paths)
        introduced = []
        for error in errors:
            key = signature(root, error)
            if previous[key]:
                previous[key] -= 1
            else:
                introduced.append(error)
        return introduced, len(errors) - len(introduced)


def policy_file(path: Path) -> bool:
    return (path.name.startswith("Dockerfile") or path.name in {"Makefile", "package.json", "package-lock.json"}
            or path.suffix in {".sh", ".py"} or ("requirements" in path.name and path.suffix in {".lock", ".in", ".txt"})
            or (".github/workflows/" in path.as_posix() and path.suffix in {".yaml", ".yml"}))


def comparison_base(root: Path, advertised: str, pr_head: str = "", base_ref: str = "") -> str:
    """PR event base SHAs may be stale; verify the actual synthetic merge parents."""
    if not pr_head:
        return advertised
    if not re.fullmatch(r"[a-f0-9]{40}", pr_head) or not base_ref:
        raise ValueError("PR comparison needs an exact head and trusted base ref")
    parents = subprocess.check_output(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=root).decode().split()
    if len(parents) != 3 or parents[2] != pr_head:
        raise ValueError("PR pin gate must check the synthetic merge, not a head checkout")
    actual = parents[1]
    # Never let an untrusted parent or PR commit become inherited debt.
    for older, newer in ((advertised, actual), (actual, "refs/remotes/origin/" + base_ref)):
        subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=root, check=True)
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Reject new violations relative to the trusted PR base; omit for a full audit")
    parser.add_argument("--pr-head", default="", help="Verify the checked-out synthetic merge's head")
    parser.add_argument("--base-ref", default="", help="Trusted PR base branch, used to verify the merge parent")
    args = parser.parse_args()
    tracked = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode().split("\0")
    paths = [ROOT / name for name in sorted(set(tracked)) if name and policy_file(Path(name))]
    if args.base:
        base = comparison_base(ROOT, args.base, args.pr_head, args.base_ref)
        errors, inherited = new_errors(ROOT, paths, base)
        print(f"Existing violations owned by the dependency migration: {inherited}")
    else:
        errors = check(ROOT, paths)
    for error in errors:
        print(error)
    print(f"Install pin check: {len(errors)} {'new ' if args.base else ''}violation(s)")
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
