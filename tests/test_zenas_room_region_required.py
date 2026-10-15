"""The Zenas room fails closed when TANK_REGION is missing.

The US compose refuses to start. The process also refuses: a missing value
is not treated as us-east.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.zenas.app import _box_region  # noqa: E402


def test_room_region_has_no_default() -> None:
    compose = yaml.safe_load((ROOT / "agents/zenas/docker-compose.yml").read_text())
    region = compose["services"]["agent-zenas"]["environment"]["TANK_REGION"]
    assert region.startswith("${TANK_REGION:?"), region
    assert ":-" not in region
    app_py = (ROOT / "agents/zenas/app.py").read_text(encoding="utf-8")
    assert 'os.environ.get("TANK_REGION", "us-east")' not in app_py


@pytest.mark.parametrize("value", ["", " ", "eu", "us"])
def test_room_region_refuses_a_missing_or_unknown_value(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    if value == "":
        monkeypatch.delenv("TANK_REGION", raising=False)
    else:
        monkeypatch.setenv("TANK_REGION", value)
    with pytest.raises(HTTPException) as caught:
        _box_region()
    assert caught.value.status_code == 409


@pytest.mark.parametrize("value", ["us-east", "za", "ZA"])
def test_room_region_accepts_only_the_two_homes(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TANK_REGION", value)
    assert _box_region() == value.strip().lower()
