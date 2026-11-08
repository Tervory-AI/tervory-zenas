"""Install Tank's existing offline SDK transport; never a product model client."""
from pathlib import Path
import sys


def install():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tank'))
    from training_jobs.hf_test_network import install as tank_fixture
    tank_fixture()


install()
