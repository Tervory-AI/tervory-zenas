"""The US Zenas room compose fails closed when TANK_REGION is missing."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_room_region_has_no_default() -> None:
    compose = yaml.safe_load((ROOT / "agents/zenas/docker-compose.yml").read_text())
    region = compose["services"]["agent-zenas"]["environment"]["TANK_REGION"]
    assert region.startswith("${TANK_REGION:?"), region
    assert ":-" not in region
