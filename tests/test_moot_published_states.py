"""Exercise real Workspace readers/components against offline transport boundaries."""
import subprocess
from pathlib import Path


def test_moot_published_states():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", "tests/live_page_sources.test.cjs"],
        cwd=root, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
