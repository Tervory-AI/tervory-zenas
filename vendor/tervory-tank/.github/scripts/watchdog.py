"""Uptime checks for the public sites and Coolify applications.

Prints status codes and names only. Never prints the Coolify token, the GitHub
token, or a response body.
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# Boston's stack has no health check on the running deploy, so it reports
# running:unknown. That uuid is the production app in docs/RUNBOOK.md.
# After the compose health checks are deployed, this exception can go.
MAIN_APP = "xnmc0jkz8j9i5jh1jwanbsfv"

# The site root returns 404 to a signed-out caller (Trigger sign-in protects it).
# /api/health is the public probe and returns 200 when the app is up.
SITES = (
    ("app.tervory.com", "https://app.tervory.com/api/health"),
    ("coolify.tervory.com", "https://coolify.tervory.com/api/health"),
    ("rig.tervory.com", "https://rig.tervory.com/health"),
    ("tervory.com", "https://tervory.com"),
)

WARN_DAYS = 14
FAIL_DAYS = 7
USER_AGENT = "tervory-watchdog"

# Repo variable ZA_OFFLINE=true: the ZA stack is switched off on purpose (box being moved).
# Skip every -za app (logged as "skip"): no new issue and no failed run. Open ZA issues stay
# open until ZA is back. Unset or any other value: today's behaviour.
# Same reading as .github/scripts/deploy_changed.py.
ZA_OFFLINE = os.environ.get("ZA_OFFLINE", "").strip().lower() == "true"

# Issue titles for per-app rows start with this. The bare "Down: Coolify" issue is the API itself.
APP_PREFIX = "Coolify "


@dataclass
class Result:
    thing: str
    state: str  # ok, down, warn, skip, gone
    detail: str


def redact(text: str) -> str:
    cleaned = text.replace("\n", " ").replace("\r", " ")
    for key in ("COOLIFY_TOKEN", "GITHUB_TOKEN"):
        secret = os.environ.get(key) or ""
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned[:300]


def say(result: Result) -> None:
    detail = redact(result.detail)
    thing = redact(result.thing)
    if result.state == "down":
        print(f"::error title={thing}::{detail}", flush=True)
    elif result.state == "warn":
        print(f"::warning title={thing}::{detail}", flush=True)
    else:
        print(f"{result.state} {thing} {detail}", flush=True)


def norm_status(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "").split("(", 1)[0]
    if re.fullmatch(r"[a-z0-9_.:-]{1,40}", text):
        return text
    return "unreadable"


def safe_label(value: object) -> str:
    text = "".join(ch if ch.isalnum() or ch in ".-_ " else " " for ch in str(value or ""))
    text = " ".join(text.split())
    return text[:60] or "unnamed"


def dns_state(host: str) -> str:
    """skip = the name is not live yet. down = lookup itself failed."""
    transient = {getattr(socket, "EAI_AGAIN", None), getattr(socket, "EAI_MEMORY", None)}
    last = "down"
    for attempt in range(2):
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            return "ok"
        except socket.gaierror as exc:
            if exc.errno not in transient:
                return "skip"
            last = "down"
            if attempt == 0:
                time.sleep(2)
    return last


def days_left(cert: dict) -> int:
    expires = ssl.cert_time_to_seconds(cert["notAfter"])
    return int((expires - time.time()) // 86400)


def handshake(host: str) -> tuple[str, int | None]:
    """ok + days left, skip when no real certificate exists yet, or down."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=20) as sock:
            with context.wrap_socket(sock, server_hostname=host) as secure:
                cert = secure.getpeercert()
                if not cert:
                    return "down", None
                return "ok", days_left(cert)
    except ssl.SSLCertVerificationError as exc:
        text = str(exc).lower()
        if "expired" in text:
            return "expired", None
        # Placeholder certificate (Traefik's default) or a name that is not on the cert yet.
        if "self-signed" in text or "hostname mismatch" in text or "not valid for" in text:
            return "skip", None
        return "down", None
    except (TimeoutError, ConnectionRefusedError, OSError):
        return "down", None


def http_status(url: str) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read(64)
            return int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read(64)
        return int(exc.code)


def connection_word(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError) or isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    return "connection failed"


def check_site(thing: str, url: str) -> list[Result]:
    host = thing
    looked = dns_state(host)
    if looked == "skip":
        return [Result(thing, "skip", "not live yet")]
    if looked == "down":
        return [Result(thing, "down", "DNS lookup failed")]

    tls_state, days = "down", None
    for attempt in range(2):
        tls_state, days = handshake(host)
        if tls_state != "down" or attempt == 1:
            break
        time.sleep(2)
    if tls_state == "skip":
        return [Result(thing, "skip", "not live yet")]
    if tls_state == "expired":
        return [Result(f"certificate {thing}", "down", "expired")]
    if tls_state == "down":
        return [Result(thing, "down", "TLS connection failed")]

    results: list[Result] = []
    if days is None:
        results.append(Result(f"certificate {thing}", "down", "expiry unreadable"))
    elif days <= FAIL_DAYS:
        word = "expired" if days < 0 else f"expires in {days} days"
        results.append(Result(f"certificate {thing}", "down", word))
    elif days <= WARN_DAYS:
        results.append(Result(f"certificate {thing}", "warn", f"expires in {days} days"))
    else:
        results.append(Result(f"certificate {thing}", "ok", f"{days} days left"))

    code: int | None = None
    last = "connection failed"
    for attempt in range(2):
        try:
            code = http_status(url)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLCertVerificationError):
                last = "TLS connection failed"
                break
            last = connection_word(exc)
            if attempt == 0:
                time.sleep(2)
    if code == 200:
        results.append(Result(thing, "ok", "HTTP 200"))
    elif code is None:
        results.append(Result(thing, "down", last))
    else:
        results.append(Result(thing, "down", f"HTTP {code}"))
    return results


def coolify_results() -> list[Result]:
    token = os.environ.get("COOLIFY_TOKEN") or ""
    if not token:
        return [Result("Coolify", "down", "COOLIFY_TOKEN secret is missing")]
    base = (os.environ.get("COOLIFY_URL") or "https://coolify.tervory.com").strip().rstrip("/")
    if base != "https://coolify.tervory.com":
        return [Result("Coolify", "down", "COOLIFY_URL must match the fixed https Coolify base")]

    request = urllib.request.Request(
        "https://coolify.tervory.com/api/v1/applications",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            code = int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read(0)
        return [Result("Coolify", "down", f"HTTP {exc.code}")]
    except (urllib.error.URLError, TimeoutError, OSError):
        return [Result("Coolify", "down", "connection failed")]

    if code != 200:
        return [Result("Coolify", "down", f"HTTP {code}")]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return [Result("Coolify", "down", "HTTP 200")]
    if isinstance(payload, dict):
        for key in ("data", "applications"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return [Result("Coolify", "down", "HTTP 200")]
    if not payload:
        return [Result("Coolify", "down", "HTTP 200 no applications")]

    # A later green call must be able to close "Down: Coolify". Per-app rows
    # use another name, so recovery never matched that issue.
    results: list[Result] = [Result("Coolify", "ok", "HTTP 200")]
    for item in payload:
        if not isinstance(item, dict):
            results.append(Result("Coolify", "down", "unreadable application"))
            continue
        name = safe_label(item.get("name") or "unnamed")
        status = norm_status(item.get("status"))
        uuid = str(item.get("uuid") or "").strip()
        thing = f"{APP_PREFIX}{name}"
        if ZA_OFFLINE and name.endswith("-za"):
            results.append(Result(thing, "skip", "ZA_OFFLINE is set"))
            continue
        allowed = status == "running:healthy" or (status == "running:unknown" and uuid == MAIN_APP)
        if allowed:
            results.append(Result(thing, "ok", status))
        else:
            results.append(Result(thing, "down", f"status {status}"))
    return results


def is_za_app(thing: str) -> bool:
    return thing.startswith(APP_PREFIX) and thing.endswith("-za")


def gone_apps(results: list[Result], open_titles: list[str]) -> list[Result]:
    """Apps with an open "Down: Coolify <name>" issue that Coolify no longer lists.

    Only when the whole Coolify list was read cleanly. The app was
    deleted, so its issue is closed instead of staying open for ever. While
    ZA_OFFLINE is set, -za issues are left alone.
    """
    api_rows = [result for result in results if result.thing == "Coolify"]
    # Every app row must have been readable, or a missing name could be an unreadable one.
    if not api_rows or any(result.state != "ok" for result in api_rows):
        return []
    seen = {result.thing for result in results}
    gone: list[Result] = []
    for title in open_titles:
        if not title.startswith("Down: "):
            continue
        thing = title[len("Down: "):]
        if not thing.startswith(APP_PREFIX) or thing in seen:
            continue
        if ZA_OFFLINE and is_za_app(thing):
            continue
        gone.append(Result(thing, "gone", "no longer in Coolify"))
    return gone


def collect() -> list[Result]:
    results: list[Result] = []
    for thing, url in SITES:
        results.extend(check_site(thing, url))
    results.extend(coolify_results())
    return results


def apply_force(results: list[Result]) -> str | None:
    name = (os.environ.get("FORCE_FAIL") or "").strip()
    if not name:
        return None
    for result in results:
        if result.thing == name:
            result.state = "down"
            result.detail = "drill: forced failure"
            return None
    known = ", ".join(result.thing for result in results)
    print(f"::error::force_fail did not match a check ({redact(name)})", flush=True)
    print(f"known checks: {redact(known)}", flush=True)
    return "bad-force"


class GitHub:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.token = token

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return int(response.status), json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = None
            return int(exc.code), parsed

    def ensure_label(self) -> None:
        code, _payload = self.request(
            "POST",
            f"/repos/{self.repo}/labels",
            {"name": "incident", "color": "E80008", "description": "Something is down"},
        )
        if code in (201, 422):
            return
        raise RuntimeError(f"label HTTP {code}")

    def open_incidents(self) -> dict[str, dict[str, object]]:
        found: dict[str, dict[str, object]] = {}
        for page in range(1, 11):
            code, payload = self.request(
                "GET",
                f"/repos/{self.repo}/issues?state=open&labels=incident&per_page=100&page={page}",
            )
            if code != 200 or not isinstance(payload, list):
                raise RuntimeError(f"list issues HTTP {code}")
            for item in payload:
                if not isinstance(item, dict) or "pull_request" in item:
                    continue
                title = str(item.get("title") or "")
                found[title] = {"number": item.get("number"), "body": str(item.get("body") or "")}
            if len(payload) < 100:
                break
        return found

    def open_issue(self, title: str, detail: str) -> int:
        code, payload = self.request(
            "POST",
            f"/repos/{self.repo}/issues",
            {"title": title, "body": detail, "labels": ["incident"]},
        )
        if code not in (200, 201) or not isinstance(payload, dict) or not payload.get("number"):
            raise RuntimeError(f"open issue HTTP {code}")
        return int(payload["number"])

    def comment(self, number: int, body: str) -> None:
        code, _payload = self.request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/comments",
            {"body": body},
        )
        if code not in (200, 201):
            raise RuntimeError(f"comment HTTP {code}")

    def close(self, number: int) -> None:
        code, _payload = self.request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            {"state": "closed", "state_reason": "completed"},
        )
        if code != 200:
            raise RuntimeError(f"close issue HTTP {code}")

    def set_body(self, number: int, body: str) -> None:
        code, _payload = self.request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            {"body": body},
        )
        if code != 200:
            raise RuntimeError(f"update issue HTTP {code}")


def sync_issues(results: list[Result]) -> None:
    token = os.environ.get("GITHUB_TOKEN") or ""
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if not token or not repo:
        print("issues: skipped (not running in GitHub Actions)", flush=True)
        return
    github = GitHub(repo, token)
    github.ensure_label()
    open_issues = github.open_incidents()
    for result in gone_apps(results, list(open_issues)):
        say(result)
        number = int(open_issues[f"Down: {result.thing}"]["number"])
        github.comment(number, "closed: this app no longer exists in Coolify")
        github.close(number)
        print(f"closed #{number} Down: {result.thing} (no longer in Coolify)", flush=True)
    for result in results:
        title = f"Down: {result.thing}"
        current = open_issues.get(title)
        if result.state == "down":
            if current is None:
                number = github.open_issue(title, result.detail)
                print(f"opened #{number} {title}", flush=True)
            elif result.detail != str(current.get("body") or ""):
                number = int(current["number"])
                github.comment(number, result.detail)
                github.set_body(number, result.detail)
                print(f"updated #{number} {title}", flush=True)
            else:
                print(f"already open #{current['number']} {title}", flush=True)
        elif result.state in ("ok", "warn") and current is not None:
            number = int(current["number"])
            github.comment(number, "recovered")
            github.close(number)
            print(f"closed #{number} {title}", flush=True)


def main() -> int:
    results = collect()
    forced = apply_force(results)
    for result in results:
        say(result)
    if forced == "bad-force":
        return 2
    try:
        sync_issues(results)
    except RuntimeError as exc:
        print(f"::error::{redact(str(exc))}", flush=True)
        return 1
    if any(result.state == "down" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
