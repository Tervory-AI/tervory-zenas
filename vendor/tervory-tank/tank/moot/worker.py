"""Tank-owned audio participant. Explicitly unavailable without a Tank audio route.

This worker does not generate legal turns. The existing durable AI executor owns
legal dispatch, CAS, receipts, cancellation and independent-ruling validation.
Only committed, authorized transcript turns may be spoken.
"""
import asyncio
from livekit import rtc
from .contracts import MediaError


class TankAudioAdapter:
    def __init__(self, tank):
        self.tank = tank

    async def route(self, tenant, operation, payload):
        route = getattr(self.tank, 'audio_route', None)
        if not callable(route):
            raise MediaError('audio_unavailable')
        # Tank must apply its policy, tenant ledger, limits and provider choice.
        # Never bypass it with a provider SDK or a chat endpoint pretending audio.
        return await route(tenant_id=tenant, task_type=operation, payload=payload)


class AudioParticipant:
    def __init__(self, speech, *, sample_rate=24000):
        self.speech = speech
        self.sample_rate = sample_rate
        self.room = None
        self.source = None

    async def connect(self, url, token):
        if self.speech.adapter is None:
            raise MediaError('audio_unavailable')
        room = rtc.Room()
        try:
            await room.connect(url, token)
            source = rtc.AudioSource(self.sample_rate, 1)
            track = rtc.LocalAudioTrack.create_audio_track('moot-voice', source)
            await room.local_participant.publish_track(track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            self.room, self.source = room, source
        except Exception:
            await room.disconnect()
            raise MediaError('audio_unavailable') from None

    async def speak(self, scope, turn, *, current, epoch):
        if self.source is None:
            raise MediaError('audio_unavailable')
        if turn.get('controller') != 'ai' or not isinstance(turn.get('number'), int):
            raise MediaError('committed_ai_turn_required')
        request = f"tts:{turn['seat']}:{epoch}:{turn['number']}"
        result = await self.speech.dispatch(scope, request, 'moot.tts',
            {'text': turn['text'], 'sample_rate': self.sample_rate, 'channels': 1},
            current=current, epoch=epoch)
        # The audio route returns actual PCM and its receipt. A retry whose
        # dispatch is uncertain must never invent/replay speech.
        if result.get('status') == 'started':
            raise MediaError('speech_pending_resolution')
        import base64
        try:
            pcm = base64.b64decode(result['pcm_s16le'], validate=True)
            if not pcm or len(pcm) % 2 or len(pcm) > self.sample_rate * 2 * 120:
                raise ValueError()
        except (KeyError, ValueError):
            raise MediaError('invalid_audio_response') from None
        step = self.sample_rate // 50 * 2
        for offset in range(0, len(pcm), step):
            if await current() != epoch:
                self.source.clear_queue()
                raise MediaError('seat_revoked')
            chunk = pcm[offset:offset+step]
            await self.source.capture_frame(rtc.AudioFrame(chunk, self.sample_rate, 1, len(chunk)//2))
        await self.source.wait_for_playout()

    async def close(self):
        if self.source is not None:
            self.source.clear_queue()
            await self.source.aclose()
        if self.room is not None:
            await self.room.disconnect()
        self.room = self.source = None

    async def listen(self, scope, track, *, seat, epoch, current, vad, detector, on_caption):
        if self.speech.adapter is None or vad is None or detector is None:
            raise MediaError('audio_unavailable')
        from livekit.agents import vad as vad_types, llm, LanguageCode
        import base64
        import hashlib
        stream = vad.stream()
        audio = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        context = llm.ChatContext()
        threshold = await detector.unlikely_threshold(LanguageCode("en"))
        if threshold is None:
            raise MediaError("audio_detection_unavailable")
        async def feed():
            try:
                async for event in audio:
                    if await current() != epoch:
                        raise MediaError('seat_revoked')
                    stream.push_frame(event.frame)
            finally:
                stream.end_input()
        feeder = asyncio.create_task(feed())
        try:
            async for event in stream:
                if event.type != vad_types.VADEventType.END_OF_SPEECH:
                    continue
                if await current() != epoch:
                    raise MediaError('seat_revoked')
                pcm = b''.join(bytes(frame.data) for frame in event.frames)
                if not pcm or len(pcm) > 16000 * 2 * 30:
                    raise MediaError('audio_segment_limit')
                digest = hashlib.sha256(pcm).hexdigest()
                # Request identity is stable across retry/reconnect of the same
                # captured segment and epoch. PCM stays inside the Tank adapter.
                result = await self.speech.dispatch(scope, f'stt:{seat}:{epoch}:{digest}', 'moot.stt',
                    {'pcm_s16le': base64.b64encode(pcm).decode(), 'sample_rate': 16000, 'channels': 1},
                    current=current, epoch=epoch)
                if result.get('status') == 'started':
                    raise MediaError('speech_pending_resolution')
                text = result.get('text')
                if not isinstance(text, str) or not 0 < len(text) <= 24000:
                    raise MediaError('invalid_audio_response')
                context.add_message(role='user', content=text)
                probability = await detector.predict_end_of_turn(context, timeout=3)
                if await current() != epoch:
                    raise MediaError('seat_revoked')
                # Captions are actual STT output; they never commit legal turns.
                await on_caption(text, probability >= threshold)
        finally:
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):
                pass
            await stream.aclose()
            await audio.aclose()
