"""SA scope regression cases. Real terms; existing refusal text is unchanged.

Sources: Justice Department Criminal Procedure Act 51 of 1977, ss 57, 60,
105A, 276, 309; National Prosecuting Authority Act 32 of 1998, s 20.
Official PDF URLs and checked extracts are listed in PR.md.
"""
import json

import pytest

from agents.zenas.core.scope_rules import REFUSED, classify, classify_with_hint

CRIMINAL = [
    "How does the Criminal Procedure Act 51 of 1977 apply in South Africa?",
    "Can you prepare a bail application in Johannesburg?",
    "How can I challenge NPA prosecutions in South Africa?",
    "Can you defend criminal charges brought against me in Cape Town?",
    "Can you negotiate a plea bargain in a South African court?",
    "Can you advise me on sentencing in Durban?",
    "Should I pay an admission of guilt fine in South Africa?",
    "What powers arise under the National Prosecuting Authority Act 32 of 1998?",
    "Can I enter a plea and sentence agreement in South Africa?",
    "Can you draft an appeal against sentence for a South African court?",
]
CIVIL = [
    "Can a landlord retain the deposit under a commercial lease in Cape Town?",
    "What notice must a tenant give to end a lease of premises in Johannesburg?",
    "Can a landlord increase rent under a commercial lease in Durban?",
    "What damages arise from a breach of a supply agreement in South Africa?",
    "Is a mutual NDA enforceable between two South African companies?",
    "May I cancel a supply agreement after late delivery in Cape Town?",
    "Does my employment contract comply with the BCEA overtime provisions?",
    "What annual leave does the BCEA give an employee in South Africa?",
    "Can a consumer return defective goods under a sale of goods contract in South Africa?",
    "Is a delivery charge permitted by a consumer sale of goods contract in Johannesburg?",
]


@pytest.mark.parametrize("question", CRIMINAL)
def test_ten_sa_criminal_questions_are_refused_without_a_model(question):
    result = classify(question, jurisdiction="za")
    assert result["criminal"] is True
    assert result["in_scope"] is False
    assert result["reason"] == REFUSED
    # A model suggestion cannot override a rule, including a civil area hint.
    hinted = classify_with_hint(question, json.dumps({"area": "contracts"}), jurisdiction="za")
    assert hinted == result


@pytest.mark.parametrize("question", CIVIL)
def test_ten_sa_civil_questions_remain_in_scope(question):
    result = classify(question, jurisdiction="za")
    assert result["criminal"] is False
    assert result["in_scope"] is True
    assert result["reason"] != REFUSED


@pytest.mark.asyncio
@pytest.mark.parametrize("question", CRIMINAL)
async def test_criminal_questions_stop_before_tank_or_any_followup_skill(question):
    from agents.zenas.core.matters import consider
    class NoCalls:
        async def run_skill(self, *args, **kwargs):
            raise AssertionError("A criminal rule must not call a skill or model")
    result = await consider(NoCalls(), tenant_id="tenant", text=question, jurisdiction="za",
                            follow=[{"skill": "zenas.memo", "input": {}}])
    assert result["stopped"] and result["scope"]["reason"] == REFUSED and result["outputs"] == []


@pytest.mark.asyncio
async def test_new_skill_runs_all_twenty_cases_offline_and_preserves_approved_versions():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    package = root / "tank/skills/zenas.scope/1.2.0"
    spec = importlib.util.spec_from_file_location("sa_scope_v12", package / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class NoCalls:
        async def model(self, *args, **kwargs):
            raise AssertionError("No model calls for these rules")
    for question in CRIMINAL + CIVIL:
        assert await module.run({"text": question, "jurisdiction": "za"}, NoCalls()) == classify(question, "za")
    assert (package / "areas.json").read_bytes() == (root / "agents/zenas/core/areas.json").read_bytes()
    previous = json.loads((package.parent / "1.1.0/areas.json").read_text())
    assert "criminal procedure act" not in previous["criminal_phrases"]
