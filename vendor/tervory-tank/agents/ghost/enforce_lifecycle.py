"""Per-client Enforce containers. Coolify API by name only — never type/see/store secrets.

Watch stays shared and tenant-scoped. Each client gets its own Enforce container on an
internal network, with its own ThreatPayload queue volume + THREAT_PAYLOAD_QUEUE env
and receipts (routes_to_queue_only). Queues are per-client — not shared across
clients; networks, secrets, and data volumes stay isolated. Optional edge
credentials are named only after a real connected hand exists (agnostic — hold CF
secret names until a real CF client). Disconnect destroys the container and any
secret name binding.

FixtureComposeBackend performs real create/destroy against a local compose-project
tree (one project dir per tenant) so tests prove isolation without Coolify/Docker.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from enforce_coolify import Coolify, CoolifyUnknown, Refused, identifier
except ModuleNotFoundError:  # Repository tests; image uses the copied file only.
    from agents.tisan.promotion import Coolify, CoolifyUnknown, Refused, identifier

COOLIFY_SETTINGS = (
    "AUTUMBRA_COOLIFY_URL", "AUTUMBRA_COOLIFY_TOKEN",
    "AUTUMBRA_COOLIFY_PROJECT_UUID", "AUTUMBRA_COOLIFY_SERVER_UUID",
    "AUTUMBRA_COOLIFY_ENVIRONMENT", "AUTUMBRA_COOLIFY_GITHUB_APP_UUID",
)
COMPOSE_PATH = Path(__file__).with_name("docker-compose.enforce.yml")
_LIFECYCLE_LOCK = asyncio.Lock()


def coolify_configured() -> bool:
    return all(os.environ.get(name, "").strip() for name in COOLIFY_SETTINGS)


# Founder correction: hold until measured dogfood / Boston RSS — do not guess 6/8/20.
DEFAULT_CONTAINER_CAP: int | None = None
FOUNDER_MUST_DECIDE_CAP = (
    "Enforce container cap is HELD until dogfood/Boston RSS is measured "
    "(ops/autumbra-enforce-memory-measure.sh on Coolify test host). "
    "Do not guess. Never live Boston. Smallest option: separate Coolify compose "
    "resource on the existing test host."
)
# Edge secret NAME pattern held until a real connected edge client (agnostic).
EDGE_SECRET_NAME_HELD = True

TENANT_RE = re.compile(r"^[0-9a-f-]{36}$", re.I)
PROJECT_PREFIX = "autumbra-enforce"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def project_name(tenant_id: str) -> str:
    if not TENANT_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id must be a UUID.")
    return f"{PROJECT_PREFIX}-{tenant_id.lower()}"


def tenant_slug(tenant_id: str) -> str:
    """Stable Coolify slug from tenant UUID. Never a secret value."""
    if not TENANT_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id must be a UUID.")
    return tenant_id.lower().replace("-", "_")


def credential_secret_name(tenant_id: str) -> str | None:
    """Coolify secret NAME only.

    Founder correction: no Cloudflare secret names yet — hold until a real edge
    client. Returns None while held.
    """
    tenant_slug(tenant_id)
    if EDGE_SECRET_NAME_HELD:
        return None
    return f"AUTUMBRA_EDGE_CREDENTIAL_{tenant_slug(tenant_id)}"


def queue_name(tenant_id: str) -> str:
    if not TENANT_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id must be a UUID.")
    return f"autumbra-threatpayload-{tenant_id.lower()}"


def network_name(tenant_id: str) -> str:
    if not TENANT_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id must be a UUID.")
    return f"autumbra-enforce-net-{tenant_id.lower()}"


def volume_name(tenant_id: str) -> str:
    if not TENANT_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id must be a UUID.")
    return f"autumbra-enforce-data-{tenant_id.lower()}"


def plan_create(
    tenant_id: str,
    *,
    existing_count: int,
    cap: int | None = DEFAULT_CONTAINER_CAP,
) -> dict[str, Any]:
    """Plan to create one Enforce container for this client."""
    if cap is not None and existing_count >= cap:
        raise RuntimeError(
            f"Enforce container cap ({cap}) reached. {FOUNDER_MUST_DECIDE_CAP}"
        )
    return {
        "action": "create_enforce",
        "tenant_id": tenant_id,
        "project": project_name(tenant_id),
        "network": {"name": network_name(tenant_id), "internal": True},
        "volume": volume_name(tenant_id),
        "credential_secret_name": credential_secret_name(tenant_id),
        "edge_secret_name_held": EDGE_SECRET_NAME_HELD,
        "threat_payload_queue": queue_name(tenant_id),
        "compose": "agents/ghost/docker-compose.enforce.yml",
        "entrypoint": "ghost.enforce_app:app",
        "coolify": "by name only — Autumbra room creates the resource; no secret values in code",
        "isolation": {
            "shares_network_with_tank": False,
            "shares_network_with_workspace": False,
            "shares_network_with_other_clients": False,
            "shares_volume_with_tervory": False,
            "public_internet": False,
            "routes_to_watch": False,
            "routes_to_queue_only": True,
            "edge_api_egress": False,
        },
        "cap": {
            "current": existing_count,
            "max": cap,
            "status": "held_until_measured" if cap is None else "set",
        },
        "mem_limit_status": "provisional_compose_placeholder — measure with ops/autumbra-enforce-memory-measure.sh",
        "founder_must_decide": FOUNDER_MUST_DECIDE_CAP,
    }


def plan_destroy(tenant_id: str) -> dict[str, Any]:
    return {
        "action": "destroy_enforce",
        "tenant_id": tenant_id,
        "project": project_name(tenant_id),
        "credential_secret_name": credential_secret_name(tenant_id),
        "threat_payload_queue": queue_name(tenant_id),
        "network": {"name": network_name(tenant_id), "internal": True},
        "volume": volume_name(tenant_id),
        "note": "Destroy container, named secret binding and internal network; keep volumes for founder cleanup.",
    }


def receipt_for(plan: dict[str, Any], *, outcome: str = "planned") -> dict[str, Any]:
    """Founder-visible receipt for create/destroy. Never includes secret values."""
    return {
        "id": str(uuid4()),
        "product": "Autumbra",
        "role": "Enforce lifecycle",
        "at": now_iso(),
        "outcome": outcome,
        "action": plan["action"],
        "tenant_id": plan["tenant_id"],
        "project": plan["project"],
        "network": plan.get("network"),
        "volume": plan.get("volume"),
        "threat_payload_queue": plan.get("threat_payload_queue"),
        "routes_to_queue_only": True,
        "queue_shared_across_clients": False,
        "queue_note": (
            "Per-client ThreatPayload queue volume + THREAT_PAYLOAD_QUEUE=/queue. "
            "Clients do not share queue, network, data volume, or secret name."
        ),
        "credential_secret_name": plan["credential_secret_name"],
        "secret_value_present": False,
    }


def assert_tenant_isolation(plan_a: dict[str, Any], plan_b: dict[str, Any]) -> None:
    """Two fixture clients must not share project, network, volume, queue or secret name."""
    if plan_a["tenant_id"] == plan_b["tenant_id"]:
        raise AssertionError("fixture clients must differ")
    for key in ("project", "threat_payload_queue", "volume"):
        if plan_a.get(key) == plan_b.get(key):
            raise AssertionError(f"shared {key}")
    a_secret, b_secret = plan_a.get("credential_secret_name"), plan_b.get("credential_secret_name")
    if a_secret is not None and b_secret is not None and a_secret == b_secret:
        raise AssertionError("shared credential_secret_name")
    if plan_a["network"]["name"] == plan_b["network"]["name"]:
        raise AssertionError("shared network")


@dataclass
class FixtureComposeBackend:
    """Local compose-project tree: one dir per tenant. Real create/destroy + receipts."""

    root: Path

    def _dir(self, project: str) -> Path:
        return self.root / project

    def create(self, plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("action") != "create_enforce":
            raise ValueError("create expects create_enforce plan")
        project_dir = self._dir(plan["project"])
        if project_dir.exists():
            raise RuntimeError(f"project already exists: {plan['project']}")
        project_dir.mkdir(parents=True)
        meta = {
            "tenant_id": plan["tenant_id"],
            "project": plan["project"],
            "network": plan["network"]["name"],
            "volume": plan["volume"],
            "credential_secret_name": plan["credential_secret_name"],
            "threat_payload_queue": plan["threat_payload_queue"],
            "entrypoint": plan.get("entrypoint") or "ghost.enforce_app:app",
            "created_at": now_iso(),
        }
        (project_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        (project_dir / "network.name").write_text(plan["network"]["name"] + "\n")
        (project_dir / "volume.name").write_text(plan["volume"] + "\n")
        secret = plan["credential_secret_name"]
        (project_dir / "secret.name").write_text(("HELD" if secret is None else secret) + "\n")
        (project_dir / "queue.name").write_text(plan["threat_payload_queue"] + "\n")
        # Per-client queue volume + env so routes_to_queue_only is real in the fixture.
        # Queue is NOT shared across clients (see assert_tenant_isolation).
        compose = (
            f"# fixture project for {plan['tenant_id']}\n"
            f"# routes_to_queue_only: ThreatPayload queue door (per-client volume)\n"
            f"name: {plan['project']}\n"
            f"networks:\n  enforce_internal:\n    name: {plan['network']['name']}\n    internal: true\n"
            f"volumes:\n"
            f"  enforce_data:\n    name: {plan['volume']}\n"
            f"  threat_queue:\n    name: {plan['threat_payload_queue']}\n"
            f"services:\n  enforce:\n    image: autumbra-enforce-fixture\n"
            f"    command: [\"uvicorn\", \"ghost.enforce_app:app\", \"--host\", \"127.0.0.1\", \"--port\", \"8000\"]\n"
            f"    environment:\n"
            f"      TENANT_ID: {plan['tenant_id']}\n"
            f"      THREAT_PAYLOAD_QUEUE: /queue\n"
            f"      DATA_DIR: /data\n"
            f"      AUTUMBRA_ROLE: enforce\n"
            f"    networks: [enforce_internal]\n"
            f"    volumes:\n"
            f"      - enforce_data:/data\n"
            f"      - threat_queue:/queue\n"
        )
        (project_dir / "docker-compose.yml").write_text(compose)
        return receipt_for(plan, outcome="created")

    def destroy(self, plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("action") != "destroy_enforce":
            # Allow destroy plan shape from plan_destroy.
            pass
        project_dir = self._dir(plan["project"])
        if project_dir.exists():
            shutil.rmtree(project_dir)
        return receipt_for(plan, outcome="destroyed")

    def read_isolation(self, tenant_id: str) -> dict[str, str | None]:
        meta_path = self._dir(project_name(tenant_id)) / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(tenant_id)
        meta = json.loads(meta_path.read_text())
        return {
            "network": meta["network"],
            "volume": meta["volume"],
            "secret_name": meta["credential_secret_name"],
            "project": meta["project"],
            "threat_payload_queue": meta.get("threat_payload_queue"),
        }


class CoolifyEnforceBackend:
    """Dedicated-team Coolify API. No deployment or environment reads, no retries."""

    prefix = PROJECT_PREFIX
    compose_location = "/agents/ghost/docker-compose.enforce.yml"
    description_prefix = "autumbra-enforce:"
    compose_path = None
    create_options = {}
    delete_connected_networks = "true"
    settings_names = COOLIFY_SETTINGS
    plan_create = staticmethod(plan_create)
    plan_destroy = staticmethod(plan_destroy)
    receipt_for = staticmethod(receipt_for)
    project_name = staticmethod(project_name)

    def __init__(self, coolify: Coolify, *, project: str, server: str,
                 environment: str, github_app: str):
        self.coolify = coolify
        self.project, self.server = identifier(project), identifier(server)
        self.environment, self.github_app = environment, identifier(github_app)

    @classmethod
    def from_env(cls, http):
        return cls(Coolify(http, os.environ[cls.settings_names[0]],
                           os.environ[cls.settings_names[1]],
                           url_setting=cls.settings_names[0], token_setting=cls.settings_names[1]),
                   project=os.environ[cls.settings_names[2]],
                   server=os.environ[cls.settings_names[3]],
                   environment=os.environ[cls.settings_names[4]],
                   github_app=os.environ[cls.settings_names[5]])

    async def _applications(self):
        rows = await self.coolify.call("GET", "/applications", allow_list=True)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in rows):
            raise Refused("Coolify application list unavailable")
        return rows

    async def list(self) -> list[dict[str, str]]:
        result = []
        for row in await self._applications():
            if re.fullmatch(re.escape(self.prefix) + r"-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", row["name"]):
                result.append({"name": row["name"], "uuid": identifier(row.get("uuid", "")),
                               "status": str(row.get("status") or "unknown"),
                               **{key: row.get(key) for key in ("description", "git_repository", "build_pack", "docker_compose_location")}})
        return result

    async def create_plan(self, tenant_id: str) -> dict[str, Any]:
        name = self.project_name(tenant_id)
        rows = await self._applications()
        if any(row["name"] == name for row in rows):
            raise Refused("Enforce project already exists")
        count = sum(row["name"].startswith(self.prefix) for row in rows)
        return self.plan_create(tenant_id.lower(), existing_count=count, cap=DEFAULT_CONTAINER_CAP)

    @staticmethod
    def settings_for(plan: dict[str, Any]) -> dict[str, str]:
        return {"TENANT_ID": plan["tenant_id"], "ENFORCE_DATA_VOLUME": plan["volume"],
                "THREAT_PAYLOAD_QUEUE_VOLUME": plan["threat_payload_queue"],
                "ENFORCE_NETWORK_NAME": plan["network"]["name"]}

    @classmethod
    def founder_fills(cls, plan: dict[str, Any]) -> list[str]:
        names = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\}", (cls.compose_path or COMPOSE_PATH).read_text()))
        return sorted(names - cls.settings_for(plan).keys())

    async def create(self, plan: dict[str, Any]) -> dict[str, Any]:
        # Watch runs one worker; serialize the count/check/write across requests.
        async with _LIFECYCLE_LOCK:
            return await self._create(plan)

    async def _create(self, plan: dict[str, Any]) -> dict[str, Any]:
        # Re-plan at the write boundary: neither a body count nor a stale plan is authority.
        plan = await self.create_plan(plan["tenant_id"])
        receipt = self.receipt_for(plan)
        receipt["founder_fills"] = self.founder_fills(plan)
        mutated = False
        try:
            created = await self.coolify.call("POST", "/applications/private-github-app", mutation=True,
                json={"project_uuid": self.project, "server_uuid": self.server,
                      "environment_name": self.environment, "github_app_uuid": self.github_app,
                      "git_repository": "https://github.com/Tervory-AI/tervory-tank.git",
                      "git_branch": "main", "build_pack": "dockercompose", "name": plan["project"],
                      "description": self.description_prefix + plan["tenant_id"],
                      "docker_compose_location": self.compose_location,
                      "instant_deploy": False, "is_auto_deploy_enabled": False, **self.create_options})
            try:
                app_uuid = identifier(created.get("uuid", ""))
            except (ValueError, TypeError):
                raise CoolifyUnknown("Coolify mutation outcome unknown") from None
            mutated = True
            receipt["uuid"] = app_uuid
            await self.coolify.call("PATCH", f"/applications/{app_uuid}/envs/bulk",
                mutation=True, discard_response=True,
                json={"data": [{"key": key, "value": value, "is_preview": False,
                                "is_buildtime": True, "is_runtime": True, "is_literal": True}
                               for key, value in self.settings_for(plan).items()]})
            receipt["outcome"] = "created"
        except CoolifyUnknown:
            receipt["outcome"] = "unknown"
        except Refused:
            # The resource may exist even when its four settings could not be written.
            receipt["outcome"] = "created_unconfigured" if mutated else "refused"
        return receipt

    async def destroy(self, plan: dict[str, Any]) -> dict[str, Any]:
        async with _LIFECYCLE_LOCK:
            return await self._destroy(plan)

    async def _destroy(self, plan: dict[str, Any]) -> dict[str, Any]:
        plan = self.plan_destroy(plan["tenant_id"].lower())
        matches = [row for row in await self.list() if row["name"] == plan["project"]]
        if len(matches) > 1:
            raise Refused("Enforce project name is not unique")
        if not matches:
            return self.receipt_for(plan, outcome="absent")
        target = matches[0]
        repository = target.get("git_repository")
        own_repository = (isinstance(repository, str) and repository.lower().removeprefix("https://github.com/").removesuffix(".git") == "tervory-ai/tervory-tank")
        if (target.get("description") != self.description_prefix + plan["tenant_id"]
                or target.get("build_pack") != "dockercompose"
                or target.get("docker_compose_location") != self.compose_location
                or not own_repository):
            raise Refused("Enforce resource was not created by Autumbra")
        receipt = self.receipt_for(plan)
        receipt["uuid"] = matches[0]["uuid"]
        try:
            await self.coolify.call("DELETE", "/applications/" + matches[0]["uuid"],
                mutation=True, discard_response=True,
                params={"delete_configurations": "true", "delete_volumes": "false",
                        "delete_connected_networks": self.delete_connected_networks, "docker_cleanup": "false"})
            receipt["outcome"] = "destroyed"
        except CoolifyUnknown:
            receipt["outcome"] = "unknown"
        return receipt
