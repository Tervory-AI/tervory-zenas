"""A small .docx writer. Redlines are Word tracked changes (w:ins and w:del)."""

from __future__ import annotations

import zipfile
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""


def _text(value: str) -> str:
    return escape(value or "", {"\"": "&quot;"})


def _run(kind: str, text: str, *, author: str, when: str, mark_id: int) -> str:
    safe = _text(text)
    if kind == "del":
        return (
            f'<w:del w:id="{mark_id}" w:author="{_text(author)}" w:date="{when}">'
            f"<w:r><w:delText xml:space=\"preserve\">{safe}</w:delText></w:r></w:del>"
        )
    if kind == "ins":
        return (
            f'<w:ins w:id="{mark_id}" w:author="{_text(author)}" w:date="{when}">'
            f"<w:r><w:t xml:space=\"preserve\">{safe}</w:t></w:r></w:ins>"
        )
    return f"<w:r><w:t xml:space=\"preserve\">{safe}</w:t></w:r>"


def document_xml(paragraphs: list[list[dict[str, str]]], *, author: str = "Zenas", when: str = "2026-10-15T00:00:00Z") -> str:
    """paragraphs is a list of paragraphs, each a list of runs {kind: plain|ins|del, text}."""
    body: list[str] = []
    mark_id = 1
    rows = paragraphs or [[{"kind": "plain", "text": ""}]]
    for runs in rows:
        pieces = []
        for run in runs:
            kind = str(run.get("kind") or "plain")
            pieces.append(_run(kind, str(run.get("text") or ""), author=author, when=when, mark_id=mark_id))
            if kind in ("ins", "del"):
                mark_id += 1
        body.append("<w:p>" + "".join(pieces) + "</w:p>")
    body.append(
        "<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )


def docx_bytes(paragraphs: list[list[dict[str, Any]]], *, author: str = "Zenas", when: str = "2026-10-15T00:00:00Z") -> bytes:
    xml = document_xml(paragraphs, author=author, when=when)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _CONTENT_TYPES)
        package.writestr("_rels/.rels", _RELS)
        package.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        package.writestr("word/document.xml", xml)
    return buffer.getvalue()
