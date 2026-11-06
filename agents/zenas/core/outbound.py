"""Keep the shared Tank protocol, with a deadline over approval polling too."""
from chassis.tank import TankClient as SharedTankClient

from .boundary import bounded_call


class TankClient(SharedTankClient):
    async def route(self, tenant_id, task_type, messages, *, background=False):
        from .savings import get_run, observed
        run = get_run(tenant_id)
        if run is not None:
            run.attempts += 1
        try:
            data = await super().route(tenant_id, task_type, messages, background=background)
        except BaseException:
            if run is not None:
                run.uncertain = True
            raise
        observed(tenant_id, data)
        return data

    async def run_skill(self, tenant_id, skill_id, payload):
        from .savings import get_run,observed
        run=get_run(tenant_id)
        if run is not None:run.attempts+=1
        try:
            data = await super().run_skill(tenant_id, skill_id, payload)
        except BaseException:
            if run is not None:run.uncertain=True
            raise
        ids=list(data.get('_ledger_ids') or [])
        single=data.get('_ledger_id') or data.get('ledger_id')
        if single:ids.append(single)
        for ident in ids:observed(tenant_id,{'ledger_id':ident})
        if not ids and run is not None:run.uncertain=True
        return data

    async def savings_quote(self, tenant_id, ledger_ids, reference, uncertain, run_id):
        import asyncio
        async with asyncio.timeout(5):
            return await self._post("/v1/zenas/savings/quote", {
                "tenant_id": tenant_id, "ledger_ids": ledger_ids, "reference":reference,
                "uncertain": uncertain, "run_id":run_id})

    async def _post(self, path, body):
        return await bounded_call(super()._post(path, body))

    async def _wait_job(self, tenant_id, job_id):
        return await bounded_call(super()._wait_job(tenant_id, job_id))
