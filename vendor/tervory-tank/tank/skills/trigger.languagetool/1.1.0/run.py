"""Pinned self-hosted LanguageTool, reached through Trigger's own corridor."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
PIN_SHA256 = "3678bc939c81707d7309bf2e8fe0537f7c3ee66019237fa74df04d40927dc72e"


async def run(payload: dict, ctx) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or not 1 <= len(text) <= 20000:
        raise ValueError("Text must contain 1 to 20000 characters")
    if set(payload) != {"text"}:
        raise ValueError("Unsupported checker input")
    raw = (ROOT / "service.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN_SHA256:
        raise ValueError("Pinned checker data changed")
    pin = json.loads(raw)
    if hashlib.sha256((ROOT / "languagetool.properties").read_bytes()).hexdigest() != pin["config_sha256"]:
        raise ValueError("Pinned checker data changed")
    key = os.environ.get("TANK_KEY_TRIGGER", "")
    if not key:
        raise RuntimeError("Trigger room authentication is unavailable")
    response = await ctx.http.post(
        "http://agent-trigger:8000/skala/languagetool",
        headers={"X-Tank-Key": key},
        json={"tenant_id": str(ctx.tenant_id), "text": text},
        timeout=30, follow_redirects=False,
    )
    response.raise_for_status()
    data = response.json()
    if data["image"] != pin["image"] or data["config_sha256"] != pin["config_sha256"]:
        raise ValueError("LanguageTool configuration does not match this skill version")
    result = data["result"]
    software = result["software"]
    if (software.get("version") != pin["version"] or software.get("buildDate") != pin["buildDate"]
            or software.get("premium") is not False):
        raise ValueError("LanguageTool runtime does not match this skill version")
    if result.get("warnings", {}).get("incompleteResults"):
        raise RuntimeError("LanguageTool returned incomplete results")
    if not isinstance(result["matches"], list):
        raise ValueError("Invalid LanguageTool result")
    issues = [{"source": "languagetool", "rule": m["rule"]["id"], "message": m["message"],
               "offset": m["offset"], "length": m["length"]} for m in result["matches"]]
    return {"issues": issues, "rules": sorted({i["rule"] for i in issues}),
            "issue_count": len(issues), "model_calls": 0}
