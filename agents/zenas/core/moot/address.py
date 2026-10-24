"""Court forms of address and procedure labels for SA vs US text sessions."""

from __future__ import annotations

from typing import Any


def court_style(region: str) -> dict[str, Any]:
    """Return forms of address and labels for a session region (za | us-east)."""
    if region == "za":
        return {
            "region": "za",
            "judge_address": "My Lord",
            "opening": "May it please the court",
            "party_labels": {"appellant": "Appellant", "respondent": "Respondent"},
            "procedure": "SA appeal practice",
        }
    if region == "us-east":
        return {
            "region": "us-east",
            "judge_address": "Your Honor",
            "opening": "May it please the Court",
            "party_labels": {"appellant": "Appellant", "respondent": "Appellee"},
            "procedure": "US federal appellate argument",
        }
    raise ValueError("region must be za or us-east")
