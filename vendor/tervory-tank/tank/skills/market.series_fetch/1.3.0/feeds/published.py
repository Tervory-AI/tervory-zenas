"""Read published CSV, spreadsheet and HTML tables without adding a vendor.

The engine supplies matching columns. Missing or changed headings fail closed;
no guessed number is emitted.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from html.parser import HTMLParser
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree


class Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.links = []
        self.table = None
        self.row = None
        self.cell = None
        self.link = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        if tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "a" and attrs.get("href"):
            self.link = [attrs["href"], []]

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)
        if self.link is not None:
            self.link[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        elif tag == "a" and self.link is not None:
            self.links.append((self.link[0], " ".join(self.link[1])))
            self.link = None


def xlsx_tables(body: bytes) -> Iterator[list[list[str]]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(body)) as book:
        if sum(info.file_size for info in book.infolist()) > 100_000_000:
            raise ValueError("spreadsheet is too large")
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()) for item in root]
        for name in sorted(book.namelist()):
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                continue
            table = []
            for row in ElementTree.fromstring(book.read(name)).findall(".//m:sheetData/m:row", ns):
                values = []
                for cell in row.findall("m:c", ns):
                    letters = re.match(r"[A-Z]+", cell.get("r", "A1")).group()
                    column = 0
                    for letter in letters:
                        column = column * 26 + ord(letter) - 64
                    while len(values) < column:
                        values.append("")
                    raw = cell.findtext("m:v", default="", namespaces=ns)
                    if cell.get("t") == "s":
                        raw = shared[int(raw)]
                    elif cell.get("t") == "inlineStr":
                        raw = "".join(cell.find("m:is", ns).itertext())
                    values[column - 1] = raw.strip()
                table.append(values)
            yield table


def published_tables(client: Any, url: str, *, link_terms: tuple[str, ...] = ()) -> Iterator[list[list[str]]]:
    body = client.get(url)
    if body.startswith(b"PK"):
        yield from xlsx_tables(body)
        return
    text = body.decode("utf-8-sig", errors="replace")
    if "<html" not in text[:500].lower() and "<!doctype" not in text[:500].lower():
        yield list(csv.reader(io.StringIO(text)))
        return
    page = Tables()
    page.feed(text)
    yield from page.tables
    # Published downloads only; stay on this publisher's domain. Do not follow
    # arbitrary pagination or user-submitted URLs.
    host = urlsplit(url).hostname or ""
    domain = ".".join(host.split(".")[-2:])
    seen = set()
    for href, label in page.links:
        target = urljoin(url, href)
        parts = urlsplit(target)
        if parts.scheme != "https" or not parts.hostname or not (parts.hostname == domain or parts.hostname.endswith("." + domain)):
            continue
        if not parts.path.lower().endswith((".csv", ".xlsx")):
            continue
        if link_terms and not all(term in (href + " " + label).lower() for term in link_terms):
            continue
        if target in seen:
            continue
        seen.add(target)
        downloaded = client.get(target)
        if downloaded.startswith(b"PK"):
            yield from xlsx_tables(downloaded)
        else:
            yield list(csv.reader(io.StringIO(downloaded.decode("utf-8-sig"))))


def normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def numeric(text: str) -> str | None:
    value = text.strip().replace(",", "").replace("$", "")
    return value if re.fullmatch(r"-?\d+(?:\.\d+)?", value) else None


def monthly_date(text: str) -> str | None:
    value = text.strip()
    match = re.fullmatch(r"(\d{4})[Mm-](\d{1,2})(?:-\d{2})?", value)
    if match and 1 <= int(match[2]) <= 12:
        return f"{match[1]}-{int(match[2]):02d}-01"
    from datetime import datetime
    for fmt in ("%b %Y", "%B %Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-01")
        except ValueError:
            pass
    return None


def commodity_rows(tables: Any, columns: dict) -> Iterator[tuple[dict, str, str]]:
    """World Bank's historical monthly workbook has a date and named columns."""
    for table in tables:
        indices = {}
        for row in table:
            for index, cell in enumerate(row):
                if normal(cell) in columns:
                    indices[index] = columns[normal(cell)]
            if not row or not indices:
                continue
            date = monthly_date(row[0])
            if not date:
                continue
            for index, spec in indices.items():
                value = numeric(row[index]) if index < len(row) else None
                if value is not None:
                    yield spec, date, value


def table_rows(tables: Any, config: dict) -> Iterator[tuple[str, str, str]]:
    """Extract configured measures only after matching a published heading."""
    aliases = {key: {normal(value) for value in values} for key, values in config["headings"].items()}
    for table in tables:
        columns = {}
        year = None
        for row in table:
            found = {key: index for key, values in aliases.items() for index, cell in enumerate(row) if normal(cell) in values}
            if "value" in found:
                columns = found
                continue
            if len(row) == 1 and re.fullmatch(r"20\d{2}", row[0].strip()):
                year = row[0].strip()
            if not columns or "value" not in columns:
                continue
            def cell(key: str) -> str:
                index = columns.get(key, len(row))
                return row[index].strip() if index < len(row) else ""
            value = numeric(cell("value"))
            if value is None:
                continue
            stamp = monthly_date(cell("date")) if "date" in columns else None
            row_year = cell("year") or year
            if config.get("frequency") == "annual" and row_year and re.fullmatch(r"(?:19|20)\d{2}", row_year):
                stamp = row_year + "-01-01"
            elif not stamp and row_year and cell("month"):
                stamp = monthly_date(f'{cell("month")} {row_year}') or monthly_date(f'{row_year}-{cell("month")}')
            geo = cell("geo") or config.get("geo", "US")
            if stamp and geo and geo.lower() not in {"total", "united states total", "us total"}:
                yield stamp, geo, value
