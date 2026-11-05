"""Tank-only controls on port 8003; the public proxy serves port 8000 only."""

import asyncio
import os
import socket
from contextlib import asynccontextmanager, nullcontext

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rigbox.retailer_api import router
from rigbox.service import RigError, Service


def create_retailer_app(service: Service) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.state.rig = service
    app.include_router(router)

    @app.exception_handler(RigError)
    async def error(request: Request, exc: RigError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    return app


class RetailerServer(uvicorn.Server):
    # The public server owns process signals. This listener shares its lifespan.
    def capture_signals(self):
        return nullcontext()


@asynccontextmanager
async def serve_retailer(service: Service, *, host: str = "0.0.0.0", port: int = 8003):
    if not os.environ.get("RIG_RETAILER_KEY"):
        # Missing setup must not take down the shopper API. Tank fails closed.
        yield
        return
    server = RetailerServer(uvicorn.Config(create_retailer_app(service), host=host, port=port,
        lifespan="off", access_log=False, log_config=None, timeout_graceful_shutdown=5))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("Retailer controls failed to start")
                    await asyncio.sleep(0.01)
            yield listener.getsockname()[1]
        finally:
            server.should_exit = True
            await task
