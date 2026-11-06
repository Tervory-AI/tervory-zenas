"""Keep the shared Tank protocol, with a deadline over approval polling too."""
from chassis.tank import TankClient as SharedTankClient

from .boundary import bounded_call


class TankClient(SharedTankClient):
    async def _post(self, path, body):
        return await bounded_call(super()._post(path, body))

    async def _wait_job(self, tenant_id, job_id):
        return await bounded_call(super()._wait_job(tenant_id, job_id))
