"""Select room tests conservatively; shared or unknown paths run the full suite."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ROOM_TESTS = {
    "base": ("test_*base*.py", "test_b3*.py", "test_c13*.py", "test_find_site*.py"),
    "tiger": ("test_*tiger*.py", "test_tg2*.py", "tiger_*/test_*.py"),
    "market_lab": ("test_market_lab*.py", "test_feeds*.py"),
    "radix": ("test_radix*.py", "test_rx*.py"),
    "rig": ("test_rig*.py",),
    "tisan": ("test_tisan*.py",),
    "zenas": ("test_zenas*.py",),
}
# The savings-page browser check renders the client dashboard and the founder
# savings panel. It runs only when one of these paths changes.
SAVINGS_PATHS = (
    "workspace/app/page.tsx", "workspace/app/tank/proof/", "workspace/app/dashboard",
    "workspace/app/savings", "workspace/package.json", "workspace/package-lock.json",
    "tests/test_core2_savings_page.mjs", "tests/client-copy.cjs",
    ".github/workflows/ci.yml", ".github/scripts/ci_scope.py", "unknown-change",
)
# Always check secrets, room/client isolation, region boundaries, and schema apply.
SAFETY = (
    "test_secrets.py", "test_agent_chassis.py", "test_agent_roles.py",
    "test_c10_rooms.py", "test_tenant_scoping.py", "test_bf2_tank_guards.py",
    "test_rig_isolation.py", "test_*region*.py", "test_*schema*.py",
    "test_rig_postgres.py", "test_deploy_changed.py", "test_train1.py",
    "test_tank_one_door.py",
)


def select(paths: list[str]) -> dict:
    code = [p for p in paths if not (p.startswith("docs/") or p.endswith(".md"))]
    rooms = set()
    full = False
    for path in code:
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "agents" and parts[1] in ROOM_TESTS:
            rooms.add(parts[1])
        else:
            full = True
    tests = set()
    if code and not full:
        for pattern in (*SAFETY, *(p for room in sorted(rooms) for p in ROOM_TESTS[room])):
            tests.update(str(p.relative_to(ROOT)) for p in (ROOT / "tests").glob(pattern))
        # Cross-room tests may have unrelated names. Keep every Python test
        # that references a changed room, using either paths or dotted imports.
        references = tuple(ref for room in rooms for ref in (f"agents/{room}", f"agents.{room}"))
        for directory in (ROOT / "tests", ROOT / "agents"):
            for pattern in ("test_*.py", "*_test.py"):
                for path in directory.rglob(pattern):
                    text = path.read_text(encoding="utf-8")
                    if any(ref in text for ref in references):
                        tests.add(str(path.relative_to(ROOT)))
        for room in rooms:
            tests.update(str(p.relative_to(ROOT)) for p in (ROOT / "agents" / room).rglob("test_*.py"))
    savings = any(path.startswith(SAVINGS_PATHS) for path in code)
    return {"run": bool(code), "full": full, "tisan": full or "tisan" in rooms,
            "tests": ["."] if full else sorted(tests), "savings": savings}


def changed_paths() -> list[str]:
    base, head = os.environ["DIFF_BASE"], os.environ["DIFF_HEAD"]
    # PRs use the merge base; pushes compare the previous and new main snapshots.
    separator = "..." if os.environ["EVENT_NAME"] == "pull_request" else ".."
    if not base or set(base) == {"0"}:
        return ["unknown-change"]  # Initial push: run everything.
    diff = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}{separator}{head}"], cwd=ROOT
    )
    return [p for p in diff.decode().split("\0") if p]


if __name__ == "__main__":
    if sys.argv[1:] == ["test"]:
        targets = json.loads(os.environ["TEST_TARGETS"])
        if not targets:
            raise SystemExit("No tests selected")
        raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", *targets], cwd=ROOT))
    scope = select(changed_paths())
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, value in scope.items():
            output.write(f"{name}={json.dumps(value)}\n")
    print(json.dumps(scope, indent=2))
