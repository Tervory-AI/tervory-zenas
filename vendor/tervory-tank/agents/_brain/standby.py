"""Provisioned Hermes runtimes with health only, no task API or model caller.

Starting a container does not authorize model work. Any future escalation must
use the owning room's approved tools and Tank's existing guards and accounting.
"""

import os

from fastapi import FastAPI

ROOM = os.environ.get("BRAIN_ROOM", "tiger")
if ROOM not in {"tiger", "zenas", "ghost", "trigger", "tisan", "radix", "market_lab"}:
    raise ValueError("Unsupported standby room")

app = FastAPI()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "room": ROOM, "hermes_version": "0.22.2"}
