"""Current-source browser/server identity boundary, entirely offline."""
import subprocess
from pathlib import Path


def test_moot_hearing_workspace():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", "tests/moot_hearing/workspace.test.cjs"],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
