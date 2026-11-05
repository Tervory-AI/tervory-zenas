"""Version ordering and OSV-style range matching. Pure functions, no network.

Three orderings:
- PyPI: PEP 440 (epochs ``1!2.0``, pre/post/dev releases, local versions).
- SEMVER / npm: Semantic Versioning 2.0 (pre-release identifiers, build ignored).
- generic: runtimes and apps (PHP, Node, Coolify, Docker): optional Debian-style
  epoch ``2:``, numeric release, then a pre-release (``RC1``, ``-beta.3``) that
  sorts before the release or a post-release (``-1ubuntu2``, ``p1``) after it.

Every function returns ``None`` instead of guessing when a version cannot be
parsed, so an unknown version never produces a false "safe" or false match.
"""
from __future__ import annotations

import re
from functools import cmp_to_key
from typing import Callable, Iterable

Key = tuple
_LOW, _HIGH = (0,), (2,)

_PEP440 = re.compile(r"""
    ^\s*v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?))?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    \s*$""", re.VERBOSE | re.IGNORECASE)
_PRE_NAMES = {"alpha": "a", "a": "a", "beta": "b", "b": "b", "c": "rc", "rc": "rc", "pre": "rc", "preview": "rc"}


def _trim(parts: Iterable[int]) -> tuple[int, ...]:
    parts = list(parts)
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def pep440_key(version: str) -> Key | None:
    match = _PEP440.match(version or "")
    if not match:
        return None
    g = match.groupdict()
    release = _trim(int(p) for p in g["release"].split("."))
    pre = (1, _PRE_NAMES[g["pre_l"].lower()], int(g["pre_n"] or 0)) if g["pre"] else None
    post = None
    if g["post"]:
        post = (1, int(g["post_n1"] or g["post_n2"] or 0))
    dev = (1, int(g["dev_n"] or 0)) if g["dev"] else None
    # PEP 440: 1.0.dev0 < 1.0a1 < 1.0 < 1.0.post1; a dev-only release sorts before its pre-releases.
    if pre is None and post is None and dev is not None:
        pre_key = _LOW
    else:
        pre_key = pre if pre is not None else _HIGH
    post_key = post if post is not None else _LOW
    dev_key = dev if dev is not None else _HIGH
    local_key: tuple = _LOW
    if g["local"]:
        local_key = (1, tuple((1, int(p), "") if p.isdigit() else (0, 0, p.lower())
                              for p in re.split(r"[-_.]", g["local"])))
    return (int(g["epoch"] or 0), release, pre_key, post_key, dev_key, local_key)


_SEMVER = re.compile(r"^\s*[v=]?\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\s*$")


def _identifiers(text: str) -> tuple:
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in text.split("."))


def semver_key(version: str) -> Key | None:
    match = _SEMVER.match(version or "")
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    # A release outranks any of its pre-releases; identifiers compare numerically, then ASCII.
    pre_key = (0, _identifiers(pre)) if pre else (1,)
    return (int(major), int(minor or 0), int(patch or 0), pre_key)


_GENERIC = re.compile(r"^\s*(?:(\d+):)?[vV]?(\d+(?:\.\d+)*)(.*)$")
_PRE_WORDS = re.compile(r"^[-_.~]?(dev|alpha|beta|preview|pre|rc|a|b)(?=$|[-_.\d])", re.IGNORECASE)
_PRE_RANK = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "preview": 3, "pre": 3, "rc": 3}


def _tokens(text: str) -> tuple:
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t.lower())
                 for t in re.findall(r"\d+|[A-Za-z]+", text))


def generic_key(version: str) -> Key | None:
    match = _GENERIC.match(version or "")
    if not match:
        return None
    epoch, release, rest = match.groups()
    rest = rest.split("+", 1)[0].strip()
    release_key = _trim(int(p) for p in release.split("."))
    if rest.startswith("~") or _PRE_WORDS.match(rest):
        word = _PRE_WORDS.match(rest)
        rank = _PRE_RANK[word.group(1).lower()] if word else 0
        tail = rest[word.end():] if word else rest[1:]
        phase = (0, rank, _tokens(tail))
    elif rest:
        phase = (2, 0, _tokens(rest))
    else:
        phase = (1, 0, ())
    return (int(epoch or 0), release_key, phase)


ORDERINGS: dict[str, Callable[[str], Key | None]] = {
    "pypi": pep440_key, "semver": semver_key, "npm": semver_key, "generic": generic_key,
}


def ordering(ecosystem: str, range_type: str = "ECOSYSTEM") -> Callable[[str], Key | None]:
    """OSV: a SEMVER range uses semver whatever the ecosystem; otherwise the ecosystem's own order."""
    if range_type.upper() == "SEMVER":
        return semver_key
    return ORDERINGS.get(normalise_ecosystem(ecosystem), generic_key)


_ECOSYSTEMS = {"pypi": "pypi", "pip": "pypi", "python": "pypi", "npm": "npm", "node": "npm",
               "composer": "packagist", "packagist": "packagist"}


def normalise_ecosystem(name: str) -> str:
    return _ECOSYSTEMS.get((name or "").strip().lower(), "generic")


def normalise_name(ecosystem: str, name: str) -> str:
    name = (name or "").strip().lower()
    if normalise_ecosystem(ecosystem) == "pypi":
        name = re.sub(r"[-_.]+", "-", name)
    return name


def compare(a: str, b: str, key: Callable[[str], Key | None]) -> int | None:
    ka, kb = key(a), key(b)
    if ka is None or kb is None:
        return None
    return (ka > kb) - (ka < kb)


def in_osv_range(version: str, events: list[dict], key: Callable[[str], Key | None]) -> bool | None:
    """OSV schema evaluation: walk events in version order; introduced opens, fixed/last_affected close.

    ``introduced: "0"`` means "from the beginning"; ``limit`` caps the whole range.
    Returns None when the version, or any boundary that matters, cannot be parsed.
    """
    current = key(version)
    if current is None:
        return None
    points: list[tuple[Key | None, str, str]] = []
    limits: list[Key] = []
    for event in events:
        if not isinstance(event, dict) or len(event) != 1:
            return None
        kind, value = next(iter(event.items()))
        if kind not in {"introduced", "fixed", "last_affected", "limit"} or not isinstance(value, str):
            return None
        if kind == "introduced" and value == "0":
            points.append((None, kind, value))
            continue
        bound = key(value)
        if bound is None:
            return None
        if kind == "limit":
            if value != "*":
                limits.append(bound)
            continue
        points.append((bound, kind, value))
    if limits and not any(current < limit for limit in limits):
        return False

    def order(left, right):
        if left[0] is None or right[0] is None:
            return (left[0] is not None) - (right[0] is not None)
        return (left[0] > right[0]) - (left[0] < right[0])

    vulnerable = False
    for bound, kind, _value in sorted(points, key=cmp_to_key(order)):
        if kind == "introduced" and (bound is None or current >= bound):
            vulnerable = True
        elif kind == "fixed" and current >= bound:
            vulnerable = False
        elif kind == "last_affected" and current > bound:
            vulnerable = False
    return vulnerable


_CONSTRAINT = re.compile(r"^\s*(>=|<=|>|<|==|=|!=)?\s*([^\s,]+)\s*$")


def in_constraints(version: str, spec: str, key: Callable[[str], Key | None]) -> bool | None:
    """GitHub advisory ranges such as ``>= 1.0.0, < 1.2.3`` or ``= 4.0.0``; every part must hold."""
    current = key(version)
    if current is None or not spec or not spec.strip():
        return None
    for part in spec.split(","):
        match = _CONSTRAINT.match(part)
        if not match:
            return None
        op, bound_text = match.groups()
        bound = key(bound_text)
        if bound is None:
            return None
        ok = {"": current == bound, None: current == bound, "=": current == bound, "==": current == bound,
              "!=": current != bound, ">": current > bound, ">=": current >= bound,
              "<": current < bound, "<=": current <= bound}[op]
        if not ok:
            return False
    return True


def in_cpe_bounds(version: str, bounds: dict, key: Callable[[str], Key | None]) -> bool | None:
    """NVD 2.0 cpeMatch: versionStart/End Including/Excluding, or an exact version."""
    current = key(version)
    if current is None:
        return None
    exact = bounds.get("exact")
    if exact not in (None, "*", "-", ""):
        other = key(exact)
        return None if other is None else current == other
    checks = (("versionStartIncluding", lambda c, b: c >= b), ("versionStartExcluding", lambda c, b: c > b),
              ("versionEndIncluding", lambda c, b: c <= b), ("versionEndExcluding", lambda c, b: c < b))
    seen = False
    for name, test in checks:
        if bounds.get(name):
            bound = key(bounds[name])
            if bound is None:
                return None
            seen = True
            if not test(current, bound):
                return False
    # A wildcard CPE with no bounds means "every version": too vague to raise an issue.
    return True if seen else None


def first_fix_above(version: str, candidates: Iterable[str], key: Callable[[str], Key | None]) -> str | None:
    current = key(version)
    if current is None:
        return None
    above = [(k, c) for c in candidates if c and (k := key(c)) is not None and k > current]
    return min(above)[1] if above else None
