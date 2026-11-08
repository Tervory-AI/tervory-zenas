"""Run the whole offline test suite, in parallel, the way the PR tank job does.

  python3 .github/scripts/offline_suite.py [extra pytest args]

Offline means: no provider keys in the environment, and in-process sockets
may only reach loopback (pytest-socket). The Postgres service, a local Redis
container started by tests/tenant_bus, Chromium and Node all run on the runner.

Three pytest runs, because two test folders import sibling modules by bare
name (test_alerts, test_review) and so cannot share one process with other
folders that have files of the same name:
  1. everything else, 2. tests/tiger_g5, 3. tests/zenas_regulation_alerts.
tests/ci_security runs in the security job (it needs the pinned gitleaks) and
tests/test_live_check.py in live-check.yml (it needs Playwright for Node).
The files in SIDE_WORKFLOW run in rig-forge.yml or tenant-rls.yml, not here.

Known failures on main are listed with reasons in tests/quarantine.txt.

Fail fast (ci run 41241674166, 31 Oct 2026: worker gw0 died in
tests/test_rig_landing_overflow.py at 21:17:42 UTC and the job then sat silent
until the 30-minute job timeout cancelled it at 21:32:44 UTC, with no stack):
  - faulthandler_timeout prints every thread's stack for a test still running
    after 240s, before the 300s per-test timeout kills its worker;
  - --max-worker-restart 0 ends a run as soon as a worker crashes, instead of
    replacing the worker and re-running the same file;
  - each run has a wall-clock budget; past it, the whole process group gets
    SIGABRT (pytest's faulthandler prints the stacks) and then SIGKILL, so the
    step fails with evidence well before the job timeout.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALONE = ["tests/tiger_g5", "tests/zenas_regulation_alerts"]
# Run by their own workflows: security job (pinned gitleaks), live-check.yml (Node Playwright).
ELSEWHERE = ["tests/ci_security", "tests/test_live_check.py"]
# Test files a side workflow already runs, by name, mapped to "workflow file: job". The main
# suite leaves them out so they no longer run twice (3 Nov, founder). A file is listed only
# when the side workflow runs on every pull request that could change it: the file and every
# repository module it imports fall inside that workflow's pull_request paths filter. The one
# exception, the autumbra_a2 probe, skips itself everywhere except its own job
# (AUTUMBRA_A2_CI_PROBE), so the main suite never ran it. tests/test_ci_side_workflow_skips.py
# checks that the named job really runs each file. Neither side workflow runs in the merge
# queue and neither is a required check (see the PR that added this list).
# rig-forge.yml: forge-image runs the first seven inside the forge image, then the containers
# file against that image. test_rig_forge_pipeline.py and test_rig_forge_containers.py skip
# themselves outside that job.
# tenant-rls.yml: test_tenant_rls.py and the other 21 files its isolation job runs stay in the
# main suite, so tenant isolation keeps blocking merges through ci-ok (founder, 3 Nov). Its paths
# filter does not list them (or the tests/ helpers they import), so a PR changing only them
# would test them nowhere.
SIDE_WORKFLOW = {
    "tests/test_rig_forge_geometry.py": "rig-forge.yml: forge-image",
    "tests/test_rig_forge_pipeline.py": "rig-forge.yml: forge-image",
    "tests/test_rig_forge_gpu.py": "rig-forge.yml: forge-image",
    "tests/test_rig_forge_review.py": "rig-forge.yml: forge-image",
    "tests/test_rig_forge_gpu_artifacts.py": "rig-forge.yml: forge-image",
    "tests/test_rig_forge_relay.py": "rig-forge.yml: forge-image",
    "tests/test_rig_forge_job_state.py": "rig-forge.yml: forge-image",
    "tests/test_rig_forge_containers.py": "rig-forge.yml: forge-image",
    "tests/autumbra_a2/test_compose_probe.py": "tenant-rls.yml: autumbra-isolation",
}
# Provider keys and tokens must never reach the suite, even if a runner has them set.
# Wall-clock seconds per run. Hosted-runner times on main, 31 Oct 2026: main 651-834s,
# tests/tiger_g5 11-15s, tests/zenas_regulation_alerts 6-7s. On 3 Nov 2026 main reached
# 1060-1065s (runs on 1ad2c794 and c5afd368) against the old 1080s budget, and a PR run
# was stopped over budget, so main gets 1560s. All three budgets plus about 1.5 minutes
# of job setup fit inside the tank job's 36-minute timeout.
BUDGETS = {"main": 1560, "tests/tiger_g5": 180, "tests/zenas_regulation_alerts": 180}
OVER_BUDGET = 124  # the exit code timeout(1) uses
KEY_HINTS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "OPENAI", "ANTHROPIC", "OPENROUTER",
             "PERPLEXITY", "DEEPSEEK", "GROQ", "TOGETHER", "MISTRAL", "GEMINI", "XAI_", "HF_")


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not any(h in k.upper() for h in KEY_HINTS)}
    env.setdefault("PYTHONPATH", "tank")
    return env


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def run(cmd: list[str], budget: float, name: str, grace: float = 20, **kwargs) -> int:
    """Run cmd in its own process group; past budget seconds, dump stacks and stop it all.

    The group holds pytest, its xdist workers and the plain subprocesses they
    start. Chromium (Playwright) makes its own process group, so it is not in it;
    it exits by itself when its --remote-debugging-pipe closes as the worker dies."""
    proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    try:
        return proc.wait(timeout=budget)
    except KeyboardInterrupt:  # Ctrl-C or a cancelled job: its own session no longer gets it
        _signal_group(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass  # it ignored SIGINT; SIGKILL below, then re-raise the interrupt
        finally:
            _signal_group(proc.pid, signal.SIGKILL)
        raise
    except subprocess.TimeoutExpired:
        print(f"\n::error title=offline suite over budget::{name} still running after {budget:.0f}s; "
              "sending SIGABRT to print stacks, then stopping it", flush=True)
        _signal_group(proc.pid, signal.SIGABRT)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        _signal_group(proc.pid, signal.SIGKILL)
        proc.wait()
        return OVER_BUDGET


def _faulthandler(test_timeout: int) -> list[str]:
    if test_timeout <= 0:  # per-test timeout switched off
        return []
    return ["-o", f"faulthandler_timeout={test_timeout - 60 if test_timeout > 120 else max(1, test_timeout // 2)}"]


def pytest_args(workers: str = "auto", test_timeout: str = "300") -> list[str]:
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs",
            "-n", workers, "--dist", "loadfile",
            # A crashed worker (hung test killed by the thread timeout, or a native
            # crash) ends the run: no replacement worker re-running the same file.
            "--max-worker-restart", os.environ.get("SUITE_MAX_WORKER_RESTART", "0"),
            # thread: a hung test (or its teardown) kills only its worker.
            "--timeout", test_timeout, "--timeout-method", "thread",
            # ...but first print its stack: the thread timeout's own dump is lost when
            # xdist reports the worker as "node down: Not properly terminated".
            *_faulthandler(int(test_timeout)),
            "--disable-socket", "--allow-unix-socket", "--allow-hosts=127.0.0.1,localhost,::1",
            "--durations", "15"]


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def shard_of() -> tuple[int, int]:
    """SUITE_SHARD/SUITE_SHARDS from the CI matrix; (1, 1) runs everything."""
    count = int(os.environ.get("SUITE_SHARDS", "1") or "1")
    index = int(os.environ.get("SUITE_SHARD", "1") or "1")
    if count < 1 or not 1 <= index <= count:
        raise SystemExit(f"invalid shard {index}/{count}")
    return index, count


def split_files(counts: dict[str, int], index: int, count: int) -> list[str]:
    """Deterministic split by test count: largest files first, each to the lightest shard."""
    loads = [0] * count
    owned: list[list[str]] = [[] for _ in range(count)]
    for path in sorted(counts, key=lambda f: (-counts[f], f)):
        lightest = min(range(count), key=lambda i: (loads[i], i))
        loads[lightest] += counts[path]
        owned[lightest].append(path)
    return sorted(owned[index - 1])


def alone_for(index: int, count: int) -> list[str]:
    """The isolated groups shard `index` runs: group i on shard (i mod count) + 1, so each runs once
    and, with two or more shards, never both on the same shard (tiger_g5 on 1, zenas_regulation_alerts on 2)."""
    return [p for i, p in enumerate(ALONE) if i % count == index - 1]


def collected_files(ignores: list[str], env: dict[str, str]) -> dict[str, int]:
    """Exactly the files the unsharded main run would collect, with their test counts."""
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *ignores],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        print(out.stdout[-4000:] + out.stderr[-4000:], flush=True)
        raise SystemExit("collection failed; see output above")
    counts: dict[str, int] = {}
    for line in out.stdout.splitlines():
        if "::" in line:
            path = line.split("::", 1)[0]
            counts[path] = counts.get(path, 0) + 1
    return counts


def main_ignores() -> list[str]:
    """What the main run leaves out: the isolated groups, ELSEWHERE and SIDE_WORKFLOW."""
    return [f"--ignore={p}" for p in [*ALONE, *ELSEWHERE, *SIDE_WORKFLOW]]


def prepare_rig_tools(files: list[str], env: dict[str, str]) -> None:
    """Only the shard owning real R3D conversions needs the pinned CPU toolchain.

    Setup is outside pytest's offline phase, with hashes enforced by the same
    installer/lockfile used by the production Forge image. Never skip the proof.
    """
    if os.environ.get("CI_PREPARE_RIG_TOOLS") != "1":
        return
    if "tests/test_rig_r3d.py" not in files and not any(p.startswith("--ignore=") for p in files):
        return
    tools = ROOT / ".tmp" / "forge-tools"
    forge = ROOT / "agents" / "rig" / "forge"
    subprocess.run([sys.executable, str(forge / "install_tools.py"), str(tools)], check=True, timeout=600)
    subprocess.run(["npm", "ci", "--prefix", str(forge), "--ignore-scripts", "--no-audit", "--no-fund"],
                   check=True, timeout=300)
    env["PATH"] = os.pathsep.join([str(tools / "bin"), str(forge / "node_modules" / ".bin"), env["PATH"]])


def main(extra: list[str]) -> int:
    signal.signal(signal.SIGTERM, _interrupt)
    common = pytest_args(os.environ.get("SUITE_WORKERS", "auto"), os.environ.get("SUITE_TEST_TIMEOUT", "300"))
    # A slower machine (fewer workers) can stretch the budgets, e.g. SUITE_BUDGET_SCALE=3.
    scale = float(os.environ.get("SUITE_BUDGET_SCALE", "1"))
    index, count = shard_of()
    ignores = main_ignores()
    env, failed = clean_env(), []
    if count == 1:
        main_args = ignores
    else:
        main_args = split_files(collected_files(ignores, env), index, count)
        print(f"shard {index}/{count}: {len(main_args)} test files", flush=True)
        if not main_args:
            print("no files in this shard", flush=True)
    prepare_rig_tools(main_args, env)
    # Each small isolated group runs once, on its own shard (alone_for).
    runs = [("main", main_args)] if main_args else []
    runs += [(p, [p]) for p in alone_for(index, count)]
    started = time.monotonic()
    for name, args in runs:
        print(f"\n===== offline suite: {name} =====", flush=True)
        t0 = time.monotonic()
        junit = []
        if os.environ.get("SUITE_JUNIT_DIR"):
            junit = [f"--junitxml={os.environ['SUITE_JUNIT_DIR']}/{name.replace('/', '_')}.xml"]
        code = run([*common, *junit, *extra, *args], BUDGETS[name] * scale, name, cwd=ROOT, env=env)
        print(f"===== {name}: exit {code} in {time.monotonic() - t0:.0f}s =====", flush=True)
        if code != 0:
            failed.append(name)
    print(f"\nOffline suite finished in {time.monotonic() - started:.0f}s; "
          f"{'failed: ' + ', '.join(failed) if failed else 'all runs passed'}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
