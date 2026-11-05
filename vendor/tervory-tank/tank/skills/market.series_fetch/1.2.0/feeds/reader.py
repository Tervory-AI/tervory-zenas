"""Read saved fixtures. Tests never open a socket."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path


def iter_dicts(
    lines: Iterable[str],
    *,
    header_marker: str | None = None,
) -> Iterator[dict[str, str]]:
    """Stream rows. Lines before the header are skipped when a marker is set."""
    iterator = iter(lines)
    header: str | None = None
    for line in iterator:
        cleaned = line.lstrip("\ufeff").rstrip("\r\n")
        if not cleaned.strip():
            continue
        if header_marker is not None and header_marker not in cleaned:
            continue
        header = cleaned
        break
    if header is None:
        return
    delimiter = "\t" if "\t" in header else ","

    def chain() -> Iterator[str]:
        yield header or ""
        for line in iterator:
            yield line.rstrip("\r\n")

    yield from csv.DictReader(chain(), delimiter=delimiter)


class FixtureReader:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots

    def _path(self, relative: str) -> Path:
        for root in self.roots:
            path = root / relative
            if path.is_file():
                return path
        raise FileNotFoundError(relative)

    def read_text(self, relative: str) -> str:
        return self._path(relative).read_text(encoding="utf-8")

    def iter_rows(
        self,
        relative: str,
        *,
        header_marker: str | None = None,
    ) -> Iterator[dict[str, str]]:
        path = self._path(relative)
        with path.open(encoding="utf-8", newline="") as handle:
            yield from iter_dicts(handle, header_marker=header_marker)
