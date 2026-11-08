"""The real SDK probe lives at Tank's paid-compute boundary."""
from pathlib import Path
import subprocess
import sys

subprocess.run([sys.executable, str(Path(__file__).resolve().parents[2] / 'tank/training_jobs/hf_sdk_probe.py')],
               check=True, timeout=30)
