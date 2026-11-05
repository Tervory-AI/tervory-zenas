"""Provider prompt-cache accounting for rung-1 model calls.

Breakpoints are placed by LiteLLM, per deployment (litellm/config.yaml
``cache_control_injection_points`` on the OpenAI GPT-6 and Anthropic models).
Tank never adds a cache marker to a request itself: the gateway's fallback
chains cross providers, and a marker on a Gemini fallback would open a billed
context cache. Tank's job is to keep the fixed text first (policy system
prompt, then fixed instructions, then the caller's messages) and to book what
the provider reports.

Usage shapes this reads (LiteLLM 1.106.4 normalises most of them):
  OpenAI      prompt_tokens_details.cached_tokens, prompt_tokens_details.cache_write_tokens
  Anthropic   cache_read_input_tokens, cache_creation_input_tokens (prompt_tokens includes both
              after LiteLLM's translation; the raw Messages API input_tokens does not)
  DeepSeek    prompt_cache_hit_tokens
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def insert_fixed(messages: list[dict[str, Any]], has_policy_prompt: bool, message: dict[str, Any]) -> None:
    """Add a fixed instruction straight after the policy system prompt.

    Never ahead of it: the policy prompt then starts every call of the task type,
    with or without the instruction, so the cached prefix is shared by both.
    """
    messages.insert(1 if has_policy_prompt else 0, message)


def _count(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _first(*values: Any) -> int:
    for value in values:
        number = _count(value)
        if number is not None:
            return number
    return 0


def cache_tokens(usage: Any) -> tuple[int, int]:
    """(cache read tokens, cache write tokens) from a chat-completions usage object.

    Missing or malformed fields count as zero: a receipt never invents a cache hit.
    """
    if not isinstance(usage, dict):
        return 0, 0
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    read = _first(details.get("cached_tokens"), usage.get("cache_read_input_tokens"),
                  usage.get("prompt_cache_hit_tokens"))
    write = _first(usage.get("cache_creation_input_tokens"), details.get("cache_write_tokens"),
                   details.get("cache_creation_tokens"))
    prompt = _count(usage.get("prompt_tokens"))
    if prompt is not None and read + write > prompt:
        # A provider total that cannot hold its own parts is not evidence of a hit.
        return 0, 0
    return read, write


def anthropic_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """Native Anthropic Messages usage in chat-completions shape.

    Anthropic's input_tokens excludes cached and newly cached tokens; the
    prompt total Tank books must include them, exactly as LiteLLM reports it.
    """
    read = _count(usage.get("cache_read_input_tokens")) or 0
    write = _count(usage.get("cache_creation_input_tokens")) or 0
    out: dict[str, Any] = {"prompt_tokens": int(usage["input_tokens"]) + read + write,
                           "completion_tokens": int(usage["output_tokens"])}
    if read or write:
        out.update(cache_read_input_tokens=read, cache_creation_input_tokens=write,
                   prompt_tokens_details={"cached_tokens": read})
    return out


def token_cost(prices: dict[str, Any], tokens_in: int, tokens_out: int, read: int, write: int) -> Decimal:
    """Undiscounted cost of one call. Cached tokens use the roster cache rates when
    the roster has them, otherwise the full input rate (never cheaper than proven)."""
    rate_in = Decimal(str(prices["input"]))
    rate_read = Decimal(str(prices["cache_read"])) if prices.get("cache_read") is not None else rate_in
    rate_write = Decimal(str(prices["cache_write"])) if prices.get("cache_write") is not None else rate_in
    if read + write > tokens_in:
        read = write = 0
    plain = tokens_in - read - write
    return (Decimal(plain) * rate_in + Decimal(read) * rate_read + Decimal(write) * rate_write
            + Decimal(tokens_out) * Decimal(str(prices["output"])))
