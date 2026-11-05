"""tier0.draft: Drafts short business writing (email, message, note) in plain direct English, no preamble."""

TEMPLATE = 'Draft the following. Output only the draft.\n\n{text}'


async def run(payload: dict, ctx) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        return {"text": "", "error": "empty input"}
    content = await ctx.model(
        [{"role": "user", "content": TEMPLATE.format(text=text)}],
        task_type="tier0.draft",
    )
    return {"text": content.strip()}
