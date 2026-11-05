"""Command-only streaming via the standard SDK, never a direct provider call."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jiter
from openai import APIError, AsyncOpenAI

TokenSink = Callable[[str], Awaitable[None]]


class StreamFailure(Exception):
    """Failed/incomplete provider stream; no raw upstream detail or invented cost."""

    def __init__(self, model: str, usage: dict):
        super().__init__("incomplete stream")
        self.model = model
        self.tokens_in = usage.get("prompt_tokens", 0)
        self.tokens_out = usage.get("completion_tokens", 0)
        self.cost_usd = usage.get("cost")


def measured_usage(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Missing final usage")
    for key in ("prompt_tokens", "completion_tokens"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError("Missing final usage")
    cost = value.get("cost")
    if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
        raise ValueError("Missing final cost")
    return {**value, "cost": float(cost)}


async def completion(base_url: str, api_key: str, body: dict, on_token: TokenSink) -> httpx.Response:
    """Normalize completed SDK output for run_route's existing accounting path.

    LiteLLM 1.106.4's include_cost_in_streaming_usage adds usage.cost using its
    configured prices. Initial streaming headers cannot know the final cost.
    A finish reason AND measured final usage are required before success.
    """
    content: list[str] = []
    finish_reason = None
    usage: dict = {}
    answered = body["model"]
    try:
        async with AsyncOpenAI(base_url=base_url.rstrip("/") + "/v1", api_key=api_key,
                               timeout=120, max_retries=0,
                               http_client=httpx.AsyncClient(timeout=120, follow_redirects=False)) as client:
            async with client.chat.completions.with_streaming_response.create(
                model=body["model"], messages=body["messages"], max_tokens=body["max_tokens"],
                extra_body={k: v for k, v in body.items() if k not in {"model", "messages", "max_tokens"}},
                stream=True, stream_options={"include_usage": True},
            ) as response:
                answered = response.headers.get("x-litellm-model-group") or answered
                stream = await response.parse()
                async for chunk in stream:
                    for choice in chunk.choices:
                        if choice.index != 0:
                            continue
                        if choice.delta.content:
                            if finish_reason is not None:
                                raise ValueError("Content after finish")
                            content.append(choice.delta.content)
                            await on_token(choice.delta.content)
                        if choice.finish_reason is not None:
                            finish_reason = choice.finish_reason
                    if chunk.usage is not None and finish_reason is not None:
                        # Some providers also send interim usage. Only terminal
                        # usage can account for the completed answer.
                        usage = measured_usage(chunk.usage.model_dump())
                if finish_reason is None or not usage:
                    raise ValueError("Missing stream completion")
    except (APIError, httpx.HTTPError, ValueError, asyncio.CancelledError):
        raise StreamFailure(answered, usage) from None
    return httpx.Response(200, json={"choices": [{"message": {"content": "".join(content)},
                                                 "finish_reason": finish_reason}], "usage": usage},
                          headers={"x-litellm-response-cost": str(usage["cost"]),
                                   "x-litellm-model-group": answered})


class AnswerText:
    """Preview only the plan's text field; actions still require full validation.

    jiter (also used by the SDK) handles incomplete JSON and split escapes. No
    tool arguments, reasoning fields or JSON envelope enter the message bubble.
    The final parse_plan remains the authority for text and proposed cards.
    """

    def __init__(self):
        self.raw = ""
        self.shown = ""

    def feed(self, delta: str) -> str:
        self.raw += delta
        if len(self.raw.encode()) > 64_000:
            raise ValueError("Answer exceeds plan limit")
        if not self.raw.lstrip():
            return ""
        if self.raw.lstrip().startswith("{"):
            try:
                plan = jiter.from_json(self.raw.encode(), partial_mode="trailing-strings", catch_duplicate_keys=True)
            except ValueError:
                return ""
            text = plan.get("text") if isinstance(plan, dict) else None
        else:
            text = self.raw
        if not isinstance(text, str) or not text.startswith(self.shown):
            return ""
        delta = text[len(self.shown):]
        self.shown = text
        return delta
