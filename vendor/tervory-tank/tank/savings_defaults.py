"""Reporting standards only. These never write serving policies or select routes."""
STANDARDS = {"openai": "sol", "anthropic": "sonnet", "gemini": "argon",
             "xai": "grok-top", "moonshot": "kimi", "deepseek": "deepseek-pro"}


async def initialize(conn):
    async with conn.transaction():
        await conn.executemany("""INSERT INTO tank.provider_standards(provider,model) VALUES($1,$2)
            ON CONFLICT(provider) DO UPDATE SET model=excluded.model
            WHERE tank.provider_standards.model IS DISTINCT FROM excluded.model""", list(STANDARDS.items()))
