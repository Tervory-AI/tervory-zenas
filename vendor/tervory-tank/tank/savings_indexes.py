"""Online savings lookup index, outside the multi-statement schema transaction."""

async def ensure_index(pool):
    async with pool.acquire() as conn:
        # Session lock serializes replicas without locking ledger writes.
        await conn.execute("SELECT pg_advisory_lock(242, 1)")
        try:
            await conn.execute("""CREATE INDEX CONCURRENTLY IF NOT EXISTS ledger_savings_model_references
                ON tank.ledger USING gin ((ARRAY[model,standard_model,top_model,baseline_model]))""")
            valid = await conn.fetchval("""SELECT indisvalid FROM pg_index
                WHERE indexrelid='tank.ledger_savings_model_references'::regclass""")
            if not valid:
                # An interrupted concurrent build can leave an invalid index.
                await conn.execute("REINDEX INDEX CONCURRENTLY tank.ledger_savings_model_references")
        finally:
            await conn.execute("SELECT pg_advisory_unlock(242, 1)")
