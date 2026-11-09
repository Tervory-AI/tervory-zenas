"""Approved CPU VAD/end-of-turn plugins. Models must already exist locally.

Called within a LiveKit Agents job context; the turn detector uses that context's
inference executor. It reads a pinned plugin's local cache and never downloads a
model from this factory. Missing packages/job context/cache are unavailable.
"""
from .contracts import MediaError


def load_detection(*, silence_seconds=1.5):
    if not 0.3 <= silence_seconds <= 5:
        raise MediaError('invalid_silence')
    try:
        from livekit.plugins import silero
        from livekit.plugins.turn_detector.english import EnglishModel
        # Construct the detector first so missing cached weights fail before any
        # audio subscription or STT/TTS dispatch begins.
        detector = EnglishModel()
        vad = silero.VAD.load(min_silence_duration=silence_seconds,
            max_buffered_speech=30.0, force_cpu=True, sample_rate=16000)
        return vad, detector
    except Exception:
        raise MediaError('audio_detection_unavailable') from None
