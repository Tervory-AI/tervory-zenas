"""Recreate Tank's layout from product sources and verified shared dependencies."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

import asyncpg
import pgserver

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
VENDOR = ROOT / 'vendor/tervory-tank'
PATHS = (ROOT / 'MOVED-PATHS.txt').read_text().splitlines()
TRACKED = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0')


def run(args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def configure_chromium():
    # Existing proof scripts request a system path unavailable in this cloud image.
    # Use Playwright's installed Chromium; preserve every browser option/assertion.
    import playwright.async_api
    import playwright.sync_api
    for api in [playwright.async_api, playwright.sync_api]:
        original = api.BrowserType.launch
        def launch(self, *args, _original=original, **kwargs):
            if kwargs.get('executable_path') == '/usr/bin/google-chrome' and not Path('/usr/bin/google-chrome').exists():
                kwargs['executable_path'] = self.executable_path
            return _original(self, *args, **kwargs)
        api.BrowserType.launch = launch


def test_layout(stage):
    shutil.copytree(VENDOR, stage, dirs_exist_ok=True)
    # Patch projections always read the immutable base, including when another
    # proposed patch has been applied to the disposable verification copy.
    (stage / 'vendor').mkdir(exist_ok=True)
    (stage / 'vendor/tervory-tank').symlink_to(VENDOR, target_is_directory=True)
    for name in TRACKED:
        if name and (any(name == p or name.startswith(p + '/') for p in PATHS)
                     or name.startswith('tank-patches/') or name == 'scripts/zenas'):
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
    # Test the proposed Workspace changes in the disposable verification copy.
    # Neither the read-only Tank checkout nor the immutable vendor is patched.
    gate4 = ROOT / 'tank-patches/gate4.patch'
    if gate4.is_file():
        run(['git', 'apply', '--check', gate4], cwd=stage)
        run(['git', 'apply', gate4], cwd=stage)
    gate5 = ROOT / 'tank-patches/gate5.patch'
    if gate5.is_file():
        run(['git', 'apply', '--check', gate5], cwd=stage)
        run(['git', 'apply', gate5], cwd=stage)
    moot = ROOT / 'tank-patches/moot.patch'
    if moot.is_file():
        run(['git', 'apply', '--check', moot], cwd=stage)
        run(['git', 'apply', moot], cwd=stage)
    sdk_patch = ROOT / 'tank-patches/training-sdk.patch'
    if sdk_patch.is_file():
        run(['git', 'apply', '--check', sdk_patch], cwd=stage)
        run(['git', 'apply', sdk_patch], cwd=stage)
    savings = ROOT / 'tank-patches/gate6.patch'
    if savings.is_file():
        run(['git', 'apply', '--check', savings], cwd=stage)
        run(['git', 'apply', savings], cwd=stage)
    deps = CACHE / 'workspace'
    deps.mkdir(exist_ok=True)
    lock = (stage / 'workspace/package-lock.json').read_bytes()
    fingerprint = hashlib.sha256(lock).hexdigest()
    stamp = deps / '.lock-sha256'
    if not (deps / 'node_modules').is_dir() or not stamp.exists() or stamp.read_text() != fingerprint:
        for name in ['package.json', 'package-lock.json']:
            shutil.copy2(stage / 'workspace' / name, deps / name)
        run(['npm', 'ci', '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=deps)
        stamp.write_text(fingerprint)
    (stage / 'workspace/node_modules').symlink_to(deps / 'node_modules', target_is_directory=True)


def check_secrets():
    # Inspect all retained history and all current Git-visible files. No allowlists.
    run(['gitleaks', 'git', '--no-banner', '--redact=100', '--ignore-gitleaks-allow',
         '--gitleaks-ignore-path', '/dev/null', '--log-opts=--all'], cwd=ROOT)
    visible = subprocess.check_output(['git', 'ls-files', '--cached', '--others',
                                       '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    with tempfile.TemporaryDirectory(prefix='zenas-secrets-') as directory:
        for name in visible:
            if name and (ROOT / name).is_file():
                target = Path(directory) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
        run(['gitleaks', 'dir', directory, '--no-banner', '--redact=100',
             '--ignore-gitleaks-allow', '--gitleaks-ignore-path', '/dev/null'])


def tests(stage):
    import pytest
    sdk = CACHE / 'hf-jobs'
    python = sdk / 'bin/python'
    expected = '1.7.2'
    ready = python.is_file() and subprocess.run([str(python), '-c',
        "import huggingface_hub; from huggingface_hub import HfApi; assert huggingface_hub.__version__ == '1.7.2'; assert callable(HfApi.list_jobs_hardware)"],
        capture_output=True).returncode == 0
    if not ready:
        run(['uv', 'venv', sdk])
        run(['uv', 'pip', 'install', '--python', python, 'huggingface-hub==1.7.2', 'asyncpg==0.30.0'])
    os.environ['HF_JOBS_PYTHON'] = str(python.absolute())
    os.chdir(stage)
    sys.path.insert(0, str(stage))
    sys.path.insert(0, str(stage / 'tests'))
    configure_chromium()
    targets = [p for p in PATHS if p.startswith('tests/') and
               ((p.endswith('.py') and Path(p).name.startswith('test_')) or
                ((stage / p).is_dir() and not p.startswith('tests/fixtures/')))]
    with tempfile.TemporaryDirectory(prefix='zenas-postgres-') as directory:
        server = pgserver.get_server(Path(directory) / 'postgres')
        try:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            pgserver.pg_ctl(['-w', 'restart', '-o', f'-h 127.0.0.1 -p {port}'], pgdata=server.pgdata)
            async def create():
                conn = await asyncpg.connect(f'postgresql://postgres@127.0.0.1:{port}/postgres')
                await conn.execute('CREATE DATABASE pooltest_test')
                await conn.close()
            asyncio.run(create())
            url = f'postgresql://postgres@127.0.0.1:{port}/pooltest_test'
            for name in ['POOL_SCHEMA_DATABASE_URL', 'MOOT_TEST_DATABASE_URL', 'ZENAS_SCHEMA_DATABASE_URL']:
                os.environ[name] = url
            results = CACHE / 'tests.xml'
            result = pytest.main(targets + ['-q', '--disable-socket', '--allow-unix-socket',
                '--allow-hosts=127.0.0.1,localhost,::1', '--timeout=120', '-p', 'no:cacheprovider',
                '--junitxml=' + str(results)])
            if result:
                raise SystemExit(result)
            import xml.etree.ElementTree as ET
            suites = list(ET.parse(results).getroot().iter('testsuite'))
            assert suites and sum(int(s.attrib['tests']) for s in suites) > 0
            assert not any(int(s.attrib.get('skipped', 0)) for s in suites), 'Skipped product tests are not allowed'
        finally:
            server.cleanup()


if __name__ == '__main__':
    check_secrets()
    with tempfile.TemporaryDirectory(prefix='zenas-check-') as directory:
        stage = Path(directory)
        test_layout(stage)
        tests(stage)
    print('All Zenas checks passed; no product tests skipped.', flush=True)
