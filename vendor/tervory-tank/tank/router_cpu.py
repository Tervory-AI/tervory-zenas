"""Bounded offline CPU benchmark process and immutable checkpoint transport."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

GIB = 1024 ** 3
MAX_DOWNLOAD = 5 * GIB
MAX_RSS = 12 * GIB
WALL_SECONDS = 600
DOWNLOAD_SECONDS = 1800
ROOT = Path(__file__).resolve().parents[1]
INTERPRETERS = {'cpu':str(ROOT/'.venv/bin/python'), 'current':sys.executable}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify(root, files):
    for item in files:
        path = Path(root) / item['name']
        if path.stat().st_size != item['bytes'] or sha256(path) != item['sha256']:
            raise ValueError('Checkpoint size/hash mismatch')


def download(specs, root, *, opener=urllib.request.urlopen):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if sum(f['bytes'] for spec in specs.values() for f in spec['files']) > MAX_DOWNLOAD:
        raise ValueError('Checkpoint download exceeds byte budget')
    if shutil.disk_usage(root).free < 8 * GIB:
        raise ValueError('Checkpoint download needs eight GiB free disk')
    started = time.monotonic()
    for model, spec in specs.items():
        folder = root / model
        folder.mkdir(exist_ok=True)
        for item in spec['files']:
            name = item['name']
            if Path(name).name != name or name.endswith(('.pt', '.pth', '.bin')):
                raise ValueError('Only reviewed checkpoint files may be downloaded')
            path = folder / name
            if path.exists() and path.stat().st_size == item['bytes'] and sha256(path) == item['sha256']:
                continue
            part = path.with_suffix(path.suffix + '.part')
            try:
                url = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}"
                offset = part.stat().st_size if part.exists() else 0
                if offset > item['bytes']:
                    part.unlink()
                    offset = 0
                request = urllib.request.Request(url,headers={'Range':f'bytes={offset}-'} if offset else {})
                with opener(request, timeout=20) as response:
                    resumed = offset and getattr(response,'status',200)==206
                    if resumed and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                        raise ValueError('Checkpoint range mismatch')
                    if not resumed:
                        offset = 0  # Server ignored Range: overwrite instead of appending.
                    size, digest = offset, hashlib.sha256()
                    if resumed:
                        with part.open('rb') as previous:
                            while block := previous.read(1024*1024):
                                digest.update(block)
                    with part.open('ab' if resumed else 'wb') as stream:
                        while block := response.read(1024 * 1024):
                            size += len(block)
                            if size > item['bytes']:
                                raise ValueError('Checkpoint byte budget exceeded')
                            if time.monotonic() - started > DOWNLOAD_SECONDS:
                                raise TimeoutError('Checkpoint transfer deadline exceeded')
                            stream.write(block)
                            digest.update(block)
                if size != item['bytes'] or digest.hexdigest() != item['sha256']:
                    raise ValueError('Checkpoint size/hash mismatch')
                part.replace(path)
            except ValueError:
                part.unlink(missing_ok=True)
                raise
        (folder / 'REVISION').write_text(spec['revision'] + '\n')


def run_worker(runtime, worker, input_path, output):
    if runtime not in INTERPRETERS:
        raise ValueError('CPU interpreter must be allowlisted')
    if runtime == 'cpu' and Path(worker).resolve() != ROOT/'tank/router_cpu_worker.py':
        raise ValueError('CPU worker must be the reviewed source')
    env = {**os.environ, 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1',
           'CUDA_VISIBLE_DEVICES':'', 'OMP_NUM_THREADS':'2', 'MKL_NUM_THREADS':'2',
           'TOKENIZERS_PARALLELISM':'false', 'PYTHONDONTWRITEBYTECODE':'1'}
    started, peak = time.monotonic(), 0
    # Logs remain task-local; reports never include prompts or exception values.
    with Path(output).with_suffix('.log').open('w') as log:
        process = subprocess.Popen([INTERPRETERS[runtime], str(worker), str(input_path), str(output)],
                                   env=env, stdout=log, stderr=log)
        try:
            while process.poll() is None:
                status = Path(f'/proc/{process.pid}/status')
                if status.exists():
                    rss = next((line.split()[1] for line in status.read_text().splitlines() if line.startswith('VmRSS:')), '0')
                    peak = max(peak, int(rss) * 1024)
                if peak > MAX_RSS:
                    raise RuntimeError('CPU process exceeded twelve GiB RSS')
                if time.monotonic() - started > WALL_SECONDS:
                    raise TimeoutError('CPU process exceeded ten-minute wall budget')
                time.sleep(.1)
            if process.returncode:
                raise RuntimeError('CPU worker failed; inspect task-local log')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    result = json.loads(Path(output).read_text())
    return {**result, 'observed_peak_rss_bytes': peak, 'worker_wall_ms': (time.monotonic() - started) * 1000}
