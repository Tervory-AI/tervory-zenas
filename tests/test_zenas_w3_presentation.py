from pathlib import Path
import subprocess


def test_zenas_w3_card_presentation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", str(root / "tests/zenas_w3_presentation.cjs")],
                            cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
