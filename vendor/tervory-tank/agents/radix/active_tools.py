"""Bounded synchronous active ZAP policy; no OAST or runtime add-on downloads."""
import asyncio
import json
import re
from pathlib import Path

import yaml

from active_policy import SCAN_SECONDS
from passive_store import target_url
from passive_r6 import command
from passive_tools import zap_command as passive_command, zap_findings

# These bundled release rules make synchronous same-origin requests, no callbacks.
RULES = (40003, 40012, 40018)  # CRLF, reflected XSS, SQL injection.


def zap_plan(target, directory):
    target_url(target)
    return {'env': {'contexts': [{'name': 'verified-active', 'urls': [target],
                'includePaths': ['^' + re.escape(target) + '.*$']}],
            'parameters': {'failOnError': True, 'failOnWarning': True}}, 'jobs': [
        {'type': 'script', 'parameters': {'action': 'add', 'type': 'httpsender',
            'engine': 'ECMAScript : Graal.js', 'name': 'active-guard', 'source': str(directory/'guard.js')}},
        {'type': 'requestor', 'requests': [{'url': target, 'method': 'GET', 'responseCode': 200}]},
        {'type': 'spider', 'parameters': {'context': 'verified-active', 'url': target,
            'maxDuration': 5, 'maxDepth': 5, 'maxChildren': 50, 'threadCount': 2,
            'processForm': False, 'postForm': False, 'parseRobotsTxt': False, 'parseSitemapXml': False}},
        {'type': 'activeScan', 'parameters': {'context': 'verified-active',
            'maxScanDurationInMins': 14, 'maxRuleDurationInMins': 2, 'threadPerHost': 2,
            'handleAntiCSRFTokens': False}, 'policyDefinition': {
            'defaultStrength': 'LOW', 'defaultThreshold': 'OFF',
            'rules': [{'id': rule, 'strength': 'LOW', 'threshold': 'MEDIUM'} for rule in RULES]}},
        {'type': 'passiveScan-wait', 'parameters': {'maxDuration': 1}},
        {'type': 'report', 'parameters': {'template': 'traditional-json',
            'reportDir': str(directory), 'reportFile': 'zap.json'}, 'sites': [target.rstrip('/')]},
    ]}


def zap_command(plan, home):
    # Same network-only peer/port, but this run has a distinct active Proxy lease.
    # The passive command and its limits are not changed.
    return passive_command(plan, home) + ['-Xmx512m', '-config', 'network.ratelimit.rules.rule(0).reqsPerSec=2',
        '-config', 'connection.timeoutInSecs=30', '-config', 'callhome.tel.enabled=false']


async def collect(target, directory, *, run=command):
    _, domain = target_url(target)
    directory = Path(directory)
    script = Path(__file__).with_name('active_guard.js').read_text()
    (directory/'guard.js').write_text(script.replace('__HOST__', json.dumps(domain))
        .replace('__OUTPUT__', json.dumps(str(directory/'coverage.json'))))
    plan = directory/'active.yaml'
    plan.write_text(yaml.safe_dump(zap_plan(target, directory)))
    findings, reason = [], None
    try:
        async with asyncio.timeout(SCAN_SECONDS):
            rc, stdout, _ = await run(zap_command(plan, directory/'zap-home'), directory, SCAN_SECONDS)
        if rc != 0:
            reason = 'scanner_incomplete'
        # HostProcess writes rule timeouts to zap.log rather than stdout.
        log = directory/'zap-home/zap.log'
        if log.exists():
            if log.stat().st_size > 4_000_000:
                reason = 'scanner_log_limit'
            else:
                stdout += log.read_bytes()
        # Time-limited ZAP jobs may exit zero; retain that as incomplete.
        if re.search(rb'skipped plugin|stopped host/plugin|(?:time limit|maximum duration|max(?:imum)? scan duration).*(?:reach|exceed)|(?:reach|exceed).*(?:time limit|maximum duration)', stdout, re.I):
            reason = 'scan_time_limit'
    except TimeoutError:
        reason = 'scan_timeout'
    except (OSError, ValueError, RuntimeError):
        reason = 'scanner_unavailable'
    try:
        report = directory/'zap.json'
        if report.stat().st_size > 4_000_000:
            raise ValueError('report limit')
        findings = zap_findings(json.loads(report.read_text()), target)
    except (OSError, ValueError, KeyError, TypeError):
        reason = reason or 'report_incomplete'
    try:
        coverage = json.loads((directory/'coverage.json').read_text())
        if (not isinstance(coverage, dict) or not coverage.get('requests') or not coverage.get('active_responses')
                or coverage.get('responses') != coverage.get('requests')):
            raise ValueError('no active requests')
        if coverage.get('blocked') or coverage.get('refused') or coverage.get('capped'):
            reason = reason or 'blocked_or_bounded'
    except (OSError, ValueError, TypeError):
        reason = reason or 'coverage_unconfirmed'
    for finding in findings:
        metadata = json.loads(finding.get('fix') or '{}')
        metadata['_measured'] = {'mode': 'active', 'active': True}
        finding['fix'] = json.dumps(metadata)
    return {'findings': findings, 'checks': {'active': {'status': 'partial' if reason else 'complete',
        **({'reason': reason} if reason else {})}}}
