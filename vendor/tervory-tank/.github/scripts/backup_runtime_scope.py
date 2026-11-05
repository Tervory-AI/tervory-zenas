"""Names-only Boston backup scope check and existing-setting metadata repair."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


MAIN_APP = "xnmc0jkz8j9i5jh1jwanbsfv"
ENV_PATH = f"/applications/{MAIN_APP}/envs"
OWN_PAIR = {"R2_ACCESS_KEY_ID_UPLOADS", "R2_SECRET_ACCESS_KEY_UPLOADS"}
REPAIR_PAIR = {"R2_ACCESS_KEY_ID_GHOST", "R2_SECRET_ACCESS_KEY_GHOST"}


class ScopeError(RuntimeError):
    pass


def inventory(api):
    code, rows = api("GET", ENV_PATH)
    if code != 200 or not isinstance(rows, list):
        raise ScopeError("Cannot verify main runtime setting names; deployment blocked")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str):
            raise ScopeError("Invalid main runtime setting inventory; deployment blocked")
        if row.get("is_preview", False):
            continue
        key = row["key"]
        runtime = row.get("is_runtime")
        if type(runtime) is not bool or key in result:
            raise ScopeError("Ambiguous main runtime setting metadata; deployment blocked")
        result[key] = runtime
    return result


def foreign_pairs(settings):
    return {key for key, runtime in settings.items()
            if runtime and key.startswith(("R2_ACCESS_KEY_ID_", "R2_SECRET_ACCESS_KEY_"))
            and key not in OWN_PAIR}


def verify_main_scope(api):
    try:
        settings = inventory(api)
        foreign = foreign_pairs(settings)
        if foreign:
            raise ScopeError("Foreign main runtime R2 names: " + ", ".join(sorted(foreign)))
        if not all(settings.get(key) is True for key in OWN_PAIR):
            raise ScopeError("Main UPLOADS runtime setting names are missing or disabled")
        print("::notice title=backup scope::Main runtime R2 credential names are UPLOADS only")
        return True
    except ScopeError as error:
        print(f"::error title=backup scope::{error}")
        return False


def repair_main_scope(api, compose: Path):
    # Normal Coolify compose adds a shared .env to every service. No main
    # consumer may need these names before their runtime availability is disabled.
    source = compose.read_text()
    if any(key in source for key in REPAIR_PAIR):
        raise ScopeError("Main compose references GHOST names; metadata repair refused")
    settings = inventory(api)
    if not REPAIR_PAIR.issubset(settings):
        raise ScopeError("Existing main GHOST setting names are missing; no settings created")
    if foreign_pairs(settings) - REPAIR_PAIR:
        raise ScopeError("Unexpected foreign main R2 names; metadata repair refused")
    if not all(settings.get(key) is True for key in OWN_PAIR):
        raise ScopeError("Main UPLOADS runtime setting names are missing or disabled")
    for key in sorted(REPAIR_PAIR):
        if settings[key] is False:
            continue
        # PATCH existing by key returns 404 if absent. Omitting value preserves it.
        code, _ = api("PATCH", ENV_PATH,
                      {"key": key, "is_preview": False, "is_runtime": False})
        if code != 201:
            raise ScopeError("Existing-setting runtime metadata update failed; no deploy requested")
        print(f"::notice title=backup scope::{key}: is_runtime=false on main only")
    return verify_main_scope(api)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(method, path, payload=None):
    if os.environ.get("COOLIFY_URL", "").rstrip("/") != "https://coolify.tervory.com":
        raise ScopeError("Coolify URL must match the fixed HTTPS host")
    request = urllib.request.Request(
        "https://coolify.tervory.com/api/v1" + path, method=method,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + os.environ["COOLIFY_TOKEN"],
                 "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            # Mutation responses may contain sensitive fields: never read/store/print them.
            return response.status, json.load(response) if method == "GET" else None
    except urllib.error.HTTPError as error:
        return error.code, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.apply:
            return 0 if repair_main_scope(api, Path(__file__).resolve().parents[2] / "docker-compose.yml") else 1
        return 0 if verify_main_scope(api) else 1
    except ScopeError as error:
        print(f"::error title=backup scope::{error}")
        return 1
    except Exception:
        print("::error title=backup scope::Metadata operation failed; no response body exposed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
