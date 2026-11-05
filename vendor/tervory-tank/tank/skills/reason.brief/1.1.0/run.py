"""reason.brief: Concise structured analysis for a business owner; leads with the conclusion."""

TEMPLATE = (
    "Write a short brief for a business owner.\n"
    "The first line must begin with \"I recommend\" and state your conclusion in one sentence.\n"
    "Then give the reasons and any numbers in a few short bullet points.\n\n"
    "{text}"
)


async def run(payload: dict, ctx) -> dict:
    text = str(payload.get("text", "")).strip()
    if not text:
        return {"text": "", "error": "empty input"}
    content = await ctx.model(
        [{"role": "user", "content": TEMPLATE.format(text=text)}],
        task_type="reason.brief",
    )
    return {"text": content.strip()}
