"""GHOST-2 part 1: what every site runs.

Sources, all read-only:
- the Coolify API (GET only, token by the name COOLIFY_TOKEN_GHOST);
- the repo's Dockerfiles and lockfiles (``scan_repo``; posted to the intake);
- each watched site's Server / X-Powered-By headers (our own sites only);
- a host report posted to ``POST /ghost/inventory/report``. Ghost has no Docker
  socket and never gets one, so host-level facts only ever arrive that way.

Only versions, names and images are kept. Header values other than the two
named headers, response bodies and any credential are never stored or logged.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from .versions import normalise_name

KINDS = ("image", "runtime", "software", "package", "domain", "edge", "port", "server_header")
# Our own sites only (the GHOST-2 dogfood list). Ghost never reads anyone else's headers.
WATCHED_SITES = {"site": "tervory.com", "workspace": "app.tervory.com",
                 "coolify": "coolify.tervory.com", "rig": "rig.tervory.com"}
SOURCE_PATTERN = r"^(host|repo|coolify|headers)(:[a-z0-9][a-z0-9-]{0,39})?$"
_NAME = r"^[A-Za-z0-9@][A-Za-z0-9@/._+:-]{0,199}$"


@dataclass(frozen=True)
class Component:
    app: str
    kind: str
    name: str
    version: str
    ecosystem: str = "generic"

    def key(self) -> tuple[str, str, str, str]:
        return (self.app, self.kind, self.name, self.version)


class ComponentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    kind: str = Field(pattern="^(" + "|".join(KINDS) + ")$")
    name: str = Field(pattern=_NAME)
    version: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._+~:!*-]+$")
    ecosystem: str = Field(default="generic", pattern=r"^(generic|pypi|npm|packagist|docker)$")


class InventoryReport(BaseModel):
    """The intake. Either explicit components, or a raw host report Ghost converts itself."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(pattern=SOURCE_PATTERN)
    components: list[ComponentIn] = Field(default_factory=list, max_length=5000)
    host_report: dict[str, Any] | None = None

    @classmethod
    def from_body(cls, raw: bytes) -> "InventoryReport":
        """The wrapped shape ({source, components, host_report}), or a raw host report posted as is
        (top-level captured_at/host/os/.../containers, exactly as the host collector writes it)."""
        data = json.loads(raw)
        if isinstance(data, dict) and "source" not in data and isinstance(data.get("containers"), list):
            return cls(source=host_source(data.get("host")), host_report=data)
        return cls.model_validate(data)

    def to_components(self) -> list[Component]:
        items = [Component(**c.model_dump()) for c in self.components]
        if self.host_report is not None:
            items.extend(from_host_report(self.host_report))
        return dedupe(items)


_IP_LIKE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-f:]*:[0-9a-f:]*)$", re.IGNORECASE)


def host_source(host: Any) -> str:
    """'name 203.0.113.7' -> 'host:name'. Addresses are dropped, never stored."""
    for token in str(host or "").split():
        if _IP_LIKE.match(token):
            continue
        slug = re.sub(r"[^a-z0-9-]+", "-", token.lower()).strip("-")[:40].strip("-")
        if slug and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", slug):
            return f"host:{slug}"
    return "host"


def dedupe(items: Iterable[Component]) -> list[Component]:
    seen: dict[tuple, Component] = {}
    for item in items:
        seen.setdefault(item.key(), item)
    return list(seen.values())


# ---------- small parsers ----------

_VERSION_TOKEN = re.compile(r"v?\d+(?:\.\d+)*(?:[-+~][A-Za-z0-9.+~-]*)?|v?\d+(?:\.\d+)*[A-Za-z]+\d*")


def clean_version(value: Any) -> str | None:
    """'3.14.4 (python3.14 3.14.4-1ubuntu0.2, ...)' -> '3.14.4'; '-' / None -> None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text in {"-", "null", "none", "unknown"}:
        return None
    first = text.split()[0]
    match = _VERSION_TOKEN.fullmatch(first) or _VERSION_TOKEN.match(first)
    if not match:
        return None
    return match.group(0).lstrip("v") or None


def split_image(ref: str) -> tuple[str, str] | None:
    ref = (ref or "").strip()
    if not ref or len(ref) > 300 or any(c.isspace() for c in ref):
        return None
    ref = ref.split("@", 1)[0]
    name, _, tag = ref.rpartition(":") if ":" in ref.rsplit("/", 1)[-1] else (ref, "", "latest")
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return (name, tag or "latest") if name else None


_FULL_RELEASE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?$")
# Images whose tag is the software's own version. Only full x.y.z tags count:
# "redis:7" says nothing precise enough to match a range against.
IMAGE_SOFTWARE = {"coollabsio/coolify": "coolify", "traefik": "traefik", "chatwoot/chatwoot": "chatwoot",
                  "qdrant/qdrant": "qdrant", "redis": "redis", "postgres": "postgresql",
                  "ghcr.io/berriai/litellm": "litellm", "coollabsio/sentinel": "coolify-sentinel",
                  "coollabsio/coolify-helper": "coolify-helper", "nginx": "nginx", "php": "php", "node": "node",
                  "python": "python"}
BASE_RUNTIMES = {"python": "python", "node": "node", "php": "php"}


# Software we watch even on a floating image tag. "redis:7" becomes version "7.x", "redis:7.4" "7.4.x",
# and "redis:alpine" / "redis:latest" "unpinned": the exact version is unknown, so matching is conservative.
FLOATING_WATCHED = {"redis"}
UNPINNED = "unpinned"
_VARIANT = re.compile(r"-(alpine|bookworm|bullseye|buster|slim|jammy|noble)[\w.]*$")


def floating_version(tag: str) -> str:
    """'7' -> '7.x', '7.4-alpine' -> '7.4.x', '7.4.6-alpine' -> '7.4.6', 'alpine'/'latest' -> 'unpinned'."""
    core = _VARIANT.sub("", tag.lower()).lstrip("v")
    if re.fullmatch(r"\d+\.\d+\.\d+", core):
        return core
    if re.fullmatch(r"\d+(\.\d+)?", core):
        return core + ".x"
    return UNPINNED


def image_components(app: str, ref: str) -> list[Component]:
    parsed = split_image(ref)
    if parsed is None:
        return []
    name, tag = parsed
    items = [Component(app, "image", name, tag, "docker")]
    software = IMAGE_SOFTWARE.get(name)
    if software in FLOATING_WATCHED:
        items.append(Component(app, "software", software, floating_version(tag)))
        return items
    version = tag.split("-")[0] if software in BASE_RUNTIMES.values() else tag
    if software and _FULL_RELEASE.match(version):
        kind = "runtime" if software in BASE_RUNTIMES.values() else "software"
        items.append(Component(app, kind, software, version.lstrip("v")))
    return items


def parse_requirements(app: str, text: str) -> list[Component]:
    items = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*===?\s*([A-Za-z0-9.!+_-]+)\s*(;.*)?$", line)
        if match:
            items.append(Component(app, "package", normalise_name("pypi", match.group(1)), match.group(3), "pypi"))
    return items


def parse_package_lock(app: str, text: str) -> list[Component]:
    data = json.loads(text)
    items = []
    packages = data.get("packages")
    if isinstance(packages, dict):
        for path, meta in packages.items():
            if not path or not isinstance(meta, dict) or "node_modules/" not in path:
                continue
            name = meta.get("name") or path.rsplit("node_modules/", 1)[-1]
            if isinstance(meta.get("version"), str) and not meta.get("link"):
                items.append(Component(app, "package", normalise_name("npm", name), meta["version"], "npm"))
    else:
        def walk(deps: dict):
            for name, meta in (deps or {}).items():
                if isinstance(meta, dict) and isinstance(meta.get("version"), str):
                    items.append(Component(app, "package", normalise_name("npm", name), meta["version"], "npm"))
                    walk(meta.get("dependencies") or {})
        walk(data.get("dependencies") or {})
    return dedupe(items)


_PNPM_KEY = re.compile(r"""^\s{2}['"]?/?((?:@[^/@\s'"]+/)?[^@\s'"/(]+)[@/]([0-9][^\s'"():]*)(?:\([^'"]*\))?['"]?:\s*$""")


def parse_pnpm_lock(app: str, text: str) -> list[Component]:
    items, section = [], None
    for line in text.splitlines():
        if line and not line.startswith(" "):
            section = line.rstrip(":").strip()
            continue
        if section in {"packages", "snapshots"}:
            match = _PNPM_KEY.match(line)
            if match:
                items.append(Component(app, "package", normalise_name("npm", match.group(1)), match.group(2), "npm"))
    return dedupe(items)


def parse_dockerfile(app: str, text: str) -> list[Component]:
    items, args = [], {}
    for line in text.splitlines():
        line = line.strip()
        arg = re.match(r"^ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)", line, re.IGNORECASE)
        if arg:
            args[arg.group(1)] = arg.group(2).strip("\"'")
        match = re.match(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", line, re.IGNORECASE)
        if not match:
            continue
        ref = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: args.get(m.group(1), m.group(0)), match.group(1))
        if "$" in ref or ref.lower() == "scratch":
            continue
        items.extend(image_components(app, ref))
        name, tag = split_image(ref) or ("", "")
        runtime = BASE_RUNTIMES.get(name)
        version = clean_version(tag.split("-")[0]) if tag else None
        if runtime and version:
            # A base image tag such as python:3.12-slim pins the runtime line, not the patch.
            items.append(Component(app, "runtime", runtime, version))
    return dedupe(items)


LOCKFILES = {"requirements.txt": parse_requirements, "package-lock.json": parse_package_lock,
             "pnpm-lock.yaml": parse_pnpm_lock}
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".next", "dist", "build"}


def repo_app(root: Path, path: Path) -> str:
    parts = path.relative_to(root).parts[:-1]
    if parts[:1] == ("agents",) and len(parts) >= 2:
        name = parts[1]
    elif parts:
        name = parts[0]
    else:
        name = "repo"
    return re.sub(r"[^a-z0-9._-]", "-", name.lower().lstrip("._")) or "repo"


# Sidecars are named after the stack they serve, so Tank's Redis and Chatwoot's Redis stay apart.
SIDECARS = {"redis", "postgres", "db"}
STACK_ANCHORS = ("tank", "chatwoot")


def parse_compose(app_prefix: str, text: str) -> list[Component]:
    """Service images from a compose file (no YAML eval; 'image:' lines under 'services:' only)."""
    items: list[Component] = []
    in_services, service = False, None
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_services, service = line.strip() == "services:", None
            continue
        if not in_services:
            continue
        match = re.fullmatch(r"\s{2}([A-Za-z0-9._-]+):\s*", line)
        if match and indent == 2:
            service = match.group(1).lower()
            continue
        match = re.fullmatch(r"\s+image:\s*['\"]?([^'\"\s]+)['\"]?", line)
        if match and service and "${" not in match.group(1):
            app = f"{app_prefix}-{service}" if app_prefix and service in SIDECARS else service
            items.extend(image_components(app, match.group(1)))
    return items


def scan_repo(root: Path, max_bytes: int = 5_000_000) -> list[Component]:
    root = root.resolve()
    items: list[Component] = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts) or not path.is_file():
            continue
        name = path.name
        parser = LOCKFILES.get(name)
        if parser is None and re.fullmatch(r"requirements[-_.A-Za-z0-9]*\.txt", name):
            parser = parse_requirements
        if parser is None and (name == "Dockerfile" or name.startswith("Dockerfile.")):
            parser = parse_dockerfile
        if parser is None and name == "docker-compose.yml":
            # The root compose is Tank's stack; other compose files name their own room.
            prefix = "tank" if path.parent == root else repo_app(root, path)
            parser = lambda app, text, prefix=prefix: parse_compose(prefix, text)  # noqa: E731
        if parser is None or path.stat().st_size > max_bytes:
            continue
        try:
            items.extend(parser(repo_app(root, path), path.read_text(encoding="utf-8")))
        except (ValueError, UnicodeDecodeError):
            continue  # A malformed lockfile is skipped, never guessed at.
    return dedupe(items)


# ---------- host report (read-only report posted by Grok) ----------

_COOLIFY_ID = re.compile(r"[a-z0-9]{24}")


def container_app(name: str) -> str:
    """'59f8...-rig-brain-1' -> 'rig-brain'; 'tank-xnmc...-20261022T210127' -> 'tank'."""
    text = name.strip().lower()
    text = re.sub(r"-\d{8}t\d{6}$", "", text)
    text = _COOLIFY_ID.sub("", text)
    text = re.sub(r"-\d+$", "", text.strip("-_"))
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    return text[:80] or "container"


def from_host_report(report: dict[str, Any]) -> list[Component]:
    """Pick known version facts out of a host report. Unknown fields are ignored, never stored."""
    items: list[Component] = []

    def add(app: str, kind: str, name: str, value: Any, ecosystem: str = "generic") -> None:
        version = clean_version(value)
        if version:
            items.append(Component(app, kind, name, version, ecosystem))

    host = "host"
    os_name = report.get("os")
    if isinstance(os_name, str):
        match = re.match(r"^\s*([A-Za-z]+)\s+(\d[\w.]*)", os_name)
        if match:
            items.append(Component(host, "software", match.group(1).lower(), match.group(2)))
    kernel = report.get("kernel") or {}
    if isinstance(kernel, dict):
        add(host, "software", "linux-kernel", kernel.get("running"))
    docker = report.get("docker_engine") or {}
    if isinstance(docker, dict):
        add(host, "software", "docker-engine", docker.get("installed"))
    runtimes = report.get("host_runtimes") or {}
    if isinstance(runtimes, dict):
        for key, name in (("python3", "python"), ("python", "python"), ("node", "node"), ("php", "php")):
            add(host, "runtime", name, runtimes.get(key))
    coolify = report.get("coolify") or {}
    if isinstance(coolify, dict):
        add("coolify", "software", "coolify", coolify.get("version"))
        add("coolify", "runtime", "php", coolify.get("php"))
        add("coolify", "runtime", "node", coolify.get("node"))
    litellm = report.get("litellm") or {}
    if isinstance(litellm, dict):
        add("litellm", "package", "litellm", litellm.get("litellm_pkg"), "pypi")
        add("litellm", "software", "litellm", litellm.get("litellm_pkg"))
        add("litellm", "runtime", "python", litellm.get("python"))
        add("litellm", "runtime", "node", litellm.get("node"))
    chatwoot = report.get("chatwoot") or {}
    if isinstance(chatwoot, dict) and isinstance(chatwoot.get("running_image"), str):
        items.extend(image_components("chatwoot", chatwoot["running_image"]))
    containers = [c for c in report.get("containers") or [] if isinstance(c, dict) and isinstance(c.get("name"), str)]
    stacks: dict[str, str] = {}
    for container in containers:
        stack = _COOLIFY_ID.search(container["name"].lower())
        if stack and container_app(container["name"]) in STACK_ANCHORS:
            stacks.setdefault(stack.group(0), container_app(container["name"]))
    for container in containers:
        app = container_app(container["name"])
        stack = _COOLIFY_ID.search(container["name"].lower())
        if app in SIDECARS and stack and stack.group(0) in stacks:
            app = f"{stacks[stack.group(0)]}-{app}"
        if isinstance(container.get("image"), str):
            items.extend(image_components(app, container["image"]))
        for key in ("python", "node", "php"):
            add(app, "runtime", key, container.get(key))
        for key in ("pip", "certifi"):
            add(app, "package", key, container.get(key), "pypi")
    return dedupe(items)


# ---------- site headers (our own sites only) ----------

_PRODUCT = re.compile(r"([A-Za-z][A-Za-z0-9._-]{0,40})/(\d[\w.]*)")


def header_components(app: str, host: str, headers: dict[str, str]) -> list[Component]:
    lowered = {k.lower(): v for k, v in headers.items()}
    items = [Component(app, "domain", host, "current")]
    server = (lowered.get("server") or "")[:200]
    powered = (lowered.get("x-powered-by") or "")[:200]
    cloudflare = "cf-ray" in lowered or server.strip().lower() == "cloudflare"
    items.append(Component(app, "edge", "cloudflare" if cloudflare else "direct", "current"))
    for header, value in (("server", server), ("x-powered-by", powered)):
        for product, version in _PRODUCT.findall(value):
            product = product.lower()
            kind = "runtime" if product in BASE_RUNTIMES else "server_header"
            items.append(Component(app, kind, product, version))
        if value and not _PRODUCT.search(value):
            word = re.match(r"^\s*([A-Za-z][A-Za-z0-9._-]{0,40})", value)
            if word:
                items.append(Component(app, "server_header", word.group(1).lower(), "unversioned"))
    return dedupe(items)


async def fetch_headers(http, app: str, host: str) -> list[Component]:
    from .probes import public_addresses
    import asyncio
    if host not in WATCHED_SITES.values():
        raise ValueError("Autumbra Watch only reads headers from its own watched sites.")
    await asyncio.wait_for(asyncio.to_thread(public_addresses, host), timeout=10)
    response = await http.get(f"https://{host}/", follow_redirects=False, timeout=15)
    return header_components(app, host, {k: response.headers.get(k, "") for k in ("server", "x-powered-by", "cf-ray")
                                         if k in response.headers})


# ---------- Coolify API (read-only) ----------

def coolify_settings() -> tuple[str, str]:
    """Return (base_url, token). The token is read from the environment by name and never logged."""
    token = os.environ.get("COOLIFY_TOKEN_GHOST", "").strip()
    base = (os.environ.get("COOLIFY_URL_GHOST", "").strip() or "https://coolify.tervory.com").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query:
        raise ValueError("COOLIFY_URL_GHOST must be a plain https base URL.")
    return base, token


def coolify_components(applications: list[dict], services: list[dict], version: str | None) -> list[Component]:
    items: list[Component] = []
    if version:
        cleaned = clean_version(version)
        if cleaned:
            items.append(Component("coolify", "software", "coolify", cleaned))
    for row in [*applications, *services]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        app = container_app(row["name"])
        image = row.get("docker_registry_image_name")
        if isinstance(image, str) and image:
            tag = row.get("docker_registry_image_tag") or "latest"
            items.extend(image_components(app, f"{image}:{tag}"))
        for fqdn in str(row.get("fqdn") or "").split(","):
            host = urlparse(fqdn.strip()).hostname if "://" in fqdn else fqdn.strip()
            if host and re.fullmatch(r"[a-z0-9.-]{1,253}", host.lower()):
                items.append(Component(app, "domain", host.lower(), "current"))
        for port in re.findall(r"(\d{1,5}):\d{1,5}", str(row.get("ports_mappings") or "")):
            if 0 < int(port) < 65536:
                items.append(Component(app, "port", port, "public"))
    return dedupe(items)


async def fetch_coolify(http) -> list[Component] | None:
    """GET-only reads. Returns None when the token is not configured (no call is made)."""
    base, token = coolify_settings()
    if not token:
        return None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}

    async def get(path: str):
        try:
            response = await http.get(base + path, headers=headers, follow_redirects=False, timeout=20)
        except Exception:
            raise RuntimeError("Coolify did not answer.") from None
        if response.status_code != 200 or len(response.content) > 10_000_000:
            raise RuntimeError("Coolify did not answer.")
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Coolify did not answer.") from None

    applications = await get("/api/v1/applications")
    services = await get("/api/v1/services")
    version = await get("/api/v1/version")
    return coolify_components(applications if isinstance(applications, list) else [],
                              services if isinstance(services, list) else [],
                              version if isinstance(version, str) else None)


def main(argv: list[str]) -> int:
    """`python -m ghost.inventory scan-repo PATH` or `host-report FILE` prints an intake body."""
    if len(argv) != 2 or argv[0] not in {"scan-repo", "host-report"}:
        print("usage: python -m ghost.inventory scan-repo PATH | host-report FILE", file=sys.stderr)
        return 2
    if argv[0] == "scan-repo":
        body = {"source": "repo", "components": [asdict(c) for c in scan_repo(Path(argv[1]))]}
    else:
        body = {"source": "host", "components": [asdict(c) for c in from_host_report(json.loads(Path(argv[1]).read_text()))]}
    print(json.dumps(body, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
