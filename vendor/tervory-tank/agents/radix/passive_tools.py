"""A ZAP passive baseline and one pinned upstream Nuclei header-only template.

No activeScan, AJAX browser, forms, fuzzing, OAST, template discovery or updates.
Raw requests, responses, cookies, CLI output and evidence are never persisted.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from pathlib import Path

import yaml

from passive_store import target_url

PROXY = 'http://radix-passive-proxy:8082'
TEMPLATE = '/opt/radix-passive/http-missing-security-headers.yaml'


def zap_plan(target: str, directory: Path) -> dict:
    target_url(target)
    return {'env': {'contexts': [{'name': 'verified', 'urls': [target],
                 'includePaths': [re.escape(target) + '.*']}],
                 'parameters': {'failOnError': True, 'failOnWarning': True}},
            'jobs': [
                {'type': 'passiveScan-config', 'parameters': {'scanOnlyInScope': True}},
                # Reuse ZAP's existing pinned proxy/lease for the load check.
                # A mismatch warns; failOnWarning refuses the whole assessment.
                {'type': 'requestor', 'requests': [{'url': target, 'method': 'GET', 'responseCode': 200}]},
                {'type': 'spider', 'parameters': {'context': 'verified', 'url': target,
                    'maxDuration': 1, 'maxDepth': 2, 'maxChildren': 5,
                    'threadCount': 1, 'processForm': False,
                    'postForm': False, 'parseRobotsTxt': False, 'parseSitemapXml': False}},
                {'type': 'passiveScan-wait', 'parameters': {'maxDuration': 1}},
                {'type': 'report', 'parameters': {'template': 'traditional-json',
                    'reportDir': str(directory), 'reportFile': 'zap.json',
                    'reportTitle': 'Radix passive baseline'}, 'sites': [target.rstrip('/')]},
            ]}


def zap_command(plan: Path, home: Path) -> list[str]:
    # Network add-on's official connection/rate-limit options. No proxy exclusions.
    config = {'network.connection.httpProxy.enabled': 'true',
              'network.connection.httpProxy.host': 'radix-passive-proxy',
              'network.connection.httpProxy.port': '8082',
              'network.ratelimit.rules.rule(0).description': 'verified-only',
              'network.ratelimit.rules.rule(0).enabled': 'true',
              'network.ratelimit.rules.rule(0).matchStr': '.*',
              'network.ratelimit.rules.rule(0).regex': 'true',
              'network.ratelimit.rules.rule(0).reqsPerSec': '1',
              'network.ratelimit.rules.rule(0).groupBy': 'RULE',
              'autoupdate.checkOnStart': 'false', 'autoupdate.downloadNewRelease': 'false',
              'autoupdate.downloadNewAddon': 'false', 'autoupdate.installAddonUpdates': 'false',
              'spider.processForm': 'false', 'spider.postForm': 'false'}
    command = ['/opt/zap/zap.sh', '-cmd', '-dir', str(home), '-autorun', str(plan)]
    for key, value in config.items():
        command.extend(['-config', key + '=' + value])
    return command


def nuclei_command(target: str, report: Path) -> list[str]:
    target_url(target)
    return ['/usr/local/bin/nuclei', '-u', target, '-t', TEMPLATE,
            '-proxy', PROXY, '-proxy-internal', '-no-interactsh', '-disable-update-check',
            '-disable-redirects', '-no-httpx', '-rate-limit', '1', '-concurrency', '1',
            '-bulk-size', '1', '-retries', '0', '-timeout', '15',
            '-jsonl-export', str(report), '-omit-raw', '-omit-template',
            '-stats', '-stats-json', '-stats-interval', '1', '-no-color']


async def command(argv: list[str], directory: Path) -> tuple[int, bytes, bytes]:
    # Do not pass room/database/provider credentials to scanner children.
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(directory),
           'LANG': 'C.UTF-8', 'JAVA_OPTS': '-Xmx512m', 'JAVA_TOOL_OPTIONS': '-Xmx512m',
           'DISABLE_NUCLEI_TEMPLATES_PUBLIC_DOWNLOAD': 'true'}
    process = await asyncio.create_subprocess_exec(*argv, cwd=directory, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=240)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    if len(stdout) + len(stderr) > 4_000_000:
        raise RuntimeError('scanner output limit')
    return process.returncode, stdout, stderr


def finding(tool: str, severity: str, rule: str, target: str) -> dict:
    if severity not in {'info', 'low', 'medium', 'high', 'critical'}:
        raise ValueError('unknown finding severity')
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,200}', rule):
        raise ValueError('invalid finding rule')
    return {'tool': tool, 'severity': severity, 'rule': rule, 'file': target, 'line': 0}


def rule_text(item: dict, explanation, remediation) -> dict:
    # Only upstream static rule fields survive; never alert instances or CLI rows.
    texts = {key: value for key, value in
             (('explanation', explanation), ('remediation', remediation))
             if isinstance(value, str) and value.strip()}
    if texts:
        item['fix'] = json.dumps({item['rule']: texts}, ensure_ascii=False)
    return item


def zap_findings(report: dict, target: str) -> list[dict]:
    if not isinstance(report.get('site'), list) or not report['site']:
        raise ValueError('incomplete baseline')
    findings = []
    for site in report['site']:
        if site.get('@name', '').removesuffix('/') != target.removesuffix('/'):
            raise ValueError('unexpected baseline site')
        if not isinstance(site.get('alerts'), list):
            raise ValueError('incomplete baseline')
        for alert in site['alerts']:
            severity = {'0': 'info', '1': 'low', '2': 'medium', '3': 'high'}.get(str(alert.get('riskcode')))
            findings.append(rule_text(finding('zap', severity, str(alert['pluginid']), target),
                                      alert.get('desc'), alert.get('solution')))
    return findings


def nuclei_findings(report: str, target: str) -> list[dict]:
    findings = []
    try:
        template = yaml.safe_load(Path(TEMPLATE).read_text())
        info = template['info'] if template.get('id') == 'http-missing-security-headers' else {}
    except FileNotFoundError:
        info = {}
    for line in report.splitlines():
        row = json.loads(line)
        if row.get('template-id') != 'http-missing-security-headers' or row.get('type') != 'http':
            raise ValueError('unexpected template')
        if row.get('matched-at', '').removesuffix('/') != target.removesuffix('/'):
            raise ValueError('unexpected nuclei target')
        findings.append(rule_text(finding('nuclei', row['info']['severity'], row['template-id'], target),
                                  info.get('description'), info.get('remediation')))
    return findings


async def collect(target: str, directory: Path, *, run=command) -> list[dict]:
    target_url(target)
    # No independent HTTP client accepts a caller URL. ZAP performs the root
    # load check only after the persisted DNS-authorized egress lease exists.
    plan = directory / 'baseline.yaml'
    plan.write_text(yaml.safe_dump(zap_plan(target, directory)))
    rc, _, _ = await run(zap_command(plan, directory / 'zap-home'), directory)
    if rc != 0:
        raise RuntimeError('baseline incomplete')
    findings = zap_findings(json.loads((directory / 'zap.json').read_text()), target)
    rc, stdout, stderr = await run(nuclei_command(target, directory / 'nuclei.jsonl'), directory)
    if rc != 0:
        raise RuntimeError('nuclei incomplete')
    stats = []
    for line in (stdout + b'\n' + stderr).splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(row, dict) and 'requests' in row and 'errors' in row:
            stats.append(row)
    if not stats or int(stats[-1]['errors']) != 0 or int(stats[-1]['requests']) < 1:
        raise RuntimeError('nuclei requests unconfirmed')
    findings.extend(nuclei_findings((directory / 'nuclei.jsonl').read_text(), target))
    return findings
