"""tier0.answer: Plain, brief answer to a business owner's question. Says which agent a bigger job belongs to rather than attempting it."""

TEMPLATE = '{text}'


async def run(payload: dict, ctx) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        return {"text": "", "error": "empty input"}
    content = await ctx.model(
        [{"role": "user", "content": TEMPLATE.format(text=text)}],
        task_type="tier0.answer",
    )
    return {"text": content.strip()}
