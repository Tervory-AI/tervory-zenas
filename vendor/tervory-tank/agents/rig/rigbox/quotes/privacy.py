"""Shopper serialization guard: pricing inputs stay on the server."""

from typing import Any

from rigbox.shopper_attributes import _PRIVATE, PRIVATE_KEY_PATTERN, private_key, shopper_attributes

__all__ = ["PRIVATE_KEY_PATTERN", "_PRIVATE", "private_key", "shopper_body"]


def shopper_body(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: shopper_body(shopper_attributes(item) if key == "attributes" and isinstance(item, dict) else item)
                for key, item in value.items() if not private_key(key)}
    if isinstance(value, list):
        return [shopper_body(item) for item in value]
    return value
