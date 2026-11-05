"""One op that gives an agent its room in Coolify, with nobody touching a secret.

1. Create the room's Coolify app (agents/<room>, docker compose, isolated) if it does not exist.
2. For AGENT_DB_PASSWORD_<ROOM>, TANK_KEY_<ROOM> (+ any extra names): if the room has no value yet,
   generate one here (48 hex chars), mask it, and write it to the main app and the room. Never printed.
   Done once per room (marker TERVORY_ROOM_SECRETS), so re-running never rotates them by accident.
3. Deploy the main app (creates the login, joins Tank/Hermes to the corridor), wait, deploy the room.
The corridor network is declared in the main docker-compose.yml (compose creates it), so no server command.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request

URL = os.environ["COOLIFY_URL"].rstrip("/") + "/api/v1"
TOKEN = os.environ["COOLIFY_TOKEN"]
ROOM = os.environ.get("ROOM", "")
EXTRA = [n for n in os.environ.get("NAMES", "").split(",") if n]
MAIN = "xnmc0jkz8j9i5jh1jwanbsfv"
PROJECT = os.environ["PROJECT_UUID"]
ENV_NAME = os.environ["ENVIRONMENT_NAME"]


def note(title: str, msg: str) -> None:
    print(f"::notice title={title}::{msg}", flush=True)


def fail(title: str, msg: str) -> None:
    print(f"::error title={title}::{msg}", flush=True)
    sys.exit(1)


def api(method: str, path: str, body: dict | None = None):
    if URL != "https://coolify.tervory.com/api/v1":
        raise ValueError("COOLIFY_URL must match the fixed https Coolify base")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://coolify.tervory.com/api/v1" + path, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode(errors="replace")


if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", ROOM):
    fail("room-setup", "room must be a lowercase agent folder name, e.g. rig")
upper = ROOM.upper()
app_name = "agent-" + ROOM.replace("_", "-")

# 1. the app
code, apps = api("GET", "/applications")
if code != 200:
    fail("applications", f"HTTP {code}")
room = next((a for a in apps if a.get("name") == app_name), None)
if room is None:
    _, servers = api("GET", "/servers")
    _, gh = api("GET", "/github-apps")
    gh_app = next((g["uuid"] for g in gh if g.get("name") == "tervory-git-hub"), None)
    if not servers or not gh_app:
        fail("create", "server or GitHub app not found")
    code, made = api("POST", "/applications/private-github-app", {
        "project_uuid": PROJECT, "environment_name": ENV_NAME, "server_uuid": servers[0]["uuid"],
        "github_app_uuid": gh_app, "git_repository": "Tervory-AI/tervory-tank", "git_branch": "main",
        "build_pack": "dockercompose", "base_directory": f"/agents/{ROOM}",
        "docker_compose_location": "/docker-compose.yml", "name": app_name,
        "ports_exposes": "8000", "instant_deploy": False})
    if code not in (200, 201):
        fail("create", f"HTTP {code} {made}")
    room = {"uuid": made["uuid"], "name": app_name}
    note("Room created", f"{app_name} {room['uuid']}")
else:
    note("Room exists", f"{app_name} {room['uuid']}")

# 2. secrets
# The API may hide env values (tokens without "sensitive" read), so we cannot tell an empty slot from
# a set one by its value. A marker variable on the room records that secrets were generated once.
MARKER = "TERVORY_ROOM_SECRETS"
code, envs = api("GET", f"/applications/{room['uuid']}/envs")
if code != 200 or not isinstance(envs, list):
    fail("envs", f"HTTP {code}")
keys = {e.get("key") for e in envs}
shared = [f"AGENT_DB_PASSWORD_{upper}", f"TANK_KEY_{upper}"]
wanted = shared + [n for n in EXTRA if n not in keys]
if MARKER in keys:
    wanted = [n for n in EXTRA if n not in keys]
made_names = []
for name in wanted:
    value = secrets.token_hex(24)
    print(f"::add-mask::{value}", flush=True)
    item = {"key": name, "value": value, "is_preview": False, "is_literal": True}
    targets = [room["uuid"]] + ([MAIN] if name in shared else [])
    for uuid in targets:
        code, out = api("PATCH", f"/applications/{uuid}/envs/bulk", {"data": [item]})
        if code not in (200, 201):
            fail("secret", f"{name} on {uuid}: HTTP {code}")
    made_names.append(name)
if MARKER not in keys:
    api("PATCH", f"/applications/{room['uuid']}/envs/bulk",
        {"data": [{"key": MARKER, "value": "generated", "is_preview": False, "is_literal": True}]})
note("Secrets", ("generated and stored: " + ", ".join(made_names)) if made_names else "already set, left alone")

# 3. deploy main, then the room
for uuid, wait in ((MAIN, 240), (room["uuid"], 0)):
    code, out = api("POST", f"/deploy?uuid={uuid}&force=false")
    if code not in (200, 201):
        fail("deploy", f"{uuid}: HTTP {code} {out}")
    note("Deploy queued", uuid)
    time.sleep(wait)
note("Next", f"Claude runs the isolation check on {app_name}")
