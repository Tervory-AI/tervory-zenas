"""ATF list download and licence-only projection for Base's nightly gold join."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

# Mirrors the FFL profile host exclusions; checked against its versioned data in tests.
BLOCKED_HOSTS = ("gunbroker.com", "armslist.com", "atf.gov")

LISTING = "https://www.atf.gov/firearms/listing-federal-firearms-licensees/complete"
USER_AGENT = "Tervory/1.0 (+https://tervory.com)"
MAX_BYTES = 40_000_000
MONTHS = dict(zip("ABCDEFGHJKLM", range(1, 13)))
FIELDS = (
    "lic_regn", "lic_dist", "lic_cnty", "lic_type", "lic_xprdte", "lic_seqn",
    "license_name", "business_name", "premise_street", "premise_city",
    "premise_state", "premise_zip_code", "voice_phone",
)


def published(row: dict[str, Any]) -> dict[str, str]:
    values = {str(key).strip().lower(): str(value or "").strip() for key, value in row.items()}
    return {key: values.get(key, "") for key in FIELDS}


def licence_id(row: dict[str, Any]) -> str:
    row = published(row)
    keys = FIELDS[:6]
    if not all(row[key] for key in keys):
        return ""
    widths = (1, 2, 3, 2, 2, 5)
    return "-".join(row[key].upper().zfill(width) for key, width in zip(keys, widths))


def expires(code: str, snapshot: date) -> date | None:
    if not re.fullmatch(r"\d[ABCDEFGHJKLM]", code):
        return None
    year = next((year for year in range(snapshot.year - 2, snapshot.year + 4) if year % 10 == int(code[0])), None)
    if year is None:
        return None
    month = MONTHS[code[1]]
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a" and dict(attrs).get("href"):
            self.href = dict(attrs)["href"]
            self.text = []

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.links.append((self.href, " ".join(self.text)))
            self.href = None


def safe_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https" and parsed.hostname in ("atf.gov", "www.atf.gov")
        and not parsed.username and not parsed.password and parsed.port in (None, 443)
        and not parsed.fragment
    )


async def fetch_list(http: Any, today: date) -> tuple[list[dict[str, str]], str, date]:
    robots = {}

    async def get(url):
        for _ in range(4):
            if not safe_url(url):
                raise ValueError("untrusted official-list URL")
            parsed = urlsplit(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in robots:
                response = await http.get(origin + "/robots.txt", headers={"User-Agent": USER_AGENT}, follow_redirects=False)
                if response.status_code not in (200, 404):
                    raise ValueError("official-list robots unavailable")
                policy = RobotFileParser()
                policy.parse(response.text.splitlines() if response.status_code == 200 else [])
                robots[origin] = policy
            if not robots[origin].can_fetch(USER_AGENT, url):
                raise ValueError("official-list robots denied")
            async with http.stream("GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=False, timeout=60) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200:
                    raise ValueError("official-list fetch refused")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("official list too large")
                text = data.decode("utf-8-sig", errors="replace")
                if any(word in text[:10000].lower() for word in ("captcha", "verify you are human", "access denied")):
                    raise ValueError("official-list challenge")
                return text, url
        raise ValueError("official-list redirect limit")

    month = today.replace(day=1)
    for _ in range(3):
        page = f"{LISTING}?field_ffl_date_value[value][year]={month.year}&ffl_date_month[value][month]={month.month}"
        text, page = await get(page)
        parser = Links()
        parser.feed(text)
        for link, label in parser.links:
            description = (link + " " + label).lower()
            stamp = re.search(r"(\d{2})(\d{2})-ffl-list", description)
            if not stamp or "complete" not in description or ".txt" not in description:
                continue
            snapshot = date(2000 + int(stamp[2]), int(stamp[1]), 1)
            if snapshot != month:
                continue
            content, source_url = await get(urljoin(page, link))
            reader = csv.DictReader(io.StringIO(content), delimiter="\t")
            if not reader.fieldnames or "lic_regn" not in {key.lower().strip() for key in reader.fieldnames}:
                raise ValueError("official-list columns missing")
            rows = [published(row) for row in reader]
            if not rows:
                raise ValueError("official list empty")
            return rows, source_url, snapshot
        month = (month - timedelta(days=1)).replace(day=1)
    raise ValueError("recent official list unavailable")
