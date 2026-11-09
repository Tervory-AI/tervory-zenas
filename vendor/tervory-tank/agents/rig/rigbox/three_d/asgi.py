"""Production deployment entry point: uvicorn rigbox.three_d.asgi:app.

Keeps other lanes' app.py unchanged. The existing image already copies rigbox.
No stand-in or automatic entitlement in this production entry point.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.responses import FileResponse
from .static import ViewerFiles
from app import app

from .api import mount
from .assets import R2Assets, ForgeFiles, PhotoInputError
from .engine import Engine
from .forge import BrainForge
from .store import Store
from rigbox.service import RigError

mount(app)
_original = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    async with _original(application):
        if getattr(application.state, 'missing_settings', []):
            yield
            return
        # Missing R2 credentials must not break Rig's existing catalogue API.
        # Initialize lazily at the first 3D storage operation and show its error.
        assets = LazyR2()
        application.state.rig_three_d = Engine(Store(application.state.pool), application.state.rig,
            BrainForge(application.state.http, os.environ.get("TANK_KEY", ""), assets), assets)
        yield
        if application.state.rig_three_d.assets.instance:
            await application.state.rig_three_d.assets.instance.storage.close()


class LazyR2:
    instance = None

    def backend(self):
        from chassis.storage import StorageError
        from rigbox.service import RigError
        if self.instance is None:
            try:
                self.instance = R2Assets(ForgeFiles("rig"))
            except StorageError:
                raise RigError("storage", "Model storage is not connected.", 503) from None
        return self.instance

    async def put(self, *args):
        from chassis.storage import StorageError
        try:
            return await self.backend().put(*args)
        except StorageError:
            raise RigError("storage", "The product file could not be read.", 503) from None

    async def get(self, *args):
        from chassis.storage import StorageError
        try:
            return await self.backend().get(*args)
        except StorageError:
            raise RigError("storage", "The product file could not be read.", 503) from None

    async def stage(self, job):
        from chassis.storage import StorageError
        try:
            return await self.backend().storage.stage(job)
        except PhotoInputError as exc:
            raise RigError("uploads", str(exc), 422) from None
        except StorageError:
            raise RigError("storage", "The uploaded files could not be prepared. Check them and try again.", 503) from None

    async def draft(self, job):
        from chassis.storage import StorageError
        try:
            return await self.backend().storage.draft(job)
        except StorageError:
            raise RigError("storage", "The product file could not be read.", 503) from None


app.router.lifespan_context = lifespan


from .page import PageConfig, page_config, router as page_router
app.include_router(page_router)


bundle = Path(__file__).with_name("widget")


@app.get('/3d/wall', include_in_schema=False)
async def wall():
    return FileResponse(bundle / 'index.html', headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


if bundle.is_dir():
    app.mount("/3d", ViewerFiles(directory=bundle, html=True), name="rig-three-d-test")
