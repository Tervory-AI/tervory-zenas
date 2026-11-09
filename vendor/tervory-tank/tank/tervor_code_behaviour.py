"""Bounded Python observations; arbitrary-Python behaviour grading is blocked.

Only trusted, predeclared test calls are selected on the host. Candidate source
is data until imported by the isolated worker. Candidate commands never run.
Stdout and interpreter state are candidate-controlled, so matching claims never
authorize a passing behaviour grade.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import tempfile
import time

from tank import tervor_code as baseline
from tank.tervor_registry import digest, encoded

SPEC = baseline.FIXTURES.parent / 'behaviour.json'
WORKER = Path(__file__).with_name('tervor_code_worker.py')
LIMITS = dict(processes=1, cpu_soft_seconds=1, cpu_hard_seconds=2,
    memory_bytes=128 * 1024 * 1024, output_bytes=16 * 1024,
    tmpfs_bytes=16 * 1024 * 1024, wall_seconds=3, open_files=32)
MAX_SOURCE = 64 * 1024


class InfrastructureError(RuntimeError):
    """Required kernel isolation or trusted bootstrap is unavailable."""


class Refused(ValueError):
    """Patch violates the declared path, command or repository contract."""


def bounded_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a bounded regular JSON file')
    with path.open('rb') as stream:
        raw = stream.read(baseline.MAX_BYTES + 1)
    if len(raw) > baseline.MAX_BYTES:
        raise ValueError('JSON exceeds the size limit')
    def nonfinite(value: str) -> None:
        raise ValueError('Nonfinite JSON')
    return json.loads(raw, parse_constant=nonfinite)


def suite(data: dict, spec: dict) -> list[dict]:
    """Bind reviewed tests to existing source/split hashes, not candidate input."""
    if spec.get('schema') != 1 or set(spec.get('tasks', {})) != {c['id'] for c in data['cases']}:
        raise ValueError('Behaviour tests must cover the sealed baseline task IDs')
    families, tests, result = {}, {}, []
    for case in data['cases']:
        task = spec['tasks'][case['id']]
        # Stable upstream/source family excludes revisions and task renames.
        family = digest(case['source']['reference'])
        if task['family_sha256'] != family:
            raise ValueError('Source family does not match')
        if family in families and families[family] != case['split']:
            raise ValueError('Held-out source family overlaps another split')
        families[family] = case['split']
        if not set(task['writable']).issubset(case['expected']['files']):
            raise ValueError('Writable paths must be predeclared in the reference')
        if not 1 <= len(task['tests']) <= 12:
            raise ValueError('Expected one to twelve predeclared tests per task')
        for test in task['tests']:
            if test['kind'] in ('call', 'attribute'):
                if (not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*(\.[a-zA-Z_][a-zA-Z_0-9]*)*', test['module'])
                        or not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*', test['function'])
                        or not isinstance(test['args'], list)):
                    raise ValueError('Invalid predeclared Python call')
                overrides = test.get('overrides', {})
                if not set(overrides).issubset(case['input']['files']) or not baseline.valid_files(overrides):
                    raise ValueError('Mutation may only replace declared input files')
            elif test['kind'] != 'commands':
                raise ValueError('Unsupported test kind')
        fingerprint = digest(task['tests'])
        if fingerprint in tests and tests[fingerprint] != case['split']:
            raise ValueError('Held-out tests overlap another split')
        tests[fingerprint] = case['split']
        result.append(dict(case=case, task=task, family_sha256=family, tests_sha256=fingerprint))
    return result


def checked_files(files: object) -> None:
    if not baseline.valid_files(files) or len(files) > 16:
        raise Refused('unsafe_path_or_file_count')
    if any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*){0,4}', path)
           or PurePosixPath(path).as_posix() != path for path in files):
        raise Refused('noncanonical_path')
    if len(encoded(files).encode()) > MAX_SOURCE:
        raise Refused('source_size')


def patch(case: dict, task: dict, answer: object) -> dict:
    if not isinstance(answer, dict) or set(answer) != {'files', 'commands'}:
        raise Refused('answer_shape')
    files, commands = answer['files'], answer['commands']
    checked_files(files)
    if not isinstance(commands, list) or any(command not in baseline.SAFE_COMMANDS for command in commands):
        raise Refused('candidate_command')
    before = case['input']['files']
    if not set(files).issubset(set(before) | set(task['writable'])):
        raise Refused('test_or_unknown_file_tampering')
    for path, text in before.items():
        if path not in task['writable'] and files.get(path) != text:
            raise Refused('repository_rule')
    return files


class Sandbox:
    """One non-root process per invocation; no Docker daemon or host repo mount."""
    def __init__(self) -> None:
        self.bwrap = '/usr/bin/bwrap' if Path('/usr/bin/bwrap').is_file() else None
        self.ready = False
        self.evidence = {}

    def _argv(self, directory: Path, status_fd: int) -> list[str]:
        if not self.bwrap or not all(Path(p).exists() for p in ('/usr/bin/python3', '/usr/lib', '/usr/lib64')):
            raise InfrastructureError('missing_bubblewrap_or_system_python')
        return [self.bwrap, '--json-status-fd', str(status_fd), '--unshare-all', '--unshare-user', '--unshare-net', '--unshare-pid',
            '--disable-userns', '--assert-userns-disabled', '--die-with-parent', '--new-session',
            '--as-pid-1', '--cap-drop', 'ALL', '--uid', '65534', '--gid', '65534', '--clearenv',
            '--setenv', 'PATH', '/usr/bin', '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONHASHSEED', '0',
            '--ro-bind', '/usr/bin/python3', '/usr/bin/python3', '--ro-bind', '/usr/lib', '/usr/lib',
            '--ro-bind', '/usr/lib64', '/usr/lib64', '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64', '--dev', '/dev',
            '--size', str(LIMITS['tmpfs_bytes']), '--tmpfs', '/tmp',
            '--ro-bind', str(directory / 'source'), '/work',
            '--ro-bind', str(directory / 'worker.py'), '/worker.py',
            '--chdir', '/work', '--remount-ro', '/', '--', '/usr/bin/python3', '-I', '-S', '-B', '/worker.py']

    def _launch(self, files: dict, request: dict) -> dict:
        checked_files(files)
        payload = encoded(request).encode()
        if len(payload) > MAX_SOURCE:
            raise Refused('test_request_size')
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='tervor-code-behaviour-') as temporary:
            directory = Path(temporary)
            source = directory / 'source'
            source.mkdir()
            # Only validated relative text files enter a new disposable directory.
            for name, content in files.items():
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            (directory / 'worker.py').write_bytes(WORKER.read_bytes())
            with (tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors,
                  tempfile.TemporaryFile() as status):
                try:
                    process = subprocess.Popen(self._argv(directory, status.fileno()), stdin=subprocess.PIPE,
                        stdout=output, stderr=errors, start_new_session=True, close_fds=True, pass_fds=(status.fileno(),))
                except (OSError, ValueError) as error:
                    raise InfrastructureError('sandbox_launch_failed') from error
                timed_out = False
                try:
                    process.communicate(payload, timeout=LIMITS['wall_seconds'])
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=2)
                output.seek(0)
                raw = output.read(LIMITS['output_bytes'] + 1)
                status.seek(0)
                try:
                    setup = [json.loads(line) for line in status.read(4096).splitlines()]
                except ValueError:
                    setup = []
                child_started = any(type(row.get('child-pid')) is int for row in setup)
                errors.seek(0)
                stderr = errors.read(LIMITS['output_bytes'] + 1)
        result = dict(elapsed_seconds=round(time.monotonic() - started, 6), exit_code=process.returncode,
                      output_sha256=hashlib.sha256(raw).hexdigest(), output_bytes=len(raw),
                      stderr_sha256=hashlib.sha256(stderr).hexdigest(), stderr_bytes=len(stderr))
        if not child_started:
            raise InfrastructureError('namespace_setup_failed')
        if timed_out:
            return dict(result, status='timeout', reason='wall_deadline')
        if max(len(raw), len(stderr)) >= LIMITS['output_bytes']:
            return dict(result, status='output_limit', reason='bounded_output')
        if process.returncode in (-signal.SIGXCPU, 128 + signal.SIGXCPU, -signal.SIGKILL, 128 + signal.SIGKILL):
            return dict(result, status='cpu_or_signal_limit', reason='worker_signal_exit')
        if process.returncode:
            return dict(result, status='candidate_failure', reason='worker_exit')
        try:
            value = json.loads(raw)
            encoded(value)  # Refuse NaN/Infinity before comparing untrusted JSON.
            if not isinstance(value, dict):
                raise ValueError('Expected worker object')
        except (ValueError, UnicodeDecodeError, RecursionError):
            return dict(result, status='candidate_failure', reason='invalid_worker_output')
        return dict(result, status='completed', value=value)

    def probe(self) -> dict:
        self.ready = False
        try:
            result = self._launch({}, {'probe': True})
            value = result.get('value', {})
            checks = value.get('checks', {})
            if (result['status'] != 'completed' or len(checks) != 8 or not all(v is True for v in checks.values())
                    or set(value.get('environment', {})) != {'PATH', 'LANG', 'PWD', 'PYTHONHASHSEED'}
                    or value.get('open_descriptors') != []
                    or value.get('uid') != 65534 or value.get('memory_bytes') != LIMITS['memory_bytes']):
                raise InfrastructureError('trusted_isolation_probe_failed')
            self.ready = True
            self.evidence = dict(status='ready', backend='bubblewrap_seccomp_rlimit', limits=LIMITS,
                checks=checks, python=value['python'],
                python_executable_sha256=hashlib.sha256(Path('/usr/bin/python3').read_bytes()).hexdigest(),
                bubblewrap_sha256=hashlib.sha256(Path(self.bwrap).read_bytes()).hexdigest(), worker_sha256=hashlib.sha256(WORKER.read_bytes()).hexdigest())
        except (InfrastructureError, OSError, subprocess.SubprocessError):
            self.evidence = dict(status='infrastructure_error', reason='secure_isolation_unavailable',
                                 backend='bubblewrap_seccomp_rlimit', limits=LIMITS)
        return self.evidence

    def run(self, files: dict, test: dict) -> dict:
        if not self.ready:
            raise InfrastructureError('isolation_probe_required')
        observation = self._launch(files, {**{key: test[key] for key in ('module', 'function', 'args')},
                                           'operation': test['kind']})
        if observation['status'] == 'completed':
            # This channel cannot prove invocation or trusted serialization.
            # Candidate import code can emit the exact envelope and exit zero,
            # or modify json/builtins/worker frames in the same interpreter.
            observation['untrusted_claim'] = observation.pop('value')
            observation.update(status='integrity_unverified', reason='candidate_controls_result_channel')
        return observation


def evaluate(item: dict, answer: object, sandbox: Sandbox) -> dict:
    case, task = item['case'], item['task']
    result = dict(id=case['id'], split=case['split'], family_sha256=item['family_sha256'],
        source_sha256=case['source']['sha256'], provenance_sha256=case['source']['provenance_sha256'],
        tests_sha256=item['tests_sha256'], patch_sha256=digest(answer), passed=False, tests=[])
    try:
        files = patch(case, task, answer)
    except Refused as error:
        return dict(result, status='refused', reason=str(error))
    if not sandbox.ready:
        return dict(result, status='infrastructure_error', reason='secure_isolation_unavailable')
    for test in task['tests']:
        if test['kind'] == 'commands':
            row = dict(name=test['name'], status='completed', contract_only=True,
                       passed=answer['commands'] == test['expected'])
        else:
            try:
                execution = sandbox.run(files | test.get('overrides', {}), test)
            except (InfrastructureError, OSError, subprocess.SubprocessError):
                return dict(result, status='infrastructure_error', reason='sandbox_invocation_failed')
            # No arbitrary-Python execution has an authenticated grading channel.
            # Even a future adapter returning completed must not reopen grading.
            observed = execution.pop('untrusted_claim', execution.pop('value', {}))
            if execution['status'] == 'completed':
                execution.update(status='integrity_unverified', reason='candidate_controls_result_channel')
            row = dict(name=test['name'], passed=False, **execution)
            row['claim_matches_expected'] = encoded(observed) == encoded(test['expected'])
            row['grading_integrity'] = 'not_established'
            if observed.get('kind') == 'exception' and observed.get('type') in (
                    'PermissionError', 'FileNotFoundError', 'OSError', 'AssertionError',
                    'ModuleNotFoundError', 'AttributeError', 'MemoryError'):
                row['exception_type'] = observed['type']
            row.pop('value', None)  # Raw candidate output never enters the persisted report.
        result['tests'].append(row)
    result['contract_only'] = all(test['kind'] == 'commands' for test in task['tests'])
    result['contract_matched'] = result['contract_only'] and all(row['passed'] for row in result['tests'])
    result['passed'] = False
    status = 'contract_checked' if result['contract_only'] else 'integrity_unverified'
    for outcome in ('timeout', 'cpu_or_signal_limit', 'output_limit', 'candidate_failure'):
        if any(row['status'] == outcome for row in result['tests']):
            status = outcome
            break
    return dict(result, status=status)


def report(data: dict, spec: dict, predictions: dict | None = None, *, split: str = 'gold',
           sandbox: Sandbox | None = None) -> dict:
    if split not in ('train', 'validation', 'gold'):
        raise ValueError('Invalid split')
    items = [item for item in suite(data, spec) if item['case']['split'] == split]
    ids = {item['case']['id'] for item in items}
    if predictions is not None and (not isinstance(predictions, dict) or set(predictions) != ids):
        raise Refused('prediction_ids')
    sandbox = sandbox or Sandbox()
    isolation = sandbox.probe()
    controls = {
        'reference_fixture': {i['case']['id']: i['case']['expected'] for i in items},
        'no_change': {i['case']['id']: dict(files=i['case']['input']['files'], commands=[]) for i in items}}
    if predictions is not None:
        controls['candidate'] = predictions
    comparisons = {}
    for name, answers in controls.items():
        rows = [evaluate(item, answers[item['case']['id']], sandbox) for item in items]
        comparisons[name] = dict(n=len(rows), passed=0,
            contract_matches=sum(r.get('contract_matched', False) for r in rows),
            unverified_cases=sum(r['status'] == 'integrity_unverified' for r in rows),
            infrastructure_errors=sum(r['status'] == 'infrastructure_error' for r in rows), rows=rows)
    return dict(schema=2, model_code='tervor-code', product=baseline.PRODUCT, tenant_id=baseline.PLATFORM,
        method='isolated_untrusted_python_observation', split=split, holdout=split != 'train',
        harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        data_sha256=digest(data), tests_sha256=digest(spec), split_hashes=baseline.preflight(data)['split_hashes'],
        isolation=isolation, comparisons=comparisons, eligible=False, model_quality_measured=False,
        grading=dict(available=False, reason='Candidate and serializer share an interpreter; stdout is unauthenticated',
                     required='Independent trustworthy grading boundary before any Python behaviour pass'),
        cost_usd=0, ledger_ids=[],
        status='infrastructure_error' if isolation['status'] != 'ready' or any(
            value['infrastructure_errors'] for value in comparisons.values()) else 'grading_blocked',
        training_preflight=baseline.preflight(data))


def integrity_demonstrations(data: dict, spec: dict, *, sandbox: Sandbox | None = None) -> dict:
    """Exact-envelope and shared-interpreter attacks: observations, never grades."""
    sandbox = sandbox or Sandbox()
    isolation = sandbox.probe()
    selected = deepcopy(next(i for i in suite(data, spec) if i['case']['id'] == 'gold-multi'))
    selected['task']['tests'] = selected['task']['tests'][:1]
    selected['tests_sha256'] = digest(selected['task']['tests'])
    envelope = '{"kind":"return","value":16}'
    variants = {
        'exact_envelope_import_exit': f'import os\nos.write(1, {envelope.encode()!r})\nos._exit(0)\n',
        'exact_envelope_function_exit': f'def page_size():\n    import os\n    os.write(1, {envelope.encode()!r})\n    os._exit(0)\n',
        'serializer_monkeypatch': f'import json\njson.dumps = lambda *a, **k: {envelope!r}\ndef page_size():\n    return -1\n',
        'worker_frame_introspection': f'def page_size():\n    import inspect\n    scope = inspect.currentframe().f_back.f_globals\n    scope["json"].dumps = lambda *a, **k: {envelope!r}\n    return -1\n',
        'ordinary_reference_observation': selected['case']['expected']['files']['page.py'],
        'ordinary_wrong_observation': 'def page_size():\n    return -1\n',
    }
    results = {}
    for name, source in variants.items():
        answer = deepcopy(selected['case']['expected'])
        answer['files']['page.py'] = source
        results[name] = evaluate(selected, answer, sandbox)
    selected = deepcopy(next(i for i in suite(data, spec) if i['case']['id'] == 'gold-tests'))
    selected['task']['tests'] = selected['task']['tests'][1:]
    selected['tests_sha256'] = digest(selected['task']['tests'])
    answer = deepcopy(selected['case']['expected'])
    envelope = b'{"kind":"exception","type":"AssertionError"}'
    answer['files']['test_discount.py'] = f'import os\nos.write(1, {envelope!r})\nos._exit(0)\n'
    results['exact_exception_import_exit'] = evaluate(selected, answer, sandbox)
    return dict(isolation=isolation, scenarios=results)


def demonstrations(data: dict, spec: dict) -> dict:
    """Adversarial fixtures, never model outputs; each runs only after the probe."""
    sandbox = Sandbox()
    isolation = sandbox.probe()
    item = next(i for i in suite(data, spec) if i['case']['id'] == 'gold-bug')
    reference = deepcopy(item['case']['expected'])
    variants = {
        'valid_but_wrong': 'def inside(n):\n    return False\n',
        'self_reported_pass': 'def inside(n):\n    import os\n    os.write(1, b\'{"passed":true}\')\n    os._exit(0)\n',
        'nonfinite_output': 'def inside(n):\n    import os\n    os.write(1, b\'{"kind":"return","value":NaN}\')\n    os._exit(0)\n',
        'equivalent_correct': 'def inside(n):\n    return n in range(11)\n',
        'network': 'def inside(n):\n    import socket\n    socket.socket()\n    return True\n',
        'runtime_test_tampering': 'def inside(n):\n    open("/worker.py", "w").write("tampered")\n    return True\n',
        'host_escape': 'def inside(n):\n    return open("/workspace/tervory-tank/AGENTS.md").read()\n',
        'process_creation': 'def inside(n):\n    import os\n    os.fork()\n    return True\n',
        'wall_timeout': 'def inside(n):\n    import time\n    time.sleep(30)\n    return True\n',
        'cpu_limit': 'def inside(n):\n    while True:\n        pass\n',
        'memory_limit': 'def inside(n):\n    return len(bytearray(256 * 1024 * 1024))\n',
        'output_limit': 'def inside(n):\n    print("x" * 32768)\n    return True\n',
    }
    results = {}
    # Only one predeclared call per adversarial fixture keeps the smoke proof bounded.
    attack_item = deepcopy(item)
    attack_item['task']['tests'] = attack_item['task']['tests'][:1]
    attack_item['tests_sha256'] = digest(attack_item['task']['tests'])
    for name, source in variants.items():
        answer = dict(files={'bounds.py': source}, commands=[])
        results[name] = evaluate(item if name == 'equivalent_correct' else attack_item, answer, sandbox)
    for name, path in (('path_escape', '../outside.py'), ('test_patch_tampering', '_harness_test.py')):
        answer = deepcopy(reference)
        answer['files'][path] = 'raise SystemExit(0)\n'
        results[name] = evaluate(attack_item, answer, sandbox)
    answer = deepcopy(reference)
    answer['commands'] = [['sh', '-c', 'echo untrusted']]
    results['host_command'] = evaluate(attack_item, answer, sandbox)
    results.update(integrity_demonstrations(data, spec, sandbox=sandbox)['scenarios'])
    return dict(isolation=isolation, scenarios=results)


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', required=True, action='store_true')
    parser.add_argument('--predictions', type=Path)
    parser.add_argument('--split', choices=('validation', 'gold'), default='gold')
    parser.add_argument('--demonstrate-controls', action='store_true')
    args = parser.parse_args(argv)
    data, spec = baseline.load(), bounded_json(SPEC)
    predictions = bounded_json(args.predictions) if args.predictions else None
    value = report(data, spec, predictions, split=args.split)
    if args.demonstrate_controls:
        value['demonstrations'] = demonstrations(data, spec)
    print(encoded(value))
    return 3 if value['status'] == 'infrastructure_error' else 2


if __name__ == '__main__':
    raise SystemExit(cli())
