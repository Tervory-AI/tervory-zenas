"""Speech goes through configured Tank adapters, with durable no-replay claims.

The default is unavailable. Test adapters are supplied only by tests, never chosen
by an environment switch in the runtime. No fabricated transcripts or receipts.
"""
import asyncio
from .contracts import MediaError


class Speech:
    def __init__(self, journal, adapter=None):
        self.journal, self.adapter = journal, adapter

    async def dispatch(self, scope, request, operation, payload, *, current, epoch):
        if operation not in {'moot.stt', 'moot.tts'}:
            raise MediaError('invalid_speech_operation')
        if self.adapter is None:
            raise MediaError('audio_unavailable')
        if await current() != epoch:
            raise MediaError('seat_revoked')
        prior = self.journal.claim(scope, request, {'operation': operation, 'payload': payload, 'epoch': epoch})
        if prior:
            return prior  # completed result or uncertain started state; never replay.
        call = asyncio.create_task(self.adapter.route(scope.tenant, operation, payload))
        try:
            while not call.done():
                await asyncio.wait({call}, timeout=0.1)
                if await current() != epoch:
                    call.cancel()
                    raise MediaError('seat_revoked')
            result = await call
            if await current() != epoch:
                raise MediaError('seat_revoked')
            # An adapter must return actual Tank receipt metadata.
            if not isinstance(result, dict) or not result.get('ledger_id'):
                raise MediaError('speech_receipt_required')
            self.journal.finish(scope, request, result)
            return result
        finally:
            if not call.done():
                call.cancel()
            try:
                await call
            except (asyncio.CancelledError, Exception):
                pass


class EndOfTurn:
    """Consumes VAD probabilities supplied by the approved VAD implementation.

    Silence is necessary, never sufficient: a configured turn detector must also
    mark the utterance complete. Without either adapter this remains unavailable.
    """
    def __init__(self, silence_ms=1500):
        if not 300 <= silence_ms <= 5000:
            raise MediaError('invalid_silence')
        self.silence_ms, self.last_voice, self.last_time = silence_ms, None, -1

    def frame(self, at_ms, speech_probability, turn_complete):
        if at_ms < self.last_time or not 0 <= speech_probability <= 1:
            raise MediaError('invalid_audio_frame')
        self.last_time = at_ms
        if speech_probability >= 0.5:
            self.last_voice = at_ms
            return False
        if self.last_voice is not None and at_ms - self.last_voice >= self.silence_ms and turn_complete is True:
            self.last_voice = None
            return True
        return False
