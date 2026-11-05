"""Run the handed-over-code scans on one handed-over tree and store the findings."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from scanlib import archive, dependencies, iac, secrets, static, store

_KEEP = ("tool", "severity", "file", "line", "rule", "fix", "type", "package", "version", "advisory")


def _public_ref(ref: str, hidden: list[str]) -> str:
    text = secrets.redact(ref.strip() or "the code", hidden)[:200]
    return text or "the code"


def summarize(ref: str, findings: list[dict]) -> str:
    critical = sum(1 for item in findings if item.get("severity") == "critical")
    count = len(findings)
    noun = "finding" if count == 1 else "findings"
    if critical:
        return f"Scanned {ref}. {count} {noun}, {critical} critical. The build must not deploy."
    if count:
        return f"Scanned {ref}. {count} {noun}, none critical. This scan does not block the build."
    return f"Scanned {ref}. No findings. This scan does not block the build."


def _redact_findings(findings: list[dict], hidden: list[str]) -> list[dict]:
    cleaned: list[dict] = []
    for item in findings:
        row: dict = {}
        for key in _KEEP:
            if key not in item:
                continue
            value = item[key]
            if isinstance(value, str):
                row[key] = secrets.redact(value, hidden)
            else:
                row[key] = value
        cleaned.append(row)
    return cleaned


def collect(root: Path, tool_status: dict | None = None) -> tuple[list[dict], list[str]]:
    secret_findings, hidden = secrets.scan_tree(root)
    infrastructure = iac.scan_tree(root)
    if tool_status is not None:
        tool_status[iac.TOOL] = infrastructure.status
    findings = secret_findings + dependencies.scan_tree(root) + static.scan_tree(root) + infrastructure
    findings.sort(key=lambda item: (item["tool"], item["file"], item["line"], item["rule"]))
    return _redact_findings(findings, hidden), hidden


def build_result(ctx) -> dict:
    payload = ctx.input if isinstance(getattr(ctx, "input", None), dict) else {}
    incoming = str(payload.get("tenant_id") or "")
    if incoming and incoming != str(ctx.tenant_id):
        raise PermissionError("tenant mismatch")
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    ref = str(payload.get("ref") or "the code")

    def read_file(name: str) -> bytes:
        return ctx.files.read_file(str(ctx.tenant_id), name)

    raw = archive.load_bytes(source, read_file)
    with tempfile.TemporaryDirectory(prefix="radix-scan-") as tmp:
        commit=payload.get('commit') if payload.get('repository')=='Tervory-AI/tervory-tank' else None
        root = archive.unpack(raw, Path(tmp) / "tree", **({'commit':commit} if commit else {}))
        tool_status = {}
        findings, hidden = collect(root, tool_status)
    shown_ref = _public_ref(ref, hidden)
    critical = sum(1 for item in findings if item.get("severity") == "critical")
    summary = summarize(shown_ref, findings)
    if tool_status[iac.TOOL] != "run":
        if not findings:
            summary = f"Scanned {shown_ref}. This scan does not block the build."
        summary += " scan.iac: not run."
    result = {
        "scan_id": str(ctx.job_id),
        "tenant_id": str(ctx.tenant_id),
        "ref": shown_ref,
        "summary": summary,
        "tool_status": tool_status,
        "scan_complete": tool_status[iac.TOOL] == "run",
        "deploy_blocked": critical > 0,
        "critical": critical,
        "tools": ["scan.secrets", "scan.dependencies", "scan.static", "scan.iac"],
        "findings": findings,
    }
    if commit:
        result.update(repository='Tervory-AI/tervory-tank',commit=commit)
    blob = json.dumps(result)
    if any(secret and secret != "REDACTED" and secret in blob for secret in hidden):
        for secret in hidden:
            blob = secrets.redact(blob, [secret])
        result = json.loads(blob)
        result["leaked"] = True
    else:
        result["leaked"] = False
    return result


async def _connect():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    import asyncpg

    return await asyncpg.connect(url)


async def scan_job(ctx, *, connect=None, schema: str | None = None) -> dict:
    result = await asyncio.to_thread(build_result, ctx)
    schema_name = store.validate_schema(schema or f"agent_{ctx.agent}")
    opener = connect or _connect
    conn = await opener()
    try:
        await store.ensure_findings(conn, schema_name)
        await store.insert_findings(
            conn,
            schema_name,
            str(ctx.tenant_id),
            str(ctx.job_id),
            result["findings"],
        )
    finally:
        close = getattr(conn, "close", None)
        if close is not None:
            await close()
    from chassis.events import publish
    for ordinal, finding in enumerate(result["findings"]):
        severity = str(finding.get("severity", "unknown")).lower()
        if severity not in {"critical", "high", "medium", "low", "info"}:
            severity = "unknown"
        await publish(str(ctx.tenant_id), "radix.finding", f"{ctx.job_id}:{ordinal}", {
            "scan_id": str(ctx.job_id), "ordinal": ordinal, "severity": severity})
    return result
