"""Keep the shared Tank protocol, with a deadline over approval polling too."""
from chassis.tank import TankClient as SharedTankClient

from .boundary import bounded_call


class TankClient(SharedTankClient):
    async def _recorded_task(self, method, actor, *args):
        import httpx
        from chassis.user_assertion import SignedUser
        from .savings import get_run, observed
        verified = SignedUser(str(actor.tenant_id), actor.user_id, actor.role)
        run = get_run(verified.tenant_id)
        try:
            data = await method(verified, *args)
        except httpx.HTTPStatusError as exc:
            if run is not None and exc.response.status_code not in {401, 403, 404, 405, 409, 422}:
                run.attempts += 1
                run.uncertain = True
            raise
        except (httpx.HTTPError, ValueError):
            if run is not None:
                run.attempts += 1
                run.uncertain = True
            raise
        bill = data['receipt']
        if run is not None and bill['model'] != 'code':
            run.attempts += 1
        observed(verified.tenant_id, bill)
        return data

    async def room_task(self, actor, run_id, messages):
        return await self._recorded_task(super().room_task, actor, run_id, messages)

    async def delegate_task(self, actor, run_id, task, payload, recall):
        return await self._recorded_task(super().delegate_task, actor, run_id, task, payload, recall)

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
                "home_region": __import__('os').environ.get('TANK_REGION'),
                "uncertain": uncertain, "run_id":run_id,
                "baseline_ledger_ids": reference.pop("baseline_ledger_ids", [])})

    async def _post(self, path, body):
        return await bounded_call(super()._post(path, body))

    async def _wait_job(self, tenant_id, job_id):
        return await bounded_call(super()._wait_job(tenant_id, job_id))
