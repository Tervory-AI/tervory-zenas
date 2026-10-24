"""Workspace reviewer release UI: Approve with a matter id hits matters/{id}/release."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "workspace" / "app" / "review"
ROUTE = ROOT / "workspace" / "app" / "api" / "zenas" / "matters" / "[id]" / "release" / "route.ts"


def _node_test(path: Path) -> None:
    result = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--import",
            str(ROOT / "tests" / "market_lab_register.mjs"),
            "--test",
            str(path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_uses_matter_release_helper() -> None:
    _node_test(REVIEW / "release-path.test.ts")


def test_desk_approve_with_matter_posts_release() -> None:
    desk = (REVIEW / "desk.tsx").read_text(encoding="utf-8")
    assert "usesMatterRelease" in desk
    assert "/api/zenas/matters/${item.matterId}/release" in desk
    assert "Release to client" in desk
    assert ROUTE.is_file()
    route = ROUTE.read_text(encoding="utf-8")
    assert "matters/${id}/release" in route
    assert "requireReviewer" in route
    assert "signOffRefusal" in route
