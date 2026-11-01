"""Decode asyncpg JSON columns without accepting a wrong top-level shape."""
import json
from typing import Any

from .hearing import HearingError


def decoded(value: Any, kind: type = dict):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise HearingError("source_incomplete") from None
    if not isinstance(value, kind):
        raise HearingError("source_incomplete")
    return value
