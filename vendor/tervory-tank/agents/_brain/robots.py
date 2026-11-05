"""Public request guard and optional advisory robots parsing.

Public navigation does not fetch or enforce robots.txt. Host blocks belong to
one check run; they are never used as permission to access a login.
"""
from __future__ import annotations

import time
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit

from brain.security import public_url

CACHE_SECONDS = 86400
USER_AGENT = "Tervory/1.0 (+https://tervory.com)"
MAX_ROBOTS_BYTES = 256_000
cache: dict[str, tuple[float, Policy]] = {}


@dataclass
class Run:
    """Fresh for each check, shared only by that check's requests/redirects."""
    stopped_hosts: set[str] = field(default_factory=set)


class HostBlocked(ValueError):
    """This run already encountered a block at the requested host."""


def stop_host(run: Run | None, url: str) -> None:
    if run is not None:
        host, _ = public_url(url)
        run.stopped_hosts.add(host.rstrip('.').removeprefix('www.'))


def host_stopped(run: Run | None, url: str) -> bool:
    host, _ = public_url(url)
    return run is not None and host.rstrip('.').removeprefix('www.') in run.stopped_hosts


def normalized_path(value: str) -> str:
    # RFC 9309 §2.2.2: decode only unreserved ASCII escapes; keep reserved
    # escapes distinct and encode non-ASCII octets before comparing paths.
    unreserved = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
    value = re.sub(r'%([0-9a-fA-F]{2})', lambda match: chr(int(match[1], 16))
        if chr(int(match[1], 16)) in unreserved else '%' + match[1].upper(), value)
    return quote(value, safe="/?=&;,:@!+'()[]%*$.~-_")


def path_matches(pattern: str, target: str) -> bool:
    """Prefix matching with * and terminal $, without regex backtracking."""
    anchored = pattern.endswith('$')
    if anchored:
        pattern = pattern[:-1]
    parts = pattern.split('*')
    if not target.startswith(parts[0]):
        return False
    if len(parts) == 1:
        return not anchored or target == parts[0]
    position = len(parts[0])
    for part in parts[1:-1]:
        found = target.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    if anchored:
        return len(target) - len(parts[-1]) >= position and target.endswith(parts[-1])
    return target.find(parts[-1], position) >= 0


class Policy:
    """RFC 9309 groups, wildcard/end anchors, and most-specific Allow ties."""
    def __init__(self) -> None:
        self.groups: list[tuple[list[str], list[tuple[str, bool]]]] = []

    def parse(self, lines: list[str]) -> None:
        agents, rules = [], []
        saw_rules = False
        for line in lines:
            key, separator, value = line.split('#', 1)[0].partition(':')
            if not separator:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == 'user-agent':
                if saw_rules:
                    self.groups.append((agents, rules))
                    agents, rules, saw_rules = [], [], False
                agents.append(value.lower())
            elif key in {'allow', 'disallow'} and agents:
                saw_rules = True
                if value == '*':
                    value = '/*'
                if value.startswith('/'):
                    rules.append((normalized_path(value), key == 'allow'))
        if agents:
            self.groups.append((agents, rules))

    def can_fetch(self, agent: str, url: str) -> bool:
        groups = [rules for agents, rules in self.groups if agent.lower() in agents]
        if not groups:
            groups = [rules for agents, rules in self.groups if '*' in agents]
        parsed = urlsplit(url)
        target = normalized_path((parsed.path or '/') + ('?' + parsed.query if parsed.query else ''))
        matches = [(len(pattern.replace('*', '').removesuffix('$')), allow)
                   for rules in groups for pattern, allow in rules if path_matches(pattern, target)]
        return max(matches)[1] if matches else True


class RobotsDenied(ValueError):
    pass


async def allowed(context, url: str, timeout: int = 10000) -> bool:
    """Advisory parser for explicit callers; never a navigation gate."""
    public_url(url)
    parsed = urlsplit(url)
    origin = f'{parsed.scheme}://{parsed.netloc}'
    saved = cache.get(origin)
    if saved and time.monotonic() - saved[0] < CACHE_SECONDS:
        return saved[1].can_fetch('Tervory', url)
    policy = Policy()
    target = origin + '/robots.txt'
    try:
        for _ in range(8):
            public_url(target)
            response = await context.request.get(target, timeout=timeout, max_redirects=0,
                                                 headers={"User-Agent": USER_AGENT})
            if response.status in {301, 302, 303, 307, 308}:
                target = urljoin(target, response.headers.get('location', ''))
                continue
            if response.status == 404:
                policy.parse(['User-agent: *', 'Allow: /'])
            elif response.status == 200:
                if int(response.headers.get('content-length', '0')) > MAX_ROBOTS_BYTES:
                    return False
                payload = await response.body()
                if len(payload) > MAX_ROBOTS_BYTES:
                    return False
                policy.parse(payload.decode('utf-8-sig', errors='replace').splitlines())
            else:
                # Includes forbidden robots and unavailable policy. Do not guess
                # that a site consents when its policy cannot be fetched.
                policy.parse(['User-agent: *', 'Disallow: /'])
            if len(cache) >= 1024:
                cache.pop(min(cache, key=lambda host: cache[host][0]))
            cache[origin] = (time.monotonic(), policy)
            return policy.can_fetch('Tervory', url)
    except Exception:
        return False
    return False


async def require_allowed(context, url: str, timeout: int = 10000, run: Run | None = None) -> None:
    """Keep URL safety and run-scoped stops, without requesting robots.txt."""
    public_url(url)
    if host_stopped(run, url):
        raise HostBlocked(url)
