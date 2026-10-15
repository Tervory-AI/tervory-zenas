"""Turn a download into text payloads. PDFs and images are never stored."""

from __future__ import annotations

import io
import zipfile

_PDF = b"%PDF"
_IMAGES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*")


def is_skipped_binary(blob: bytes) -> bool:
    head = blob[:8]
    if head.startswith(_PDF):
        return True
    return any(head.startswith(magic) for magic in _IMAGES)


def text_payloads(blob: bytes) -> list[bytes]:
    """XML, HTML, or JSON from a blob or a zip. Skip PDF and image members."""
    if blob[:2] == b"PK":
        out: list[bytes] = []
        try:
            archive = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            return []
        with archive:
            for name in archive.namelist():
                lower = name.lower()
                if lower.endswith((".pdf", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff")):
                    continue
                if not lower.endswith((".xml", ".html", ".htm", ".json", ".txt")):
                    continue
                member = archive.read(name)
                if is_skipped_binary(member):
                    continue
                out.append(member)
        return out
    if is_skipped_binary(blob):
        return []
    return [blob]
