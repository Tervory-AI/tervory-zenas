from agents.zenas.core.moot.publication import public_rows
from .test_hearing import HUMANS, started


def test_unpaired_ai_counsel_never_claim_fairness_or_claim_to_be_a_human_match():
    s = started({seat: "ai" for seat in HUMANS})
    s["phase"] = "closed"; s["published"] = True
    s["ruling"] = {"order": "Offline independent order", "reasons": ["Offline evidence"], "authority_ids": []}
    _, match = public_rows(s)
    assert match["mode"] == "ai_ai"
    assert match["fairness"]["reading"] == "Fairness check not measured."


def test_offline_marker_still_prevents_publication():
    import pytest
    from agents.zenas.core.moot.hearing import HearingError
    s = started({seat: "ai" for seat in HUMANS})
    s["phase"] = "closed"; s["published"] = True
    s["ruling"] = {"order": "Order", "reasons": ["Fixture result [TEST]"], "authority_ids": []}
    with pytest.raises(HearingError, match="offline_content_not_publishable"):
        public_rows(s)
