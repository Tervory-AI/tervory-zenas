"""Contract assembly inside agent-zenas: approved clauses, Mako slot layouts, a docxtpl Word file.

No separate server, no network and no model. The box region comes from the room's
TANK_REGION (agents/zenas/app.py refuses to start a request without it). Every
clause, the prior matter and the jurisdiction must belong to that region, or the
request is refused before anything is assembled.
Clause text and user fields are data, never executable template source.
"""
from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from docxtpl import DocxTemplate
from mako.template import Template

from agents.zenas.core import drafting

TEMPLATE_DIR = Path(__file__).parent / "templates"
DOCUMENT = TEMPLATE_DIR / "document.docx"
_JURISDICTION = {"us-east": "us", "za": "za"}


class AssemblyUnavailable(RuntimeError):
    """The request does not belong to this region, or the box's region is not set to it."""


def render_clause(text: str, terms: dict[str, str]) -> tuple[str, list[str]]:
    """Fill {placeholders} through Mako without ever compiling clause or term text."""
    literals, source, missing = [], [], []
    cursor = 0
    for match in drafting._PLACEHOLDER.finditer(text):
        literals.append(text[cursor:match.start()])
        source.append("${ literals[" + str(len(literals) - 1) + "] }")
        key = match.group(1)
        if key in terms and terms[key] != "":
            source.append("${ terms[" + repr(key) + "] }")
        else:
            missing.append(key)
            literals.append(match.group(0))
            source.append("${ literals[" + str(len(literals) - 1) + "] }")
        cursor = match.end()
    literals.append(text[cursor:])
    source.append("${ literals[" + str(len(literals) - 1) + "] }")
    return Template("".join(source), strict_undefined=True).render(literals=literals, terms=terms), missing


def validate(request: dict[str, Any], region: str, template: str) -> None:
    expected = _JURISDICTION.get(region)
    if (expected is None or request.get("region") != region
            or request.get("jurisdiction") not in {expected, "both"}
            or request.get("template") != template or template not in drafting.TEMPLATES):
        raise AssemblyUnavailable("regional assembly mismatch")
    for clause in request.get("clauses") or []:
        if (clause.get("region") != region or clause.get("jurisdiction") not in {expected, "both"}
                or clause.get("template") != template or clause.get("status") != "lawyer_approved"):
            raise AssemblyUnavailable("regional clause mismatch")
    prior = request.get("prior")
    if prior and (prior.get("region") != region or prior.get("jurisdiction") not in {expected, "both"}
                  or prior.get("source") != "released"):
        raise AssemblyUnavailable("regional prior mismatch")


def document_text(assembled: dict[str, Any], template: str) -> str:
    if assembled["reused"]:
        return assembled["text"]
    paragraphs = {item["slot"]: item["text"] for item in assembled["sentences"]}
    path = TEMPLATE_DIR / (template + ".md")
    return Template(filename=str(path), strict_undefined=True).render(paragraphs=paragraphs)


def prepare(request: dict[str, Any], region: str, template: str) -> dict[str, Any]:
    validate(request, region, template)
    assembled = drafting.assemble_draft(template=template, clauses=request.get("clauses") or [],
                                        terms=request.get("terms"), prior=request.get("prior"),
                                        render_clause=render_clause)
    if assembled.get("ok"):
        assembled["text"] = document_text(assembled, template)
    return assembled


def finish(request: dict[str, Any], region: str, template: str) -> dict[str, Any]:
    validate(request, region, template)
    assembled = request["assembled"]
    text = document_text(assembled, template)
    if assembled["reused"]:
        blocks = [[{"kind": "plain", "text": text}]]
    else:
        blocks = [[{"kind": "ins" if item.get("review") else "plain", "text": item["text"]}]
                  for item in assembled["sentences"]]
    notice = request["notice"]
    if not text.rstrip().endswith(notice):
        blocks.append([{"kind": "plain", "text": notice}])
        text = text.rstrip() + "\n\n" + notice
    return {"text": text.rstrip(), "blocks": blocks}


def render_docx(blocks: list[list[dict[str, str]]]) -> bytes:
    """Fill document.docx: one paragraph per block, marked sentences as tracked insertions."""
    document = DocxTemplate(DOCUMENT)
    document.render({"result": {"blocks": blocks}}, autoescape=True)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class Assembler:
    """In-process assembly for one region. Same calls draft_contract has always made."""

    def __init__(self, region: str):
        if region not in _JURISDICTION:
            raise AssemblyUnavailable("unknown region")
        self.region = region

    async def assemble(self, *, template, jurisdiction, clauses, terms, prior):
        # PostgreSQL prior rows also carry timestamps and IDs; only the policy fields are used.
        rows = [{key: row.get(key) for key in ("id", "region", "jurisdiction", "status", "template", "kind", "text")}
                for row in clauses]
        previous = ({key: prior.get(key) for key in ("region", "jurisdiction", "source", "fact_pattern")}
                    if prior else None)
        request = {"region": self.region, "template": template, "jurisdiction": jurisdiction,
                   "clauses": rows, "terms": terms, "prior": previous}
        return prepare(request, self.region, template)

    async def finish(self, *, template, jurisdiction, assembled, notice):
        request = {"region": self.region, "template": template, "jurisdiction": jurisdiction,
                   "assembled": assembled, "notice": notice}
        result = finish(request, self.region, template)
        data = await asyncio.to_thread(render_docx, result["blocks"])
        return {"text": result["text"], "docx_base64": base64.b64encode(data).decode("ascii")}
