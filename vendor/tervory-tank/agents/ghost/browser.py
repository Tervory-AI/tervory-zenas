"""Private browser service; no generic URL, shell, upload or browser-control API."""
import asyncio

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .app import require_tank
from .models import Look
from .probes import browser_check

app = FastAPI(title="Autumbra Watch browser", docs_url=None, redoc_url=None, openapi_url=None)
lock = asyncio.Lock()


@app.exception_handler(RequestValidationError)
async def invalid_input(_request, _error):
    return JSONResponse(status_code=422, content={"detail": "Invalid browser check."})


@app.get("/health")
async def health():
    from .configuration import setting_status
    status = setting_status("watch")
    return {"ok": True, "ready": status["configured"], **status}


@app.post("/check", dependencies=[Depends(require_tank)])
async def check(body: Look):
    try:
        async with lock:
            return await browser_check(body)
    except Exception:
        raise HTTPException(503, "The read-only browser check could not finish.") from None
