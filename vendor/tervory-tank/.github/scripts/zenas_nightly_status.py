"""Read only the two existing regional Zenas tasks; never emit API bodies."""
from __future__ import annotations

from datetime import datetime
import json
import os
import urllib.error
import urllib.request


TARGETS = (
    ("us", "r7qzm460bc4scnskurlowbvn", "agent-zenas", "fz7xlcsopjtgpmycnb0swwt1"),
    ("za", "rslmzhfmq5tf9ipx385gmu6p", "agent-zenas-za", "m6ggpxv2iegsecpkakqzlwxd"),
)
TASK_NAME = "zenas-pool-nightly"
HOST = "https://coolify.tervory.com"
ALLOWED_PATHS = frozenset(
    path
    for _, app, _, task in TARGETS
    for path in (
        f"/applications/{app}",
        f"/applications/{app}/scheduled-tasks",
        f"/applications/{app}/scheduled-tasks/{task}/executions",
    )
)


class MetadataError(Exception):
    pass


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise MetadataError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MetadataError
    return parsed.isoformat()


def region_status(get, target):
    region, app, name, task = target
    result = {"region": region, "resource_uuid": app, "task_uuid": task,
              "name": TASK_NAME}
    resource = get(f"/applications/{app}")
    if not isinstance(resource, dict) or resource.get("uuid") != app or resource.get("name") != name:
        raise MetadataError
    tasks = get(f"/applications/{app}/scheduled-tasks")
    if not isinstance(tasks, list) or any(not isinstance(row, dict) for row in tasks):
        raise MetadataError
    matches = [row for row in tasks if row.get("uuid") == task]
    if len(matches) != 1 or matches[0].get("name") != TASK_NAME:
        raise MetadataError
    enabled = matches[0].get("enabled")
    frequency = matches[0].get("frequency")
    if type(enabled) is not bool or not isinstance(frequency, str) or not frequency or len(frequency) > 100:
        raise MetadataError
    if any(char not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ*/,- @" for char in frequency):
        raise MetadataError
    result.update(enabled=enabled, frequency=frequency)
    executions = get(f"/applications/{app}/scheduled-tasks/{task}/executions")
    if not isinstance(executions, list):
        raise MetadataError
    safe = []
    for row in executions:
        if not isinstance(row, dict) or row.get("status") not in ("success", "failed", "running"):
            raise MetadataError
        started = timestamp(row.get("started_at"))
        finished = timestamp(row.get("finished_at"))
        # The installed API has no structured exit code. Do not parse output or infer one.
        item = {"status": row["status"], "started_at": started,
                "finished_at": finished, "exit_code": None}
        order = started or timestamp(row.get("created_at"))
        if order is None:
            raise MetadataError
        if finished is not None and datetime.fromisoformat(finished) < datetime.fromisoformat(order):
            raise MetadataError
        safe.append((datetime.fromisoformat(order), item))
    result["executions"] = [item for _, item in sorted(safe, key=lambda pair: pair[0], reverse=True)[:10]]
    result["last_execution"] = result["executions"][0] if safe else None
    result["metadata_status"] = "verified" if safe else "no_executions"
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_metadata(path):
    if path not in ALLOWED_PATHS or os.environ.get("COOLIFY_URL", "").rstrip("/") != HOST:
        raise MetadataError
    request = urllib.request.Request(
        HOST + "/api/v1" + path, method="GET",
        headers={"Authorization": "Bearer " + os.environ["COOLIFY_TOKEN"], "Accept": "application/json"},
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        if response.status != 200:
            raise MetadataError
        return json.load(response)


def main(get=get_metadata):
    results = []
    failed = False
    for target in TARGETS:
        try:
            results.append(region_status(get, target))
        except Exception:
            # Neither exception strings nor response fields are safe diagnostic output.
            region, app, _, task = target
            results.append({"region": region, "resource_uuid": app, "task_uuid": task,
                            "name": TASK_NAME, "metadata_status": "unverified"})
            failed = True
    print(json.dumps(results, sort_keys=True))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
