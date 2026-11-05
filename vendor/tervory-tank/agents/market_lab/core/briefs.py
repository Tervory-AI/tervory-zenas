"""Monthly saved briefs: room-owned claims, existing mail and signed Tank corridor."""
from __future__ import annotations

import inspect
from datetime import datetime

from core.evidence import gated_facts
from core.predictions import clock
from core.warning_bridge import call


async def result(value):
    return await value if inspect.isawaitable(value) else value


async def gate_reason(ledger, tenant: str, report: dict, *, now: datetime | None = None) -> str | None:
    if not report['answer'].strip():
        return 'no_cited_claims'
    citations = report['citations']
    accepted = await gated_facts(ledger, tenant, citations, now=now)
    return 'closed_or_unavailable_citation' if len(accepted) != len(citations) else None


async def save(ledger, report: dict, *, now: datetime | None = None) -> None:
    reason = await gate_reason(ledger, str(report['tenant_id']), report, now=now)
    await result(ledger.save_brief(report, 'held' if reason else 'pending', reason))


def letter(report: dict) -> dict:
    # Only the saved report and citation values, with no greeting/footer/heading.
    lines = [report['answer']]
    for citation in report['citations']:
        url = citation.get('url') or citation.get('href')
        if isinstance(url, str) and url.startswith('/market-lab/sources/'):
            url = 'https://app.tervory.com' + url
        lines.extend(str(value) for value in (
            citation.get('claim') or citation.get('text'), citation.get('date'), url) if value is not None)
    return {'subject': report['question'], 'text': '\n'.join(lines)}


class MailStore:
    def __init__(self, ledger, corridor, now=None):
        self.ledger, self.corridor, self.now = ledger, corridor, now
        self.reports = {}

    async def allowed(self, tenant, report, channel):
        reason = await gate_reason(self.ledger, tenant, report, now=self.now)
        if reason:
            await result(self.ledger.update_brief(tenant, report['id'], channel, 'held', reason, expected='pending'))
            return False
        settings = await self.corridor('alerts/settings', {'tenant_id': tenant})
        since = settings.get(channel + '_since')
        if not settings.get(channel) or not since or clock(report['made_at']) < clock(since):
            await result(self.ledger.update_brief(tenant, report['id'], channel, 'suppressed', 'alerts_off_or_report_before_opt_in', expected='pending'))
            return False
        return True

    async def pending(self, tenant, channel):
        rows = await result(self.ledger.pending_briefs(tenant, channel))
        self.reports.update({str(row['id']): row for row in rows})
        return [str(row['id']) for row in rows if await self.allowed(tenant, row, channel)]

    async def pending_mail(self, tenant, at):
        return await self.pending(tenant, 'email')

    async def claim(self, tenant, ident, channel):
        report = self.reports.get(str(ident))
        if report is None or not await self.allowed(tenant, report, channel):
            return None
        changed = await result(self.ledger.update_brief(tenant, ident, channel, 'sending', expected='pending'))
        return report if changed else None

    async def claim_mail(self, tenant, ident, at):
        report = await self.claim(tenant, ident, 'email')
        return {'allowed': True, 'letter': letter(report)} if report else None

    async def finish_mail(self, tenant, ident, status, at):
        await result(self.ledger.update_brief(tenant, ident, 'email', status))


async def deliver(ledger, tenant: str, *, corridor=None, transport=None, recipient=None, now=None) -> dict:
    from agents.tiger.alert_email import deliver_pending
    store = MailStore(ledger, corridor or call, now)
    mail = await deliver_pending(store, tenant, namespace='market_lab', transport=transport, recipient=recipient)
    sent = 0
    for ident in await store.pending(tenant, 'webhook'):
        report = await store.claim(tenant, ident, 'webhook')
        if report is None:
            continue
        try:
            response = await store.corridor('alerts/brief-webhook', {
                'tenant_id': tenant, 'report_id': str(report['id']), 'title': report['question'],
                'text': report['answer'], 'citations': report['citations']})
            state = response['status']
            if state not in {'sent', 'uncertain', 'suppressed'}:
                state = 'uncertain'
        except Exception:
            state = 'uncertain'  # Accepted-but-lost HTTP responses must never retry.
        await result(ledger.update_brief(tenant, ident, 'webhook', state))
        sent += state == 'sent'
    return {'mail': mail, 'webhooks': {'sent': sent}}
