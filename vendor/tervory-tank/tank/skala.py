"""Skala: the skills lifecycle (Build Pack v2, "Skala — the skills lifecycle").

A skill is a versioned package on disk:

    skills/<id>/<version>/
        skill.yaml      id, version, agent, rung, task_type, description, schemas, eval_threshold
        run.py          async def run(input: dict, ctx: SkillContext) -> dict
        evals/cases.jsonl   one {"input":…, "expected":…, "tolerance":…} per line
        evals/cases.<region>.jsonl   optional; same-name rows with this box's input
        CHANGELOG.md

Phase 1 registry = this folder (Git) + tank.skills (Postgres).

States: candidate -> staging -> approved -> retired
    candidate  the package exists and parsed
    staging    its evals scored at or above threshold (Skala moves it)
    approved   a human signed off; only approved versions run for clients
    retired    superseded; kept for rollback

Rules enforced here: agents may call `run` on approved versions only (preview runs
are for the owner's own testing and are labelled as such in the ledger); approval
is a separate, human-only call; superseded versions wait in staging for the
30-day retirement gate and remain available for explicit founder rollback.
"""

from __future__ import annotations

import importlib.util
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import yaml

from gold import PLATFORM_TENANT
from skala_patch import patch_refusal

SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", os.path.join(os.path.dirname(__file__), "skills")))

ModelFn = Callable[..., Awaitable[dict[str, Any]]]


# ---------- package on disk ----------

@dataclass
class SkillPackage:
    id: str
    version: str
    agent: str
    rung: int
    task_type: str | None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    eval_threshold: float
    path: Path
    cases: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


def _read_jsonl(f: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def _load_cases(path: Path) -> list[dict[str, Any]]:
    """cases.jsonl, with an optional evals/cases.<TANK_REGION>.jsonl for this box.

    A regional row replaces the shared row with the same name, only for its
    input: expected, tolerance, must_refuse and every other key must be equal,
    and no case may be added or dropped. A bad regional file fails closed.
    """
    f = path / "evals" / "cases.jsonl"
    if not f.exists():
        return []
    out = _read_jsonl(f)
    region = os.environ.get("TANK_REGION", "us-east").strip().lower()
    regional = path / "evals" / f"cases.{region}.jsonl"
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", region) or not regional.exists():
        return out
    index = {case.get("name"): i for i, case in enumerate(out)}
    if None in index or len(index) != len(out):
        raise ValueError("regional eval cases need unique case names")
    seen: set[str] = set()
    for case in _read_jsonl(regional):
        name = case.get("name")
        if name not in index or name in seen:
            raise ValueError(f"regional eval case {name!r} does not replace exactly one shared case")
        shared = out[index[name]]
        if {k: v for k, v in case.items() if k != "input"} != {k: v for k, v in shared.items() if k != "input"}:
            raise ValueError(f"regional eval case {name!r} may change only its input")
        seen.add(name)
        out[index[name]] = case
    return out


def load_packages(root: Path = SKILLS_DIR) -> dict[tuple[str, str], SkillPackage]:
    """Every skills/<id>/<version>/skill.yaml under root."""
    found: dict[tuple[str, str], SkillPackage] = {}
    if not root.exists():
        return found
    for yaml_path in sorted(root.glob("*/*/skill.yaml")):
        pkg_dir = yaml_path.parent
        try:
            pkg = _parse_package(yaml_path, pkg_dir)
        except Exception as exc:  # noqa: BLE001 — one broken package must never stop Tank
            log.error("skala: skipping %s: %s", yaml_path, exc)
            continue
        found[(pkg.id, pkg.version)] = pkg
    return found


log = logging.getLogger("tank.skala")


def _parse_package(yaml_path: Path, pkg_dir: Path) -> SkillPackage:
    meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if True:
        pkg = SkillPackage(
            id=str(meta["id"]),
            version=str(meta["version"]),
            agent=str(meta.get("agent", "tier0")),
            rung=int(meta.get("rung", 1)),
            task_type=meta.get("task_type"),
            description=str(meta.get("description", "")).strip(),
            input_schema=meta.get("input", {}) or {},
            output_schema=meta.get("output", {}) or {},
            eval_threshold=float(meta.get("eval_threshold", 0.95)),
            path=pkg_dir,
            cases=_load_cases(pkg_dir),
        )
        if pkg_dir.parent.name != pkg.id or pkg_dir.name != pkg.version:
            raise ValueError(f"folder is {pkg_dir.parent.name}/{pkg_dir.name} but skill.yaml says {pkg.ref}")
        return pkg


_run_cache: dict[tuple[str, str], Callable[..., Awaitable[dict[str, Any]]]] = {}


def _import_run(pkg: SkillPackage) -> Callable[[dict[str, Any], "SkillContext"], Awaitable[dict[str, Any]]]:
    cached = _run_cache.get((pkg.id, pkg.version))
    if cached is not None:
        return cached
    run = _import_run_uncached(pkg)
    _run_cache[(pkg.id, pkg.version)] = run
    return run


def _import_run_uncached(pkg: SkillPackage) -> Callable[[dict[str, Any], "SkillContext"], Awaitable[dict[str, Any]]]:
    spec = importlib.util.spec_from_file_location(f"skill_{pkg.id}_{pkg.version}".replace(".", "_").replace("-", "_"), pkg.path / "run.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    run = getattr(mod, "run", None)
    if run is None:
        raise ValueError(f"{pkg.ref}: run.py has no run()")
    return run


# ---------- what a skill may touch ----------

@dataclass
class SkillContext:
    """Everything a skill is allowed to reach. Models only through Tank (so the
    call is policy-routed and in the ledger, tagged with this skill); the web
    through a shared HTTP client with a sane timeout and user agent."""

    tenant_id: uuid.UUID
    skill_ref: str
    agent: str
    task_id: uuid.UUID | None
    _model: ModelFn
    http: httpx.AsyncClient
    env: dict[str, str] = field(default_factory=dict)   # optional provider keys a skill may use
    calls: int = 0
    cost_usd: float = 0.0
    live_evaluation: bool = False  # B3 must test the new model, not saved fixture output.

    async def model(self, messages: list[dict[str, str]], task_type: str, max_tokens: int | None = None) -> str:
        res = await self._model(
            tenant_id=self.tenant_id, agent=self.agent, task_type=task_type, skill=self.skill_ref,
            messages=messages, task_id=self.task_id, max_tokens=max_tokens,
        )
        self.calls += 1
        self.cost_usd += float(res.get("cost_usd") or 0)
        return str(res.get("content") or "")


# ---------- skill scratch space ----------
# The trigger.* engines (tank/skills/trigger.*/1.0.0/engine.py:12) unpack rules.zip into a
# tempfile.TemporaryDirectory(prefix="trigger-skill-") when they start. Python removes it
# only on a clean exit. Their run.py SIGKILLs the engine on its 30 s timeout and whenever
# the call is cancelled (Tank shutting down cancels in-flight calls), and SIGTERM or SIGHUP
# skip the cleanup too, so every such stop left ~350 inodes in /tmp for good. Approved
# versions are never edited in place (AGENTS.md rule 7), so Tank, which starts every run,
# removes what a killed engine left behind.
SKILL_TEMP_PREFIXES = ("trigger-skill-",)
# Far past the engines' 30 s limit: no live engine owns a folder this old.
SKILL_TEMP_MAX_AGE_S = 3600
SKILL_TEMP_SWEEP_EVERY_S = 600
_last_temp_sweep: float | None = None


def sweep_skill_temp(root: str | os.PathLike[str] | None = None, *, now: float | None = None,
                     max_age_s: float = SKILL_TEMP_MAX_AGE_S) -> list[str]:
    """Remove this user's skill scratch folders older than max_age_s; return their names."""
    base = Path(root if root is not None else tempfile.gettempdir())
    now = time.time() if now is None else now
    removed: list[str] = []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.startswith(SKILL_TEMP_PREFIXES):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            if not entry.is_dir(follow_symlinks=False) or info.st_uid != os.getuid():
                continue
            if now - info.st_mtime < max_age_s:
                continue
        except OSError:
            continue
        shutil.rmtree(entry.path, ignore_errors=True)
        if not os.path.lexists(entry.path):
            removed.append(entry.name)
    return removed


def _sweep_skill_temp_quietly() -> None:
    try:
        removed = sweep_skill_temp()
    except Exception:  # never let housekeeping fail a skill run
        log.exception("skill scratch sweep failed")
        return
    if removed:
        log.info("removed %d stale skill scratch folders", len(removed))


def _maybe_sweep_skill_temp() -> None:
    """At most every SKILL_TEMP_SWEEP_EVERY_S per process, sweep in a background thread."""
    global _last_temp_sweep
    # Tests set this (tests/conftest.py) so no test sweeps the shared /tmp of the box.
    if os.environ.get("TANK_SKILL_TEMP_SWEEP", "").strip().lower() == "off":
        return
    now = time.monotonic()
    if _last_temp_sweep is not None and now - _last_temp_sweep < SKILL_TEMP_SWEEP_EVERY_S:
        return
    _last_temp_sweep = now
    try:
        threading.Thread(target=_sweep_skill_temp_quietly, name="skill-temp-sweep", daemon=True).start()
    except Exception:  # e.g. RuntimeError: can't start new thread; housekeeping never fails a call
        log.exception("skill scratch sweep could not start")


# ---------- running ----------

async def run_skill(pkg: SkillPackage, payload: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    _maybe_sweep_skill_temp()
    run = _import_run(pkg)
    started = time.perf_counter()
    out = await run(payload, ctx)
    if not isinstance(out, dict):
        raise ValueError(f"{pkg.ref}: run() must return a dict")
    out.setdefault("_skill", pkg.ref)
    out["_ms"] = int((time.perf_counter() - started) * 1000)
    return out


# ---------- scoring evals ----------

def _norm(v: Any) -> str:
    return " ".join(str(v).lower().split())


def score_case(expected: dict[str, Any], output: dict[str, Any], tolerance: str) -> tuple[bool, str]:
    """exact: every expected key equal. contains: expected text appears in output text (per key).
    fields: like exact but lists compared as sets. Returns (passed, reason)."""
    for key, want in expected.items():
        got = output.get(key)
        if tolerance == "contains":
            if _norm(want) not in _norm(got):
                return False, f"{key}: {want!r} not in {str(got)[:80]!r}"
        elif tolerance == "fields":
            if isinstance(want, list) and isinstance(got, list):
                if set(map(_norm, want)) != set(map(_norm, got)):
                    return False, f"{key}: {want!r} != {got!r}"
            elif _norm(want) != _norm(got):
                return False, f"{key}: {want!r} != {got!r}"
        else:  # exact
            if want != got:
                return False, f"{key}: {want!r} != {got!r}"
    return True, "ok"


def zenas_approval_reason(skill_id: str, area: str | None) -> str | None:
    """Why a zenas.* skill cannot be approved. None means this box's gate passed.

    The latest made-up-case row must be 50 of 50 through answer_question.
    Any other skill is not this gate.
    """
    if not str(skill_id or "").startswith("zenas."):
        return None
    text = " ".join(str(area or "").split())
    if text.startswith("Made-up cases: 50 of 50.") and "path: answer_question" in text:
        return None
    if not text or "not run" in text:
        return (
            "This Zenas skill stays unapproved. The made-up-case test has not run "
            "on this box through answer_question."
        )
    if "path: answer_question" not in text:
        return (
            "This Zenas skill stays unapproved. The latest made-up-case test on this box "
            "did not run through answer_question."
        )
    return (
        "This Zenas skill stays unapproved. The latest made-up-case test on this box is "
        f"{text.removeprefix('Made-up cases: ').split('. path:')[0]}, not 50 of 50 through answer_question."
    )


_MADE_UP_AREA_SQL = """
SELECT area
FROM agent_zenas.selftest_runs
WHERE region = $1
  AND area LIKE 'Made-up cases:%'
ORDER BY created_at DESC
LIMIT 1
""".strip()


async def zenas_approval_refusal(conn: Any, skill_id: str, *, region: str) -> str | None:
    """The sentence Skala shows when this box has not passed the made-up-case test."""
    if not str(skill_id or "").startswith("zenas."):
        return None
    if conn is not None and hasattr(conn, "fetchval"):
        exists = await conn.fetchval("SELECT to_regclass('agent_zenas.selftest_runs')")
        if exists is None:
            return "This Zenas skill stays unapproved. The made-up-case test is unavailable on this box."
    box = "za" if str(region or "").strip().lower() == "za" else "us-east"
    area = ""
    if conn is not None and hasattr(conn, "fetchrow"):
        row = await conn.fetchrow(_MADE_UP_AREA_SQL, box)
        if row is not None:
            try:
                area = str(row["area"] or "")
            except (KeyError, IndexError, TypeError):
                area = ""
    return zenas_approval_reason(skill_id, area)


def eval_properties(case: dict[str, Any], output: dict[str, Any], model_configured: bool) -> tuple[bool, str]:
    from collections import Counter
    import re

    properties = case.get("properties")
    if not properties:
        return True, "ok"
    text = str(output.get("text") or "")
    reference = properties.get("reference_text", case["input"].get("text", ""))
    citations = properties.get("citations", case["input"].get("citations", []))
    if any(_norm(citation) not in {_norm(allowed) for allowed in citations}
           for citation in output.get("citations", [])):
        return False, "new citation"
    if not text.strip() or not reference.strip():
        return False, "legal wording needs text and a reference"
    for citation in citations:
        if _norm(citation) not in _norm(text):
            return False, "original citation missing"
    stripped = text
    for citation in sorted(citations, key=len, reverse=True):
        stripped = re.sub(re.escape(citation), "", stripped, flags=re.I)
    if re.search(r"§|\b(?:section|article|rule)\s+\d|\[\d{4}\]|\b\w+\s+v\.?\s+\w+|\d+\s+(?:F\.|U\.S\.|Del\.)", stripped, re.I):
        return False, "new citation"
    def effect(value: str) -> tuple:
        value = re.sub(r"(?m)^Issue:.*$", "", value)
        folded = _norm(value)
        folded = re.sub(r"\b(?:can be enforced|is enforceable)\b", "enforceable", folded)
        folded = re.sub(r"\b(?:shall|is required to|has to)\b", "must", folded)
        folded = re.sub(r"\b(?:may|is permitted to)\b", "can", folded)
        markers = Counter(re.findall(
            r"\b(?:not|never|must|can|enforceable|unenforceable|met|unclear|liable|unless|except|only|before|after|within)\b|\d+",
            folded,
        ))
        if "met" in markers:
            markers["met"] = 1
        return tuple(sorted(markers.items()))
    if effect(reference) != effect(text):
        return False, "legal effect changed"
    if output.get("smoothed") and not model_configured:
        return False, "smoothed without a configured model"
    return True, "ok"


async def run_evals(pkg: SkillPackage, make_ctx: Callable[[], SkillContext],
                    execute: Callable[..., Awaitable[dict[str, Any]]] | None = None) -> dict[str, Any]:
    source_fingerprint = package_hash(pkg)
    details: list[dict[str, Any]] = []
    passed = 0
    cost = 0.0
    for i, case in enumerate(pkg.cases):
        ctx = make_ctx()
        if "model" in case and not getattr(ctx, "live_evaluation", False):
            from dataclasses import replace

            async def saved_model(**_kwargs: Any) -> dict[str, Any]:
                return {"content": case["model"], "cost_usd": 0}

            ctx = replace(ctx, _model=saved_model)
        goal = case.get("expected", {})
        try:
            out = await (execute or run_skill)(pkg, copy.deepcopy(case["input"]), ctx)
            ok, why = score_case(goal, out, case.get("tolerance", "exact"))
            if case.get("expected_error"):
                ok, why = False, "expected refusal exception was not raised"
            if ok:
                configured = getattr(ctx, "live_evaluation", False) or case.get("model_configured", "model" in case or bool(pkg.task_type))
                ok, why = eval_properties(case, out, configured and getattr(ctx, "calls", 0) > 0)
        except Exception as exc:  # noqa: BLE001 — an exception is a failed case, recorded
            ok, why, out = False, f"error: {exc}", {}
            expected_error = case.get("expected_error") or {}
            if (case.get("must_refuse") and type(exc).__name__ == expected_error.get("type")
                    and str(exc) == expected_error.get("message")):
                ok, why = True, "expected refusal"
        cost += ctx.cost_usd
        passed += int(ok)
        details.append({
            "case": i,
            "name": case.get("name"),
            "passed": ok,
            "why": why,
            "goal": goal,
            "result": out,
            "case_hash": evidence_hash(case),
            "package_hash": source_fingerprint,
            "used_model": getattr(ctx, "calls", 0) > 0,
        })
    total = len(pkg.cases)
    distinct = {json.dumps(case["input"], sort_keys=True) + json.dumps(case.get("model"), sort_keys=True)
                for case in pkg.cases}
    negative_results = [detail["passed"] for case, detail in zip(pkg.cases, details)
                        if case.get("must_refuse") is True and (case.get("expected") or case.get("expected_error"))]
    negative_passed = bool(negative_results) and all(negative_results)
    return {
        "skill": pkg.ref,
        "passed": passed,
        "total": total,
        "score": round(passed / total, 4) if total else 0.0,
        "threshold": pkg.eval_threshold,
        "eligible": len(distinct) >= 5 and negative_passed,
        "evidence_reason": ("ok" if len(distinct) >= 5 and negative_passed else
                            "At least five distinct cases and passing refusal or abstention evidence are required."),
        "cost_usd": round(cost, 6),
        "details": details,
    }


# Approval evidence is deliberately separate from execution and routing.
def evidence_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_files(pkg: SkillPackage) -> list[dict[str, str]]:
    """The complete Python source, including helpers in nested directories."""
    return [{"path": path.relative_to(pkg.path).as_posix(), "source": path.read_text(encoding="utf-8")}
            for path in sorted(pkg.path.rglob("*.py")) if path.is_file()]


def package_hash(pkg: SkillPackage, files: list[dict[str, str]] | None = None) -> str:
    return evidence_hash([source_files(pkg) if files is None else files, pkg.cases,
                          pkg.eval_threshold, pkg.task_type, pkg.rung])


def version_key(version: str) -> tuple[int, int, int]:
    import re
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("Approval requires a release version such as 1.2.3.")
    return tuple(map(int, version.split(".")))


def room_authored(skill_id: str) -> bool:
    return len(skill_id.split(".")) >= 3 and skill_id.split(".")[1] == "learned"


def eval_details(row: dict[str, Any]) -> list[dict[str, Any]]:
    details = row["details"]
    return json.loads(details) if isinstance(details, str) else details


def passing_runs(pkg: SkillPackage, runs: list[dict[str, Any]]) -> int:
    count = 0
    for row in runs[:3]:
        details = eval_details(row)
        if (row["total"] != len(pkg.cases) or row["passed"] != row["total"] or not details
                or any(d.get("package_hash") != package_hash(pkg) for d in details)):
            break
        count += 1
    return count


def approval_reason(pkg: SkillPackage, target: dict[str, Any], approved: dict[str, Any] | None,
                    runs: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> str | None:
    try:
        if approved and version_key(pkg.version) <= version_key(approved["version"]):
            return f"{pkg.version} is older than or equal to approved {approved['version']}."
        version_key(pkg.version)
    except ValueError as exc:
        return str(exc)
    # A patch release must keep the code of the release before it
    # (skala_patch.py; the same rule as tests/test_skala_patch_code_unchanged.py).
    patch = patch_refusal(pkg.id, pkg.version, pkg.path)
    if patch:
        return patch
    if target["state"] != "staging" or not target["present"]:
        return "Run evals until this version reaches staging first."
    distinct = {evidence_hash([c["input"], c.get("model")]) for c in pkg.cases}
    refusals = [i for i, c in enumerate(pkg.cases)
                if c.get("must_refuse") is True and (c.get("expected") or c.get("expected_error"))]
    if len(distinct) < 5 or not refusals:
        return "At least five distinct cases including a refusal are required."
    if not runs:
        return "Run evals to record approval evidence."
    latest = runs[0]
    details = eval_details(latest)
    hashes = [evidence_hash(c) for c in pkg.cases]
    if (len(details) != len(hashes) or [d.get("case_hash") for d in details] != hashes
            or any(d.get("package_hash") != package_hash(pkg) for d in details)):
        return "Run evals again: the evidence does not match this package and its cases."
    if float(latest["score"]) < pkg.eval_threshold or not all(details[i]["passed"] for i in refusals):
        return "The latest eval does not pass this skill's gate."
    model = bool(pkg.task_type) or pkg.rung in {1, 2} or any(d.get("used_model") for d in details)
    if model and passing_runs(pkg, runs) < 3:
        return f"{passing_runs(pkg, runs)}/3 runs. Every case must pass on three consecutive runs."
    if approved:
        base_details = eval_details(baseline[0]) if baseline else []
        if not base_details or any(not isinstance(d.get("case_hash"), str) or not d["case_hash"] for d in base_details):
            return f"Run evals on approved {approved['version']} first"
        base_hashes = {d["case_hash"] for d in base_details}
        missing = base_hashes - set(hashes)
        if missing:
            return f"{pkg.version} drops or changes {len(missing)} of approved {approved['version']}'s cases"
        # Extra cases must pass the candidate's own gate, but cannot mask a
        # regression on the approved suite. Keep each approved case's weight;
        # repeating a candidate case cannot turn its failure into a pass.
        passed_by_hash = {h: all(d["passed"] is True for d in details if d["case_hash"] == h)
                          for h in base_hashes}
        score = sum(passed_by_hash[d["case_hash"]] for d in base_details) / len(base_details)
        previous = sum(d["passed"] is True for d in base_details) / len(base_details)
        if score < previous:
            return f"scores {score * 100:.1f}%, below approved {previous * 100:.1f}%."
    return None


async def review_evidence(conn: Any, pkg: SkillPackage, *, region: str) -> dict[str, Any]:
    rows = [dict(r) for r in await conn.fetch("SELECT * FROM tank.skills WHERE id=$1", pkg.id)]
    target = next((r for r in rows if r["version"] == pkg.version), None)
    approved = next((r for r in rows if r["state"] == "approved"), None)
    runs = [dict(r) for r in await conn.fetch(
        "SELECT * FROM tank.skill_evals WHERE skill_id=$1 AND version=$2 ORDER BY ran_at DESC, id DESC LIMIT 3",
        pkg.id, pkg.version)]
    baseline = []
    if approved:
        baseline = [dict(r) for r in await conn.fetch(
            "SELECT * FROM tank.skill_evals WHERE skill_id=$1 AND version=$2 ORDER BY ran_at DESC, id DESC LIMIT 1",
            pkg.id, approved["version"])]
    reason = await zenas_approval_refusal(conn, pkg.id, region=region)
    reason = reason or (approval_reason(pkg, target, approved, runs, baseline) if target else "Version is missing.")
    return {"id": pkg.id, "version": pkg.version, "reason": reason,
            "score": float(runs[0]["score"]) if runs else None,
            "runs": passing_runs(pkg, runs), "model": bool(pkg.task_type) or pkg.rung in {1, 2}
            or any(d.get("used_model") for r in runs for d in eval_details(r)), "room_authored": room_authored(pkg.id),
            "fingerprint": evidence_hash([target, approved, runs, baseline, package_hash(pkg), reason])}


async def lock_approvals(conn: Any) -> None:
    # All lifecycle writers serialize; eval inserts cannot race the evidence check.
    await conn.execute("SELECT pg_advisory_xact_lock(73202102)")
    await conn.execute("LOCK TABLE tank.skill_evals IN SHARE MODE")
    await conn.execute("LOCK TABLE tank.skills IN SHARE ROW EXCLUSIVE MODE")


async def log_action(conn: Any, skill_id: str, version: str, by: str, action: str,
                     previous: str | None, evidence: dict[str, Any]) -> None:
    await conn.execute(
        """INSERT INTO tank.skill_approvals (tenant_id, skill_id, version, action, acted_by, previous_version, evidence)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
        PLATFORM_TENANT, skill_id, version, action, by,
        previous, json.dumps(evidence))


async def promote(conn: Any, pkg: SkillPackage, by: str, evidence: dict[str, Any], *, rollback: bool = False) -> None:
    rows = await conn.fetch("SELECT * FROM tank.skills WHERE id=$1", pkg.id)
    previous = next((r["version"] for r in rows if r["state"] == "approved"), None)
    for row in rows:
        if row["state"] == "approved" and row["version"] != pkg.version:
            # A new approval is not permission to retire the previous version.
            # Keep its approved_at history for rollback during probation.
            await conn.execute("UPDATE tank.skills SET state='staging', updated_at=now() WHERE id=$1 AND version=$2",
                               pkg.id, row["version"])
    await conn.execute(
        "UPDATE tank.skills SET state='approved', approved_by=$3, approved_at=now(), updated_at=now() WHERE id=$1 AND version=$2",
        pkg.id, pkg.version, by)
    await log_action(conn, pkg.id, pkg.version, by, "rollback" if rollback else "approve", previous, evidence)


async def change_version(db: Any, pkg: SkillPackage, by: str, action: str, *, region: str,
                         confirmed: bool = False, review_token: str | None = None) -> dict[str, Any]:
    from fastapi import HTTPException
    if action == "retire":
        return await retire_version(db, pkg.id, pkg.version, by)
    async with db.acquire() as conn:
        async with conn.transaction():
            await lock_approvals(conn)
            if action == "approve":
                evidence = await review_evidence(conn, pkg, region=region)
                if evidence["reason"]:
                    raise HTTPException(409, evidence["reason"])
                if room_authored(pkg.id):
                    if not confirmed or not review_token:
                        raise HTTPException(409, "Review every Python file, including run.py, and confirm this individual approval.")
                    review = await conn.fetchrow(
                        """SELECT * FROM tank.skill_source_reviews WHERE token=$1 AND tenant_id=$2
                        AND acted_by=$3 AND skill_id=$4 AND version=$5 AND expires_at>now() AND used_at IS NULL FOR UPDATE""",
                        review_token, PLATFORM_TENANT, by, pkg.id, pkg.version)
                    if not review or review["package_hash"] != package_hash(pkg):
                        raise HTTPException(409, "The source review expired, changed or was used. Review every Python file again.")
                await promote(conn, pkg, by, evidence)
                if room_authored(pkg.id):
                    await conn.execute(
                        "UPDATE tank.skill_source_reviews SET used_at=now() WHERE token=$1 AND tenant_id=$2",
                        review_token, PLATFORM_TENANT)
            elif action == "rollback":
                if not confirmed:
                    raise HTTPException(409, "Confirm the rollback first.")
                row = await conn.fetchrow("SELECT * FROM tank.skills WHERE id=$1 AND version=$2", pkg.id, pkg.version)
                if not row or row["state"] not in {"retired", "staging"} or row["approved_at"] is None or not row["present"]:
                    raise HTTPException(409, "Only a retired version that was previously approved can be restored.")
                blocked = await zenas_approval_refusal(conn, pkg.id, region=region)
                if blocked:
                    raise HTTPException(409, blocked)
                await promote(conn, pkg, by, {"confirmed": True}, rollback=True)
    return {"ok": True, "skill": pkg.ref, "state": "approved"}


async def retire_version(db: Any, skill_id: str, version: str, by: str, *, now: datetime | None = None) -> dict[str, Any]:
    """State-only retirement after a newer approval has completed probation."""
    from fastapi import HTTPException
    async with db.acquire() as conn:
        async with conn.transaction():
            await lock_approvals(conn)
            row = await conn.fetchrow("SELECT * FROM tank.skills WHERE id=$1 AND version=$2", skill_id, version)
            if row is None:
                raise HTTPException(404, "No such skill version.")
            if row["state"] not in {"candidate", "staging"}:
                raise HTTPException(409, "Only candidate or staging versions can be retired here. The approved version stays in service.")
            evidence = await __import__("skala_probation").evidence(conn, skill_id, version, now=now)
            if not evidence["allowed"]:
                raise HTTPException(409, evidence)
            await conn.execute("UPDATE tank.skills SET state='retired', updated_at=now() WHERE id=$1 AND version=$2", skill_id, version)
            await log_action(conn, skill_id, version, by, "retire", None, evidence)
    return {"ok": True, "skill": f"{skill_id}@{version}", "state": "retired"}


async def source_review(db: Any, pkg: SkillPackage, by: str) -> dict[str, Any]:
    token = str(uuid.uuid4())
    files = source_files(pkg)
    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO tank.skill_source_reviews (token, tenant_id, skill_id, version, acted_by, package_hash)
            VALUES ($1,$2,$3,$4,$5,$6)""", token,
            PLATFORM_TENANT, pkg.id, pkg.version, by, package_hash(pkg, files))
    return {"source": next((f["source"] for f in files if f["path"] == "run.py"), ""),
            "files": files, "review_token": token}


async def bulk_preview(db: Any, packages: dict[tuple[str, str], SkillPackage], by: str, *, region: str) -> dict[str, Any]:
    async with db.acquire() as conn:
        async with conn.transaction():
            await lock_approvals(conn)
            ready: dict[str, dict[str, Any]] = {}
            for pkg in packages.values():
                if room_authored(pkg.id):
                    continue
                item = await review_evidence(conn, pkg, region=region)
                if item["reason"] is None and (pkg.id not in ready or version_key(pkg.version) > version_key(ready[pkg.id]["version"])):
                    ready[pkg.id] = item
            items = sorted(ready.values(), key=lambda r: r["id"])
            token = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO tank.skill_approval_previews (token, tenant_id, acted_by, items) VALUES ($1,$2,$3,$4::jsonb)",
                token, PLATFORM_TENANT, by, json.dumps(items))
    return {"token": token, "items": items}


async def bulk_confirm(db: Any, packages: dict[tuple[str, str], SkillPackage], by: str, token: str,
                       confirmed: bool, *, region: str) -> dict[str, Any]:
    from fastapi import HTTPException
    if not confirmed:
        raise HTTPException(409, "Confirm the listed versions first.")
    async with db.acquire() as conn:
        async with conn.transaction():
            await lock_approvals(conn)
            row = await conn.fetchrow(
                """SELECT * FROM tank.skill_approval_previews WHERE token=$1 AND tenant_id=$2
                AND acted_by=$3 AND expires_at>now() AND used_at IS NULL FOR UPDATE""",
                token, PLATFORM_TENANT, by)
            if row is None:
                raise HTTPException(409, "The approval list expired or was already used. Load it again.")
            items = json.loads(row["items"]) if isinstance(row["items"], str) else row["items"]
            for item in items:
                pkg = packages.get((item["id"], item["version"]))
                if pkg is None or room_authored(pkg.id):
                    raise HTTPException(409, "The approval list changed. Load it again.")
                fresh = await review_evidence(conn, pkg, region=region)
                if fresh["reason"] or fresh["fingerprint"] != item["fingerprint"]:
                    raise HTTPException(409, f"{pkg.ref}: evidence changed. Load the approval list again.")
            for item in items:
                await promote(conn, packages[(item["id"], item["version"])], by, item)
            await conn.execute("UPDATE tank.skill_approval_previews SET used_at=now() WHERE token=$1 AND tenant_id=$2",
                               token, PLATFORM_TENANT)
    return {"ok": True, "approved": [{"id": i["id"], "version": i["version"]} for i in items]}
