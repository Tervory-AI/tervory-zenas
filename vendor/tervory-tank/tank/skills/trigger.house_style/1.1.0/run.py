"""Versioned, isolated code checker. No model or room-code imports."""
from __future__ import annotations
import asyncio
import hashlib
import json
from importlib.metadata import version
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
ARCHIVE_SHA256 = "f6dc84b95bf04cb3d5e4a05379ad36d97f0af8937982cee166aa8870a2aefa1d"
COMPONENT = "house_style"


async def run(payload: dict, ctx) -> dict:
    del ctx
    text = payload.get("text")
    if not isinstance(text, str) or not 1 <= len(text) <= 20000:
        raise ValueError("Text must contain 1 to 20000 characters")
    if set(payload) - {"text", "voice"} or len(json.dumps(payload)) > 250000:
        raise ValueError("Unsupported checker input")
    if hashlib.sha256((ROOT / "rules.zip").read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Pinned checker data changed")
    # Native parsing/regex dependencies may not drift beneath an old approval.
    for distribution, pinned in {"google-re2": "1.1.20251105", "pydantic": "2.10.6"}.items():
        if version(distribution) != pinned:
            raise ValueError("Pinned checker dependency changed")
    # This process owns the scratch folder, so it is removed when the engine
    # exits, is killed or times out, and when this call is cancelled.
    with tempfile.TemporaryDirectory(prefix="trigger-skill-", ignore_cleanup_errors=True) as scratch:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(ROOT / "engine.py"), COMPONENT, scratch,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), 30)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
    if process.returncode:
        raise ValueError("Invalid voice or unavailable checker dependency")
    return json.loads(stdout)
