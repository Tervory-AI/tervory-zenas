"""Stdlib HTML parsers for the official page shapes this wave saves."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser

from agents.zenas.pool.model import Section

_MARKER = re.compile(r"^\(([0-9]+|[A-Za-z]+)\)\s*")
_EFF = re.compile(r"eff\.\s+([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})")
_SECTION_NO = re.compile(r"§\s*([0-9]+[0-9A-Za-z-]*)")
_RULE_NO = re.compile(r"Rule\s+([0-9]+(?:\.[0-9]+)?)")
_TITLE_NO = re.compile(r"Title\s+(\d+)")
_USC_LABEL = re.compile(r"(\d+)\s+U\.S\.C\.")


def _depth(class_name: str) -> int:
    for level, mark in ((4, "4em"), (3, "3em"), (2, "2em"), (1, "1em")):
        if mark in class_name:
            return level
    if "indent-4" in class_name:
        return 3
    if "indent-3" in class_name:
        return 2
    if "indent-2" in class_name:
        return 1
    if "indent-1" in class_name or "subsection" in class_name:
        return 0
    return 0


def _heading_of(text: str) -> str:
    if "\u2014" in text:
        head = text.split("\u2014", 1)[0]
        head = re.sub(r"^\([^)]+\)\s*", "", head).strip(" .")
        if head and len(head) <= 140:
            return head
    return ""


def nest_paragraphs(paragraphs: list[tuple[int, str]]) -> list[Section]:
    root = Section("", "", "", [])
    stack: list[tuple[int, Section]] = [(-1, root)]
    for depth, raw in paragraphs:
        text = re.sub(r"\s+", " ", raw).strip()
        if not text:
            continue
        match = _MARKER.match(text)
        marker = match.group(0).strip() if match else ""
        node = Section(marker, _heading_of(text), text, [])
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack:
            stack = [(-1, root)]
        parent = stack[-1][1]
        if marker:
            node.path = f"{parent.path}{marker}" if parent.path else marker
        parent.children.append(node)
        stack.append((depth, node))
    return root.children


def last_effective(credit: str) -> date | None:
    found = list(_EFF.finditer(credit))
    if not found:
        return None
    last = found[-1]
    month = datetime.strptime(last.group(1), "%b").month
    return date(int(last.group(3)), month, int(last.group(2)))


@dataclass
class GovinfoInstrument:
    usc_title: str
    heading: str
    number: str
    current_through: date | None
    sections: list[Section]
    notes: list[Section]
    credit: str


class _GovinfoHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.mode = ""
        self.skip = 0
        self.in_h3 = False
        self.h3: list[str] = []
        self.heading = ""
        self.current_through: date | None = None
        self.usc_title = ""
        self._p_class = ""
        self._in_p = False
        self._buf: list[str] = []
        self.statute: list[tuple[int, str]] = []
        self.notes: list[tuple[int, str]] = []
        self.credit: list[str] = []
        self._in_title = False
        self._title_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        values = {key: value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
            self._title_buf = []
        if tag == "h3" and "section-head" in values.get("class", ""):
            self.in_h3 = True
            self.h3 = []
        if tag == "p" and self.mode in ("statute", "notes", "credit"):
            self._in_p = True
            self._p_class = values.get("class", "")
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "title" and self._in_title:
            self._in_title = False
            match = _TITLE_NO.search("".join(self._title_buf))
            if match and not self.usc_title:
                self.usc_title = match.group(1)
        if tag == "h3" and self.in_h3:
            self.in_h3 = False
            self.heading = re.sub(r"\s+", " ", "".join(self.h3)).strip()
        if tag == "p" and self._in_p:
            self._in_p = False
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if not text:
                return
            if self.mode == "statute":
                self.statute.append((_depth(self._p_class), text))
            elif self.mode == "notes":
                self.notes.append((_depth(self._p_class), text))
            elif self.mode == "credit":
                self.credit.append(text)

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self._in_title:
            self._title_buf.append(data)
        if self.in_h3:
            self.h3.append(data)
        if self._in_p:
            self._buf.append(data)
        if not self.usc_title:
            match = _USC_LABEL.search(data)
            if match:
                self.usc_title = match.group(1)

    def handle_comment(self, data: str) -> None:
        if "currentthrough:" in data:
            match = re.search(r"currentthrough:(\d{8})", data)
            if match:
                raw = match.group(1)
                self.current_through = date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
        if "field-start:statute" in data:
            self.mode = "statute"
        elif "field-end:statute" in data and self.mode == "statute":
            self.mode = ""
        elif "field-start:notes" in data:
            self.mode = "notes"
        elif "field-end:notes" in data and self.mode == "notes":
            self.mode = ""
        elif "field-start:sourcecredit" in data:
            self.mode = "credit"
        elif "field-end:sourcecredit" in data and self.mode == "credit":
            self.mode = ""


def parse_govinfo_html(blob: bytes) -> GovinfoInstrument:
    parser = _GovinfoHTML()
    parser.feed(blob.decode("utf-8", errors="replace"))
    heading = parser.heading
    number = ""
    section = _SECTION_NO.search(heading)
    rule = _RULE_NO.search(heading)
    if section:
        number = section.group(1)
    elif rule:
        number = rule.group(1)
    notes = nest_paragraphs(parser.notes)
    return GovinfoInstrument(
        usc_title=parser.usc_title,
        heading=heading,
        number=number,
        current_through=parser.current_through,
        sections=nest_paragraphs(parser.statute),
        notes=notes,
        credit=" ".join(parser.credit).strip(),
    )


@dataclass
class EcfrSection:
    citation: str
    heading: str
    as_of: date | None
    paragraphs: list[tuple[str, str, str]] = field(default_factory=list)
    body: str = ""


class _EcfrHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.section_div = 0
        self.sections: list[EcfrSection] = []
        self._current: EcfrSection | None = None
        self._in_h4 = False
        self._h4: list[str] = []
        self._meta = ""
        self._in_p = False
        self._p: list[str] = []
        self._em: list[str] = []
        self._in_em = False
        self._data_title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "")
        if tag == "div" and "section" in classes.split():
            self.section_div += 1
            self._current = EcfrSection("", "", None)
            self.sections.append(self._current)
        if self._current is None:
            return
        if tag == "h4" and self._current.heading == "":
            self._in_h4 = True
            self._h4 = []
            self._meta = values.get("data-hierarchy-metadata", "")
        if tag == "p":
            self._in_p = True
            self._p = []
            self._em = []
            self._data_title = values.get("data-title", "")
        if tag == "em" and "paragraph-heading" in classes and self._in_p:
            self._in_em = True
            self._em = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "h4" and self._in_h4 and self._current is not None:
            self._in_h4 = False
            self._current.heading = re.sub(r"\s+", " ", "".join(self._h4)).strip()
            if self._meta:
                try:
                    meta = json.loads(self._meta)
                except json.JSONDecodeError:
                    meta = {}
                self._current.citation = str(meta.get("citation") or "")
                path = str(meta.get("path") or "")
                match = re.search(r"/on/(\d{4}-\d{2}-\d{2})/", path)
                if match:
                    self._current.as_of = date.fromisoformat(match.group(1))
        if tag == "em" and self._in_em:
            self._in_em = False
        if tag == "p" and self._in_p and self._current is not None:
            self._in_p = False
            text = re.sub(r"\s+", " ", "".join(self._p)).strip()
            heading = re.sub(r"\s+", " ", "".join(self._em)).strip()
            if self._data_title:
                self._current.paragraphs.append((self._data_title, heading, text))
            elif text:
                self._current.body = f"{self._current.body} {text}".strip()

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self._in_h4:
            self._h4.append(data)
        if self._in_p:
            self._p.append(data)
        if self._in_em:
            self._em.append(data)


def _ecfr_path(section_id: str, data_title: str) -> str:
    if data_title.startswith(section_id):
        return data_title[len(section_id) :]
    return data_title


def ecfr_sections(blob: bytes) -> list[tuple[EcfrSection, list[Section]]]:
    parser = _EcfrHTML()
    parser.feed(blob.decode("utf-8", errors="replace"))
    out: list[tuple[EcfrSection, list[Section]]] = []
    for section in parser.sections:
        match = re.search(r"(\d+\.\d+)", section.citation or section.heading)
        section_id = match.group(1) if match else ""
        nodes: list[Section] = []
        by_path: dict[str, Section] = {}
        for data_title, heading, text in section.paragraphs:
            path = _ecfr_path(section_id, data_title)
            node = Section(path, heading, text, [])
            by_path[path] = node
            parent_path = path.rsplit("(", 1)[0] if path.count("(") > 1 else ""
            # "(b)(1)" -> parent "(b)"; "(b)" -> root
            if path.count("(") > 1:
                parent_path = path[: path.rfind("(")]
                parent = by_path.get(parent_path)
                if parent is not None:
                    parent.children.append(node)
                    continue
            nodes.append(node)
        if not nodes and section.body:
            nodes = [Section("", section.heading, section.body, [])]
        out.append((section, nodes))
    return out


class _DelawareHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.sections: list[tuple[str, str, list[tuple[int, str]]]] = []
        self._in_section = False
        self._depth = 0
        self._head = False
        self._head_buf: list[str] = []
        self._head_id = ""
        self._in_p = False
        self._p_class = ""
        self._p_buf: list[str] = []
        self._paras: list[tuple[int, str]] = []
        self._heading = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "")
        if tag == "div" and "Section" in classes.split():
            if self._in_section:
                self._flush()
            self._in_section = True
            self._depth = 1
            self._paras = []
            self._heading = ""
            self._head_id = ""
            return
        if self._in_section and tag == "div":
            self._depth += 1
            if "SectionHead" in classes.split():
                self._head = True
                self._head_buf = []
                self._head_id = values.get("id", "")
        if self._in_section and tag == "p":
            self._in_p = True
            self._p_class = classes
            self._p_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "p" and self._in_p:
            self._in_p = False
            text = re.sub(r"\s+", " ", "".join(self._p_buf)).strip()
            if text:
                self._paras.append((_depth(self._p_class), text))
        if tag == "div" and self._head:
            self._head = False
            self._heading = re.sub(r"\s+", " ", "".join(self._head_buf)).strip()
        if tag == "div" and self._in_section:
            self._depth -= 1
            if self._depth <= 0:
                self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self._head:
            self._head_buf.append(data)
        if self._in_p:
            self._p_buf.append(data)

    def close(self) -> None:
        super().close()
        if self._in_section:
            self._flush()

    def _flush(self) -> None:
        if self._heading or self._paras:
            self.sections.append((self._head_id, self._heading, self._paras))
        self._in_section = False
        self._depth = 0
        self._paras = []
        self._heading = ""


def parse_delaware_html(blob: bytes) -> list[tuple[str, str, list[Section]]]:
    parser = _DelawareHTML()
    parser.feed(blob.decode("utf-8", errors="replace"))
    parser.close()
    out = []
    for head_id, heading, paras in parser.sections:
        number = head_id or ""
        match = _SECTION_NO.search(heading)
        if match:
            number = match.group(1)
        out.append((number, heading, nest_paragraphs(paras)))
    return out
