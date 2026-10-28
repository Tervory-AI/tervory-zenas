"""Docassemble's questionless API on this room's private regional network.

Only fixed interview/variable names cross the API. Fields and approved clauses
are nested JSON data. Credentials stay in headers or session POST bodies.
"""
from __future__ import annotations

import base64
import os

import httpx

from agents.zenas.core.drafting import TEMPLATES


class AssemblyUnavailable(RuntimeError):
    """Use the route's existing backend-failure handling, without new page copy."""


class DocassembleClient:
    def __init__(self, region: str, *, transport=None):
        if region not in {"us-east", "za"}:
            raise AssemblyUnavailable()
        self.region = region
        self.jurisdiction = "za" if region == "za" else "us"
        self.transport = transport

    def _validate(self, payload):
        if payload.get("template") not in TEMPLATES or payload.get("jurisdiction") not in {self.jurisdiction, "both"}:
            raise AssemblyUnavailable()
        for clause in payload.get("clauses", []):
            if (clause.get("region") != self.region or clause.get("jurisdiction") not in {self.jurisdiction, "both"}
                    or clause.get("status") != "lawyer_approved" or clause.get("template") != payload["template"]):
                raise AssemblyUnavailable()
        prior = payload.get("prior")
        if prior and (prior.get("region") != self.region or prior.get("jurisdiction") not in {self.jurisdiction, "both"}
                      or prior.get("source") != "released"):
            raise AssemblyUnavailable()

    async def _run(self, payload, *, download=False):
        self._validate(payload)
        if os.environ.get("DOCASSEMBLE_REGION") != self.region:
            raise AssemblyUnavailable()
        if os.environ.get("DOCASSEMBLE_URL", "http://docassemble").rstrip("/") != "http://docassemble":
            raise AssemblyUnavailable()
        key = os.environ.get("DOCASSEMBLE_API_KEY", "")
        if not key:
            raise AssemblyUnavailable()
        interview = "docassemble.zenas:data/questions/region/" + payload["template"] + ".yml"
        try:
            async with httpx.AsyncClient(base_url="http://docassemble", transport=self.transport,
                                         headers={"X-API-Key": key}, timeout=120,
                                         trust_env=False, follow_redirects=False) as client:
                response = await client.get("/api/session/new", params={"i": interview})
                if response.status_code != 200:
                    raise AssemblyUnavailable()
                session = response.json()
                if session.get("i") != interview or not isinstance(session.get("session"), str) or not session["session"]:
                    raise AssemblyUnavailable()
                body = {"i": interview, "session": session["session"], "raw": 0,
                        "variables": {"request": {**payload, "region": self.region}}}
                if session.get("encrypted"):
                    if not isinstance(session.get("secret"), str) or not session["secret"]:
                        raise AssemblyUnavailable()
                    body["secret"] = session["secret"]
                response = await client.post("/api/session", json=body)
                if response.status_code != 200:
                    raise AssemblyUnavailable()
                result = response.json()
                if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                    raise AssemblyUnavailable()
                if download:
                    number = result.get("file_number")
                    if type(number) is not int or number < 1:
                        raise AssemblyUnavailable()
                    document = await client.get("/api/file/" + str(number), params={"extension": "docx"})
                    if document.status_code != 200 or not document.content.startswith(b"PK"):
                        raise AssemblyUnavailable()
                    return {"text": result["text"], "docx_base64": base64.b64encode(document.content).decode("ascii")}
                if result.get("ok") is not True or not isinstance(result.get("sentences"), list):
                    raise AssemblyUnavailable()
                return result
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            # Never forward response bodies, session encryption material or driver errors.
            raise AssemblyUnavailable() from None

    async def assemble(self, *, template, jurisdiction, clauses, terms, prior):
        # PostgreSQL prior rows also carry timestamps and IDs. Only the existing
        # fact pattern/policy fields belong in the interview, not the whole row.
        rows = [{key: row.get(key) for key in ("id", "region", "jurisdiction", "status", "template", "kind", "text")}
                for row in clauses]
        previous = ({key: prior.get(key) for key in ("region", "jurisdiction", "source", "fact_pattern")}
                    if prior else None)
        return await self._run({"mode": "assemble", "template": template, "jurisdiction": jurisdiction,
                                "clauses": rows, "terms": terms, "prior": previous})

    async def finish(self, *, template, jurisdiction, assembled, notice):
        return await self._run({"mode": "finish", "template": template, "jurisdiction": jurisdiction,
                                "assembled": assembled, "notice": notice}, download=True)
