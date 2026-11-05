"""Bounded watch measurements inside the existing, TXT-authorized passive lease."""
from __future__ import annotations

import asyncio
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import signal
import ssl
import time
from urllib.parse import urljoin, urlsplit

import httpx

from live import tls_findings
from passive_store import target_url
from passive_tools import PROXY, rule_text
from scanlib.comply import scan_html


class Budget:
    def __init__(self, seconds=390, *, now=time.monotonic):
        self.now = now
        self.end = now() + max(0, seconds)

    def remaining(self):
        return max(0, self.end - self.now())

    def require(self, seconds=1):
        if self.remaining() < seconds:
            raise TimeoutError('lease_budget')


async def command(argv, directory, seconds):
    # No database, Tank, provider or storage credentials in untrusted tool children.
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(directory),
           'TMPDIR': str(directory), 'LANG': 'C.UTF-8',
           'PLAYWRIGHT_BROWSERS_PATH': '/opt/radix-browsers'}
    def prefer_child_oom():
        # The existing room cgroup enforces 1 GiB. Make a large ClamAV child
        # the OOM victim so the application can record the skip and continue.
        try:
            Path('/proc/self/oom_score_adj').write_text('1000')
        except OSError:
            os._exit(125)
    process = await asyncio.create_subprocess_exec(*argv, cwd=directory, env=env,
        preexec_fn=prefer_child_oom if argv[0] == '/usr/bin/clamscan' else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=seconds)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    if len(out) + len(err) > 2_000_000:
        raise ValueError('tool_output_limit')
    return process.returncode, out, err


def measured(tool, rule, url, facts, *, severity='unknown', item=None, text=None, help_url=None):
    result = {'tool': tool, 'rule': rule, 'file': url, 'line': 0,
              'item': item or url, 'severity': severity, 'facts': facts}
    # Metadata travels through the existing findings fix column, without new UI labels.
    rule_text(result, text, help_url)
    encoded = json.loads(result.get('fix', '{}'))
    encoded['_measured'] = {'item': result['item'], 'facts': facts}
    result['fix'] = json.dumps(encoded)
    return result


class Links(HTMLParser):
    def __init__(self, target):
        super().__init__()
        self.target, self.links = target, []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href')
        try:
            url = urljoin(self.target, href or '')
            p, root = urlsplit(url), urlsplit(self.target)
            if (p.scheme == 'https' and p.hostname == root.hostname and p.port in (None,443)
                    and not p.username and not p.password and not p.query and not p.fragment
                    and '\\' not in url and len(url) <= 4096 and url not in self.links):
                self.links.append(url)
        except ValueError:
            pass


def page_changes(current, previous):
    return [measured('sha256', 'changed' if url in previous and url in current else
            'new' if url in current else 'gone', url,
            {'url': url, 'old_sha256': previous.get(url), 'new_sha256': current.get(url)})
            for url in sorted(current.keys() | previous.keys()) if current.get(url) != previous.get(url)]


def tls_rows(text):
    """Keep only complete JSON objects if a bounded testssl process was cut off."""
    decoder, rows = json.JSONDecoder(), []
    text = text.lstrip()
    if not text.startswith('['):
        raise ValueError('tls_report_incomplete')
    offset = 1
    while offset < len(text):
        while offset < len(text) and text[offset] in ' \r\n\t,':
            offset += 1
        if offset < len(text) and text[offset] == ']':
            return rows, True
        try:
            row, offset = decoder.raw_decode(text, offset)
        except ValueError:
            break
        if not isinstance(row, dict) or 'id' not in row:
            raise ValueError('tls_report_incomplete')
        rows.append(row)
    return rows, False


def testssl_command(target, report):
    return ['/opt/testssl/testssl.sh', '--warnings', 'batch', '--color', '0',
            '--ip=proxy', '--nodns=none', '--proxy', 'radix-passive-proxy:8082',
            '--protocols', '--server-defaults', '--connect-timeout', '5',
            '--openssl-timeout', '5', '--jsonfile', str(report), target]


async def collect(target, directory, *, previous=None, budget=None, run=command, transport=None, notice_only=False):
    _, domain = target_url(target)
    budget = budget or Budget()
    directory = Path(directory)
    findings, checks, hashes = [], {}, {}
    previous = previous or {}

    async def step(name, work):
        try:
            budget.require(2)
            async with asyncio.timeout(budget.remaining()):
                data = await work()
            checks[name] = {'status': 'complete', **(data or {})}
        except (TimeoutError, FileNotFoundError, MemoryError) as exc:
            checks[name] = {'status': 'skipped', 'reason': 'lease_budget' if isinstance(exc, TimeoutError)
                            else 'memory_limit' if isinstance(exc, MemoryError) else 'tool_unavailable'}
        except Exception as exc:
            # Never record exception text, stdout, raw responses or cookie values.
            safe = {'tool_output_limit','incomplete_response','html_required','response_limit',
                    'page_unavailable','certificate_unavailable','tls_report_unavailable',
                    'tls_report_incomplete','tls_run_incomplete','signature_database_unavailable',
                    'malware_run_incomplete','browser_incomplete','memory_guard_unavailable'}
            reason = str(exc) if isinstance(exc, ValueError) and str(exc) in safe else type(exc).__name__
            checks[name] = {'status': 'skipped' if reason in {'signature_database_unavailable','memory_guard_unavailable'} else 'partial',
                            'reason': reason}
        if checks[name]['status'] != 'complete':
            tool = {'tls':'testssl.sh','certificate':'scan.tls','pages':'sha256','malware':'clamav',
                    'browser':'chromium','notices':'comply.gdpr','page_load':'http'}.get(name,name)
            findings.append(measured(tool, checks[name]['status'], target, checks[name], item=name))

    # verify=False is measurement-only, so expired certificates can be reported.
    # The separate testssl check measures certificate trust. No promotion claim.
    async with httpx.AsyncClient(proxy=PROXY if transport is None else None, transport=transport,
            trust_env=False, verify=False, timeout=20, follow_redirects=False) as client:
        pages, files = {}, {}
        cert, protocol = None, None

        async def fetch(url, *, missing_ok=False, file_only=False):
            nonlocal cert, protocol
            budget.require(2)
            async with client.stream('GET', url, headers={'Accept-Encoding': 'identity'}) as response:
                mime = response.headers.get('content-type', '')
                if missing_ok and response.status_code == 404:
                    return None, mime
                if response.status_code != 200:
                    raise ValueError('incomplete_response')
                if file_only and 'text/html' in mime.lower():
                    return None, mime
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1_000_000:
                        raise ValueError('response_limit')
                if url == target:
                    stream = response.extensions.get('network_stream')
                    peer = stream.get_extra_info('ssl_object') if stream else None
                    if peer:
                        certificate = directory / 'peer.pem'
                        certificate.write_text(ssl.DER_cert_to_PEM_cert(peer.getpeercert(binary_form=True)))
                        cert = ssl._ssl._test_decode_cert(str(certificate))
                        protocol = peer.version()
                return bytes(data), response.headers.get('content-type', '')

        async def pages_work():
            root, kind = await fetch(target)
            if 'text/html' not in kind.lower():
                raise ValueError('html_required')
            pages[target] = root.decode('utf-8', errors='replace')
            hashes[target] = hashlib.sha256(root).hexdigest()
            parser = Links(target)
            parser.feed(pages[target])
            # Bound both page count and file discoveries. Every fetch remains proxied.
            eligible = [u for u in parser.links if u != target]
            ordered = [u for u in eligible if u in previous] + [u for u in eligible if u not in previous]
            linked = ordered[:30]
            visited = []
            for url in linked:
                budget.require(5)
                visited.append(url)
                data, mime = await fetch(url, missing_ok=True)
                if data is None:
                    continue
                if 'text/html' in mime.lower() and len(pages) < 11:
                    pages[url] = data.decode('utf-8', errors='replace')
                    hashes[url] = hashlib.sha256(data).hexdigest()
                elif 'text/html' not in mime.lower():
                    files[url] = data
                if len(pages) >= 11:
                    break
            findings.extend(page_changes(hashes, previous))
            skipped = [{'url':u,'status':'skipped','reason':'page_limit' if len(pages)>=11 else 'link_limit'}
                       for u in eligible if u not in visited]
            if skipped:
                findings.append(measured('sha256','skipped',target,{'links':skipped},item='page_scope'))
            return {'pages': len(hashes), 'discovery_truncated': bool(skipped), 'skipped':skipped}

        if not notice_only:
            await step('pages', pages_work)
        else:
            async def root_only():
                data, kind = await fetch(target)
                if 'text/html' not in kind.lower():
                    raise ValueError('html_required')
                pages[target] = data.decode('utf-8', errors='replace')
            await step('page_load', root_only)

        async def notices_work():
            if not pages:
                raise ValueError('page_unavailable')
            for url, html in pages.items():
                result = scan_html(html, name='page.html', page_host=domain)
                for f in result['findings']:
                    findings.append(measured(f['tool'], f['rule'], url, {'page': url},
                        severity=f['severity'], text=f['fix']))
            return {'pages': len(pages)}
        await step('notices', notices_work)

        if not notice_only:
            async def cert_work():
                if cert is None:
                    raise ValueError('certificate_unavailable')
                for f in tls_findings(cert, protocol, target, host=domain):
                    findings.append(measured(f['tool'], f['rule'], target,
                        {'not_after': cert.get('notAfter'), 'protocol': protocol},
                        severity=f['severity'], text=f['fix']))
                return {'not_after': cert.get('notAfter'), 'protocol': protocol}
            await step('certificate', cert_work)

            async def tls_work():
                report = directory / 'testssl.json'
                timed_out = False
                try:
                    rc, _, _ = await run(testssl_command(target, report), directory, min(90, budget.remaining()))
                except TimeoutError:
                    rc, timed_out = -1, True
                if not report.exists():
                    raise ValueError('tls_report_unavailable')
                rows, complete = tls_rows(report.read_text())
                if not rows:
                    raise ValueError('tls_report_incomplete')
                for row in rows:
                    level = str(row.get('severity', 'unknown')).lower()
                    if level in {'ok','info'}:
                        continue
                    findings.append(measured('testssl.sh', str(row['id']), target,
                        {'finding': row.get('finding')}, severity=level, text=row.get('finding')))
                if timed_out:
                    raise TimeoutError('lease_budget')
                if rc or not complete:
                    raise ValueError('tls_run_incomplete')
                return {'scope': ['protocols','certificate'], 'full_scan': 'skipped', 'reason': 'proxy_limits'}
            await step('tls', tls_work)

            async def malware_work():
                rc, version, _ = await run(['/usr/bin/clamscan','--version'], directory, min(5,budget.remaining()))
                if rc == 125:
                    raise ValueError('memory_guard_unavailable')
                if rc:
                    raise ValueError('signature_database_unavailable')
                stamp = version.decode(errors='replace').strip().split('/')
                if len(stamp) < 3:
                    raise ValueError('signature_database_unavailable')
                # Discover additional file links from every fetched same-host page.
                urls = []
                for url, html in pages.items():
                    parser = Links(url); parser.feed(html)
                    urls.extend(u for u in parser.links if u not in pages and u not in urls)
                scanned = 0
                for number, url in enumerate(urls[:20]):
                    budget.require(5)
                    if url not in files:
                        data, mime = await fetch(url, file_only=True)
                        if data is None:
                            continue
                        files[url] = data
                    path = directory / ('download-' + str(number))
                    path.write_bytes(files[url])
                    rc, out, err = await run(['/usr/bin/clamscan','--no-summary','--max-filesize=1M',str(path)],
                                            directory, min(90,budget.remaining()))
                    if rc in {137, -9} or (rc == 2 and any(word in err.lower() for word in
                            (b'memory', b'allocat', b'map failed'))):
                        raise MemoryError
                    if rc == 125:
                        raise ValueError('memory_guard_unavailable')
                    if rc not in {0,1}:
                        raise ValueError('malware_run_incomplete')
                    scanned += 1
                    if rc == 1:
                        for line in out.decode(errors='replace').splitlines():
                            if line.startswith(str(path)+': ') and line.endswith(' FOUND'):
                                signature = line[len(str(path))+2:-6]
                                findings.append(measured('clamav', signature, url,
                                    {'url':url,'signature':signature,'database_date':stamp[2]}, text=signature))
                    path.unlink(missing_ok=True)
                skipped = [{'url':u,'status':'skipped','reason':'file_limit'} for u in urls[20:]]
                if skipped:
                    findings.append(measured('clamav','skipped',target,{'links':skipped},item='file_scope'))
                return {'scanned':scanned,'database_date':stamp[2], 'discovery_truncated':bool(skipped), 'skipped':skipped}
            await step('malware', malware_work)

        async def browser_work():
            rc, out, _ = await run(['/usr/bin/node','/app/browser/check.cjs',target,'notice-only' if notice_only else 'full'], directory,
                                   min(45,budget.remaining()))
            if rc in {137,-9}:
                raise MemoryError
            if rc:
                raise ValueError('browser_incomplete')
            data = json.loads(out)
            for f in data['findings']:
                findings.append(measured(f['tool'],f['rule'],f['file'],f['facts'], severity=f['severity'],
                    item=f['item'],text=f['explanation'],help_url=f['remediation']))
            for cookie in data['cookies']:
                item = json.dumps([cookie['name'],cookie['domain'],cookie['path']])
                findings.append(measured('chromium','cookie',target,cookie,item=item))
            return {k:data[k] for k in ('cookies','third_party_refused','client_refused')}
        await step('browser', browser_work)

    return {'findings': findings, 'checks': checks,
            'page_hashes': hashes if checks.get('pages',{}).get('status') == 'complete' else None}


async def collect_notices(target, directory, *, run=command, transport=None):
    return await collect(target, directory, notice_only=True, budget=Budget(350), run=run, transport=transport)
