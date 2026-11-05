"""Read a PNG with Tesseract. The binary lives in this box, not in Tank."""

from __future__ import annotations

import asyncio


async def ocr_png(raw: bytes) -> str:
    """Return the text Tesseract can see. Empty when it cannot run."""
    if not raw:
        return ""
    proc = await asyncio.create_subprocess_exec(
        "tesseract",
        "stdin",
        "stdout",
        "--psm",
        "6",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _err = await asyncio.wait_for(proc.communicate(raw), timeout=20)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    if proc.returncode != 0:
        return ""
    return out.decode("utf-8", errors="replace")[:20_000]
