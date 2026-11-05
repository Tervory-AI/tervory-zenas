"""Solved incidents are candidates, never self-approved training examples."""
from .models import PLATFORM_TENANT
from .store import Store


def candidates(store: Store, region: str, limit: int = 200) -> list[dict]:
    if region != store.region or region not in {"us-east", "za"} or not 1 <= limit <= 200:
        raise ValueError("Invalid gold request.")
    return [{"skill_id": "ghost.incident", "input": {"target": row["target"], "signal": row["signal"]},
             "expected": {"cause": row["cause"], "fix": row["fix"]},
             "label_source": f"ghost:incident:{row['id']}",
             "label_family": f"ghost:incident:{row['solved_at'][:7]}",
             "region": region, "public": False}
            for row in store.gold(PLATFORM_TENANT, limit)]
