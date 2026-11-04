"""Load the room from the Dockerfile's copied layout, not the repo import path."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_packaged_room_registers_street_law_without_uncopied_modules(tmp_path):
    image = tmp_path / 'placeholder-image'
    image.mkdir()
    context = ROOT / 'agents'
    for line in (context / 'zenas/Dockerfile').read_text().splitlines():
        if not line.startswith('COPY '):
            continue
        fields = shlex.split(line)
        if not fields or fields[0] != 'COPY':
            continue
        source, target = fields[1:]
        if not target.startswith('/app/'):
            continue  # dependency manifests do not affect this import smoke test
        source, target = context / source, image / target.removeprefix('/app/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            shutil.copyfile(source, target)
    check = '''
from pathlib import Path
import app
from agents.zenas.core import street_law_api
assert Path(app.__file__).resolve() == Path.cwd() / 'app.py'
assert Path(street_law_api.__file__).resolve() == Path.cwd() / 'agents/zenas/core/street_law_api.py'
paths = {route.path for route in app.app.routes if hasattr(route, 'path')}
assert '/street-law/sessions' in paths
assert '/street-law/sessions/{session_id}/questions/{case_id}/release' in paths
print('placeholder packaged Street Law routes ready')
'''
    result = subprocess.run([sys.executable, '-c', check], cwd=image,
        env={**os.environ, 'PYTHONPATH': str(image), 'TANK_REGION': 'za', 'AGENT_NAME': 'zenas'},
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
