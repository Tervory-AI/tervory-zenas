"""Explicit paid-policy fixtures for pre-existing protocol-only test doubles.

The Ask/Auto/card/checkpoint proof uses actual SQL in test_paid_steps.py.
These preserve the real master stop while allowing older fake-DB protocol tests
that do not implement the paid journal's schema to keep their narrower scope.
"""
import pytest


@pytest.fixture
def paid_protocol(monkeypatch):
    import controls
    import paid_steps
    async def checkpoint(key,invoke):
        return await invoke()
    async def admission(tenant,task,model,**kwargs):
        await controls.refuse_paid(task=task,path='paid_steps')
        return None
    monkeypatch.setattr(paid_steps,'checkpoint',checkpoint)
    monkeypatch.setattr(paid_steps,'model_admission',admission)


def configure_paid_auto(monkeypatch,get_pool):
    """Use real SQL and real admission; configure these protocol seats as Auto."""
    import paid_steps
    original=paid_steps.admit
    monkeypatch.setattr(paid_steps,'db',get_pool)
    async def admit(step_id,**kwargs):
        if kwargs.get('paid',True):
            async with paid_steps.connection(kwargs['tenant_id']) as conn:
                await paid_steps.register(conn,step_id,kwargs['owner'],kwargs['kind'],kwargs['source'])
                await conn.execute("""INSERT INTO tank.paid_step_settings(tenant_id,step_id,mode,updated_by)
                    VALUES($1,$2,'auto','placeholder-fixture-admin') ON CONFLICT(tenant_id,step_id)
                    DO UPDATE SET mode='auto'""",kwargs['tenant_id'],step_id)
        return await original(step_id,**kwargs)
    monkeypatch.setattr(paid_steps,'admit',admit)
