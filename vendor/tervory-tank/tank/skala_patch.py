"""Patch releases keep their code.

A patch release (x.y.Z with Z > 0) may only change evals, docs and its version
number. Anything that can change what the skill does needs a new minor version
(x.Y.0). The approval gate (skala.approval_reason) and the repository test
(tests/test_skala_patch_code_unchanged.py) both use this module, so they cannot
disagree about what "code" is.

What counts as code: every file in the version folder, compared byte for byte,
except
  - evals/**            (the cases are what a patch is for; the gate checks them),
  - *.md                (CHANGELOG, BUNDLE and other notes are not read at run time),
  - __pycache__/**, *.pyc (build output),
and skill.yaml is compared with its version line removed.

Data files count as code. scan.dependencies reads osv/*.json advisories,
zenas.scope reads areas.json and policy.sql, ffl.profile reads data/*,
trigger.* read rules.zip. Changing them changes the skill's answers exactly as
a .py change would, and package_hash (the eval evidence hash) covers only .py
files, so without this rule a patch could change behaviour while its old
evidence still matched.
"""

from __future__ import annotations

import re
from pathlib import Path

_RELEASE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_VERSION_LINE = re.compile(r'(?m)^\s*"?version"?\s*:.*$')

# Patch releases that changed code before this rule existed. Each entry must
# still differ in exactly these files, so an entry cannot hide a later change.
KNOWN: dict[tuple[str, str], frozenset[str]] = {
    # Dropped an unused "import re"; behaviour is the same. Approved before the rule.
    ("base.socials", "1.0.1"): frozenset({"run.py"}),
    # Moved the bundled OSV advisories out of the package (1.0.1 reads none).
    ("scan.dependencies", "1.0.1"): frozenset({"osv/GHSA-8q59-q68h-6hv4.json",
                                              "osv/GHSA-xvch-5gv4-984h.json"}),
    # Changed detect.py in a patch. Approved on Boston on 31 Oct 2026 before
    # the rule; re-released unchanged as 1.2.0 (see SUPERSEDED).
    ("zenas.pii", "1.1.2"): frozenset({"detect.py"}),
}

# Known exceptions that must not be approved anywhere new: approve the
# correctly numbered release instead. Rows already approved keep their state;
# the gate only decides new approvals.
SUPERSEDED: dict[tuple[str, str], str] = {
    ("zenas.pii", "1.1.2"): "1.2.0",
}


def release(version: str) -> tuple[int, int, int] | None:
    match = _RELEASE.fullmatch(version)
    return tuple(map(int, match.groups())) if match else None  # type: ignore[return-value]


def code_files(folder: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder)
        name = rel.as_posix()
        if (rel.parts[0] == "evals" or "__pycache__" in rel.parts
                or path.suffix.lower() in {".md", ".pyc"}):
            continue
        if name == "skill.yaml":
            files[name] = _VERSION_LINE.sub("", path.read_text(encoding="utf-8")).encode()
        else:
            files[name] = path.read_bytes()
    return files


def changed_code(old: Path, new: Path) -> set[str]:
    a, b = code_files(old), code_files(new)
    return {name for name in a.keys() | b.keys() if a.get(name) != b.get(name)}


def previous_release(folder: Path) -> Path | None:
    """The highest earlier x.y.* folder next to this x.y.Z folder."""
    key = release(folder.name)
    if key is None or not folder.parent.is_dir():
        return None
    earlier = {k: d for d in folder.parent.iterdir()
               if d.is_dir() and (k := release(d.name)) and k[:2] == key[:2] and k < key}
    return earlier[max(earlier)] if earlier else None


def patch_refusal(skill_id: str, version: str, folder: Path) -> str | None:
    """None when this version may be approved under the patch rule."""
    key = release(version)
    if key is None or key[2] == 0:
        return None
    if release(folder.name) != key:
        return f"{skill_id} {version}: package folder {folder.name} does not match its version."
    previous = previous_release(folder)
    if previous is None:
        return (f"{skill_id} {version} is a patch with no earlier {key[0]}.{key[1]}.x release. "
                f"Release it as {key[0]}.{key[1]}.0.")
    changed = changed_code(previous, folder)
    known = KNOWN.get((skill_id, version), frozenset())
    if changed != known:
        new = sorted(changed - known) or sorted(known - changed)
        return (f"{skill_id} {version} changes {', '.join(new)} against {previous.name}. "
                f"A patch may only change evals and docs; release code changes as "
                f"{key[0]}.{key[1] + 1}.0.")
    if (skill_id, version) in SUPERSEDED:
        return (f"{skill_id} {version} changed code in a patch; approve "
                f"{SUPERSEDED[(skill_id, version)]} instead.")
    return None
