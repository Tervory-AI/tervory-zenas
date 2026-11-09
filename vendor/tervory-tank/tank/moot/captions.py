"""Ordered STT/TTS caption events, distinct from committed legal submissions."""
from .contracts import MediaError, SEATS


class CaptionPublisher:
    def __init__(self, journal):
        self.journal = journal

    async def emit(self, scope, request, expected, *, seat, epoch, text, final, current):
        if seat not in SEATS or type(epoch) is not int or not isinstance(text, str) or not 0 < len(text) <= 24000 or type(final) is not bool:
            raise MediaError('invalid_caption')
        if await current() != epoch:
            raise MediaError('seat_revoked')
        # No actor identity, prompt, bench notes, reveal or automatic legal turn.
        return self.journal.append(scope, request, expected,
            {'kind': 'caption', 'seat': seat, 'text': text, 'final': final})
