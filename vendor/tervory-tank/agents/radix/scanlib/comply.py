"""comply.gdpr — GDPR / POPIA privacy and cookie notices on a saved page.

Reads HTML that is already on disk. It does not fetch a page, follow a link,
or open a socket. A missing notice is medium and does not block a deploy.
The tool id stays comply.gdpr; privacy notice wording covers GDPR and South
Africa's POPIA. Notice presence alone does not establish legal compliance.

Data retention, a right-to-erasure control, a POPIA information-officer check,
card-data rules, and a full accessibility audit are not this check. Those need
another page, a browser, or a live site.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

TOOL = "comply.gdpr"
_MAX_BYTES = 1_000_000
_HOST = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_PRIVACY_TEXT = re.compile(
    r"\b(?:privacy(?:\s+(?:policy|notice))?|popia|ccpa|cpra|protection of personal information)\b",
    re.IGNORECASE,
)
_PRIVACY_HREF = re.compile(
    r"(?:privacy(?:[-_](?:policy|notice))?|popia|ccpa|cpra|data[-_]protection)",
    re.IGNORECASE,
)
_NOTICE_PAGE = re.compile(
    r"\b(?:privacy(?:\s+(?:policy|notice))?|popia(?:\s+notice)?|ccpa(?:\s+notice)?|cpra(?:\s+notice)?|protection of personal information)\b",
    re.IGNORECASE,
)
_COOKIE = re.compile(r"\bcookies?\b", re.IGNORECASE)
_ADDRESS = re.compile(r"^\s*https?://", re.IGNORECASE)


class PageRefused(ValueError):
    """The input is not a saved page this check can read."""


def _page_name(name: str) -> str:
    base = Path(str(name).replace("\\", "/")).name.strip()
    base = re.sub(r"[\r\n\x00]", "", base)
    if not base or base in {".", ".."}:
        return "page.html"
    return base[:200]


def _page_host(value: str | None) -> str | None:
    if value is None:
        return None
    host = value.strip().lower().rstrip(".")
    if _HOST.fullmatch(host) is None:
        raise PageRefused("page host must be a site name")
    return host


def _other_site(src: str, page_host: str | None) -> str | None:
    text = src.strip()
    if not text or text.startswith(("#", "data:", "javascript:", "mailto:")):
        return None
    if text.startswith("//"):
        text = "https:" + text
    elif text.startswith(("/", ".")) or "://" not in text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == page_host:
        return None
    return host


def _host_phrase(hosts: list[str]) -> str:
    labels: list[str] = []
    for host in hosts:
        label = host if host.isascii() and _HOST.fullmatch(host) else "another site"
        if label not in labels:
            labels.append(label)
    if not labels:
        return "another site"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{labels[0]}, {labels[1]}, and {len(labels) - 2} more"


def _finding(rule: str, fix: str, name: str, line: int | None) -> dict[str, Any]:
    return {
        "tool": TOOL,
        "severity": "medium",
        "file": name,
        "line": line,
        "rule": rule,
        "fix": fix,
    }


class _SavedPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str, int]] = []
        self.loads: list[tuple[str, int]] = []
        self.visible: list[str] = []
        self.title = ""
        self.h1 = ""
        self._skip: list[str] = []
        self._anchor: list[Any] | None = None
        self._title: list[str] | None = None
        self._h1: list[str] | None = None
        self._h1_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, close=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, close=True)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, close: bool) -> None:
        name = tag.lower()
        attr = {key.lower(): (value or "") for key, value in attrs}
        line = self.getpos()[0]
        if self._skip:
            if name in {"script", "style"} and not close:
                self._skip.append(name)
            return
        if name in {"script", "style"}:
            if name == "script":
                self.loads.append((attr.get("src", ""), line))
            if not close:
                self._skip.append(name)
            return
        if name == "iframe":
            self.loads.append((attr.get("src", ""), line))
        if name == "a":
            self._anchor = [attr.get("href", ""), line, []]
            if close:
                self._finish_anchor()
        elif name == "title" and self._title is None:
            self._title = []
        elif name == "h1" and not self._h1_done:
            self._h1 = []
        for key in ("aria-label", "alt", "placeholder"):
            text = attr.get(key, "")
            if text:
                self.visible.append(text)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if self._skip:
            if name == self._skip[-1]:
                self._skip.pop()
            return
        if name == "a" and self._anchor is not None:
            self._finish_anchor()
        elif name == "title" and self._title is not None:
            self.title = "".join(self._title)
            self._title = None
        elif name == "h1" and self._h1 is not None:
            self.h1 = "".join(self._h1)
            self._h1 = None
            self._h1_done = True

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        if self._anchor is not None:
            self._anchor[2].append(data)
        if self._title is not None:
            self._title.append(data)
        if self._h1 is not None:
            self._h1.append(data)
        self.visible.append(data)

    def finish(self) -> None:
        if self._anchor is not None:
            self._finish_anchor()
        if self._title is not None:
            self.title = "".join(self._title)
            self._title = None
        if self._h1 is not None:
            self.h1 = "".join(self._h1)
            self._h1 = None

    def _finish_anchor(self) -> None:
        if self._anchor is None:
            return
        href, line, parts = self._anchor
        self.anchors.append((str(href), "".join(parts), int(line)))
        self._anchor = None


def _has_privacy_link(page: _SavedPage) -> bool:
    for href, text, _line in page.anchors:
        if _PRIVACY_HREF.search(href) or _PRIVACY_TEXT.search(text):
            return True
    return False


def _is_notice_page(page: _SavedPage) -> bool:
    return bool(_NOTICE_PAGE.search(page.title) or _NOTICE_PAGE.search(page.h1))


def _has_cookie_notice(page: _SavedPage) -> bool:
    return bool(_COOKIE.search(" ".join(page.visible)))


def _other_loads(page: _SavedPage, page_host: str | None) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for src, line in page.loads:
        host = _other_site(src, page_host)
        if host is None or host in seen:
            continue
        seen.add(host)
        found.append((host, line))
        if len(found) == 20:
            break
    return found


def summarize(name: str, findings: list[dict[str, Any]]) -> str:
    count = len(findings)
    noun = "finding" if count == 1 else "findings"
    if count == 0:
        return (
            f"Checked {name}. No GDPR / POPIA privacy or cookie notice findings. "
            "This check does not block the build."
        )
    return (
        f"Checked {name} for GDPR / POPIA privacy and cookie notices. "
        f"{count} {noun}, none critical. "
        "This check does not block the build."
    )


def scan_html(html: str, *, name: str = "page.html", page_host: str | None = None) -> dict[str, Any]:
    """Check one saved HTML document. `page_host` is compared, never contacted."""
    if not isinstance(html, str):
        raise PageRefused("saved page must be text")
    host = _page_host(page_host)
    page_name = _page_name(name)
    if len(html.encode("utf-8", errors="replace")) > _MAX_BYTES:
        raise PageRefused("saved page is too large")
    if not html.strip():
        raise PageRefused("saved page is empty")
    if _ADDRESS.match(html) and "<" not in html:
        raise PageRefused("a web address is not a saved page")
    parser = _SavedPage()
    try:
        parser.feed(html)
        parser.close()
        parser.finish()
    except PageRefused:
        raise
    except Exception as exc:
        raise PageRefused("saved page could not be read") from exc
    findings: list[dict[str, Any]] = []
    if not _has_privacy_link(parser) and not _is_notice_page(parser):
        findings.append(
            _finding(
                "missing-privacy-notice",
                "Add a link to a privacy notice covering GDPR or POPIA, as applicable.",
                page_name,
                None,
            )
        )
    others = _other_loads(parser, host)
    if others and not _has_cookie_notice(parser):
        findings.append(
            _finding(
                "missing-cookie-notice",
                (
                    f"This page loads content from {_host_phrase([item[0] for item in others])}. "
                    "Add a cookie notice on this page, or load that content from this site."
                ),
                page_name,
                others[0][1],
            )
        )
    return {
        "tool": TOOL,
        "page": page_name,
        "fetched": False,
        "deploy_blocked": False,
        "findings": findings,
        "summary": summarize(page_name, findings),
    }


def scan_file(path: Path, *, page_host: str | None = None) -> dict[str, Any]:
    """Read a saved page from disk. The path is a file, not a web address."""
    file_path = Path(path)
    if not file_path.is_file():
        raise PageRefused("saved page is not a file")
    raw = file_path.read_bytes()
    if len(raw) > _MAX_BYTES:
        raise PageRefused("saved page is too large")
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PageRefused("saved page is not text") from exc
    return scan_html(html, name=file_path.name, page_host=page_host)
