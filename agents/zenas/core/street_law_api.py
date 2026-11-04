"""Protected operator API for Street Law; no public pages or body-asserted roles."""
from __future__ import annotations

import hmac
import os
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from agents.zenas.core.street_law import Actor, Refused, StreetLaw
from agents.zenas.core.street_law_store import PgSessions


class Context(BaseModel):
    tenant_id: UUID
    home_region: str


class SessionIn(Context):
    name: str = Field(min_length=1, max_length=200)


class QuestionIn(Context):
    question: str = Field(min_length=1, max_length=8000)
    victim_civil_claim: bool = False


class VolunteerIn(Context):
    user_id: str = Field(min_length=1, max_length=200)
    admission_no: str = Field(default='', max_length=200)
    remove: bool = False


class ReleaseIn(Context):
    confirmed: bool = False


class AssignmentIn(Context):
    user_id: str = Field(min_length=1, max_length=200)


def identity(request: Request, tenant_id: UUID, home_region: str, box: str) -> Actor:
    expected = os.environ.get('TANK_KEY', '')
    supplied = request.headers.get('x-tank-key', '')
    actor = request.headers.get('x-street-law-actor', '')
    if (not expected or not supplied or not hmac.compare_digest(expected.encode(), supplied.encode())
            or not actor or len(actor) > 200
            or request.headers.get('x-street-law-tenant') != str(tenant_id)):
        raise HTTPException(404, 'not_found')
    if box != 'za' or home_region != 'za':
        raise HTTPException(403, 'needs the South Africa region')
    return Actor(str(tenant_id), actor, request.headers.get('x-street-law-founder') == '1')


def register_routes(app, *, matters_factory, box_region):
    async def call(request, tenant_id, home_region, operation, *args, **kwargs):
        actor = identity(request, tenant_id, home_region, box_region())
        matters = matters_factory()
        await matters.apply_schema()
        service = StreetLaw(PgSessions(app.state.pool), matters,
                            getattr(app.state, 'law_pool', None), box_region=box_region())
        try:
            return await getattr(service, operation)(actor, home_region, *args, **kwargs)
        except Refused as exc:
            raise HTTPException(403, str(exc)) from None

    @app.post('/street-law/sessions', status_code=201)
    async def create(request: Request, body: SessionIn):
        return await call(request, body.tenant_id, body.home_region, 'create', body.name)

    @app.post('/street-law/sessions/{session_id}/questions', status_code=201)
    async def submit(request: Request, session_id: UUID, body: QuestionIn):
        return await call(request, body.tenant_id, body.home_region, 'submit', str(session_id),
                          body.question, victim_civil_claim=body.victim_civil_claim)

    @app.post('/street-law/sessions/{session_id}/volunteers')
    async def volunteers(request: Request, session_id: UUID, body: VolunteerIn):
        return await call(request, body.tenant_id, body.home_region, 'volunteers', str(session_id),
                          body.user_id, body.admission_no, remove=body.remove)

    @app.get('/street-law/sessions/{session_id}')
    async def queue(request: Request, session_id: UUID, tenant_id: UUID, home_region: str):
        return await call(request, tenant_id, home_region, 'queue', str(session_id))

    @app.get('/street-law/sessions/{session_id}/questions/{case_id}')
    async def read(request: Request, session_id: UUID, case_id: UUID, tenant_id: UUID, home_region: str):
        return await call(request, tenant_id, home_region, 'read', str(session_id), str(case_id))

    @app.post('/street-law/sessions/{session_id}/questions/{case_id}/answer')
    async def answer(request: Request, session_id: UUID, case_id: UUID, body: Context):
        return await call(request, body.tenant_id, body.home_region, 'answer', str(session_id), str(case_id))

    @app.post('/street-law/sessions/{session_id}/questions/{case_id}/assign')
    async def assign(request: Request, session_id: UUID, case_id: UUID, body: AssignmentIn):
        return await call(request, body.tenant_id, body.home_region, 'assign', str(session_id), str(case_id), body.user_id)

    @app.post('/street-law/sessions/{session_id}/questions/{case_id}/handover')
    async def handover(request: Request, session_id: UUID, case_id: UUID, body: Context):
        return await call(request, body.tenant_id, body.home_region, 'handover', str(session_id), str(case_id))

    @app.post('/street-law/sessions/{session_id}/questions/{case_id}/release')
    async def release(request: Request, session_id: UUID, case_id: UUID, body: ReleaseIn):
        return await call(request, body.tenant_id, body.home_region, 'release', str(session_id), str(case_id),
                          confirmed=body.confirmed)
