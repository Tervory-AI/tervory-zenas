"""Only bounded facts cross the monitoring boundary; never raw logs or headers."""
from __future__ import annotations

from enum import Enum
import re
from pydantic import BaseModel, ConfigDict, Field

PLATFORM_TENANT = "00000000-0000-0000-0000-000000000001"
SITES = {"workspace": "https://app.tervory.com", "za": "https://za.tervory.com", "site": "https://tervory.com"}
ROOMS = ("base", "tiger", "market_lab", "rig", "tisan", "radix", "zenas", "ghost")


class Target(str, Enum):
    workspace = "workspace"
    za = "za"
    site = "site"
    tank = "tank"
    base = "base"
    tiger = "tiger"
    market_lab = "market_lab"
    rig = "rig"
    tisan = "tisan"
    radix = "radix"
    zenas = "zenas"
    ghost = "ghost"


class Signal(str, Enum):
    health = "health"
    slow = "slow"
    dns = "dns"
    certificate = "certificate"
    headers = "headers"
    errors = "errors"
    deploy = "deploy"
    ci = "ci"
    radix = "radix"
    human_check = "human_check"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Observation(Strict):
    target: Target
    signal: Signal
    failing: bool
    count: int = Field(default=1, ge=0, le=1000000)
    latency_ms: int | None = Field(default=None, ge=0, le=120000)
    status_code: int | None = Field(default=None, ge=100, le=599)


class Look(Strict):
    target: Target
    signal: Signal = Signal.health


class Message(Strict):
    # Transient only: never stored, logged, or sent to a model.
    text: str = Field(min_length=1, max_length=1000)


class Resolution(Strict):
    cause: str = Field(pattern=r"^(crashed_room|failed_deploy|application_bug|dns_configuration|certificate_expired|missing_headers|dependency_failure)$")
    fix: str = Field(pattern=r"^(room_restarted|deploy_rerun|reviewed_pr_deployed|dns_corrected|certificate_renewed|headers_corrected)$")


def classify_message(text: str) -> Look:
    """A message points at a target, never becomes executable instructions."""
    lowered = re.sub(r"^\s*ghost\s*[,!:]\s*", "", text.lower())
    target = next((name for name in Target if re.search(r"\b" + name.value + r"\b", lowered)), Target.workspace)
    signal = Signal.slow if any(word in lowered for word in ("slow", "latency")) else Signal.health
    if any(word in lowered for word in ("captcha", "human check")):
        signal = Signal.human_check
    return Look(target=target, signal=signal)
