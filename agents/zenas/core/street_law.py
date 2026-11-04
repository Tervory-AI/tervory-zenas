"""ZA Street Law intake. Library extracts only; client release always needs a human.

Client questions stay on this box. Only fixed legal-topic queries reach the local
law pool; no model, Knowledge Base HTTP client, or training writer is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import uuid4

from agents.zenas.core.citation_sources import public_source_url
from agents.zenas.core.matters import CURRENT_WORDING_VERSION, _redact, _use_home
from agents.zenas.core.scope_rules import REFUSED, classify
from agents.zenas.core.service import _with_legal_notice


@dataclass(frozen=True)
class Actor:
    tenant_id: str
    user_id: str
    founder: bool = False


class Refused(ValueError):
    pass


_CRIMINAL = re.compile(r'\b(criminal|bail|arrest|conviction|sentencing|prosecut\w*|murder|rape|robbery|assault\w*|theft)\b', re.I)
_CRIMINAL_HELP = re.compile(r'\b(bail|defen[cs]e|defend|prosecut\w*|sentence|sentencing|plead|conviction|charges?|arrest)\b', re.I)
_VICTIM = re.compile(r'\b(i was (?:assaulted|robbed|injured|attacked|a victim|the victim)|i am the victim|my (?:stolen|damaged)|stolen from me|hurt me)\b', re.I)
_REMEDY = re.compile(r'\b(civil claim|compensation|damages|medical bills|medical expenses|return my property)\b', re.I)
_TOPICS = (
    ('deposit', re.compile(r'\b(deposit|landlord|rent|rental)\b', re.I), 'rental deposit refund'),
    ('employment', re.compile(r'\b(wages|working hours|overtime|employer|salary)\b', re.I), 'employment working hours wages'),
    ('goods', re.compile(r'\b(refund|faulty|broken goods|bought|shop)\b', re.I), 'consumer goods refund'),
)
_COMPLEX = re.compile(r'\b(evict\w*|summons|court|deadline|appeal|urgent|divorce|custody|maintenance|injur\w*)\b', re.I)


def triage(question: str, victim_civil_claim: bool = False) -> tuple[str, str]:
    """A victim exception admits only a civil handover, never criminal advice."""
    criminal = classify(question, 'za')['criminal'] or bool(_CRIMINAL.search(question))
    if criminal:
        if (victim_civil_claim and _VICTIM.search(question) and _REMEDY.search(question)
                and not _CRIMINAL_HELP.search(question)):
            return 'handed_over', ''
        raise Refused(REFUSED)
    if victim_civil_claim or len(question) > 500 or question.count('?') > 1 or _COMPLEX.search(question):
        return 'handed_over', ''
    topics = [(name, query) for name, pattern, query in _TOPICS if pattern.search(question)]
    return ('queued', topics[0][1]) if len(topics) == 1 else ('handed_over', '')


class StreetLaw:
    def __init__(self, sessions, matters, law_pool, *, box_region: str):
        self.sessions, self.matters, self.law_pool = sessions, matters, law_pool
        self.box_region = box_region

    async def guard(self, actor: Actor, home_region: str):
        # Refuse foreign boxes before the first database or library read.
        if self.box_region != 'za' or home_region != 'za':
            raise Refused('needs the South Africa region')
        _, refused = await _use_home(self.matters, tenant_id=actor.tenant_id,
                                    home_region=home_region, box_region=self.box_region)
        if refused:
            raise Refused(refused['reason'])

    async def create(self, actor: Actor, home_region: str, name: str):
        await self.guard(actor, home_region)
        if not actor.founder:
            raise Refused('founder required')
        name, _ = _redact(name.strip())
        if not name or len(name) > 200:
            raise Refused('invalid session')
        row = {'id': str(uuid4()), 'name': name, 'creator': actor.user_id,
               'members': {actor.user_id: {'kind': 'founder', 'admission_no': ''}}, 'cases': []}
        await self.sessions.create(actor.tenant_id, row)
        return {'id': row['id'], 'name': row['name']}

    async def submit(self, actor: Actor, home_region: str, session_id: str,
                     question: str, *, victim_civil_claim: bool = False):
        # Criminal refusal does not inspect the database or contact a provider.
        status, query = triage(question, victim_civil_claim)
        await self.guard(actor, home_region)
        if not question.strip() or len(question) > 8000:
            raise Refused('invalid question')
        if not await self.matters.has_current_consent(actor.tenant_id, actor.user_id, CURRENT_WORDING_VERSION):
            raise Refused('A current consent is required before a matter can be opened.')
        text, _ = _redact(question)
        async with self.sessions.edit(actor.tenant_id, session_id) as (session, matters):
            if len(session['cases']) >= 1000:
                raise Refused('session full')
            matter = await matters.add_matter({'tenant_id': actor.tenant_id, 'jurisdiction': 'za',
                'area': 'street_law', 'title': text, 'parties': [], 'status': 'open', 'created_by': actor.user_id})
            row = {'id': str(uuid4()), 'client': actor.user_id, 'question': text, 'status': status,
                   'query': query, 'matter_id': matter['id'], 'document_id': None,
                   'assigned_to': None, 'answer': None, 'review_id': None}
            session['cases'].append(row)
            return {'id': row['id'], 'status': status}

    @staticmethod
    def member(session, actor):
        member = session['members'].get(actor.user_id)
        if member is None:
            raise Refused('volunteer required')
        return member

    @staticmethod
    def case(session, case_id):
        row = next((row for row in session['cases'] if row['id'] == case_id), None)
        if row is None:
            raise Refused('not found')
        return row

    async def answer(self, actor: Actor, home_region: str, session_id: str, case_id: str):
        await self.guard(actor, home_region)
        async with self.sessions.edit(actor.tenant_id, session_id) as (session, matters):
            self.member(session, actor)
            row = self.case(session, case_id)
            if row['status'] != 'queued':
                raise Refused('question is not queued')
            try:
                chunks = await self.law_pool.search_text(row['query'], jurisdiction='za', limit=3)
                metadata = await self.law_pool.source_metadata(chunks, jurisdiction='za')
            except Exception:
                # Never retain exception bodies, client text or headers in diagnostics.
                chunks, metadata = [], {}
            sources = []
            for chunk in chunks:
                meta = metadata.get((chunk.document_id, chunk.version_id), {})
                url = public_source_url(meta.get('url'))
                if (chunk.jurisdiction != 'za' or not chunk.citation or not chunk.text.strip()
                        or len(chunk.text) > 2000 or not url or not meta.get('version_date')):
                    continue
                sources.append({'citation': chunk.citation, 'quote': chunk.text, 'url': url,
                    'doc_id': chunk.document_id, 'version_id': chunk.version_id,
                    **{key: str(meta[key]) for key in ('version_date', 'attribution', 'licence') if meta.get(key)}})
            if not sources:
                row['status'] = 'handed_over'
                return {'id': row['id'], 'status': row['status']}
            body = _with_legal_notice('\n\n'.join(f'“{s["quote"]}” ({s["citation"]})' for s in sources))
            document = await matters.add_document({'tenant_id': actor.tenant_id, 'matter_id': row['matter_id'],
                'kind': 'street_law', 'version': 1, 'body': body, 'sources': sources,
                'state': 'in_review', 'reviewed': False})
            row.update(status='answered', document_id=document['id'],
                       answer={'text': body, 'sources': sources}, answered_by=actor.user_id)
            # Only volunteers receive drafts. The client read path withholds them.
            return {'id': row['id'], 'status': row['status'], 'answer': row['answer'], 'released': False}

    @staticmethod
    def view(row, *, volunteer=False):
        return {key: row[key] for key in ('id', 'question', 'status', 'assigned_to')} | {
            'released': row['review_id'] is not None,
            'answer': row['answer'] if volunteer or row['review_id'] is not None else None}

    async def volunteers(self, actor: Actor, home_region: str, session_id: str,
                         user_id: str, admission_no: str = ''):
        await self.guard(actor, home_region)
        async with self.sessions.edit(actor.tenant_id, session_id) as (session, _):
            if not actor.founder or session['creator'] != actor.user_id:
                raise Refused('session organizer required')
            if not user_id.strip() or len(user_id) > 200 or len(admission_no) > 200:
                raise Refused('invalid volunteer')
            if user_id == session['creator']:
                raise Refused('organizer membership is fixed')
            session['members'][user_id] = {'kind': 'reviewer', 'admission_no': admission_no.strip()}
            return {'user_id': user_id, 'can_review': bool(admission_no.strip())}

    async def queue(self, actor: Actor, home_region: str, session_id: str):
        await self.guard(actor, home_region)
        async with self.sessions.edit(actor.tenant_id, session_id) as (session, _):
            self.member(session, actor)
            return {'id': session['id'], 'name': session['name'],
                    'volunteers': [{'user_id': user, 'can_review': bool(member['admission_no']) or member['kind'] == 'founder'}
                                   for user, member in session['members'].items()],
                    **{state: [self.view(row, volunteer=True) for row in session['cases'] if row['status'] == state]
                       for state in ('queued', 'answered', 'handed_over')}}

    async def read(self, actor: Actor, home_region: str, session_id: str, case_id: str):
        await self.guard(actor, home_region)
        async with self.sessions.edit(actor.tenant_id, session_id) as (session, _):
            row = self.case(session, case_id)
            volunteer = actor.user_id in session['members']
            if not volunteer and row['client'] != actor.user_id:
                raise Refused('not found')
            return self.view(row, volunteer=volunteer)

    async def assign(self, actor: Actor, home_region: str, session_id: str, case_id: str, user_id: str):
        await self.guard(actor, home_region)
        async with self.sessions.edit(actor.tenant_id, session_id) as (session, _):
            self.member(session, actor)
            row = self.case(session, case_id)
            if (user_id not in session['members'] or row['review_id'] is not None
                    or (not actor.founder and (user_id != actor.user_id or row['assigned_to'] not in (None, actor.user_id)))):
                raise Refused('assignment refused')
            row['assigned_to'] = user_id
            return self.view(row, volunteer=True)

    async def handover(self, actor: Actor, home_region: str, session_id: str, case_id: str):
        await self.guard(actor, home_region)
        async with self.sessions.edit(actor.tenant_id, session_id) as (session, matters):
            self.member(session, actor)
            row = self.case(session, case_id)
            if row['review_id'] is not None:
                raise Refused('answer already released')
            if row['document_id']:
                await matters.set_document_state(actor.tenant_id, row['document_id'], 'rejected')
            row.update(status='handed_over', answer=None)
            return self.view(row, volunteer=True)
