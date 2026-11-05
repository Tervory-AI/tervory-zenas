"""Real Chromium, local-only pages. Run directly in CI or under pytest."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def workflow_python(name):
    """Execute the actual workflow snippets with offline dependencies below."""
    workflow = (ROOT / '.github/workflows/live-check.yml').read_text()
    step = workflow.split('- name: ' + name, 1)[1].split('\n      - ', 1)[0]
    script = step.split("python3 - <<'PY'", 1)[1].split('\n', 1)[1]
    script = script.split('\n          PY', 1)[0]
    return '\n'.join(line[10:] for line in script.splitlines())


def load_gate():
    """Import .github/scripts/live_check_gate.py with an offline watchdog.norm_status."""
    import importlib.util
    import sys
    watchdog = types.ModuleType('watchdog')
    watchdog.norm_status = lambda value: str(value or '').strip().lower()
    sys.modules['watchdog'] = watchdog
    spec = importlib.util.spec_from_file_location(
        'live_check_gate_offline', ROOT / '.github/scripts/live_check_gate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCoolify:
    """Scripted Coolify: each snapshot (one GET /applications) reads the next state.

    A state maps app name -> (status, latest deployment id, latest deployment status).
    The last state repeats. Records the order of snapshots and probes.
    """

    UUIDS: ClassVar[dict[str, str]] = {'main': 'main-id', 'agent-rig': 'rig-id', 'agent-zenas': 'zenas-id',
             'agent-zenas-za': 'zenas-za-id', 'agent-template': 'template-id',
             'tervory-tank-za': 'za-id'}

    def __init__(self, states):
        self.states, self.index, self.events = states, -1, []

    def state(self):
        return self.states[min(self.index, len(self.states) - 1)]

    def api(self, method, path):
        assert method == 'GET', (method, path)
        if path == '/applications':
            self.index += 1
            self.events.append('read')
            return 200, [{'uuid': self.UUIDS[name], 'name': name if name != 'main' else 'tervory-tank:main',
                          'status': row[0]} for name, row in self.state().items()]
        uuid = path.split('/deployments/applications/', 1)[1].split('?', 1)[0]
        name = next(n for n, u in self.UUIDS.items() if u == uuid)
        _, deployment, status = self.state()[name]
        return 200, {'deployments': [{'deployment_uuid': deployment, 'status': status}]}

    def probe(self, codes):
        codes = list(codes)

        def run():
            self.events.append('probe')
            return codes.pop(0)
        return run


def quiet(main='d1', rig='r1', zenas='z1', **over):
    """Settled Coolify. A string sets that app's latest deployment id; a tuple sets the row."""
    def row(value):
        return value if isinstance(value, tuple) else ('running:healthy', value, 'finished')
    state = {'main': row(main), 'agent-rig': row(rig), 'agent-zenas': row(zenas),
             # ZA and the template never hold back a Boston check.
             'agent-zenas-za': ('exited', 'za1', 'in_progress'), 'agent-template': ('exited', 't1', 'failed'),
             'tervory-tank-za': ('running:unhealthy', 'za2', 'queued')}
    state.update(over)
    return state


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class GateTests(unittest.TestCase):
    def setUp(self):
        self.gate = load_gate()
        self.clock = Clock()
        patcher = patch('builtins.print')
        self.printed = patcher.start()
        self.addCleanup(patcher.stop)

    def run_gate(self, coolify, codes, **kw):
        return self.gate.run(coolify.api, 'main-id', coolify.probe(codes), clock=self.clock,
                             sleep=self.clock.sleep, **kw)

    def output(self):
        return '\n'.join(' '.join(map(str, call.args)) for call in self.printed.call_args_list)

    def test_probe_waits_for_deploy_to_finish_and_health_on_two_reads(self):
        coolify = FakeCoolify([
            quiet(main=('running:healthy', 'd2', 'queued')),
            quiet(main=('running:healthy', 'd2', 'in_progress')),
            quiet(main=('running:unknown', 'd2', 'finished')),
            quiet(main=('running:healthy', 'd2', 'finished')),
            quiet(main=('running:healthy', 'd2', 'finished')),
        ])
        self.assertEqual(self.run_gate(coolify, [0]), 0)
        self.assertEqual(coolify.events, ['read'] * 5 + ['probe'])

    def test_one_green_read_is_not_enough_when_a_new_deploy_appears(self):
        coolify = FakeCoolify([quiet(main='d1'), quiet(main='d2'), quiet(main='d2')])
        self.assertEqual(self.run_gate(coolify, [0]), 0)
        self.assertEqual(coolify.events, ['read', 'read', 'read', 'probe'])

    def test_31_oct_overlap_is_rechecked_after_the_other_deploy_settles(self):
        # #471: Coolify was settled, then an API deploy of main replaced containers while the
        # browser probed, so every Workspace host returned 503. That is not a site failure.
        coolify = FakeCoolify([
            quiet(main='d829'), quiet(main='d829'),                    # settled before probe
            quiet(main=('running:healthy', 'd831', 'in_progress')),    # read after the failed probe
            quiet(main=('running:healthy', 'd831', 'in_progress')),
            quiet(main='d831'), quiet(main='d831'),                    # settled again
        ])
        self.assertEqual(self.run_gate(coolify, [1, 0]), 0)
        self.assertEqual(coolify.events, ['read', 'read', 'probe', 'read', 'read', 'read', 'read', 'probe'])
        self.assertIn('main redeployed', self.output())

    def test_failure_while_coolify_stayed_quiet_is_real_and_not_retried(self):
        coolify = FakeCoolify([quiet()])
        self.assertEqual(self.run_gate(coolify, [1, 0]), 1)
        self.assertEqual(coolify.events.count('probe'), 1)
        self.assertIn('no deploy ran', self.output())

    def test_room_deploy_during_probe_counts_as_overlap(self):
        coolify = FakeCoolify([quiet(), quiet(), quiet(zenas='z2'), quiet(zenas='z2')])
        self.assertEqual(self.run_gate(coolify, [1, 0]), 0)
        self.assertIn('agent-zenas redeployed', self.output())

    def test_overlap_on_every_attempt_fails_bounded(self):
        states = []
        for n in range(1, 4):
            states += [quiet(main=f'd{n}'), quiet(main=f'd{n}')]
        states.append(quiet(main='d9'))
        coolify = FakeCoolify(states)
        self.assertEqual(self.run_gate(coolify, [1, 1, 1, 0], attempts=3), 1)
        self.assertEqual(coolify.events.count('probe'), 3)

    def test_never_settles_times_out_without_probing(self):
        for state in [quiet(main=('running:unknown', 'd1', 'finished')),
                      quiet(main=('running:healthy', 'd1', 'in_progress')),
                      {k: v for k, v in quiet().items() if k != 'agent-rig'}]:
            with self.subTest(state=state):
                self.clock.now = 0
                coolify = FakeCoolify([state])
                with self.assertRaises(SystemExit) as error:
                    self.run_gate(coolify, [0], total_s=120)
                self.assertEqual(error.exception.code, 1)
                self.assertNotIn('probe', coolify.events)

    def test_unreadable_coolify_is_not_settled(self):
        with self.assertRaises(SystemExit):
            self.gate.settle(lambda *_: (502, 'bad gateway'), 'main-id', 30, clock=self.clock,
                             sleep=self.clock.sleep)


def workflow(name):
    import yaml
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text())


def group_for(expression, op):
    """Evaluate coolify-ops' concurrency group expression for one op (tiny subset):
    `(op == 'a' || op == 'b') && 'group' || (...) && 'group2' || format(...)`."""
    import re
    body = expression.strip()[3:-2].strip()
    pairs = re.findall(r"\(([^()]*)\) && '([^']+)' \|\| ", body)
    fallback = re.sub(r"^(?:\([^()]*\) && '[^']+' \|\| )+", '', body)
    for condition, group in pairs:
        ops = [part.split("== '", 1)[1].rstrip("'") for part in condition.split(' || ')]
        if op in ops:
            return group
    return fallback.replace("format('coolify-ops-{0}', github.run_id)", 'coolify-ops-RUN')


class WorkflowTests(unittest.TestCase):
    def test_deploy_on_merge_queues_one_deploy_and_live_check_at_a_time(self):
        flow = workflow('deploy-on-merge.yml')
        self.assertEqual(flow['concurrency'], {'group': 'coolify-deploy', 'cancel-in-progress': False,
                                               'queue': 'max'})
        check = flow['jobs']['live-check']
        # The live check is a job of the same run, so the group is held until it finishes.
        self.assertEqual(check['needs'], 'deploy')
        self.assertEqual(check['uses'], './.github/workflows/live-check.yml')
        self.assertNotIn('concurrency', flow['jobs']['deploy'])

    def test_every_workflow_that_redeploys_coolify_shares_the_group(self):
        deployers = {}
        for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
            text = path.read_text()
            if any(marker in text for marker in ['/deploy?', 'deploy_changed.py', 'room_setup.py']):
                deployers[path.name] = yaml_load(path)
        self.assertEqual(set(deployers), {'deploy-on-merge.yml', 'coolify-ops.yml'})
        ops = deployers['coolify-ops.yml']['concurrency']
        self.assertFalse(ops['cancel-in-progress'])
        self.assertEqual(ops['queue'], 'max')
        for op in ['deploy', 'room-setup']:
            self.assertEqual(group_for(ops['group'], op), 'coolify-deploy')
        for op in ['status', 'env-refs', 'create-room', 'backup-scope', 'nightly-status']:
            self.assertEqual(group_for(ops['group'], op), 'coolify-ops-RUN')
        # Both renormalise ops share one fixed group (never the deploy group), so two
        # dispatches can never run side by side.
        for op in ['zenas-renormalise', 'zenas-renormalise-report']:
            self.assertEqual(group_for(ops['group'], op), 'coolify-zenas-renormalise')

    def test_live_check_probes_through_the_coolify_gate(self):
        flow = workflow('live-check.yml')
        self.assertNotIn('inputs', flow[True]['workflow_call'])
        job = flow['jobs']['check']
        self.assertEqual(job['concurrency']['queue'], 'max')
        self.assertFalse(job['concurrency']['cancel-in-progress'])
        steps = {step.get('name'): step for step in job['steps']}
        live = steps['Open every live host signed out']
        self.assertIn('python3 .github/scripts/live_check_gate.py --output "$LIVE_CHECK_OUTPUT"', live['run'])
        self.assertNotIn('node ops/live_check.mjs', live['run'])
        self.assertEqual(live['env']['COOLIFY_TOKEN'], '${{ secrets.COOLIFY_TOKEN }}')
        self.assertNotIn('Wait for Coolify to report the public apps healthy', steps)
        self.assertIn('.github/scripts/live_check_gate.py', flow[True]['pull_request']['paths'])
        self.assertIn('.github/workflows/coolify-ops.yml', flow[True]['pull_request']['paths'])

    def test_failure_issue_is_updated_without_duplicates(self):
        opened = {}
        events = []

        class FakeGitHub:
            def __init__(self, *_):
                pass

            def ensure_label(self):
                pass

            def open_incidents(self):
                return opened

            def open_issue(self, title, body):
                opened[title] = {'number': 1}
                events.append(('create', title, body))

            def set_body(self, number, body):
                events.append(('update', number, body))

            def comment(self, number, body):
                events.append(('comment', number, body))

            def close(self, number):
                events.append(('close', number))

        watchdog = types.ModuleType('watchdog')
        watchdog.GitHub = FakeGitHub
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'results.json').write_text(json.dumps([
                {'host': 'good.example', 'passed': True, 'viewports': []},
                {'host': 'bad.example', 'passed': False, 'viewports': [
                    {'width': 375, 'reasons': ['Visible text: Fixture'], 'screenshot': 'bad.example-375.png'},
                ]},
            ]))
            env = {'LIVE_CHECK_OUTPUT': directory, 'GITHUB_REPOSITORY': 'offline/check',
                   'GITHUB_TOKEN': 'offline', 'GITHUB_SERVER_URL': 'https://github.com',
                   'GITHUB_RUN_ID': '123', 'ARTIFACT_URL': 'https://github.com/offline/artifacts/456'}
            with patch.dict(os.environ, env), patch.dict('sys.modules', watchdog=watchdog), \
                    patch('builtins.print'):
                for _ in range(2):
                    exec(compile(workflow_python('Open or update one issue per failed host'),
                                 '<issue-workflow>', 'exec'), {})
        self.assertEqual([event[0] for event in events], ['create', 'update'])
        self.assertEqual(events[0][1], 'Live check failed: bad.example')
        self.assertIn('bad.example-375.png', events[0][2])
        self.assertIn('https://github.com/offline/artifacts/456', events[0][2])

    def test_a_host_that_passes_again_closes_its_issue(self):
        events = []
        opened = {'Live check failed: good.example': {'number': 7}, 'Live check failed: bad.example': {'number': 8}}

        class FakeGitHub:
            def __init__(self, *_):
                pass

            def ensure_label(self):
                pass

            def open_incidents(self):
                return opened

            def open_issue(self, title, body):
                events.append(('create', title))

            def set_body(self, number, body):
                events.append(('update', number))

            def comment(self, number, body):
                events.append(('comment', number))

            def close(self, number):
                events.append(('close', number))

        watchdog = types.ModuleType('watchdog')
        watchdog.GitHub = FakeGitHub
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'results.json').write_text(json.dumps([
                {'host': 'good.example', 'passed': True, 'viewports': []},
                {'host': 'bad.example', 'passed': False, 'viewports': [
                    {'width': 375, 'reasons': ['HTTP 503'], 'screenshot': ''}]},
            ]))
            env = {'LIVE_CHECK_OUTPUT': directory, 'GITHUB_REPOSITORY': 'offline/check',
                   'GITHUB_TOKEN': 'offline', 'GITHUB_SERVER_URL': 'https://github.com',
                   'GITHUB_RUN_ID': '123', 'ARTIFACT_URL': ''}
            with patch.dict(os.environ, env), patch.dict('sys.modules', watchdog=watchdog), \
                    patch('builtins.print'):
                exec(compile(workflow_python('Open or update one issue per failed host'),
                             '<issue-workflow>', 'exec'), {})
        self.assertEqual(events, [('comment', 7), ('close', 7), ('update', 8)])


def yaml_load(path):
    import yaml
    return yaml.safe_load(path.read_text())


class Pages(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == '/disconnect':
            self.close_connection = True
            return
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/good')
            self.end_headers()
            return
        if self.path == '/slow.js':
            time.sleep(3)
        pages = {
            '/good': '<h1>Ready</h1><div hidden>Fixture</div><script>/* sample data */</script>',
            '/crash': '<h1>Application error</h1>',
            '/client': '<h1>A client-side exception has occurred</h1>',
            '/banned': '<h1>SAMPLE DATA Fixture [TEST] Demo price CVE-2024-0000 00000000-0000-0000-0000</h1>',
            '/throw': '<script>setTimeout(() => {throw Error("boom")}, 200)</script>',
            '/reject': '<script>setTimeout(() => Promise.reject(Error("boom")), 200)</script>',
            '/console': '<script>console.error("Uncaught Error: boom")</script>',
            '/http500': '<h1>Unavailable</h1>',
            '/timeout': '<h1>Waiting</h1><script src="/slow.js"></script>',
            '/mobile': '<style>@media(min-width:500px){.bad{display:none}}</style><p class="bad">Fixture</p>',
            '/cookie': '<script>document.body.textContent = document.cookie ? "Fixture" : "Ready";document.cookie="seen=1"</script>',
        }
        body = ('<!doctype html><html><body>' + pages.get(self.path, '') + '</body></html>').encode()
        self.send_response(503 if self.path == '/http500' else 200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LiveCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.paths = ['good', 'crash', 'client', 'banned', 'throw', 'reject', 'console',
                     'http500', 'timeout', 'mobile', 'redirect', 'cookie', 'cookie', 'disconnect']
        cls.urls = cls.directory / 'urls.json'
        cls.urls.write_text(json.dumps([
            f'http://127.0.0.1:{cls.server.server_port}/{item}' for item in cls.paths
        ]))
        cls.output = cls.directory / 'output'
        cls.command = ['node', 'ops/live_check.mjs', '--local-urls', str(cls.urls),
                       '--output', str(cls.output), '--timeout-ms', '1000']
        cls.browser_run = subprocess.run(cls.command, cwd=ROOT, text=True, capture_output=True, timeout=150)
        if not (cls.output / 'results.json').exists():
            raise AssertionError(cls.browser_run.stdout + cls.browser_run.stderr)
        cls.rows = json.loads((cls.output / 'results.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def reasons(self, index):
        return '; '.join(reason for row in self.rows[index]['viewports'] for reason in row['reasons'])

    def test_good_hidden_text_redirect_and_isolation_pass(self):
        for index in [0, 10, 11, 12]:
            with self.subTest(path=self.paths[index]):
                self.assertTrue(self.rows[index]['passed'], self.reasons(index))

    def test_visible_application_and_client_crashes_fail(self):
        for index, marker in [(1, 'Application error'), (2, 'client-side exception')]:
            self.assertFalse(self.rows[index]['passed'])
            self.assertIn(marker, self.reasons(index))

    def test_every_banned_token_fails(self):
        for marker in ['sample data', 'Fixture', '[TEST]', 'Demo price',
                       'CVE-2024-0000', '00000000-0000-0000-0000']:
            self.assertIn(marker, self.reasons(3))
        self.assertFalse(self.rows[3]['passed'])

    def test_runtime_throw_rejection_and_console_fail(self):
        for index in [4, 5, 6]:
            self.assertFalse(self.rows[index]['passed'])
            self.assertIn('exception', self.reasons(index))

    def test_http_failure_and_navigation_deadline(self):
        self.assertIn('HTTP 503', self.reasons(7))
        self.assertIn('did not load within 1000ms', self.reasons(8))

    def test_connection_failure_has_a_safe_browser_diagnostic(self):
        self.assertFalse(self.rows[13]['passed'])
        self.assertIn('ERR_EMPTY_RESPONSE', self.reasons(13))
        self.assertNotIn('http://', self.reasons(13))

    def test_mobile_visible_text_fails_host(self):
        row = self.rows[9]
        self.assertFalse(row['passed'])
        self.assertEqual(row['viewports'][0]['reasons'], [])
        self.assertIn('Visible text: Fixture', row['viewports'][1]['reasons'])

    def test_every_host_including_failures_has_both_screenshots(self):
        self.assertEqual(len(self.rows), len(self.paths))
        for row in self.rows:
            self.assertEqual([item['width'] for item in row['viewports']], [1440, 375])
            for view in row['viewports']:
                data = (self.output / view['screenshot']).read_bytes()
                self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(int.from_bytes(data[16:20], 'big'), view['width'])

    def test_mixed_run_is_red_and_summary_names_every_host(self):
        self.assertEqual(self.browser_run.returncode, 1, self.browser_run.stderr)
        summary = (self.output / 'summary.md').read_text()
        for row in self.rows:
            self.assertIn(row['host'], summary)
            self.assertIn(row['host'], self.browser_run.stdout)
        self.assertIn('Signed-in checks skipped', summary)

    def test_all_good_run_exits_zero(self):
        urls = self.directory / 'good.json'
        urls.write_text(json.dumps([f'http://127.0.0.1:{self.server.server_port}/good']))
        run = subprocess.run(['node', 'ops/live_check.mjs', '--local-urls', str(urls),
                              '--output', str(self.directory / 'good-output')], cwd=ROOT,
                             text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_test_mode_refuses_nonlocal_urls(self):
        urls = self.directory / 'invalid.json'
        urls.write_text(json.dumps(['https://app.tervory.com/']))
        run = subprocess.run(['node', 'ops/live_check.mjs', '--local-urls', str(urls),
                              '--output', str(self.directory / 'invalid-output')], cwd=ROOT,
                             text=True, capture_output=True, timeout=10)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Offline targets must use', run.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
