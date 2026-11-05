"""Autumbra's adapter to the unchanged Rig/Tiger SMTP sender; no email copy."""
from __future__ import annotations

import asyncio
import hashlib
import os
import math
import time
from email.headerregistry import Address
from pathlib import Path

from agents.rig.rigbox.reserve import SmtpMailer
from .durable_state import locked_state


def settings():
    names = ('HOST', 'PORT', 'FROM', 'USER', 'PASSWORD')
    values = {n: os.environ.get('AUTUMBRA_SMTP_' + n, '') for n in names}
    if not any(values.values()):
        return None
    host, sender = values['HOST'].strip(), values['FROM'].strip()
    try:
        address = Address(addr_spec=sender)
        port = int(values['PORT'] or '587')
    except Exception:
        # Address can raise HeaderParseError, IndexError or other parser errors.
        raise ValueError('mail_not_configured') from None
    if (not host or not address.username or not address.domain or not 0 < port < 65536
            or any(c in host + sender for c in '\r\n')):
        raise ValueError('mail_not_configured')
    smtp_available_since()
    return SmtpMailer(host, port, values['USER'].strip(), values['PASSWORD'], address.addr_spec)


def smtp_available_since() -> float:
    """One durable activation boundary for this Watch volume, across restarts."""
    path = Path(os.environ.get('DATA_DIR', '/data')) / 'owner-email-availability.json'
    with locked_state(path) as state:
        since = state.setdefault('available_since', time.time())
        if isinstance(since, bool) or not isinstance(since, (int, float)) or not math.isfinite(since) or since <= 0:
            raise ValueError('mail_not_configured')
        return since


def transport_status() -> str:
    try:
        return 'smtp' if settings() is not None else 'fixture'
    except (ValueError, TypeError, OSError):
        return 'unavailable'


class SmtpEmailTransport:
    def __init__(self, mailer, *, tenant_id: str, message_key: str, data_dir=None):
        if not tenant_id or not message_key:
            raise ValueError('mail_not_configured')
        self.mailer = mailer
        self.ident = hashlib.sha256((tenant_id + ':' + message_key).encode()).hexdigest()
        self.path = Path(data_dir or os.environ.get('DATA_DIR', '/data')) / 'owner-email-attempts.json'

    def send(self, *, to: str, subject: str, body: str) -> dict:
        recipient = Address(addr_spec=to)
        if not recipient.username or not recipient.domain or any(c in to + subject for c in '\r\n'):
            raise ValueError('mail_not_configured')
        with locked_state(self.path) as state:
            if self.ident in state:
                return {'sent': False, 'transport': 'smtp', 'state': state[self.ident]}
            state[self.ident] = 'sending'
        try:
            # Watch calls this synchronous transport from a worker thread.
            # Reuse Rig's complete TLS/message sending implementation unchanged.
            asyncio.run(self.mailer.send({'to': recipient.addr_spec, 'subject': subject, 'text': body,
                                         'message_id': '<autumbra.' + self.ident + '@tervory.com>'}))
        except Exception:
            outcome = 'uncertain'
        else:
            outcome = 'sent'
        with locked_state(self.path) as state:
            state[self.ident] = outcome
        return {'sent': outcome == 'sent', 'transport': 'smtp', 'state': outcome}


def configured_transport(tenant_id: str, message_key: str):
    mailer = settings()
    if mailer is None:
        from .owner_email import DEFAULT_FIXTURE_TRANSPORT
        return DEFAULT_FIXTURE_TRANSPORT
    return SmtpEmailTransport(mailer, tenant_id=tenant_id, message_key=message_key)
