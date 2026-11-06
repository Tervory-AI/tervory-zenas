"""Local dictation evidence on the existing validated hearing turn contract."""
from __future__ import annotations

import math
from typing import Any

from .hearing import HearingError


def finite(number):
    try:
        return type(number) in (int,float) and math.isfinite(number)
    except OverflowError:
        return False


def capture(value: Any, text: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {'engine', 'language', 'segments'}:
        raise HearingError('invalid_voice')
    if value['engine'] != 'web_speech_local' or value['language'] not in {'en-US', 'en-ZA', 'en-GB'}:
        raise HearingError('invalid_voice')
    segments = value['segments']
    if not isinstance(segments, list) or not 0 < len(segments) <= 500:
        raise HearingError('invalid_voice')
    result, prior = [], 0
    for row in segments:
        if not isinstance(row, dict) or set(row) != {'text','start_ms','end_ms'}:
            raise HearingError('invalid_voice')
        start, end = row['start_ms'], row['end_ms']
        if any(not finite(n) for n in (start,end)) or not 0 <= prior <= start <= end:
            raise HearingError('invalid_voice')
        if not isinstance(row['text'], str) or not row['text'].strip() or len(row['text']) > 24000:
            raise HearingError('invalid_voice')
        result.append({'text':row['text'].strip(),'start_ms':start,'end_ms':end})
        prior = end
    if ' '.join(row['text'] for row in result).strip() != text.strip():
        raise HearingError('invalid_voice')
    return {'engine':value['engine'],'language':value['language'],'segments':result}
