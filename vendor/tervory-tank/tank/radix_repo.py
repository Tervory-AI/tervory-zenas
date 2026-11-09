"""Client archive scans through Command confirmation and Radix's existing jobs."""
import base64
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException


async def owned_attachment(actor, attachment_id):
    from command import db

    async with db().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name,content FROM tank.command_attachments WHERE attachment_id=$1 AND tenant_id=$2 AND user_id=$3",
            attachment_id, actor.tenant_id, actor.user_id)
    if row is None:
        raise HTTPException(404, "Attachment not found")
    return row


async def scan(actor, body, *, attachment=None):
    from command import MAX_ATTACHMENT_BYTES
    from trigger_commands import confirmation

    confirmation(actor, "radix.repo.scan", body.model_dump(mode="json", exclude_none=True))
    row = attachment if attachment is not None else await owned_attachment(actor, body.attachment_id)
    raw = bytes(row["content"])
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "Attachment exceeds 5 MiB")
    own_commit=getattr(body,'commit',None)
    if own_commit:
        from command import require_founder
        from radix_deploy import verify_main
        require_founder(actor)
        if not raw.startswith((b'# v2 git bundle',b'# v3 git bundle')):
            raise HTTPException(422,'The action arguments are invalid')
        await verify_main(own_commit)
    # Same signature check as ghost_routes; extraction remains in Radix.
    if not (own_commit or raw.startswith((b"PK\x03\x04", b"\x1f\x8b")) or raw[257:262] == b"ustar"):
        raise HTTPException(422, "Hand over a tar or zip archive.")
    extra = {"repository": "Tervory-AI/tervory-tank", "commit": own_commit} if own_commit else None
    submitted = await submit(actor.tenant_id, raw, row["name"], extra=extra)
    if own_commit:
        from radix_deploy import register
        await register(actor, submitted["scan_id"], own_commit)
    return submitted


async def submit(tenant, raw: bytes, ref: str, *, extra: dict | None = None):
    """Shared R5 job submission; caller supplies its already-authorized archive."""
    from ghost_routes import call_room
    response = await call_room("POST", "radix", "/jobs", body={
        "tenant_id": str(tenant), "kind": "scan",
        "input": {"tenant_id": str(tenant), "source": "b64:" + base64.b64encode(raw).decode("ascii"),
                  "ref": ref, **(extra or {})},
    })
    if response.status_code != 201:
        raise HTTPException(503, "Radix did not accept the scan.")
    data = response.json()
    if data.get("tenant_id") != str(tenant) or data.get("kind") != "scan":
        raise HTTPException(503, "Radix did not accept the scan.")
    return {"scan_id": str(UUID(data["id"])), "status": "pending"}


async def findings(actor, scan_id: UUID):
    from ghost_routes import call_room

    response = await call_room("GET", "radix", f"/jobs/{scan_id}", params={"tenant_id": str(actor.tenant_id)})
    if response.status_code != 200:
        raise HTTPException(404 if response.status_code == 404 else 503, "Radix results are unavailable.")
    data = response.json()
    if (data.get("tenant_id") != str(actor.tenant_id) or data.get("id") != str(scan_id)
            or data.get("kind") != "scan"):
        raise HTTPException(404, "Radix results are unavailable.")
    status = {"queued": "pending", "running": "running", "done": "complete", "failed": "failed"}.get(data.get("status"))
    if status is None:
        raise HTTPException(503, "Radix results are unavailable.")
    public = {"scan_id": str(scan_id), "status": status}
    if status == "complete":
        result = data.get("result")
        if (not isinstance(result, dict) or result.get("tenant_id") != str(actor.tenant_id)
                or result.get("scan_id") != str(scan_id)):
            raise HTTPException(404, "Radix results are unavailable.")
        if not isinstance(result.get("findings"), list):
            raise HTTPException(503, "Radix results are unavailable.")
        public.update({key: result.get(key) for key in
                       ("summary", "critical", "tools", "tool_status", "scan_complete", "ref", "leaked")})
        if result.get('repository')=='Tervory-AI/tervory-tank' and result.get('commit'):
            public.update(repository=result['repository'],commit=result['commit'])
        receipt = result.get("receipt")
        if receipt is not None:
            if (not isinstance(receipt, dict) or receipt.get("tenant_id") != str(actor.tenant_id)
                    or receipt.get("user_id") != actor.user_id):
                raise HTTPException(404, "Radix results are unavailable.")
            public["receipt"] = receipt
        public["findings"] = [
            {**{key: row[key] for key in ("rule", "severity", "tool", "file", "line") if key in row},
             **({"explanation": row["explanation"]} if row.get("explanation") else {}),
             **({"remediation": row["fix"]} if row.get("fix") else {})}
            for row in result.get("findings", []) if isinstance(row, dict)]
    return public


async def with_receipt(actor, tool, operation):
    from main import RouteRequest, _record_local

    status = 200
    try:
        result = await operation()
    except HTTPException as exc:
        status, result = exc.status_code, {"detail": exc.detail}
    except (ValueError, TypeError, KeyError, AttributeError):
        status, result = 503, {"detail": "Radix results are unavailable."}
    # Existing code receipt writer; no uploaded source or finding text is stored here.
    receipt = await _record_local(RouteRequest(tenant_id=actor.tenant_id, agent="radix", task_type=tool, messages=[{"role": "user", "content": tool}]),
                                  {}, model="code", rung=3, content="",
                                  outcome="delivered" if status < 300 else "failed")
    from fastapi.responses import JSONResponse
    return JSONResponse({**result, "ledger_id": str(receipt.ledger_id), "cost_usd": 0}, status_code=status,
                        headers={"Cache-Control": "private, no-store"})


def build_router():
    from command import Actor, RadixRepoArgs, identity, require_tool
    router = APIRouter(prefix="/radix/repo")

    @router.post("/scan")
    async def start(body: RadixRepoArgs, actor: Actor = Depends(identity)):
        require_tool(actor, "radix.repo.scan")
        from trigger_commands import confirmation
        confirmation(actor, "radix.repo.scan", body.model_dump(mode="json", exclude_none=True))
        attachment = await owned_attachment(actor, body.attachment_id)
        return await with_receipt(actor, "radix.repo.scan", lambda: scan(actor, body, attachment=attachment))

    @router.get("/findings")
    async def read(scan_id: UUID, actor: Actor = Depends(identity)):
        require_tool(actor, "radix.repo.findings")
        return await with_receipt(actor, "radix.repo.findings", lambda: findings(actor, scan_id))

    return router


async def code_plan(actor, messages, task_id):
    """Explicit repo commands bind upload metadata in code, without reading source."""
    import json
    import re
    from command import authorized_actor, db, require_tool, unpack

    last = messages[-1] if messages else {}
    if last.get("role") != "user" or not re.match(r"\s*radix\.repo\.(scan|findings)\b", last.get("content", ""), re.I):
        return None
    from main import RouteRequest, _record_local
    actor = await authorized_actor(actor)
    source = last
    if task_id:
        async with db().acquire() as conn:
            raw = await conn.fetchval(
                "SELECT state FROM tank.command_threads WHERE tenant_id=$1 AND user_id=$2 AND state->>'active_turn'=$3",
                actor.tenant_id, actor.user_id, str(task_id))
        source = next((m for m in reversed(unpack(raw or {}).get("messages", [])) if m.get("role") == "user"), last)
    match = re.fullmatch(r"\s*radix\.repo\.(scan|findings)\s*:?\s*([0-9a-f-]*)(?:\s+([0-9a-f]{40}))?\s*", source.get("content", ""), re.I)
    if not match:
        raise HTTPException(422, "The action arguments are invalid")
    tool = "radix.repo." + match[1].lower()
    require_tool(actor, tool)
    identifier = match[2]
    if tool == "radix.repo.scan" and not identifier:
        attachments = source.get("attachments", [])
        if len(attachments) != 1 or not attachments[0].get("attachment_id"):
            raise HTTPException(404, "Attachment not found")
        identifier = attachments[0]["attachment_id"]
    try:
        identifier = str(UUID(identifier))
    except ValueError:
        raise HTTPException(422, "The action arguments are invalid") from None
    proposed = {"text": "", "actions": [{"tool": tool, "arguments": {
        "attachment_id" if tool == "radix.repo.scan" else "scan_id": identifier}}]}
    if match[3]:
        from command import require_founder
        require_founder(actor)
        if tool!='radix.repo.scan':
            raise HTTPException(422,"The action arguments are invalid")
        proposed['actions'][0]['arguments']['commit']=match[3]
    return await _record_local(RouteRequest(tenant_id=actor.tenant_id, agent="radix", task_type="command.turn", messages=[{"role": "user", "content": tool}]),
                               {}, model="code", rung=3, content=json.dumps(proposed), task_id=task_id)
