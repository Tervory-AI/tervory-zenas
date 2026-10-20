"""Resolve model pointers against a stored public judgment."""

from __future__ import annotations

import re
from typing import Any

from agents.zenas.core import engine
from agents.zenas.core.engine import fold_for_quote


_COURT_CODE = r"(?:ZA[A-Z]+|SCA|CC)\b"
_MARK = re.compile(r"\[(\d+)\](?!\s*" + _COURT_CODE + ")")
_PARAGRAPH = re.compile(r"(?m)^[ \t]*(?:\[(\d+)\]|(\d+)\.)(?!\s*" + _COURT_CODE + r")[ \t]*(?:\n|(?=\S))")
_NOTE = re.compile(r"^[ \t]*(?:Footnote[ \t]+)?(\d+)(?::|[ \t]+)[ \t]*(?=[A-Z(\"'‘“])")
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\b"
_DATE = re.compile(r"^\s*(?:[12]\d{3}\b|(?:\d+\s+)?" + _MONTH + ")")


class PointerError(ValueError):
    pass


def judgment_text(text: str) -> str:
    pending: set[int] = set()
    kept: list[str] = []
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        protected = bool(_PARAGRAPH.match(line) or re.match(r"^\s*\d+\.", line) or _DATE.match(line))
        # Plain-text wrapping carries no proof that the next line belongs to a
        # footnote. Delete only its marked header, never arbitrary continuations.
        note = _NOTE.match(line) if not protected else None
        previous = lines[index - 1] if index else ""
        boundary = not previous.strip() or bool(note and any(
            mark.group(1) == note.group(1) and previous[:mark.start()].strip()
            for mark in _MARK.finditer(previous)
        ))
        if not boundary:
            note = None
        if note and int(note.group(1)) in pending:
            pending.remove(int(note.group(1)))
            continue
        for mark in _MARK.finditer(line):
            following = line[mark.end():] + (lines[index + 1] if index + 1 < len(lines) else "")
            if int(mark.group(1)) < 1000 and line[:mark.start()].strip() and not re.match(r"\s*" + _COURT_CODE, following):
                pending.add(int(mark.group(1)))
        kept.append(line)
    return "".join(kept)


def _fold_with_offsets(text: str) -> tuple[str, list[tuple[int, int]]]:
    characters: list[str] = []
    offsets: list[tuple[int, int]] = []
    for index, character in enumerate(text):
        translated = character.translate(engine._QUOTE_CHARS)
        characters.extend(translated)
        offsets.extend([(index, index + 1)] * len(translated))
    folded = "".join(characters)
    for pattern, replacement in (
        (engine._PARA_NO, " "), (engine._LINE_NO, " "),
        (engine._FOOT_STAR, ""), (engine._HYPHEN_BREAK, ""), (engine._HYPHEN_WORD, ""),
    ):
        pieces = []
        mapped: list[tuple[int, int]] = []
        cursor = 0
        for match in pattern.finditer(folded):
            pieces.append(folded[cursor:match.start()])
            mapped.extend(offsets[cursor:match.start()])
            pieces.append(replacement)
            if replacement:
                mapped.append((offsets[match.start()][0], offsets[match.end() - 1][1]))
            cursor = match.end()
        pieces.append(folded[cursor:])
        mapped.extend(offsets[cursor:])
        folded, offsets = "".join(pieces), mapped
    pieces = []
    mapped = []
    for match in re.finditer(r"\S+", folded):
        if pieces:
            pieces.append(" ")
            mapped.append((mapped[-1][1], offsets[match.start()][0]))
        pieces.append(match.group())
        mapped.extend(offsets[match.start():match.end()])
    return "".join(pieces), mapped


def _passage(text: str) -> str:
    if not text.strip() or len(fold_for_quote(text).split()) < 8:
        raise PointerError("pointer did not resolve")
    return text


def resolve_pointer(pointer: Any, text: str) -> str:
    if not isinstance(pointer, dict):
        raise PointerError("pointer did not resolve")
    matches = list(_PARAGRAPH.finditer(text))
    numbers = [int(match.group(1) or match.group(2)) for match in matches]
    if any(previous >= current for previous, current in zip(numbers, numbers[1:])):
        raise PointerError("pointer did not resolve")
    paragraphs = pointer.get("paragraphs")
    if paragraphs is not None:
        if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 8:
            raise PointerError("pointer did not resolve")
        if any(type(number) is not int for number in paragraphs) or any(
            previous >= current for previous, current in zip(paragraphs, paragraphs[1:])
        ):
            raise PointerError("pointer did not resolve")
        found: dict[int, list[str]] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            number = int(match.group(1) or match.group(2))
            found.setdefault(number, []).append(text[match.end():end].strip())
        if any(type(number) is not int or len(found.get(number, [])) != 1 for number in paragraphs):
            raise PointerError("pointer did not resolve")
        positions = [next(index for index, match in enumerate(matches) if int(match.group(1) or match.group(2)) == number) for number in paragraphs]
        if positions != sorted(positions):
            raise PointerError("pointer did not resolve")
        return " ... ".join(_passage(found[number][0]) for number in paragraphs)
    first = pointer.get("first")
    last = pointer.get("last")
    if not isinstance(first, str) or not isinstance(last, str):
        raise PointerError("pointer did not resolve")
    first, last = fold_for_quote(first), fold_for_quote(last)
    source, offsets = _fold_with_offsets(text)
    if len(first.split()) != 8 or len(last.split()) != 8:
        raise PointerError("pointer did not resolve")
    starts = list(re.finditer(r"(?<!\w)" + re.escape(first) + r"(?!\w)", source))
    ends = list(re.finditer(r"(?<!\w)" + re.escape(last) + r"(?!\w)", source))
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].start() or ends[0].end() < starts[0].end():
        raise PointerError("pointer did not resolve")
    start, end = offsets[starts[0].start()][0], offsets[ends[0].end() - 1][1]
    if end - start > 3000:
        raise PointerError("pointer did not resolve")
    return _passage(text[start:end])


def resolve_answer(data: dict[str, Any], text: str) -> dict[str, Any]:
    if "facts_pointer" in data:
        data.pop("narrative", None)
        data["facts"] = resolve_pointer(data["facts_pointer"], text)
    for field in ("elements", "element_findings", "citations"):
        for item in data.get(field) or []:
            if isinstance(item, dict) and "quote_pointer" in item:
                item["quote"] = resolve_pointer(item["quote_pointer"], text)
    return data
