"""reason.brief: Concise structured analysis for a business owner; leads with the conclusion."""

TEMPLATE = '{text}'


async def run(payload: dict, ctx) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        return {"text": "", "error": "empty input"}
    content = await ctx.model(
        [{"role": "user", "content": TEMPLATE.format(text=text)}],
        task_type="reason.brief",
    )
    return {"text": content.strip()}
