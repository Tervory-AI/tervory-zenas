"""tier0.summarize: Summarises a piece of text in at most three sentences, keeping names and numbers."""

TEMPLATE = 'Summarise the following in at most three sentences. Keep every name and number that matters.\n\n{text}'


async def run(payload: dict, ctx) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        return {"text": "", "error": "empty input"}
    content = await ctx.model(
        [{"role": "user", "content": TEMPLATE.format(text=text)}],
        task_type="tier0.answer",
    )
    return {"text": content.strip()}
