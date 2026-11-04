from tank.training_jobs.evaluate import outcome, score_rows
from pathlib import Path
import yaml


def row(label):
    return {"output": {"outcome": label, "reasoning": "court text", "order": "court order"}}


def test_outcome_scorer_requires_exact_code_label():
    result = score_rows(
        [row("granted"), row("dismissed"), row("set aside")],
        [{"outcome": "granted"}, '{"outcome":"refused"}', "The order was set aside"],
    )
    assert result["agreement"] == 1 / 3
    assert result["n"] == 3
    assert [item["passed"] for item in result["rows"]] == [True, False, False]


def test_outcome_scorer_rejects_missing_or_guessed_labels():
    assert outcome({"outcome": "probably granted"}) is None
    assert outcome("The application is granted") is None
    assert score_rows([row("refused")], [{}])["agreement"] == 0


def test_gate_needs_both_99_percent_and_200_rows():
    from tank.graduate import Evidence, proven_reason
    policy = {"eval_threshold": .99, "min_shadow_samples": 200, "max_reject_rate": .05}
    assert proven_reason(policy, Evidence(samples=199, agreement=1))[0] is False
    assert proven_reason(policy, Evidence(samples=200, agreement=.9899))[0] is False
    assert proven_reason(policy, Evidence(samples=200, agreement=.99))[0] is True


def test_own_model_is_sealed_and_disabled_by_default():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "agents/zenas/docker-compose.za.yml").read_text())
    service = compose["services"]["zenas-own-model"]
    assert service["profiles"] == ["own-model"]
    assert "ports" not in service and "volumes" not in service
    assert service["read_only"] is True and service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert set(service["environment"]) == {"HF_TOKEN", "HF_HOME", "ZENAS_OWN_MODEL_KEY"}
    assert service["command"][-2:] == ["--api-key", "${ZENAS_OWN_MODEL_KEY:-}"]
    assert "@sha256:" in service["image"]
    assert compose["networks"]["own_model"]["external"] is True
