"""Explicit fake founder choice for existing offline training-contract tests."""
import pytest


@pytest.fixture(autouse=True)
def configured_base(monkeypatch):
    monkeypatch.setenv('HF_TRAIN_BASE_MODEL', 'fixture/training-base')
    monkeypatch.setenv('HF_TRAIN_BASE_REVISION', 'a' * 40)
    monkeypatch.setenv('HF_TRAIN_MAX_USD', '50')
