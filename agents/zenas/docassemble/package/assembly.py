"""Docassemble glue: existing clause policy, native Mako fields, native DOCX output.

Clause text and user fields are data, never executable template source. The
legacy module is the room's existing policy module, mounted read-only.
"""
from __future__ import annotations

import os
from pathlib import Path

from mako.template import Template

from . import legacy


def render_clause(text, terms):
    literals, source, missing = [], [], []
    cursor = 0
    for match in legacy._PLACEHOLDER.finditer(text):
        literals.append(text[cursor:match.start()])
        source.append('${ literals[' + str(len(literals) - 1) + '] }')
        key = match.group(1)
        if key in terms and terms[key] != "":
            source.append('${ terms[' + repr(key) + '] }')
        else:
            missing.append(key)
            literals.append(match.group(0))
            source.append('${ literals[' + str(len(literals) - 1) + '] }')
        cursor = match.end()
    literals.append(text[cursor:])
    source.append('${ literals[' + str(len(literals) - 1) + '] }')
    return Template(''.join(source), strict_undefined=True).render(literals=literals, terms=terms), missing


def validate(request, jurisdiction, template):
    region = os.environ.get("TANK_REGION")
    expected = {"us-east": "us", "za": "za"}.get(region)
    if (expected is None or jurisdiction != expected or request.get("region") != region
            or request.get("jurisdiction") not in {expected, "both"}
            or request.get("template") != template or template not in legacy.TEMPLATES):
        raise ValueError("regional assembly mismatch")
    for clause in request.get("clauses", []):
        if (clause.get("region") != region or clause.get("jurisdiction") not in {expected, "both"}
                or clause.get("template") != template or clause.get("status") != "lawyer_approved"):
            raise ValueError("regional clause mismatch")
    prior = request.get("prior")
    if prior and (prior.get("region") != region or prior.get("jurisdiction") not in {expected, "both"}
                  or prior.get("source") != "released"):
        raise ValueError("regional prior mismatch")


def document_text(assembled, template):
    if assembled["reused"]:
        return assembled["text"]
    paragraphs = {item["slot"]: item["text"] for item in assembled["sentences"]}
    path = Path(__file__).parent / "data" / "templates" / (template + ".md")
    return Template(filename=str(path), strict_undefined=True).render(paragraphs=paragraphs)


def prepare(request, jurisdiction, template):
    validate(request, jurisdiction, template)
    assembled = legacy.assemble_draft(template=template, clauses=request.get("clauses", []),
                                     terms=request.get("terms"), prior=request.get("prior"),
                                     render_clause=render_clause)
    assembled["text"] = document_text(assembled, template)
    return assembled


def finish(request, jurisdiction, template):
    validate(request, jurisdiction, template)
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
