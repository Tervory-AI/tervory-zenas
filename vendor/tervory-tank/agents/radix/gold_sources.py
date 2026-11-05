"""Fixed, offline fixture answers for review; never live/client training data."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query

from verify.routes import require_tank

FIXTURES = Path(__file__).resolve().parent / "evals" / "gold"
SKILLS = (
    "scan.secrets", "scan.dependencies", "scan.static",
    "radix.verify_business", "radix.entity_links", "radix.account_links",
    "radix.site_risk", "radix.business_risk",
)


def fixture_family(skill: str, case: dict) -> str:
    """Fixtures are one family per skill. A planted wrong answer is its own family."""
    family = f"radix:fixture:v1:{skill}"
    if case.get("synthetic") is True:
        return family.replace(":", "-synthetic:", 1)
    return family


def candidates(limit: int = 200) -> list[dict]:
    """Read frozen labels, not predictions from the implementation under test."""
    region = os.environ.get("TANK_REGION", "us-east")
    if region not in {"us-east", "za"}:
        raise ValueError("Unsupported room region.")
    if not 1 <= limit <= 200:
        raise ValueError("Candidate limit must be between 1 and 200.")
    rows: list[dict] = []
    for skill in SKILLS:
        for case in json.loads((FIXTURES / f"{skill}.json").read_text()):
            payload = case["input"]
            if skill.startswith("radix."):
                business = json.loads((FIXTURES / "business.json").read_text())
                # Never relabel US register evidence as South African material.
                if business["region"] != region:
                    continue
                business["lookup"]["value"] = payload["lookup_id"]
                payload = business
            elif skill == "scan.secrets":
                for file in payload["files"]:
                    # Synthetic scanner marker only; no credential is stored.
                    file["text"] = file["text"].replace(
                        "{{fixture_key}}", "-".join(("tvfy", "example", "fixture", "key", "0001"))
                    )
            rows.append({
                "skill_id": skill,
                "input": payload,
                "expected": case["expected"],
                "label_source": f"radix:fixture:v1:{skill}:{case['name']}",
                "label_family": fixture_family(skill, case),
                "region": region,
                # These are repository-authored synthetic examples, including
                # the business records whose client:// URIs are fixture IDs.
                "public": True,
            })
            if len(rows) == limit:
                return rows
    return rows


def attach(app: FastAPI) -> None:
    router = APIRouter(prefix="/gold", dependencies=[Depends(require_tank)])

    @router.get("/candidates")
    async def get_candidates(limit: int = Query(default=200, ge=1, le=200)) -> list[dict]:
        try:
            return candidates(limit)
        except ValueError:
            raise HTTPException(503, "Gold fixture configuration is unavailable.") from None

    app.include_router(router)
